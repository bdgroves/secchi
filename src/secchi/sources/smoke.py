"""Wildfire smoke over Lake Tahoe, from NOAA's Hazard Mapping System.

HMS analysts trace smoke plumes on GOES satellite imagery every day and
publish them as polygons graded light, medium or heavy. It is the standard
source for "smoke day" studies — including the lake smoke-day work at the
University of Nevada, Reno — and it's free.

    https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/
        KML/YYYY/MM/hms_smokeYYYYMMDD.kml

Two cautions shape everything below.

**Smoke seen from above.** HMS maps smoke at any altitude, as a satellite
sees it. A plume high over the basin can leave the lake surface clear, so
an HMS smoke day is "smoke overhead", not "smoke in the water".

**A missing file is not a clear day.** The archive has holes: in 2025 August
has files only for the 1st and the 25th to 31st, a dozen October days are
missing, and November has only its last six (that autumn's federal
shutdown). Recording a missing day as "no smoke" would turn three weeks of
peak fire season into clear skies. So each day gets one of:

    hms_analysed   1 if a real KML file exists for the day, 0 if not
    smoke_density  only on analysed days: 0 none over the lake,
                   1 light, 2 medium, 3 heavy (the densest polygon)
    hms_polygons   only on analysed days: smoke polygons in the whole file

"A real KML file" is checked, not assumed: the first full run reported no
missing days at all, though the shapefile archive is missing most of August
2025. A server that answers a missing file with an HTML page (HTTP 200)
would otherwise turn every such day into "no smoke". So a response whose
root element isn't <kml> counts as not analysed, and the polygon count
lets a run of suspiciously empty summer files be seen.

Density comes from the folder a polygon sits in ("Smoke (Light)" and so on),
falling back to the polygon's description. Older files used numbers
(5, 16, 21 or 27) where newer ones say Light, Medium or Heavy; both are read.

"Over the lake" means any of a handful of points across the lake falls inside
a smoke polygon — the centre and points near each shore.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import pandas as pd

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.sources.smoke")

HMS_ROOT = "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/KML/"
FIRST_DAY = date(2024, 6, 1)
STORE_KEY = ["uuid", "site", "variable"]
SITE = "Lake Tahoe"

# (lon, lat) — the lake's centre and a point off each shore.
LAKE_POINTS = [
    (-120.04, 39.09),   # centre
    (-120.12, 39.17),   # off Tahoe City, northwest
    (-119.96, 39.21),   # off Incline, northeast
    (-119.95, 38.97),   # off Stateline, southeast
    (-120.10, 38.96),   # off Emerald Bay, southwest
]

_WORDS = {"light": 1, "medium": 2, "heavy": 3}
_NUMBERS = {"5": 1, "16": 2, "21": 3, "27": 3}


class SmokeShapeError(RuntimeError):
    """A file that doesn't look like an HMS smoke KML."""


def url_for(day: date) -> str:
    return f"{HMS_ROOT}{day:%Y}/{day:%m}/hms_smoke{day:%Y%m%d}.kml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def is_kml(text: str) -> bool:
    """True only for an XML document whose root element is <kml>."""
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        return False
    return _local(root.tag).lower() == "kml"


def _density(*texts: str) -> int | None:
    for t in texts:
        if not t:
            continue
        m = re.search(r"\((light|medium|heavy)\)", t, re.I) or \
            re.search(r"density:\s*(light|medium|heavy|\d+)", t, re.I)
        if m:
            v = m.group(1).lower()
            return _WORDS.get(v, _NUMBERS.get(v))
    return None


def _rings(placemark) -> list[list[tuple[float, float]]]:
    rings = []
    for el in placemark.iter():
        if _local(el.tag) == "coordinates" and el.text:
            pts = []
            for tok in el.text.split():
                parts = tok.split(",")
                if len(parts) >= 2:
                    pts.append((float(parts[0]), float(parts[1])))
            if len(pts) >= 3:
                rings.append(pts)
    return rings


def parse_kml(text: str) -> list[tuple[int, list[list[tuple[float, float]]]]]:
    """(density 1-3, rings) for every smoke polygon in a file."""
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as exc:
        raise SmokeShapeError(f"not XML: {exc}") from exc
    out = []
    for folder in (e for e in root.iter() if _local(e.tag) == "Folder"):
        fname = next((c.text or "" for c in folder if _local(c.tag) == "name"), "")
        for pm in (c for c in folder if _local(c.tag) == "Placemark"):
            desc = " ".join((c.text or "") for c in pm.iter()
                            if _local(c.tag) in ("description", "name", "styleUrl"))
            d = _density(fname, desc)
            if d is None:
                raise SmokeShapeError(f"no density for a polygon (folder {fname!r}, "
                                      f"description {desc[:120]!r})")
            out.append((d, _rings(pm)))
    if not out and "<Placemark" in str(text):
        raise SmokeShapeError("polygons found outside any density folder")
    return out


def _inside(pt: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def density_over_lake(polygons) -> int:
    """The densest smoke polygon covering any lake point; 0 if none."""
    best = 0
    for d, rings in polygons:
        if d <= best or not rings:
            continue
        outer = rings[0]
        holes = rings[1:]
        if any(_inside(p, outer) and not any(_inside(p, h) for h in holes) for p in LAKE_POINTS):
            best = d
    return best


def _rows(day: date, analysed: bool, density: int | None, polygons: int | None = None) -> list[dict]:
    ts = pd.Timestamp(day)
    base = {"source": "HMS", "site": SITE, "sensor_type": "SmokeAnalysis",
            "timestamp": ts, "lat": 39.09, "lng": -120.04}
    rows = [{**base, "uuid": f"hms:{day:%Y-%m-%d}:hms_analysed",
             "variable": "hms_analysed", "value": 1.0 if analysed else 0.0}]
    if analysed:
        rows.append({**base, "uuid": f"hms:{day:%Y-%m-%d}:smoke_density",
                     "variable": "smoke_density", "value": float(density)})
        if polygons is not None:
            rows.append({**base, "uuid": f"hms:{day:%Y-%m-%d}:hms_polygons",
                         "variable": "hms_polygons", "value": float(polygons)})
    return rows


def ingest(since: date | None = None, until: date | None = None, pause: float = 0.2) -> int:
    """Fetch daily HMS smoke over the lake into data/processed/smoke_observations."""
    import httpx
    from secchi.store import write_partitions

    root = PROCESSED_DIR / "smoke_observations"
    if since is None:
        since = (date.today() - timedelta(days=14)
                 if any(root.rglob("part*.parquet")) else FIRST_DAY)
    # HMS posts a day's analysis the next morning; today has no file yet.
    until = until or (date.today() - timedelta(days=1))

    rows, smoky, missing, not_kml, empty = [], 0, 0, 0, 0
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        day = since
        while day <= until:
            resp = client.get(url_for(day))
            if resp.status_code == 404:
                rows += _rows(day, False, None)
                missing += 1
            else:
                resp.raise_for_status()
                if not is_kml(resp.text):
                    log.warning("%s: response is not KML (%s...); counted as not analysed",
                                day, resp.text[:60].replace("\n", " "))
                    rows += _rows(day, False, None)
                    not_kml += 1
                else:
                    polys = parse_kml(resp.text)
                    d = density_over_lake(polys)
                    rows += _rows(day, True, d, len(polys))
                    smoky += d > 0
                    empty += not polys
            day += timedelta(days=1)
            time.sleep(pause)
    df = pd.DataFrame(rows)
    if df.empty:
        return 0
    write_partitions(df, root, "hms", STORE_KEY)
    days = (until - since).days + 1
    log.info("HMS smoke %s to %s: %d day(s), %d with smoke over the lake; not analysed: "
             "%d missing, %d not KML; %d analysed files with no smoke polygons anywhere",
             since, until, days, smoky, missing, not_kml, empty)
    return days
