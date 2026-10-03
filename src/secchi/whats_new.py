"""What's new: the short list at the top of the dashboard.

Built each time the page is built, from things already on disk:

* the watcher's event log (``data/reference/news_log.json``): hand-collected
  data arriving, stations coming back or going quiet, new sensors, sites
  hidden or shown by TEON;
* battery swaps and restorations (``analysis.station_health``);
* the first freezing night of the water year at a forest station (TEON);
* Beyond TEON: a new TERC Secchi release, and the first snow on the ground
  at the Snow Lab.

Grouped and worded for people, not logs: six loggers uploading on one boat
trip is one line, and a site hidden and shown again within a day is not
news. Each item says where it came from, so TEON's news reads first.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from secchi.config import PROCESSED_DIR, REFERENCE_DIR, SITE_LABELS

log = logging.getLogger("secchi.whats_new")

PACIFIC = ZoneInfo("America/Los_Angeles")
WINDOW_DAYS = 14
MAX_ITEMS = 8
# A site TEON hides and shows again within this long is a blip, not news.
BLIP_HOURS = 24
FREEZE_C = 0.0
FIRST_SNOW_CM = 2.5

SENSOR_WORDS = {
    "Tree stress and growth": ("tree sensor", "tree sensors"),
    "Minidot": ("oxygen logger", "oxygen loggers"),
    "Hobo": ("temperature logger", "temperature loggers"),
    "EXO": ("water-quality sonde", "water-quality sondes"),
    "Field Camera": ("camera", "cameras"),
    "Stream Level": ("stream gauge", "stream gauges"),
    "Stream Chemistry": ("stream chemistry sensor", "stream chemistry sensors"),
    "Precipitation Gauge": ("rain gauge", "rain gauges"),
    "Soil Environmental Conditions": ("soil sensor", "soil sensors"),
    "Air Temperature & Relative Humidity": ("weather sensor", "weather sensors"),
}


def label(site: str) -> str:
    return SITE_LABELS.get(site, site)


def _list(names: list[str]) -> str:
    names = list(dict.fromkeys(names))
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _local_day(at: str | datetime) -> str:
    t = at if isinstance(at, datetime) else datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(PACIFIC).date().isoformat()


def _day_of(ts: str | None) -> str | None:
    """A naive Pacific timestamp's calendar day ("2026-09-28T15:46:00" -> "2026-09-28")."""
    return str(ts)[:10] if ts else None


def _item(at, kind, text, source="TEON", link=None, **extra) -> dict:
    at_iso = at.isoformat() if isinstance(at, datetime) else str(at)
    return {"at": at_iso, "day": _local_day(at_iso), "kind": kind, "text": text,
            "source": source, "link": link, **extra}


# ---------------------------------------------------------------------------
# From the watcher's log
# ---------------------------------------------------------------------------

def from_log(events: list[dict], live_sites: set[str], since: datetime) -> list[dict]:
    ev = [e for e in events
          if datetime.fromisoformat(e["at"].replace("Z", "+00:00")) >= since]
    items: list[dict] = []

    # Hidden and shown again within BLIP_HOURS: drop both.
    hid = [e for e in ev if e["kind"] == "site hidden by TEON"]
    shown = [e for e in ev if e["kind"] == "site un-hidden by TEON"]
    blips = set()
    for h in hid:
        th = datetime.fromisoformat(h["at"])
        for s in shown:
            ts = datetime.fromisoformat(s["at"])
            if s["detail"] == h["detail"] and timedelta(0) <= ts - th <= timedelta(hours=BLIP_HOURS):
                blips |= {id(h), id(s)}
    for e in hid + shown:
        if id(e) in blips:
            continue
        name = label(e["detail"])
        if e["kind"] == "site hidden by TEON":
            items.append(_item(e["at"], "hidden", f"TEON took {name} out of public view; the "
                               f"dashboard leaves it out too.", link="#observatory-inventory"))
        else:
            items.append(_item(e["at"], "shown", f"{name} is public again.",
                               link="#observatory-inventory"))

    # New sensors, grouped by day and kind of sensor.
    new_by = defaultdict(list)
    for e in ev:
        if e["kind"] == "new sensor" and e.get("site"):
            new_by[(_local_day(e["at"]), e.get("sensor_type", ""))].append(e)
    for (_, stype), es in new_by.items():
        one, many = SENSOR_WORDS.get(stype, (stype.lower() + " sensor", stype.lower() + " sensors"))
        sites = [label(e["site"]) for e in sorted(es, key=lambda e: e["at"])]
        noun = many if len(sites) > 1 else one
        link = "#trees" if stype == "Tree stress and growth" else "#observatory-inventory"
        items.append(_item(max(e["at"] for e in es), "new_sensor",
                           f"New {noun} at {_list(sites)}.", link=link))

    # Lake loggers read by hand: one boat trip is one line.
    lake_up = defaultdict(list)
    for e in ev:
        if e["kind"] == "dormant sensor received an upload" and e.get("category") == "lake":
            lake_up[_local_day(e["at"])].append(e)
    for day, es in lake_up.items():
        sites = sorted({label(e["site"]) for e in es})
        through = max(filter(None, (_day_of(e.get("last_update")) for e in es)), default=None)
        kinds = sorted({SENSOR_WORDS.get(e.get("sensor_type", ""), (e.get("sensor_type", ""),) * 2)[1]
                        for e in es})
        added = [e["added"] for e in es if e.get("added")]
        per = f" About {round(sum(added) / len(added), -2):,.0f} readings per logger." if added else ""
        where = (f"{len(sites)} nearshore sites: {_list(sites)}" if len(sites) > 2
                 else _list(sites))
        items.append(_item(max(e["at"] for e in es), "manual_upload",
                           f"Hand-collected data arrived from {where}"
                           + (f", up to {{day:{through}}}" if through else "") + ".",
                           detail=f"From the {_list(kinds)}.{per}", link="#manual-cards"))

    # Forest and stream stations: back, quiet, or a backlog arriving.
    by_site_day = defaultdict(list)
    for e in ev:
        if e.get("category") in ("terrestrial", "stream") and e.get("site"):
            by_site_day[(e["site"], _local_day(e["at"]))].append(e)
    for (site, _), es in by_site_day.items():
        kinds = {e["kind"] for e in es}
        at = max(e["at"] for e in es)
        if "sensor resumed" in kinds:
            items.append(_item(at, "resumed", f"{label(site)} is reporting again.",
                               link="#forest-stations"))
        elif "dormant sensor received an upload" in kinds:
            up = [e for e in es if e["kind"] == "dormant sensor received an upload"]
            through = max(filter(None, (_day_of(e.get("last_update")) for e in up)), default=None)
            n = max((e.get("added") or 0) for e in up)
            still = "" if site in live_sites else " The station itself is still quiet."
            items.append(_item(at, "backlog", f"{label(site)}’s records"
                               + (f" up to {{day:{through}}}" if through else "")
                               + f" came through{f' ({n:,} readings)' if n else ''}.{still}",
                               link="#forest-stations"))
        elif "sensor went quiet" in kinds and site not in live_sites:
            last = max(filter(None, (_day_of(e.get("last_update")) for e in es)), default=None)
            items.append(_item(at, "quiet", f"{label(site)} has gone quiet"
                               + (f"; last reading {{day:{last}}}" if last else "") + ".",
                               link="#forest-stations"))
    return items


# ---------------------------------------------------------------------------
# From the data
# ---------------------------------------------------------------------------

def from_batteries(df_long: pd.DataFrame, since: datetime) -> list[dict]:
    from secchi.analysis.station_health import EVENT_LOOKBACK_DAYS, analyse
    r = analyse(df_long)
    items = []
    for site, v in (r or {}).get("stations", {}).items():
        for e in v.get("events", []):
            at = pd.Timestamp(e["at"]).tz_localize(PACIFIC).to_pydatetime()
            if at < since:
                continue
            text = (f"New battery at {site}." if e["kind"] == "replaced"
                    else f"Power is back at {site}.")
            if v.get("not_charging"):
                text += " Its solar panel still isn’t charging it."
            items.append(_item(at, "battery", text, link="#forest-stations"))
    return items


def from_first_freeze(df_long: pd.DataFrame, since: datetime) -> list[dict]:
    """The first night at or below freezing this water year, at any forest station."""
    now = datetime.now(PACIFIC)
    wy_start = pd.Timestamp(now.year if now.month >= 10 else now.year - 1, 10, 1)
    a = df_long[(df_long["variable"] == "Air_Temp")
                & (pd.to_datetime(df_long["timestamp"]) >= wy_start)
                & df_long["value"].between(-40, FREEZE_C)]   # -40: dropouts read as very cold
    if a.empty:
        return []
    first = a.sort_values("timestamp").iloc[0]
    day = pd.Timestamp(first["timestamp"]).normalize()
    lows = a[pd.to_datetime(a["timestamp"]).dt.normalize() == day]
    coldest = lows.loc[lows["value"].idxmin()]
    at = day.tz_localize(PACIFIC).to_pydatetime() + timedelta(hours=6)
    if at < since:
        return []
    others = sorted(set(lows["site"]) - {coldest["site"]})
    return [_item(at, "freeze", f"First freezing night of the season: {{temp:{coldest['value']:.1f}}} "
                  f"at {coldest['site']}" + (f", and below freezing at {_list(others)}" if others else "")
                  + ".", link="#forest-stations")]


def from_terc(since: datetime) -> list[dict]:
    p = REFERENCE_DIR / "terc_package.json"
    if not p.exists():
        return []
    st = json.loads(p.read_text(encoding="utf-8"))
    if not st.get("fetched_at"):
        return []
    at = datetime.fromisoformat(st["fetched_at"])
    if at < since:
        return []
    return [_item(at, "secchi", "UC Davis TERC published new Secchi readings, up to "
                  f"{{day:{st.get('newest_reading')}}}.", source="Beyond TEON", link="#beyond-teon")]


def from_first_snow(since: datetime) -> list[dict]:
    from secchi.sources.cssl import SNOTEL_NAME
    from secchi.store import read_partitions
    root = PROCESSED_DIR / "snotel_observations"
    s = read_partitions(root) if root.exists() else pd.DataFrame()
    if s.empty:
        return []
    now = datetime.now(PACIFIC)
    wy_start = pd.Timestamp(now.year if now.month >= 10 else now.year - 1, 10, 1)
    c = s[(s["site"] == SNOTEL_NAME) & (pd.to_datetime(s["timestamp"]) >= wy_start - pd.Timedelta(days=1))]
    if c.empty:
        return []
    p = c.pivot_table(index="timestamp", columns="variable", values="value").sort_index()
    for col in ("SNWD", "TAVG", "PRCP", "WTEQ"):
        if col not in p:
            p[col] = float("nan")
    # Snow, not sensor noise. On 2026-10-02 the depth sensor read 2.5 cm on
    # a 15 C day with no precipitation and no water in the snowpack. Real
    # snow comes with a cold day and either precipitation (that day or the
    # day before) or water on the snow pillow.
    wet = (p["PRCP"].fillna(0) > 0) | (p["PRCP"].shift(1).fillna(0) > 0) | (p["WTEQ"].fillna(0) > 0)
    snowy = p[(p["SNWD"] >= FIRST_SNOW_CM) & (p["TAVG"] <= 5) & wet
              & (p.index >= wy_start)]
    if snowy.empty:
        return []
    day = snowy.index[0]
    at = pd.Timestamp(day).tz_localize(PACIFIC).to_pydatetime() + timedelta(hours=8)
    if at < since:
        return []
    return [_item(at, "snow", f"First snow on the ground at the Snow Lab this season: "
                  f"{{cm:{snowy['SNWD'].iloc[0]:.0f}}}.", source="Beyond TEON", link="#beyond-teon")]


def build(df_long: pd.DataFrame, inventory_rows: list[dict],
          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    live_sites = {r.get("site") for r in inventory_rows or [] if r.get("is_live")}
    log_path = REFERENCE_DIR / "news_log.json"
    events = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []

    items: list[dict] = []
    for name, fn in (("log", lambda: from_log(events, live_sites, since)),
                     ("batteries", lambda: from_batteries(df_long, since)),
                     ("freeze", lambda: from_first_freeze(df_long, since)),
                     ("terc", lambda: from_terc(since)),
                     ("snow", lambda: from_first_snow(since))):
        try:
            items += fn()
        except Exception as exc:              # noqa: BLE001 - one source must not cost the rest
            log.warning("what's new: %s unavailable: %s", name, exc)
    items.sort(key=lambda i: i["at"], reverse=True)
    return {"window_days": WINDOW_DAYS, "items": items[:MAX_ITEMS],
            "more": max(0, len(items) - MAX_ITEMS)}
