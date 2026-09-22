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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from secchi.config import (
    LIVE_WINDOW_HOURS,
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


def _snapshot_state(teon_inventory: dict, disabled: set[str]) -> dict:
    """Reduce the inventory to the facts worth watching.

    Deliberately narrow. Record counts change every hour, so including
    them would make every run look like a change; what matters is whether
    a sensor exists, whether it is reporting, and whether it is hidden.
    """
    now = datetime.now(timezone.utc)
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
                        parsed = datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
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
        for c in notable:
            note = f" — {c['note']}" if c.get("note") else ""
            lines.append(f"- **{c['kind']}**: `{c['detail']}`{note}")
        lines.append("")
    if info:
        lines.append("### Other")
        lines.append("")
        for c in info:
            note = f" — {c['note']}" if c.get("note") else ""
            lines.append(f"- {c['kind']}: `{c['detail']}`{note}")
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
