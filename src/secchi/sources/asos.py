"""Daily weather at the two airports nearest the lake, from NOAA's ASOS network.

    TVL  South Lake Tahoe airport   1,909 m, in the basin, 3 km from the south shore
    TRK  Truckee-Tahoe airport      1,798 m, Martis Valley, just outside the basin

Why: every other weather record here is up on a ridge (SNOTEL) or in a
forest clearing (TEON's stations). The airports are at lake level, report
around the clock, and have done so since the 1970s — the in-basin, lake-
level check on "was that rain or snow". They are context, not TEON, and
live under "Beyond TEON" on the page.

Source: the Iowa Environmental Mesonet's daily summaries of ASOS/METAR
reports (https://mesonet.agron.iastate.edu/request/daily.phtml), the usual
free route to this data. Verified 2026-10-02 from a GitHub runner: CSV
with ``station,day,max_temp_f,min_temp_f,precip_in,...``. Days are local
calendar days. ASOS does not measure snowfall, so there is none.

IEM is a university service and was "over capacity" for much of that
evening. So: one request for both stations, only days before today (a
day still in progress would be stored half-finished), nothing at all if
yesterday is already held, and a 503 or 429 is a warning, not a failure.
"""

from __future__ import annotations

import io
import logging
import time
from datetime import date, timedelta

import pandas as pd

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.sources.asos")

URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
NETWORK = "CA_ASOS"
# From IEM's CA_ASOS station list (geojson), 2026-10-02.
STATIONS: dict[str, dict] = {
    "TVL": {"name": "South Lake Tahoe airport", "lat": 38.8939, "lng": -119.9953,
            "elevation_m": 1909, "in_basin": True},
    "TRK": {"name": "Truckee-Tahoe airport", "lat": 39.32, "lng": -120.1396,
            "elevation_m": 1798, "in_basin": False},
}
FIRST_DAY = date(2024, 6, 1)
ROOT = PROCESSED_DIR / "asos_observations"
STORE_KEY = ["uuid", "site", "variable"]
EXPECTED = ["station", "day", "max_temp_f", "min_temp_f", "precip_in"]


class AsosShapeError(RuntimeError):
    """A reply that isn't the CSV this was written against."""


class AsosBusyError(RuntimeError):
    """IEM said it is over capacity or rate-limited. Try again next run."""


def parse(text: str) -> pd.DataFrame:
    """IEM's daily CSV as store rows: TMAX and TMIN in °C, PRCP in mm."""
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    missing = [c for c in EXPECTED if c not in df.columns]
    if missing:
        raise AsosShapeError(f"columns {list(df.columns)}, missing {missing}; "
                             f"starts {text[:200]!r}")
    rows = []
    for _, r in df.iterrows():
        st = STATIONS.get(r["station"].strip())
        if st is None:
            continue
        day = pd.Timestamp(r["day"].strip()[:10])
        for var, col, conv, unit in (
            ("TMAX", "max_temp_f", lambda f: (f - 32) * 5 / 9, "degC"),
            ("TMIN", "min_temp_f", lambda f: (f - 32) * 5 / 9, "degC"),
            ("PRCP", "precip_in", lambda i: i * 25.4, "mm"),
        ):
            raw = r[col].strip()
            if raw in ("", "M", "None"):
                continue                     # missing stays missing, never zero
            # IEM writes a trace of precipitation as a tiny positive number.
            try:
                v = float(raw)
            except ValueError:
                if raw.upper() == "T":
                    v = 0.0001
                else:
                    raise AsosShapeError(f"{r['station']} {day:%Y-%m-%d} {col}: {raw!r}") from None
            rows.append({
                "uuid": f"asos:{r['station'].strip()}:{var}:{day:%Y-%m-%d}",
                "source": "ASOS", "site": st["name"], "sensor_type": "Asos",
                "timestamp": day, "lat": st["lat"], "lng": st["lng"],
                "variable": var, "value": round(conv(v), 3), "unit": unit,
            })
    return pd.DataFrame(rows)


def _window(today: date) -> tuple[date, date] | None:
    from secchi.store import read_partitions
    yesterday = today - timedelta(days=1)
    held = read_partitions(ROOT, columns=["site", "timestamp"]) if ROOT.exists() else None
    if held is None or held.empty or set(held["site"]) < {s["name"] for s in STATIONS.values()}:
        return FIRST_DAY, yesterday
    newest = pd.to_datetime(held["timestamp"]).groupby(held["site"]).max().min().date()
    if newest >= yesterday:
        return None
    # A few days back: IEM fills late METARs into recent days.
    return newest - timedelta(days=3), yesterday


def fetch(begin: date, end: date) -> str:
    import httpx
    params = [("network", NETWORK)] + [("stations", s) for s in STATIONS] + [
        ("year1", begin.year), ("month1", begin.month), ("day1", begin.day),
        ("year2", end.year), ("month2", end.month), ("day2", end.day),
        ("var", "max_temp_f"), ("var", "min_temp_f"), ("var", "precip_in"),
        ("na", "blank"), ("format", "csv")]
    with httpx.Client(timeout=120, follow_redirects=True,
                      headers={"User-Agent": "secchi (+https://github.com/bdgroves/secchi)"}) as c:
        for attempt in (1, 2):
            resp = c.get(URL, params=params)
            if resp.status_code in (429, 503):
                if attempt == 1:
                    time.sleep(30)
                    continue
                raise AsosBusyError(f"IEM HTTP {resp.status_code}: {resp.text[:120].strip()}")
            resp.raise_for_status()
            return resp.text
    raise AsosBusyError("IEM did not answer")


def _today_at_the_lake() -> date:
    # IEM's days are local calendar days, and CI runs in UTC: after 4 or 5
    # pm Pacific, UTC's "yesterday" is the lake's today, still in progress.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def ingest(since: date | None = None, today: date | None = None) -> int:
    from secchi.store import write_partitions
    today = today or _today_at_the_lake()
    window = (since, today - timedelta(days=1)) if since is not None else _window(today)
    if window is None:
        log.info("ASOS: yesterday already held; nothing to fetch")
        return 0
    begin, end = window
    df = parse(fetch(begin, end))
    if df.empty:
        log.warning("ASOS returned no values for %s to %s", begin, end)
        return 0
    out = write_partitions(df, ROOT, "asos", STORE_KEY)
    log.info("ASOS: %s value(s) for %s to %s; +%s new", f"{len(df):,}", begin, end,
             f"{out['rows_added']:,}")
    return len(df)
