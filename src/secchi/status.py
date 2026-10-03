"""One screen: is everything all right, and is there anything to do?

    pixi run status

Reads only what's already on disk — the hourly inventory snapshot CI
commits, the store, the watcher's baseline and the page's data — so after a
`git pull` it answers in a few seconds without touching the network.

It doesn't make any judgement of its own. Every rule comes from the part
of the project that owns it, so there is one place each rule lives:

    stations live or dark     transform.build_inventory_summary (the map's rule)
    batteries                 analysis.station_health.analyse
    data waiting to pull      the page's banner (transform.build_upload_alert)
    impossible readings       sources.watch.scan_readings

Plain ASCII on purpose: Windows consoles and Tee-Object files mangle dashes
and arrows.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from secchi.config import PROCESSED_DIR, RAW_DIR, REFERENCE_DIR, WEB_DIR

# A snapshot older than this is worth a pull. Not 1-2 h: CI's "hourly"
# fetch actually landed every 3-8 hours before 2026-09-28, because GitHub
# delays and drops scheduled runs at the top of the hour.
STALE_COPY_HOURS = 8
RAIN_GAUGE = "Precipitation Gauge"
# The map calls a sensor live within this window (config LIVE_WINDOW_HOURS).
CAMERA = "Field Camera"


def _hours(dt: datetime | None, now: datetime) -> float | None:
    return None if dt is None else (now - dt).total_seconds() / 3600


def _age(h: float | None) -> str:
    if h is None:
        return "?"
    if h < 48:
        return f"{h:.0f} h"
    return f"{h / 24:.0f} d"


def _snapshot_time(raw_dir) -> datetime | None:
    """Capture time of the newest inventory snapshot, from its path (UTC)."""
    root = raw_dir / "inventory"
    files = sorted(root.rglob("*.json")) if root.exists() else []
    if not files:
        return None
    m = re.search(r"(\d{4})[\\/](\d{2})[\\/](\d{2})[\\/](\d{6})\.json$", str(files[-1]))
    if not m:
        return None
    y, mo, d, hms = m.groups()
    return datetime(int(y), int(mo), int(d), int(hms[:2]), int(hms[2:4]),
                    int(hms[4:]), tzinfo=timezone.utc)


def report() -> int:
    from secchi.transform import (_parse_iso, build_inventory_summary,
                                  load_latest_json)

    now = datetime.now(timezone.utc)
    todo: list[str] = []
    # Checks that could not run. A check that didn't run must never be
    # reported as passing: a missing DuckDB once made this print "all
    # reporting stations fine" and "nothing needs doing" with two
    # stations failing.
    unavailable: list[str] = []

    print(f"\n  secchi status  {now.astimezone():%Y-%m-%d %H:%M}\n")

    # ---- the local copy ------------------------------------------------
    snap = _snapshot_time(RAW_DIR)
    snap_age = _hours(snap, now)
    print("  DATA")
    if snap is None:
        print("    no inventory snapshot on disk")
        todo.append("git pull (no inventory snapshot found)")
    else:
        print(f"    latest hourly snapshot     {_age(snap_age)} old")
        if snap_age is not None and snap_age > STALE_COPY_HOURS:
            todo.append(f"git pull - the latest snapshot is {_age(snap_age)} old "
                        f"(if it still is after pulling, CI is running late)")

    try:
        import duckdb
        from secchi.query import _glob
        g = _glob("observations")
        if g:
            n, newest = duckdb.sql(
                f"SELECT count(*), max(timestamp) FROM read_parquet('{g}', "
                f"hive_partitioning = true, union_by_name = true)").fetchone()
            print(f"    observations stored        {n:,}, newest reading {newest:%Y-%m-%d %H:%M}")
        from secchi.store import undated_summary
        und = undated_summary(PROCESSED_DIR / "observations")
        if und["undated"]:
            if und["only_undated"]:
                print(f"    undated readings           {und['undated']:,}, "
                      f"{und['only_undated']:,} records held nowhere else")
                todo.append("undated readings with no dated copy - a timestamp "
                            "field needs configuring (see docs/timestamp-fields.md)")
            else:
                print(f"    undated readings           {und['undated']:,}, all "
                      f"duplicates of dated ones")
                todo.append(f"pixi run drop-undated - {und['undated']:,} duplicate "
                            f"undated rows inflate the totals")
    except Exception as exc:                         # never fail the report
        print(f"    (store summary unavailable: {exc})")
        unavailable.append("store summary")

    # ---- stations, by the map's rule -----------------------------------
    inventory = load_latest_json(RAW_DIR, "inventory")
    rows = build_inventory_summary(inventory)
    sites: dict[str, dict] = {}
    for r in rows:
        if r["category"] == "lake" or r["sensor_type"] == CAMERA:
            continue
        last = _parse_iso(r.get("last_update"))
        s = sites.setdefault(r["site"], {"last": None, "live": False, "gauge": None})
        s["live"] = s["live"] or r["is_live"]
        # The rain gauge is a separate device: Blackwood 2's kept reporting
        # until 2026-08-14, two months after the station's logger stopped.
        # Date a station by its logger and mention the gauge separately.
        if r["sensor_type"] == RAIN_GAUGE:
            s["gauge"] = last
            continue
        if last and (s["last"] is None or last > s["last"]):
            s["last"] = last

    # Judge liveness as of the SNAPSHOT, not as of now. The inventory is
    # only as fresh as the last CI run that captured TEON, which has been
    # 13 h old; measured from now, every station looked 13 h staler than
    # it was and two healthy ones were reported dark.
    from secchi.config import LIVE_WINDOW_HOURS
    ref = snap or now
    for v in sites.values():
        v["live"] = bool(v["last"] and _hours(v["last"], ref) <= LIVE_WINDOW_HOURS)
    dark = {k: v for k, v in sites.items() if not v["live"]}
    live = sorted(k for k, v in sites.items() if v["live"])
    as_of = f" (as of the snapshot, {_age(snap_age)} ago)" if snap_age and snap_age > 2 else ""
    print(f"\n  FOREST STATIONS              {len(live)} of {len(sites)} reporting{as_of}")

    # Battery at the last reading, for the dark ones and for warnings.
    health = {}
    try:
        import duckdb
        from secchi.query import _glob
        from secchi.analysis.station_health import analyse
        g = _glob("observations")
        if g:
            batt = duckdb.sql(
                f"SELECT site, sensor_type, variable, timestamp, value, uuid "
                f"FROM read_parquet('{g}', hive_partitioning = true, "
                f"union_by_name = true) WHERE variable = 'BattV_Avg'").df()
            health = (analyse(batt) or {}).get("stations", {})
    except Exception as exc:
        print(f"    (battery check unavailable: {exc})")
        unavailable.append("battery check")

    for site, v in sorted(dark.items(), key=lambda kv: kv[1]["last"] or now):
        since = v["last"].astimezone() if v["last"] else None
        batt = health.get(site, {}).get("final_min")
        bs = f"   battery {batt:.2f} V at last reading" if batt is not None else ""
        g = v.get("gauge")
        if g and v["last"] and (g - v["last"]).days >= 1:
            bs += f", rain gauge until {g.astimezone():%Y-%m-%d}"
        print(f"    dark   {site:18} since {since:%Y-%m-%d %H:%M}  "
              f"({_age(_hours(v['last'], ref))} quiet at snapshot){bs}" if since else
              f"    dark   {site:18}")
    if live:
        print(f"    live   {', '.join(live)}")

    # ---- batteries -----------------------------------------------------
    warn = []
    for site, h in sorted(health.items()):
        if site in dark:
            continue
        le = h.get("last_event")
        if h.get("not_charging"):
            warn.append(f"{site:16} NOT CHARGING - peak {h.get('peak_14d'):.2f} V in 14 days, "
                        f"overnight low {h.get('current_floor'):.2f} V"
                        + (f", falling {h['weeks_falling']} weeks" if h.get('weeks_falling', 0) >= 3 else ""))
            if le:
                verb = "replaced" if le["kind"] == "replaced" else "power restored"
                warn.append(f"{'':16} battery {verb} {le['day']} "
                            f"({le['floor_before']:.2f} -> {le['floor_after']:.2f} V), "
                            f"still not charging")
            todo.append(f"{site}: battery not being charged - needs a site visit (tell TEON)")
        elif h.get("flatlined"):
            warn.append(f"{site:16} battery channel stuck on one value")
        elif (h.get("current_floor") or 99) < 11.5:
            warn.append(f"{site:16} AT RISK - overnight floor {h['current_floor']:.2f} V")
            todo.append(f"{site}: battery below 11.5 V")
        elif h.get("weeks_to_floor") is not None and h["weeks_to_floor"] < 12:
            warn.append(f"{site:16} {h['weeks_to_floor']:.0f} weeks to the 11.5 V floor at this rate")
        # Separately from the chain above (an `if` inside it splits the
        # elif sequence): any swap or restoration in the last two weeks,
        # at a station that is charging, is news. It's how a site visit
        # shows up in the data. Not-charging stations say it on their line.
        if le and not h.get("not_charging") and \
                (datetime.now().date() - datetime.fromisoformat(le["day"]).date()).days <= 14:
            verb = "replaced" if le["kind"] == "replaced" else "power restored"
            warn.append(f"{site:16} battery {verb} {le['day']} "
                        f"({le['floor_before']:.2f} -> {le['floor_after']:.2f} V)")
    print("\n  BATTERIES")
    if "battery check" in unavailable:
        print("    COULD NOT CHECK - see above")
    else:
        print("    " + ("\n    ".join(warn) if warn else "all reporting stations fine"))

    # ---- lake sondes ---------------------------------------------------
    lake = [r for r in rows if r["category"] == "lake"]
    tele = {}
    manual = {}
    for r in lake:
        last = _parse_iso(r.get("last_update"))
        bucket = manual if r["is_manual"] else tele
        cur = bucket.get(r["site"])
        if cur is None or (last and cur[0] and last > cur[0]) or cur[0] is None:
            bucket[r["site"]] = (last, r["is_live"])
    print("\n  LAKE")
    tl = sorted(s for s, (t, _) in tele.items() if t and _hours(t, ref) <= LIVE_WINDOW_HOURS)
    td = sorted(s for s in tele if s not in tl)
    print(f"    live sondes      {', '.join(tl) or 'none'}")
    for s in td:
        last = tele[s][0]
        print(f"    quiet sonde      {s}, {_age(_hours(last, ref))} quiet at snapshot")
    if manual:
        newest = max((v[0] for v in manual.values() if v[0]), default=None)
        print(f"    by hand          {len(manual)} sites, most recent upload "
              f"{newest.astimezone():%Y-%m-%d}" if newest else f"    by hand  {len(manual)} sites")

    # ---- data waiting to pull (the page's banner) ----------------------
    page = WEB_DIR / "assets" / "latest.json"
    print("\n  WAITING TO PULL")
    if not page.exists():
        print("    unknown - run `pixi run transform` to build the page's data")
    else:
        d = json.loads(page.read_text(encoding="utf-8"))
        built = _parse_iso(d.get("generated_at"))
        if snap and built and built < snap:
            todo.append("pixi run transform - the page's data is older than the latest snapshot")
        alert = d.get("upload_alert")
        if not alert:
            print("    nothing")
        else:
            for e in alert.get("sites", []):
                print(f"    {e['site']:18} {e['records']:,} records   "
                      + " then ".join(e.get("commands", [])))
            for c in alert.get("commands", []):
                todo.append(c)

    # ---- impossible readings ------------------------------------------
    print("\n  IMPOSSIBLE READINGS")
    try:
        import duckdb  # noqa: F401  — the scan quietly returns nothing without it
        from secchi.sources.watch import load_baseline, scan_readings
        base = load_baseline(REFERENCE_DIR) or {}
        known = base.get("quality_reported")
        new, _ = scan_readings(known or [])
        if known is None:
            print(f"    the watcher hasn't run this check yet; its first run will "
                  f"report {len(new)} known episode(s)")
        elif new:
            by_site = sorted({c["detail"].split("/")[-1] for c in new})
            print(f"    {len(new)} new episode(s) not yet reported: {', '.join(by_site)}")
            todo.append("new impossible readings - see docs/impossible-readings.md")
        else:
            print("    none new since the watcher's last report")
    except Exception as exc:
        print(f"    COULD NOT CHECK ({exc})")
        unavailable.append("impossible-reading scan")

    # ---- beyond TEON ------------------------------------------------------
    # Context sources, read from the page's own data so the dates here are
    # the dates the page shows. A daily source more than 4 days behind
    # usually means its step is failing quietly; the Actions run says why.
    print("\n  BEYOND TEON")
    try:
        snap = json.loads((WEB_DIR / "assets" / "latest.json").read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).date()

        def behind(day: str | None) -> int | None:
            return None if not day else (today - datetime.fromisoformat(day).date()).days

        rain = snap.get("rain") or {}
        lab = (snap.get("snowlab") or {}).get("daily") or {}
        air = snap.get("airports") or {}
        clar = snap.get("clarity") or {}
        climo = (snap.get("snowlab") or {}).get("climatology") or {}
        for label, day in (("SNOTEL (shores)", rain.get("newest_day")),
                           ("Snow Lab, SNOTEL 428", lab.get("newest_day")),
                           ("airports", air.get("newest_day"))):
            d = behind(day)
            flag = "" if d is not None and d <= 4 else "   <- behind"
            print(f"    {label:22} newest day {day or 'none'}{flag}")
            if flag:
                todo.append(f"{label} is behind - check the fetch run's warnings on GitHub")
        print(f"    {'TERC Secchi':22} edi.1340 rev {clar.get('revision') or '?'}, "
              f"newest reading {clar.get('newest_day') or 'none'} (published a few times a year)")
        print(f"    {'Snow Lab record':22} water years {climo.get('first_year', '?')}-"
              f"{climo.get('last_year', '?')}")
    except Exception as exc:
        print(f"    COULD NOT CHECK ({exc}) - run `pixi run transform`")

    # ---- to do ---------------------------------------------------------
    if unavailable:
        todo.insert(0, f"some checks could not run ({', '.join(unavailable)}) - "
                       f"run `pixi install`, then `pixi run status` again")
    print("\n  TO DO")
    for t in dict.fromkeys(todo):
        print(f"    - {t}")
    if not todo:
        print("    nothing needs doing")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(report())
