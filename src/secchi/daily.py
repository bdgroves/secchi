"""A morning report: the numbers worth knowing, and anything that changed.

    pixi run daily                 # print it
    pixi run daily --out FILE.md   # also write it to a file

Reads the page snapshot (``web/assets/latest.json``, so run ``transform``
first) and the store. Two halves:

* **Changes** - only what happened in the last ``--hours`` (default 24):
  "What's new" items, a battery crossing the 11.5 V floor, a lake temperature
  swing or turbidity jump, a frost, rain, and the pipeline itself stalling.
  "Nothing changed" is a normal, good answer.
* **Numbers** - the lake, the forest stations, lake level and outflow, and
  the Beyond TEON context, as of each source's latest data.

Stations upload in 12-hour batches (docs/upload-cadence.md), so a station's
"last 24 h" here is the 24 h ending at its own newest reading, not the
24 h ending now. Timestamps in the store are the loggers' clock (UTC-8 all
year); the report shows them in Pacific time as people read a clock.

Every section is gathered on its own: one missing source prints as
"unavailable" and never costs the rest. A check that couldn't run is said
to have not run, never reported as fine.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from secchi.config import RAW_DIR, WEB_DIR

log = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
SNAPSHOT = WEB_DIR / "assets" / "latest.json"

CHANGE_HOURS = 24
# Pipeline: CI fetches 4-6 times a day, and stations upload twice a day.
STALE_SNAPSHOT_HOURS = 12     # no CI fetch landed in this long: look at Actions
STALE_READING_HOURS = 18      # nothing new from any station in this long
LOW_FLOOR_V = 11.5            # same floor as status / station_health
LAKE_SWING_C = 1.0            # a daily-mean change this big at a sonde is news
TURBIDITY_JUMP = 2.0          # x the 30-day median ...
TURBIDITY_MIN_FNU = 1.0       # ... and above this, to ignore noise near zero
FOREST_VARS = ("Air_Temp", "Soil_VWC", "BattV_Avg")
LAKE_VARS = ("Temp", "Turbidity", "Chl_a", "Do_percent")
LAKE_LEVEL_GAUGE = "10337000"
OUTLET_GAUGE = "10337500"


# --------------------------------------------------------------------------
# formatting


def f_of(c: float | None) -> str:
    return "-" if c is None else f"{c * 9 / 5 + 32:.0f} °F"


def temp(c: float | None, digits: int = 0) -> str:
    if c is None:
        return "-"
    return f"{c * 9 / 5 + 32:.{digits}f} °F ({c:.{digits}f} °C)"


def inches(mm: float | None) -> str:
    if mm is None:
        return "-"
    return "dry" if mm == 0 else f'{mm / 25.4:.2f}"'


def fmt_day(day: str | None) -> str:
    """'2026-10-03' -> 'Sat Oct 3'. Calendar days stay calendar days."""
    if not day:
        return "-"
    d = datetime.strptime(day[:10], "%Y-%m-%d")
    return f"{d:%a %b} {d.day}"


def fmt_local(ts: str | datetime | None) -> str:
    if ts is None:
        return "-"
    t = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
    if t.tzinfo is None:
        # A store timestamp: the loggers' clock, UTC-8 all year.
        from secchi.config import TEON_TIMEZONE
        t = t.replace(tzinfo=ZoneInfo(TEON_TIMEZONE))
    t = t.astimezone(PACIFIC)
    h = t.strftime("%I:%M %p").lstrip("0").lower()
    return f"{t:%a} {h}"


def fill(text: str) -> str:
    """Resolve the What's-new placeholders ({day:...}, {temp:...}, {cm:...})."""
    import re

    def sub(m):
        kind, val = m.group(1), m.group(2)
        if kind == "day":
            return fmt_day(val)
        if kind == "temp":
            return f_of(float(val))
        if kind == "cm":
            return f'{float(val) / 2.54:.0f}"'
        return val
    return re.sub(r"\{(day|temp|cm):([^}]+)\}", sub, text)


# --------------------------------------------------------------------------
# gathering


def _store_con():
    from secchi.query import connect
    return connect()


def lake_stats(con, sondes: list[str]) -> dict:
    """Per sonde: latest reading, 24 h mean and the 24 h mean a day earlier."""
    if not sondes:
        return {}
    names = ", ".join("'" + s.replace("'", "''") + "'" for s in sondes)
    vars_ = ", ".join(f"'{v}'" for v in LAKE_VARS)
    rows = con.sql(f"""
        WITH r AS (
          SELECT site, variable, timestamp, value
          FROM obs
          WHERE sensor_type = 'ExoSensor' AND site IN ({names})
            AND variable IN ({vars_})
            AND timestamp > now()::TIMESTAMP - INTERVAL 40 DAY),
        e AS (SELECT site, max(timestamp) AS t_end FROM r GROUP BY site)
        SELECT r.site, r.variable, e.t_end::VARCHAR,
          avg(value) FILTER (WHERE timestamp > t_end - INTERVAL 24 HOUR),
          avg(value) FILTER (WHERE timestamp > t_end - INTERVAL 48 HOUR
                               AND timestamp <= t_end - INTERVAL 24 HOUR),
          max(value) FILTER (WHERE timestamp > t_end - INTERVAL 24 HOUR),
          median(value) FILTER (WHERE timestamp > t_end - INTERVAL 30 DAY),
          arg_max(value, timestamp)
        FROM r JOIN e USING (site)
        GROUP BY ALL""").fetchall()
    out: dict = {}
    for site, var, t_end, mean24, mean_prev, max24, med30, last in rows:
        s = out.setdefault(site, {"t_end": t_end})
        s[var] = {"mean24": mean24, "mean_prev": mean_prev, "max24": max24,
                  "median30": med30, "last": last}
    return out


def forest_stats(con, stations: list[str]) -> dict:
    """Per station: min / max / mean of the 24 h ending at its newest reading."""
    if not stations:
        return {}
    names = ", ".join("'" + s.replace("'", "''") + "'" for s in stations)
    vars_ = ", ".join(f"'{v}'" for v in FOREST_VARS)
    rows = con.sql(f"""
        WITH r AS (
          SELECT site, variable, timestamp, value
          FROM obs
          WHERE site IN ({names}) AND variable IN ({vars_})
            AND sensor_type IN ('AirTemperatureRelativeHumidity',
                                'SoilEnvironmentalConditions')
            AND timestamp > now()::TIMESTAMP - INTERVAL 5 DAY),
        e AS (SELECT site, max(timestamp) AS t_end FROM r GROUP BY site)
        SELECT r.site, r.variable, e.t_end::VARCHAR,
          min(value) FILTER (WHERE timestamp > t_end - INTERVAL 24 HOUR),
          max(value) FILTER (WHERE timestamp > t_end - INTERVAL 24 HOUR),
          avg(value) FILTER (WHERE timestamp > t_end - INTERVAL 24 HOUR),
          min(value) FILTER (WHERE timestamp > t_end - INTERVAL 48 HOUR
                               AND timestamp <= t_end - INTERVAL 24 HOUR)
        FROM r JOIN e USING (site)
        GROUP BY ALL""").fetchall()
    out: dict = {}
    for site, var, t_end, lo, hi, mean, lo_prev in rows:
        s = out.setdefault(site, {"t_end": t_end})
        s[var] = {"min": lo, "max": hi, "mean": mean, "min_prev": lo_prev}
    return out


def lake_level(con) -> dict:
    """Gage height now and 7 days earlier at Tahoe City; outflow now."""
    row = con.sql(f"""
        WITH g AS (
          SELECT timestamp, value FROM usgs
          WHERE site_number = '{LAKE_LEVEL_GAUGE}' AND variable = '00065'
            AND timestamp > now() - INTERVAL 10 DAY)
        SELECT arg_max(value, timestamp),
               (SELECT arg_max(value, timestamp) FROM g
                WHERE timestamp <= (SELECT max(timestamp) FROM g) - INTERVAL 7 DAY)
        FROM g""").fetchone()
    return {"gage_ft": row[0], "gage_week_ago_ft": row[1]} if row else {}


def batteries(con) -> dict:
    from secchi.analysis.station_health import analyse
    from secchi.query import _glob
    g = _glob("observations")
    if not g:
        return {}
    df = con.sql(
        f"SELECT site, sensor_type, variable, timestamp, value, uuid "
        f"FROM read_parquet('{g}', hive_partitioning = true, "
        f"union_by_name = true) WHERE variable = 'BattV_Avg'").df()
    return (analyse(df) or {}).get("stations", {})


def snapshots_last_day(ref: datetime) -> int | None:
    try:
        from secchi.status import _snapshots_in_last_day
        return _snapshots_in_last_day(RAW_DIR, ref)
    except Exception:                       # noqa: BLE001
        return None


def gather(now: datetime | None = None, hours: int = CHANGE_HOURS) -> dict:
    """Everything the report says, as plain data (so `render` is testable)."""
    now = now or datetime.now(timezone.utc)
    facts: dict = {"now": now.isoformat(), "hours": hours, "unavailable": []}

    if not SNAPSHOT.exists():
        facts["unavailable"].append("page snapshot (run `pixi run transform` first)")
        return facts
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    facts["generated_at"] = snap.get("generated_at")
    facts["labels"] = snap.get("site_labels") or {}

    sondes = sorted((snap.get("sites") or {}).keys())
    inv = snap.get("inventory") or []
    # A forest station is anything terrestrial with an air or soil logger
    # (the inventory's display names, not the store's sensor_type).
    forest = sorted({r["site"] for r in inv
                     if r.get("category") == "terrestrial"
                     and r.get("sensor_type") in ("Air Temperature & Relative Humidity",
                                                  "Soil Environmental Conditions")})
    facts["sondes"] = sondes
    facts["forest"] = forest

    # Newest reading per station, from the inventory the map uses.
    last_seen: dict[str, str] = {}
    for r in inv:
        if r.get("sensor_type") in ("Field Camera", "Precipitation Gauge") or r.get("is_manual"):
            continue
        lu = r.get("last_update")
        if lu and (r["site"] not in last_seen or lu > last_seen[r["site"]]):
            last_seen[r["site"]] = lu
    facts["last_seen"] = last_seen

    since = now - timedelta(hours=hours)
    wn = (snap.get("whats_new") or {}).get("items") or []
    facts["news"] = [i for i in wn
                     if datetime.fromisoformat(i["at"]) >= since]

    for key in ("gauges", "airports", "snowlab", "rain", "clarity"):
        facts[key] = snap.get(key)

    try:
        con = _store_con()
    except Exception as exc:                # noqa: BLE001
        facts["unavailable"].append(f"store ({exc})")
        return facts

    for name, fn in (("lake", lambda: lake_stats(con, sondes)),
                     ("forest_stats", lambda: forest_stats(con, forest)),
                     ("level", lambda: lake_level(con)),
                     ("batteries", lambda: batteries(con))):
        try:
            facts[name] = fn()
        except Exception as exc:            # noqa: BLE001 - one source must not cost the rest
            log.warning("daily: %s unavailable: %s", name, exc)
            facts["unavailable"].append(name)

    try:
        from secchi.status import _snapshot_time
        snap_t = _snapshot_time(RAW_DIR)
        facts["snapshot_at"] = snap_t.isoformat() if snap_t else None
        facts["snapshots_24h"] = snapshots_last_day(snap_t) if snap_t else None
    except Exception:                       # noqa: BLE001
        facts["unavailable"].append("snapshot times")
    return facts


# --------------------------------------------------------------------------
# judging


def _name(facts: dict, site: str) -> str:
    return (facts.get("labels") or {}).get(site, site)


def changes(facts: dict) -> list[str]:
    """What happened in the window. Empty means a quiet day."""
    now = datetime.fromisoformat(facts["now"])
    out: list[str] = []

    for i in facts.get("news") or []:
        tag = "" if i.get("source", "TEON") == "TEON" else " *(Beyond TEON)*"
        out.append(fill(i["text"]) + tag)

    # Pipeline.
    sa = facts.get("snapshot_at")
    if sa:
        age = (now - datetime.fromisoformat(sa)).total_seconds() / 3600
        if age > STALE_SNAPSHOT_HOURS:
            out.append(f"**The data hasn't updated in {age:.0f} hours.** The last "
                       f"fetch landed {fmt_local(sa)}; check GitHub Actions.")
    seen = [datetime.fromisoformat(t) for t in (facts.get("last_seen") or {}).values()]
    if seen:
        newest = max(seen)
        age = (now - newest).total_seconds() / 3600
        if age > STALE_READING_HOURS:
            out.append(f"**No station has sent anything new in {age:.0f} hours** "
                       f"(newest reading {fmt_local(newest)}).")

    # Batteries crossing the floor today.
    for site, s in (facts.get("forest_stats") or {}).items():
        b = s.get("BattV_Avg") or {}
        lo, prev = b.get("min"), b.get("min_prev")
        if lo is not None and prev is not None and lo < LOW_FLOOR_V <= prev:
            out.append(f"{_name(facts, site)}'s battery dropped below {LOW_FLOOR_V} V "
                       f"overnight ({prev:.2f} → {lo:.2f} V).")
        a = s.get("Air_Temp") or {}
        if a.get("min") is not None and a["min"] <= 0:
            out.append(f"Frost at {_name(facts, site)}: down to {temp(a['min'])}.")

    # The lake.
    for site, s in (facts.get("lake") or {}).items():
        t = s.get("Temp") or {}
        if t.get("mean24") is not None and t.get("mean_prev") is not None:
            d = t["mean24"] - t["mean_prev"]
            if abs(d) >= LAKE_SWING_C:
                word = "cooled" if d < 0 else "warmed"
                out.append(f"The water at {_name(facts, site)} {word} "
                           f"{abs(d) * 9 / 5:.1f} °F ({abs(d):.1f} °C) in a day - "
                           f"wind mixing or upwelling, usually.")
        tb = s.get("Turbidity") or {}
        mx, med = tb.get("max24"), tb.get("median30")
        if mx is not None and med is not None and mx >= TURBIDITY_MIN_FNU \
                and mx >= TURBIDITY_JUMP * max(med, 0.1):
            out.append(f"Turbidity at {_name(facts, site)} reached {mx:.1f} FNU "
                       f"(typical this month: {med:.1f}).")

    # Rain anywhere we can see.
    wet = []
    for s in (facts.get("airports") or {}).get("stations", []):
        day = s.get("last_day")
        if day and s.get("last7_mm") and _recent_day(day, now):
            wet.append(f"{s['name']} {inches(s['last7_mm'])} this week")
    if wet:
        out.append("Rain: " + "; ".join(wet) + ".")
    return out


def _recent_day(day: str, now: datetime) -> bool:
    d = datetime.strptime(day[:10], "%Y-%m-%d").date()
    return (now.astimezone(PACIFIC).date() - d).days <= 2


def ongoing(facts: dict) -> list[str]:
    """Known issues, one line each, so a quiet day still shows what's open."""
    now = datetime.fromisoformat(facts["now"])
    out = []
    for site, t in sorted((facts.get("last_seen") or {}).items()):
        if site not in (facts.get("forest") or []) and site not in (facts.get("sondes") or []):
            continue
        age_h = (now - datetime.fromisoformat(t)).total_seconds() / 3600
        if age_h > 36:
            b = (facts.get("batteries") or {}).get(site, {}).get("final_min")
            bs = f"; battery was {b:.2f} V" if b is not None else ""
            out.append(f"{_name(facts, site)} quiet since {fmt_day(t)} "
                       f"({age_h / 24:.0f} days{bs}).")
    for site, h in sorted((facts.get("batteries") or {}).items()):
        if h.get("dark"):
            continue
        if h.get("not_charging"):
            out.append(f"{_name(facts, site)} isn't charging: overnight low "
                       f"{h.get('current_floor', 0):.2f} V, best day "
                       f"{h.get('peak_14d', 0):.2f} V in two weeks.")
        elif (h.get("current_floor") or 99) < LOW_FLOOR_V:
            out.append(f"{_name(facts, site)} battery low: {h['current_floor']:.2f} V overnight.")
    return out


# --------------------------------------------------------------------------
# rendering


def render(facts: dict) -> str:
    now = datetime.fromisoformat(facts["now"]).astimezone(PACIFIC)
    L: list[str] = [f"# Secchi daily - {now:%A %B} {now.day}, {now:%Y}", ""]

    ch = changes(facts)
    L.append(f"## What changed (last {facts.get('hours', CHANGE_HOURS)} hours)")
    L += [f"- {c}" for c in ch] if ch else ["- Nothing new. Every station that was reporting still is."]
    L.append("")

    og = ongoing(facts)
    if og:
        L.append("## Still open")
        L += [f"- {o}" for o in og]
        L.append("")

    lake = facts.get("lake") or {}
    if lake:
        L += ["## The lake", "",
              "| Sonde | Water | vs day before | Turbidity | Chlorophyll | Oxygen sat. | Latest |",
              "|---|---|---|---|---|---|---|"]
        for site in sorted(lake):
            s = lake[site]
            t, tb, ch_, do = (s.get(v) or {} for v in LAKE_VARS)
            d = (t.get("mean24") - t.get("mean_prev")) if t.get("mean24") is not None \
                and t.get("mean_prev") is not None else None
            ds = "-" if d is None else f"{d * 9 / 5:+.1f} °F"
            L.append(f"| {_name(facts, site)} | {temp(t.get('mean24'), 1)} | {ds} | "
                     f"{_num(tb.get('mean24'), ' FNU')} | {_num(ch_.get('mean24'), ' µg/L', 2)} | "
                     f"{_num(do.get('mean24'), '%', 0)} | {fmt_local(s.get('t_end'))} |")
        L.append("")
        L.append("*24-hour means ending at each sonde's newest reading.*")
        L.append("")

    lv = facts.get("level") or {}
    g = facts.get("gauges") or {}
    bits = []
    elev = ((g.get(LAKE_LEVEL_GAUGE) or {}).get("readings") or {}).get("00065:00011:datum")
    if elev:
        s = f"Lake level {elev['value']:,.2f} ft"
        if lv.get("gage_ft") is not None and lv.get("gage_week_ago_ft") is not None:
            d_in = (lv["gage_ft"] - lv["gage_week_ago_ft"]) * 12
            s += f" ({d_in:+.1f} in this week)"
        bits.append(s)
    q = ((g.get(OUTLET_GAUGE) or {}).get("readings") or {}).get("00060:00011")
    if q:
        bits.append(f"Truckee River leaving the lake {q['value']:.0f} cfs")
    if bits:
        L += ["; ".join(bits) + ".", ""]

    fs = facts.get("forest_stats") or {}
    if fs:
        L += ["## Forest stations", "",
              "| Station | Air low-high | Soil moisture | Battery low-high | Latest |",
              "|---|---|---|---|---|"]
        for site in sorted(fs):
            s = fs[site]
            a, sm, b = (s.get(v) or {} for v in FOREST_VARS)
            air = f"{f_of(a.get('min'))} - {f_of(a.get('max'))}" if a else "-"
            soil = f"{sm['mean'] * 100:.1f}%" if sm.get("mean") is not None else "-"
            bat = f"{b['min']:.2f} - {b['max']:.2f} V" if b.get("min") is not None else "-"
            L.append(f"| {_name(facts, site)} | {air} | {soil} | {bat} | {fmt_local(s.get('t_end'))} |")
        L += ["", "*The 24 hours ending at each station's newest reading.*", ""]

    beyond = []
    for s in (facts.get("airports") or {}).get("stations", []):
        if s.get("tmax_c") is None:
            continue
        beyond.append(f"{s['name']}, {fmt_day(s.get('last_day'))}: high {f_of(s['tmax_c'])}, "
                      f"low {f_of(s.get('tmin_c'))}; {inches(s.get('last7_mm'))} in the last week.")
    sl = (facts.get("snowlab") or {}).get("daily") or {}
    if sl.get("newest_day"):
        swe = sl.get("swe_mm") or 0
        snow = f"{inches(swe)} of snow water on the ground" if swe > 0 else "no snow on the ground"
        wy = sl.get("water_year_mm")
        wys = ("no precipitation yet this water year" if not wy
               else f"{inches(wy)} precipitation this water year")
        beyond.append(f"Snow Lab, {fmt_day(sl['newest_day'])}: {snow}; {wys}.")
    rain = (facts.get("rain") or {}).get("stations") or []
    if rain:
        beyond.append("SNOTEL, last 7 days: " + ", ".join(
            f"{r['name']} {inches(r.get('last7_mm'))}" for r in rain) + ".")
    if beyond:
        L += ["## Beyond TEON"] + [f"- {b}" for b in beyond] + [""]

    foot = []
    if facts.get("snapshot_at"):
        foot.append(f"last fetch {fmt_local(facts['snapshot_at'])}")
    if facts.get("snapshots_24h") is not None:
        foot.append(f"{facts['snapshots_24h']} fetches in 24 h")
    if foot:
        L.append("*Pipeline: " + ", ".join(foot) + ". Stations upload twice a day, "
                 "so readings a few hours old are normal.*")
    if facts.get("unavailable"):
        L += ["", "**Couldn't check:** " + "; ".join(facts["unavailable"]) + "."]
    return "\n".join(L).rstrip() + "\n"


def _num(v: float | None, unit: str, digits: int = 1) -> str:
    return "-" if v is None else f"{max(v, 0):.{digits}f}{unit}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, help="also write the report here")
    p.add_argument("--hours", type=int, default=CHANGE_HOURS,
                   help="how far back counts as a change (default 24)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    text = render(gather(hours=args.hours))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
