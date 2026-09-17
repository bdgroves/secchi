"""Ingest TEON snapshots to ``data/raw``.

Two artifacts per run:

- ``data/raw/inventory/YYYY/MM/DD/HHMMSS.json`` — the full
  ``/sensors/locations`` payload, captured verbatim.
- ``data/raw/teon/{sensor_slug}/{site}/YYYY/MM/DD/HHMMSS.json`` — a
  paginated pull of recent observations for each targeted sensor.

Targets default to the three live-EXO sites configured in
:data:`secchi.config.LIVE_EXO_SITES`; passing ``--all-live`` widens to
every sensor whose inventory ``last_update`` is within the live window.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from secchi.config import (
    LIVE_EXO_SITES,
    LIVE_WINDOW_HOURS,
    RAW_DIR,
)
from secchi.sources.teon import TeonClient, slugify_site

log = logging.getLogger("secchi.ingest")


def _snapshot_path(root: Path, ts: datetime) -> Path:
    return root / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}" / f"{ts.strftime('%H%M%S')}.json"


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("wrote %s (%s bytes)", path, path.stat().st_size)
    return path


def write_inventory(payload: dict, root: Path = RAW_DIR) -> Path:
    ts = datetime.now(timezone.utc)
    payload = {"fetched_at": ts.isoformat(), "source": "TEON", **payload}
    return _write_json(_snapshot_path(root / "inventory", ts), payload)


def write_visibility(disabled_slugs: set[str], root: Path = RAW_DIR) -> Path:
    """Persist the current visibility flags so transform can bake them into
    ``latest.json`` — the dashboard should never call TEON directly, so
    hosting from GitHub Pages has no cross-origin dependency."""
    ts = datetime.now(timezone.utc)
    payload = {
        "fetched_at": ts.isoformat(),
        "source": "TEON",
        "disabled": sorted(disabled_slugs),
    }
    return _write_json(_snapshot_path(root / "visibility", ts), payload)


def write_sensor_snapshot(snapshot: dict, root: Path = RAW_DIR) -> Path:
    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    slug = snapshot["sensor_slug"]
    # Filesystem-safe site name.
    site = snapshot["site"].replace("/", "_").replace(" ", "_")
    root_dir = root / "teon" / slug / site
    return _write_json(_snapshot_path(root_dir, fetched_at), snapshot)


def build_targets(client: TeonClient, mode: str) -> list[tuple[str, str]]:
    """Decide which (sensor_type, site) pairs to pull this run."""
    if mode == "live-exo":
        return [("ExoSensor", site) for site in LIVE_EXO_SITES]

    if mode == "all-live":
        pairs: list[tuple[str, str]] = []
        for sensor in client.live_sensors(window_hours=LIVE_WINDOW_HOURS):
            pairs.append((sensor["_sensor_type"], sensor["site"]))
        return pairs

    raise ValueError(f"unknown mode {mode!r}")


def run(mode: str = "live-exo") -> int:
    with TeonClient() as client:
        # 1) Snapshot the full inventory every run — cheap, and it's the
        #    source of truth for what sensors even exist.
        try:
            inventory = client.list_sensors()
            write_inventory(inventory)
        except Exception:
            log.exception("inventory pull failed")

        # 2) Respect TEON's own visibility flags — the frontend hides some
        #    sites (as of Sep 2026, "4H Camp" for lake/EXO), and so should we.
        disabled = client.disabled_sites()
        write_visibility(disabled)
        if disabled:
            log.info("visibility: %d site slug(s) disabled by TEON: %s",
                     len(disabled), ", ".join(sorted(disabled)))

        # 3) Pull time series for the targeted sensors, skipping any TEON
        #    has asked us to hide.
        raw_targets = build_targets(client, mode)
        targets = [
            (sensor_type, site) for (sensor_type, site) in raw_targets
            if slugify_site(site) not in disabled
        ]
        skipped = len(raw_targets) - len(targets)
        log.info("ingest mode=%s targets=%d (skipped %d disabled)",
                 mode, len(targets), skipped)

        successes = 0
        for snapshot in client.fetch_many(targets):
            if snapshot["record_count"] == 0:
                log.warning("empty snapshot for %s @ %s", snapshot["sensor_type"], snapshot["site"])
                continue
            write_sensor_snapshot(snapshot)
            successes += 1

        log.info("ingest complete: %d/%d snapshots", successes, len(targets))
        return 0 if successes > 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pull a TEON snapshot into data/raw.")
    parser.add_argument(
        "--mode",
        choices=("live-exo", "all-live"),
        default="live-exo",
        help="live-exo: only the three curated EXO sites. all-live: every sensor with recent data.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    try:
        return run(mode=args.mode)
    except Exception:
        log.exception("ingest failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
