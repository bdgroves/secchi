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
    USGS_DEFAULT_PERIOD,
    USGS_GAUGES,
    USGS_PARAMETERS,
)
from secchi.sources.teon import TeonClient, slugify_site
from secchi.sources.usgs import UsgsClient, UsgsRateLimited

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
    """Decide which (sensor_type, site) pairs to pull this run.

    ``sensor_type`` here is the DISPLAY name as it appears in the
    /sensors/locations payload — "EXO", "Stream Level", etc. — because
    that's what TeonClient.resolve_slug keys on.
    """
    if mode == "live-exo":
        return [("EXO", site) for site in LIVE_EXO_SITES]

    if mode == "all-live":
        pairs: list[tuple[str, str]] = []
        for sensor in client.live_sensors(window_hours=LIVE_WINDOW_HOURS):
            pairs.append((sensor["_sensor_type"], sensor["site"]))
        return pairs

    raise ValueError(f"unknown mode {mode!r}")


def probe_slugs(client: TeonClient) -> int:
    """Diagnostic: resolve a URL slug for every live sensor type, no data pull.

    Prints a table of sensor type → resolved slug (or MISSING), so we can
    see at a glance how much of the network is reachable. Useful after
    TEON ships backend changes.
    """
    # Sites TEON hides return 404 on the time-series endpoint even when the
    # slug is correct, so they must not be used as probe representatives.
    # (4H Camp is hidden and sorts first among EXO sites, which previously
    # made the confirmed-good exo-sensor slug look broken.)
    disabled = client.disabled_sites()

    # One representative site per sensor type, preferring the freshest
    # non-disabled site.
    by_type: dict[str, str] = {}
    for sensor in client.live_sensors(window_hours=LIVE_WINDOW_HOURS):
        site = sensor["site"]
        if slugify_site(site) in disabled:
            continue
        by_type.setdefault(sensor["_sensor_type"], site)

    if not by_type:
        log.warning("no live, visible sensors found to probe")
        return 1

    log.info("probing %d sensor types (skipping %d disabled site slug(s))",
             len(by_type), len(disabled))
    resolved: dict[str, str | None] = {}
    for sensor_type, site in sorted(by_type.items()):
        resolved[sensor_type] = client.resolve_slug(sensor_type, site)

    width = max(len(t) for t in resolved) + 2
    print("\n  Sensor type".ljust(width + 4) + "Resolved slug")
    print("  " + "-" * (width + 30))
    for sensor_type, slug in sorted(resolved.items()):
        status = f"/sensors/{slug}" if slug else "— MISSING —"
        print(f"  {sensor_type.ljust(width)}{status}")
    hits = sum(1 for s in resolved.values() if s)
    print(f"\n  {hits}/{len(resolved)} sensor types reachable\n")
    return 0


def write_usgs_snapshot(snapshot: dict, root: Path = RAW_DIR) -> Path:
    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    collection = snapshot["collection"]
    site = snapshot["site_number"]
    return _write_json(_snapshot_path(root / "usgs" / collection / site, fetched_at), snapshot)


def probe_usgs(client: UsgsClient) -> int:
    """Ask each configured gauge what it actually measures.

    This is the honest replacement for assuming parameter availability.
    /time-series-metadata reports one row per (parameter, statistic) series
    with its period of record, so we learn both what exists and how far
    back it goes.
    """
    log.info("USGS probe: %d gauges, key %s",
             len(USGS_GAUGES), "set" if client.authenticated else "NOT set")

    described = client.describe_gauges()
    for site, series in described.items():
        meta = USGS_GAUGES.get(site, {})
        print(f"\n  {site} — {meta.get('name', '?')}")
        if not series:
            print("    no time series returned")
            continue

        rows = []
        for feat in series:
            props = feat.get("properties") or feat
            rows.append((
                props.get("parameter_code") or "?",
                (props.get("parameter_name") or "")[:42],
                props.get("statistic_id") or "",
                props.get("unit_of_measure") or "",
                (props.get("begin") or "")[:10],
                (props.get("end") or "")[:10],
            ))
        rows.sort()
        print(f"    {'code':6} {'parameter':44} {'stat':6} {'units':10} {'begin':11} end")
        print("    " + "-" * 92)
        for code, name, stat, unit, begin, end in rows:
            print(f"    {code:6} {name:44} {stat:6} {unit:10} {begin:11} {end}")

        wanted = set(meta.get("parameters", ()))
        present = {r[0] for r in rows}
        missing = wanted - present
        if missing:
            print(f"    NOTE: configured but not reported: {', '.join(sorted(missing))}")
        extra = present - wanted
        if extra:
            known = [c for c in sorted(extra) if c in USGS_PARAMETERS]
            if known:
                print(f"    NOTE: available and not configured: {', '.join(known)}")

    if client.rate_remaining is not None:
        print(f"\n  {client.rate_remaining} requests left this hour\n")
    return 0


def run_usgs(mode: str) -> int:
    """Ingest USGS gauges. Modes: usgs-probe (discovery), usgs (data)."""
    with UsgsClient() as client:
        if mode == "usgs-probe":
            return probe_usgs(client)

        successes = 0
        for site, meta in USGS_GAUGES.items():
            params = list(meta.get("parameters", ())) or None
            try:
                snapshot = client.fetch_continuous(
                    site, parameter_codes=params, period=USGS_DEFAULT_PERIOD)
            except UsgsRateLimited as exc:
                log.error("%s", exc)
                return 1
            except Exception:
                log.exception("USGS fetch failed for %s", site)
                continue

            if snapshot["feature_count"] == 0:
                log.warning("no features for %s (%s)", site, meta.get("name"))
                continue
            write_usgs_snapshot(snapshot)
            successes += 1

        log.info("USGS ingest complete: %d/%d gauges", successes, len(USGS_GAUGES))
        return 0 if successes else 1


def run(mode: str = "live-exo") -> int:
    if mode.startswith("usgs"):
        return run_usgs(mode)

    with TeonClient() as client:
        # Probe mode is diagnostic only: no snapshots written.
        if mode == "probe":
            return probe_slugs(client)

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
        choices=("live-exo", "all-live", "probe", "usgs", "usgs-probe"),
        default="live-exo",
        help=(
            "live-exo: only the curated EXO sites. "
            "all-live: every live TEON sensor (default for CI). "
            "probe: resolve TEON URL slugs and print a report, writing nothing. "
            "usgs: pull the configured USGS gauges. "
            "usgs-probe: ask each USGS gauge what it measures, writing nothing."
        ),
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
