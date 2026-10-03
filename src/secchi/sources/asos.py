"""Daily weather at the two airports nearest the lake (NWS, via NOAA's ACIS).

    TVL  South Lake Tahoe airport   1,907 m, in the basin, about 5 km from the south shore
    TRK  Truckee-Tahoe airport      1,798 m, Martis Valley, just outside the basin

Why: every other weather record here is up on a ridge (SNOTEL) or in a
forest clearing (TEON's stations). The airports are at lake level and
report every day — the in-basin, lake-level check on "was that rain or
snow". They are context, not TEON, and live under "Beyond TEON".

Source: the Applied Climate Information System of NOAA's Regional
Climate Centers (https://data.rcc-acis.org/StnData) — the official NWS
daily climate record. Verified 2026-10-02 from a GitHub runner:
South Lake Tahoe since 1968-05-01, Truckee since 2000-01-01; Truckee
also reports snowfall and snow depth, South Lake Tahoe doesn't ("M").
Values are strings: a number, "M" (missing) or "T" (trace). Days are
local calendar days.

The first version read the Iowa Environmental Mesonet instead. It
answered small requests but refused the two-year backfill with "server
over capacity" for a whole evening, so it never stored a day. ACIS
returned the same two years in under a second. The module and mode keep
the name ``asos`` (the stations are ASOS sites); only the route changed.

Only days before today are fetched (a day in progress would be stored
half-finished), and nothing at all once yesterday is held.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta

import pandas as pd

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.sources.asos")

URL = "https://data.rcc-acis.org/StnData"
# Coordinates and elevations as ACIS reports them (StnMeta, 2026-10-02).
STATIONS: dict[str, dict] = {
    "TVL": {"name": "South Lake Tahoe airport", "lat": 38.89838, "lng": -119.99617,
            "elevation_m": 1907, "in_basin": True, "since": "1968-05-01"},
    "TRK": {"name": "Truckee-Tahoe airport", "lat": 39.32, "lng": -120.13944,
            "elevation_m": 1798, "in_basin": False, "since": "2000-01-01"},
}
FIRST_DAY = date(2024, 6, 1)
ROOT = PROCESSED_DIR / "asos_observations"
STORE_KEY = ["uuid", "site", "variable"]
# ACIS element -> (stored variable, conversion, unit). Order matters: it is
# the order of the values in each returned row.
ELEMENTS = (
    ("maxt", "TMAX", lambda f: (f - 32) * 5 / 9, "degC"),
    ("mint", "TMIN", lambda f: (f - 32) * 5 / 9, "degC"),
    ("pcpn", "PRCP", lambda i: i * 25.4, "mm"),
    ("snow", "SNOW", lambda i: i * 2.54, "cm"),
    ("snwd", "SNWD", lambda i: i * 2.54, "cm"),
)
TRACE = 0.001        # a trace is real precipitation, too small to measure: not zero


class AsosShapeError(RuntimeError):
    """A reply that isn't the JSON this was written against."""


class AsosBusyError(RuntimeError):
    """The service is down or refusing. Try again next run."""


def parse(payload: dict, code: str) -> pd.DataFrame:
    """One station's ACIS reply as store rows: °C, mm, cm."""
    st = STATIONS[code]
    if not isinstance(payload, dict) or "data" not in payload:
        raise AsosShapeError(f"{code}: expected {{meta, data}}, got {str(payload)[:200]}")
    if "error" in payload:
        raise AsosShapeError(f"{code}: ACIS error {payload['error']!r}")
    rows = []
    for rec in payload["data"]:
        if not isinstance(rec, list) or len(rec) != 1 + len(ELEMENTS):
            raise AsosShapeError(f"{code}: row {rec!r} doesn't have {1 + len(ELEMENTS)} fields")
        day = pd.Timestamp(rec[0])
        for raw, (_el, var, conv, unit) in zip(rec[1:], ELEMENTS):
            raw = str(raw).strip()
            if raw in ("M", "", "S"):        # missing, or accumulated into a later day
                continue                      # stays missing, never zero
            if raw == "T":
                v = TRACE
            else:
                try:
                    v = float(raw.rstrip("A"))  # "A": the value is an accumulation
                except ValueError:
                    raise AsosShapeError(f"{code} {day:%Y-%m-%d} {var}: {raw!r}") from None
            rows.append({
                "uuid": f"asos:{code}:{var}:{day:%Y-%m-%d}",
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
    # A few days back: the NWS daily climate record is revised for a few
    # days after the fact (late reports, quality control).
    return newest - timedelta(days=3), yesterday


def fetch(code: str, begin: date, end: date) -> dict:
    import httpx
    body = {"sid": code, "sdate": begin.isoformat(), "edate": end.isoformat(),
            "elems": ",".join(el for el, *_ in ELEMENTS), "meta": "name,ll,elev"}
    with httpx.Client(timeout=120, follow_redirects=True,
                      headers={"User-Agent": "secchi (+https://github.com/bdgroves/secchi)"}) as c:
        for attempt in (1, 2):
            resp = c.post(URL, json=body)
            if resp.status_code in (429, 502, 503, 504):
                if attempt == 1:
                    time.sleep(20)
                    continue
                raise AsosBusyError(f"ACIS HTTP {resp.status_code}: {resp.text[:120].strip()}")
            resp.raise_for_status()
            try:
                return resp.json()
            except json.JSONDecodeError:
                raise AsosShapeError(f"{code}: not JSON: {resp.text[:200]!r}") from None
    raise AsosBusyError("ACIS did not answer")


def _today_at_the_lake() -> date:
    # The record's days are local calendar days, and CI runs in UTC: after
    # 4 or 5 pm Pacific, UTC's "yesterday" is the lake's today, in progress.
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
    df = pd.concat([parse(fetch(code, begin, end), code) for code in STATIONS],
                   ignore_index=True)
    if df.empty:
        log.warning("ASOS returned no values for %s to %s", begin, end)
        return 0
    out = write_partitions(df, ROOT, "asos", STORE_KEY)
    log.info("ASOS: %s value(s) for %s to %s; +%s new", f"{len(df):,}", begin, end,
             f"{out['rows_added']:,}")
    return len(df)
