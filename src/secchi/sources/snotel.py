"""Daily precipitation and air temperature from NRCS SNOTEL.

Why this exists: the transect compares how two shores' soils respond to
storms, and the obvious check is the rain itself. TEON has one rain gauge,
at Blackwood 2 on the west shore, and it is missing 136 days — most of
November 2025 and January 2026 among them — with nothing on the east side
to compare against. SNOTEL has long-running stations near both ends of the
transect:

    west   Ward Creek #3   a few miles north of Homewood
    west   Rubicon #2      south of Homewood (backup)
    east   Marlette Lake   in the hills just above Glenbrook

Stations are named, not numbered. Their IDs ("station triplets",
``StationID:State:SNTL``) are looked up by name from SNOTEL's own station
list on first run and saved to ``data/reference/snotel_stations.json``,
rather than typed in from memory.

Service: the NRCS Air and Water Database REST API,
https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/ — the ``stations``
and ``data`` endpoints. Daily SNOTEL values cover midnight to midnight
Pacific Standard Time and are requested with ``periodRef=END``, so each
value is dated to its own day.

Elements:

    PRCP   precipitation increment for the day
    PREC   precipitation accumulated since the water year began (Oct 1)
    TAVG   average air temperature for the day — tells rain from snow

Both precipitation forms are kept so one can check the other. Values are
stored in millimetres and degrees Celsius; SNOTEL reports inches and
degrees Fahrenheit, and the conversion uses the unit SNOTEL states.

The response shape was taken from the service's documentation and from
independent clients of it, not tested from here (this workspace can't
reach the service). So the parser is strict: if a response doesn't look
as expected it stops and prints a sample of what arrived, instead of
quietly storing nothing.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

import pandas as pd

from secchi.config import PROCESSED_DIR, REFERENCE_DIR

log = logging.getLogger("secchi.sources.snotel")

SNOTEL_ROOT = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/"
STATION_CACHE = REFERENCE_DIR / "snotel_stations.json"
SNOTEL_STATES = ("CA", "NV")

# name -> which shore it represents
SNOTEL_STATIONS: dict[str, str] = {
    "Ward Creek #3": "west",
    "Rubicon #2": "west",
    "Marlette Lake": "east",
}
ELEMENTS = ("PRCP", "PREC", "TAVG")

# The first day of TEON's record; a first run fetches from here.
FIRST_DAY = date(2024, 6, 1)
STORE_KEY = ["uuid", "site", "variable"]


class SnotelShapeError(RuntimeError):
    """A response that doesn't match the documented layout."""


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


def _sample(payload) -> str:
    text = json.dumps(payload, default=str)
    return text[:400] + ("…" if len(text) > 400 else "")


def resolve_stations(client, refresh: bool = False) -> dict[str, dict]:
    """Station name -> {triplet, lat, lng, elevation, side}, cached on disk."""
    if STATION_CACHE.exists() and not refresh:
        cached = json.loads(STATION_CACHE.read_text(encoding="utf-8"))
        if all(n in cached for n in SNOTEL_STATIONS):
            return cached

    triplets = ",".join(f"*:{s}:SNTL" for s in SNOTEL_STATES)
    resp = client.get(SNOTEL_ROOT + "stations", params={"stationTriplets": triplets})
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list) or not payload:
        raise SnotelShapeError(f"stations: expected a list of stations, got {_sample(payload)}")

    by_name = {}
    for st in payload:
        name = st.get("name") or st.get("stationName")
        triplet = st.get("stationTriplet")
        if name and triplet:
            by_name[_norm(name)] = st
    if not by_name:
        raise SnotelShapeError(f"stations: no name/stationTriplet fields in {_sample(payload[:2])}")

    out, missing = {}, []
    for name, side in SNOTEL_STATIONS.items():
        st = by_name.get(_norm(name))
        if st is None:
            missing.append(name)
            continue
        out[name] = {
            "triplet": st["stationTriplet"],
            "lat": st.get("latitude"),
            "lng": st.get("longitude"),
            "elevation_ft": st.get("elevation"),
            "side": side,
        }
    if missing:
        near = sorted(n for n in by_name
                      if any(w in n for w in ("ward", "rubicon", "marlette")))
        raise SnotelShapeError(
            f"stations not found by name: {missing}. Similar names in SNOTEL's "
            f"list: {near}. Adjust SNOTEL_STATIONS in sources/snotel.py.")

    STATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    STATION_CACHE.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    log.info("resolved %d SNOTEL station(s): %s", len(out),
             ", ".join(f"{n} = {v['triplet']}" for n, v in out.items()))
    return out


def fetch_daily(client, triplets: list[str], begin: date, end: date):
    resp = client.get(SNOTEL_ROOT + "data", params={
        "stationTriplets": ",".join(triplets),
        "elements": ",".join(ELEMENTS),
        "duration": "DAILY",
        "beginDate": begin.isoformat(),
        "endDate": end.isoformat(),
        "periodRef": "END",
    })
    resp.raise_for_status()
    return resp.json()


def _convert(element: str, unit: str | None, value: float) -> tuple[float, str]:
    u = _norm(unit)
    if element in ("PRCP", "PREC"):
        if u in ("in", "inch", "inches"):
            return value * 25.4, "mm"
        if u in ("mm", "millimeter", "millimeters"):
            return value, "mm"
        raise SnotelShapeError(f"{element}: unexpected unit {unit!r}")
    if element == "TAVG":
        if u in ("degf", "f", "degrees fahrenheit", "fahrenheit"):
            return (value - 32) * 5 / 9, "degC"
        if u in ("degc", "c", "degrees celsius", "celsius"):
            return value, "degC"
        raise SnotelShapeError(f"TAVG: unexpected unit {unit!r}")
    return value, unit or ""


def parse(payload, stations: dict[str, dict]) -> pd.DataFrame:
    """Flatten a ``data`` response into the store's observation layout."""
    if not isinstance(payload, list):
        raise SnotelShapeError(f"data: expected a list, got {_sample(payload)}")
    by_triplet = {v["triplet"]: (name, v) for name, v in stations.items()}
    rows = []
    for st in payload:
        triplet = st.get("stationTriplet") if isinstance(st, dict) else None
        series = st.get("data") if isinstance(st, dict) else None
        if triplet is None or not isinstance(series, list):
            raise SnotelShapeError(f"data: station entry without stationTriplet/data: {_sample(st)}")
        name, meta = by_triplet.get(triplet, (triplet, {}))
        for block in series:
            se = block.get("stationElement") if isinstance(block, dict) else None
            values = block.get("values") if isinstance(block, dict) else None
            if not isinstance(se, dict) or not isinstance(values, list):
                raise SnotelShapeError(f"data: element block without stationElement/values: {_sample(block)}")
            element = se.get("elementCode")
            unit = se.get("storedUnitCode") or se.get("unitCode")
            for v in values:
                raw, day = v.get("value"), v.get("date")
                if raw is None or day is None:
                    continue
                value, out_unit = _convert(element, unit, float(raw))
                ts = pd.Timestamp(str(day)[:10])
                rows.append({
                    "uuid": f"snotel:{triplet}:{element}:{ts:%Y-%m-%d}",
                    "source": "SNOTEL",
                    "site": name,
                    "sensor_type": "Snotel",
                    "timestamp": ts,
                    "lat": meta.get("lat"),
                    "lng": meta.get("lng"),
                    "variable": element,
                    "value": value,
                    "unit": out_unit,
                })
    return pd.DataFrame(rows)


def ingest(since: date | None = None, refresh_stations: bool = False) -> int:
    """Fetch daily SNOTEL data into ``data/processed/snotel_observations``."""
    import httpx
    from secchi.store import write_partitions

    root = PROCESSED_DIR / "snotel_observations"
    if since is None:
        since = (date.today() - timedelta(days=30)
                 if any(root.rglob("part*.parquet")) else FIRST_DAY)
    end = date.today()

    with httpx.Client(timeout=90, follow_redirects=True) as client:
        stations = resolve_stations(client, refresh=refresh_stations)
        payload = fetch_daily(client, [v["triplet"] for v in stations.values()], since, end)
    df = parse(payload, stations)
    if df.empty:
        log.warning("SNOTEL returned no values for %s to %s", since, end)
        return 0
    out = write_partitions(df, root, "snotel", STORE_KEY)
    log.info("SNOTEL: %s value(s) for %s to %s across %d station(s); +%s new",
             f"{len(df):,}", since, end, df["site"].nunique(), f"{out['rows_added']:,}")
    return len(df)
