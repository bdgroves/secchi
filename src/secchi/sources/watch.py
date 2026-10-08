"""Detect changes in what the upstream networks are publishing.

The point of this is to stop having to check. Both networks are actively
being built out, and several things we care about will change without
announcement:

- The two manual EXO sondes (Blackwood 3, Meeks) publish nothing until
  someone snorkels out, retrieves them and uploads. When that happens
  their record jumps about two months at once, through the same endpoint
  we already poll.
- MiniDot and HOBO stopped around 2026-06-10, the manual sondes on
  2026-07-09 — a clustering that looks like a summer haul-out rather than
  failure. If so they are probably back in the water now and will resume.
- TEON's `/site-visibility/disabled` currently hides 4H Camp. That flag
  could lift.
- Whole sensor types could appear: the StoryMap describes a fourth
  monitoring domain (aquatic — ponds, wet meadows, upland lakes) that the
  API does not expose at all.
- USGS gauges gain and lose parameters; the fine-sediment series at Upper
  Truckee only began in 2014.

This module compares the current inventory against a stored baseline and
reports what moved. The workflow then opens a GitHub issue, so a change
arrives as a notification rather than something you find weeks later.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from secchi.config import (
    LIVE_WINDOW_HOURS,
    teon_zone,
    REFERENCE_DIR,
    USGS_GAUGES,
)

log = logging.getLogger("secchi.sources.watch")

BASELINE_FILE = "watch_baseline.json"

# A sensor that has been quiet longer than this is "dormant". Chosen to sit
# well beyond the live window so a sensor doesn't flap between states on a
# missed hourly run.
DORMANT_AFTER_DAYS = 30

# How many new records on a non-live sensor count as an upload rather than
# noise. A manual sonde retrieval delivers thousands at once — Blackwood 3
# and Meeks each hold about 15,450 records for a single deployment — so
# this is set well above any plausible trickle and well below a real batch.
DORMANT_UPLOAD_THRESHOLD = 50


def _snapshot_state(teon_inventory: dict, disabled: set[str],
                    now: datetime | None = None) -> dict:
    """Reduce the inventory to the facts worth watching.

    Deliberately narrow. Record counts change every hour, so including
    them would make every run look like a change; what matters is whether
    a sensor exists, whether it is reporting, and whether it is hidden.

    ``now`` is the moment the inventory describes. It defaults to the
    present; replaying stored snapshots (``news_from_snapshots``) passes
    each snapshot's own capture time.
    """
    now = now or datetime.now(timezone.utc)
    live_cutoff = now - timedelta(hours=LIVE_WINDOW_HOURS)
    dormant_cutoff = now - timedelta(days=DORMANT_AFTER_DAYS)

    sensors: dict[str, dict] = {}
    types: set[str] = set()
    categories: set[str] = set()

    for category, by_type in (teon_inventory.get("locations") or {}).items():
        categories.add(category)
        for sensor_type, entries in (by_type or {}).items():
            types.add(sensor_type)
            for s in entries:
                site = s.get("site") or "?"
                key = f"{category}/{sensor_type}/{site}"
                last = s.get("last_update")
                parsed = None
                if isinstance(last, str):
                    try:
                        parsed = datetime.fromisoformat(last)
                        # TEON's last_update is naive logger time: PST all
                        # year, or Pacific local for MiniDOT (config
                        # teon_zone). Read as UTC it was 7-8 h too old, so a
                        # station looked quiet at ~17 h instead of 24 h, and
                        # one late 12-hour batch was enough to report five
                        # stations quiet on 2026-10-05.
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=ZoneInfo(teon_zone(sensor_type)))
                    except ValueError:
                        parsed = None
                if parsed is None:
                    state = "unknown"
                elif parsed >= live_cutoff:
                    state = "live"
                elif parsed >= dormant_cutoff:
                    state = "quiet"
                else:
                    state = "dormant"
                sensors[key] = {
                    "state": state,
                    "last_update": last,
                    # Record count IS compared, but only for sensors that
                    # aren't live — see diff_state. For a live sensor it
                    # changes every hour and would drown the report; for a
                    # dormant one it should never move at all, so a jump is
                    # the signature of a manual download being uploaded.
                    "data_count": s.get("data_count"),
                }

    return {
        "captured_at": now.isoformat(),
        "sensor_types": sorted(types),
        "categories": sorted(categories),
        "disabled_sites": sorted(disabled),
        "usgs_gauges": sorted(USGS_GAUGES),
        "sensors": sensors,
    }


# Readings no lake can produce. Each rule is (instrument, variable, low,
# high): a value outside [low, high] is impossible, not merely unusual.
#
# Why this exists: three sondes had their channels filed under the wrong
# names — 4H Camp for a week in July 2026, Sunnyside for eight weeks from
# 2026-04-30 (a "temperature" of 85-102), and the values looked plausible
# enough in most columns that nothing flagged them. They were found by
# hand, months later. The two most reliable tells are oxygen and
# temperature, because a scramble almost always drops some other channel's
# value somewhere physically impossible.
#
# pH of exactly 0 is NOT flagged: several sondes report a dead channel as
# 0 continuously, which is known and reported, and would drown the rest.
IMPOSSIBLE = [
    ("ExoSensor",     "Temp",                        -2.0,  35.0),
    ("ExoSensor",     "Do_mgL",                       0.0,  20.0),
    ("ExoSensor",     "Do_percent",                  50.0, 150.0),
    ("ExoSensor",     "pH",                           0.0,  14.0),
    ("MiniDotSensor", "Temperature",                 -2.0,  35.0),
    ("MiniDotSensor", "Dissolved Oxygen",             0.0,  20.0),
    ("MiniDotSensor", "Dissolved Oxygen Saturation", 50.0, 150.0),
    ("HoboSensor",    "temperature",                 -2.0,  35.0),
]

INSTRUMENT_LABEL = {"ExoSensor": "EXO sonde", "MiniDotSensor": "MiniDOT",
                    "HoboSensor": "HOBO"}


def scan_readings(already_reported: list[str]) -> tuple[list[dict], list[str]]:
    """Find episodes of impossible lake readings not reported before.

    An episode is one site, one variable, one month. Each is reported
    ONCE: the keys already raised travel in the baseline, so a bad week
    doesn't reopen an issue every six hours. Scans the whole record, not
    just recent days, because hand-collected sondes upload months at once
    and a bad stretch can arrive with old timestamps.

    Returns (changes, all_reported_keys). Anything going wrong — no store,
    no DuckDB — returns no changes and leaves the keys as they were; the
    inventory watch must never fail because of this.
    """
    try:
        import duckdb
        from secchi.query import _glob
    except ImportError:
        log.warning("DuckDB unavailable; skipping the impossible-reading scan")
        return [], list(already_reported)
    g = _glob("observations")
    if g is None:
        return [], list(already_reported)

    rules = " OR ".join(
        f"(sensor_type = '{inst}' AND variable = '{var}' "
        f"AND (value < {lo} OR value > {hi}))"
        for inst, var, lo, hi in IMPOSSIBLE)
    # Exact-zero pH is a known dead channel, not a new fault.
    sql = f"""
        SELECT site, sensor_type, variable, strftime(timestamp, '%Y-%m') AS ym,
               count(*) AS n, min(timestamp) AS first, max(timestamp) AS last,
               min(value) AS lo, max(value) AS hi
        FROM read_parquet('{g}', hive_partitioning = true, union_by_name = true)
        WHERE ({rules}) AND NOT (variable = 'pH' AND value = 0)
        GROUP BY ALL ORDER BY site, ym, variable"""
    try:
        rows = duckdb.sql(sql).fetchall()
    except Exception as exc:                      # never break the watch
        log.warning("impossible-reading scan failed: %s", exc)
        return [], list(already_reported)

    seen = set(already_reported)
    changes: list[dict] = []
    for site, inst, var, ym, n, first, last, lo, hi in rows:
        key = f"{site}|{var}|{ym}"
        if key in seen:
            continue
        seen.add(key)
        span = (f"{first:%Y-%m-%d %H:%M}" if n == 1 else
                f"{first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M}")
        changes.append({
            "severity": "notable",
            "kind": "impossible readings",
            "detail": f"lake/{INSTRUMENT_LABEL.get(inst, inst)}/{site}",
            "note": f"{var}: {n:,} reading(s) {lo:g} to {hi:g}, {span}",
        })
    return changes, sorted(seen)


def load_baseline(out_dir: Path = REFERENCE_DIR) -> dict | None:
    path = out_dir / BASELINE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("baseline is malformed (%s); treating as absent", exc)
        return None


def save_baseline(state: dict, out_dir: Path = REFERENCE_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / BASELINE_FILE
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The news log: what the page's "What's new" box reads
# ---------------------------------------------------------------------------
# The watcher opens a GitHub issue for each change, which reaches Brooks's
# inbox but leaves nothing for the page. So CI also appends each change to
# a small committed log. Added 2026-10-03.

NEWS_LOG = REFERENCE_DIR / "news_log.json"
NEWS_KEEP_DAYS = 120
# Kinds worth keeping. "state change" (quiet <-> dormant) is bookkeeping,
# and impossible-reading episodes stay in the issues, not on the page.
NEWS_KINDS = {"new sensor type", "sensor type gone", "new category",
              "site hidden by TEON", "site un-hidden by TEON", "new sensor",
              "sensor removed", "dormant sensor received an upload",
              "sensor resumed", "sensor went quiet"}


def news_events(changes: list[dict], new_state: dict) -> list[dict]:
    """Changes from ``diff_state`` as log entries: when, what, where."""
    out = []
    for c in changes:
        if c.get("kind") not in NEWS_KINDS:
            continue
        e = {"at": new_state["captured_at"], "kind": c["kind"], "detail": c["detail"]}
        parts = str(c["detail"]).split("/")
        if len(parts) == 3:
            e["category"], e["sensor_type"], e["site"] = parts
            s = new_state["sensors"].get(c["detail"], {})
            e["last_update"] = s.get("last_update")
        elif c["kind"].startswith("site "):
            e["site"] = c["detail"]
        note = c.get("note") or ""
        m = re.search(r"\(\+([\d,]+)\)", note)
        if m:
            e["added"] = int(m.group(1).replace(",", ""))
        out.append(e)
    return out


def append_news(events: list[dict], path: Path = NEWS_LOG,
                now: datetime | None = None) -> int:
    """Add events to the log, drop ones older than NEWS_KEEP_DAYS, write atomically.

    Idempotent: an event already logged (same time, kind and detail) is
    not added twice, so replaying snapshots that overlap the log is safe.
    """
    now = now or datetime.now(timezone.utc)
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    seen = {(e["at"], e["kind"], e["detail"]) for e in old}
    fresh = [e for e in events if (e["at"], e["kind"], e["detail"]) not in seen]
    cutoff = now - timedelta(days=NEWS_KEEP_DAYS)
    keep = [e for e in old + fresh
            if datetime.fromisoformat(e["at"].replace("Z", "+00:00")) >= cutoff]
    keep.sort(key=lambda e: (e["at"], e["kind"], e["detail"]))
    text = json.dumps(keep, indent=1, ensure_ascii=False) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return len(fresh)


def news_from_snapshots(raw_dir: Path) -> list[dict]:
    """Replay the stored inventory snapshots (the 7-day raw buffer) as events.

    Each snapshot is reduced at its own capture time and diffed against
    the one before, exactly as the watcher would have. Used once to seed
    the log; harmless to rerun.
    """
    inv_root, vis_root = raw_dir / "inventory", raw_dir / "visibility"
    events, prev = [], None
    for f in sorted(inv_root.rglob("*.json")):
        inv = json.loads(f.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(str(inv.get("fetched_at")).replace("Z", "+00:00"))
        vf = vis_root / f.relative_to(inv_root)
        disabled = set(json.loads(vf.read_text(encoding="utf-8")).get("disabled", [])) \
            if vf.exists() else set()
        state = _snapshot_state(inv, disabled, now=at)
        if prev is not None:
            events += news_events(diff_state(prev, state), state)
        prev = state
    return events


def diff_state(old: dict, new: dict) -> list[dict]:
    """Compare two states and return the changes worth reporting.

    Each change carries a severity so the workflow can decide whether to
    raise an issue: "notable" means something became available or
    unavailable, "info" means a smaller shift.
    """
    changes: list[dict] = []

    # Whole sensor types appearing is the biggest signal — the StoryMap
    # describes an aquatic domain the API has never exposed.
    for t in sorted(set(new["sensor_types"]) - set(old.get("sensor_types", []))):
        changes.append({"severity": "notable", "kind": "new sensor type",
                        "detail": t})
    for t in sorted(set(old.get("sensor_types", [])) - set(new["sensor_types"])):
        changes.append({"severity": "notable", "kind": "sensor type gone",
                        "detail": t})

    for c in sorted(set(new["categories"]) - set(old.get("categories", []))):
        changes.append({"severity": "notable", "kind": "new category",
                        "detail": c})

    # Visibility flags.
    old_dis = set(old.get("disabled_sites", []))
    new_dis = set(new["disabled_sites"])
    for site in sorted(new_dis - old_dis):
        changes.append({"severity": "notable", "kind": "site hidden by TEON",
                        "detail": site})
    for site in sorted(old_dis - new_dis):
        changes.append({"severity": "notable", "kind": "site un-hidden by TEON",
                        "detail": site})

    old_sensors = old.get("sensors", {})
    new_sensors = new["sensors"]

    for key in sorted(set(new_sensors) - set(old_sensors)):
        changes.append({"severity": "notable", "kind": "new sensor",
                        "detail": key,
                        "note": f"state {new_sensors[key]['state']}"})
    for key in sorted(set(old_sensors) - set(new_sensors)):
        changes.append({"severity": "info", "kind": "sensor removed",
                        "detail": key})

    # A dormant sensor whose record suddenly grows has had data uploaded.
    # This is the specific thing to watch for with Blackwood 3 and Meeks:
    # they are self-logging sondes that publish nothing until someone
    # snorkels out, retrieves them and uploads. When that lands, two months
    # of 15-minute data appear at once.
    #
    # State alone can miss it. If the uploaded records end before the
    # dormancy threshold, the sensor stays "dormant" and a state-only diff
    # reports nothing — which is exactly the silent failure this project
    # keeps producing. Record count catches it regardless.
    for key in sorted(set(old_sensors) & set(new_sensors)):
        was_state = old_sensors[key].get("state")
        old_count = old_sensors[key].get("data_count")
        new_count = new_sensors[key].get("data_count")
        if old_count is None or new_count is None:
            continue
        # Only for sensors that WERE not live. A live sensor's count moves
        # every hour and comparing it would make every run a change.
        if was_state == "live":
            continue
        growth = new_count - old_count
        if growth >= DORMANT_UPLOAD_THRESHOLD:
            changes.append({
                "severity": "notable",
                "kind": "dormant sensor received an upload",
                "detail": key,
                "note": f"record count {old_count:,} -> {new_count:,} "
                        f"(+{growth:,}), newest {new_sensors[key].get('last_update')}",
            })
        elif growth < 0:
            # Records disappearing is worth knowing about too — a reload,
            # a correction, or a bug upstream.
            changes.append({
                "severity": "info",
                "kind": "record count decreased",
                "detail": key,
                "note": f"{old_count:,} -> {new_count:,}",
            })

    # State transitions. Waking up is the one worth interrupting someone
    # for — it means data that was unreachable is now flowing.
    for key in sorted(set(old_sensors) & set(new_sensors)):
        was = old_sensors[key].get("state")
        now_state = new_sensors[key].get("state")
        if was == now_state:
            continue
        waking = was in ("dormant", "quiet") and now_state == "live"
        going_dark = was == "live" and now_state in ("quiet", "dormant")
        if waking:
            changes.append({
                "severity": "notable", "kind": "sensor resumed",
                "detail": key,
                "note": f"{was} -> live, last update {new_sensors[key].get('last_update')}",
            })
        elif going_dark:
            changes.append({
                "severity": "info", "kind": "sensor went quiet",
                "detail": key,
                "note": f"live -> {now_state}, last update {new_sensors[key].get('last_update')}",
            })
        else:
            changes.append({"severity": "info", "kind": "state change",
                            "detail": key, "note": f"{was} -> {now_state}"})

    return changes


def _grouped_lines(changes: list[dict], bold: bool) -> list[str]:
    """One line per (kind, site) rather than per endpoint.

    A forest station's soil, air and tree endpoints are one logger, so one
    outage produced six alerts — two signals times three endpoints — for a
    single event at Glenbrook 4. Grouping by the site at the end of the
    key makes one event read as one line. When the endpoints' notes agree
    (they usually do, sharing record counts) the note is shown once;
    otherwise each endpoint's note is listed beneath.
    """
    groups: dict[tuple, list[tuple]] = {}
    for c in changes:
        parts = c["detail"].split("/")
        site = parts[-1] if len(parts) >= 3 else c["detail"]
        endpoint = "/".join(parts[1:-1]) if len(parts) >= 3 else None
        groups.setdefault((c["kind"], site), []).append((endpoint, c.get("note")))

    out: list[str] = []
    for (kind, site), items in groups.items():
        endpoints = [e for e, _ in items if e]
        notes = list(dict.fromkeys(n for _, n in items if n))
        label = f"**{kind}**" if bold else kind
        what = f" ({', '.join(dict.fromkeys(endpoints))})" if endpoints else ""
        if len(notes) <= 1:
            note = f" — {notes[0]}" if notes else ""
            out.append(f"- {label}: `{site}`{what}{note}")
        else:
            out.append(f"- {label}: `{site}`{what}")
            for e, n in items:
                if n:
                    prefix = f"{e}: " if e and len(set(endpoints)) > 1 else ""
                    out.append(f"  - {prefix}{n}")
    return out


def format_report(changes: list[dict], new: dict) -> str:
    """Markdown suitable for a GitHub issue body."""
    if not changes:
        return "No changes since the last check."

    notable = [c for c in changes if c["severity"] == "notable"]
    info = [c for c in changes if c["severity"] != "notable"]

    lines = [f"Upstream check at {new['captured_at']}.", ""]
    if notable:
        lines.append("### Notable")
        lines.append("")
        lines += _grouped_lines(notable, bold=True)
        lines.append("")
    if info:
        lines.append("### Other")
        lines.append("")
        lines += _grouped_lines(info, bold=False)
        lines.append("")

    live = sum(1 for s in new["sensors"].values() if s["state"] == "live")
    lines += [
        "---",
        "",
        f"{live} of {len(new['sensors'])} TEON sensors reporting within "
        f"{LIVE_WINDOW_HOURS} h · {len(new['sensor_types'])} sensor types · "
        f"{len(new['usgs_gauges'])} USGS gauges configured.",
    ]
    return "\n".join(lines)
