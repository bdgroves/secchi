"""Fetch a snapshot of TEON sensor readings and archive it to ``data/raw``.

Designed to run on a schedule (GitHub Actions cron) or on demand. Each run
writes a timestamped JSON file so the raw record is preserved even as the
processed layer is regenerated.

Usage
-----
    python -m secchi.ingest
    secchi-ingest             # after `pip install -e .`
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from secchi.config import (
    HTTP_TIMEOUT_SECONDS,
    RAW_DIR,
    STATIONS,
    TEON_API_BASE,
    USER_AGENT,
    VARIABLES,
)

log = logging.getLogger("secchi.ingest")


def fetch_station(client: httpx.Client, station_id: str) -> dict:
    """Fetch the current readings for a single station.

    TODO: adjust the URL template once TEON's endpoint shape is confirmed.
    The current guess is ``/stations/{id}/latest`` returning JSON with a
    ``variables`` field keyed by sensor name.
    """
    url = f"{TEON_API_BASE}/stations/{station_id}/latest"
    log.info("GET %s", url)
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


def fetch_all() -> dict:
    """Fetch every station in :data:`STATIONS` and package a snapshot."""
    ts = datetime.now(timezone.utc)
    snapshot: dict = {
        "fetched_at": ts.isoformat(),
        "source": "TEON",
        "variables_requested": list(VARIABLES),
        "stations": {},
    }

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, headers=headers) as client:
        for station_id, meta in STATIONS.items():
            try:
                snapshot["stations"][station_id] = {
                    "meta": meta,
                    "readings": fetch_station(client, station_id),
                }
            except httpx.HTTPError as exc:
                log.warning("station %s failed: %s", station_id, exc)
                snapshot["stations"][station_id] = {
                    "meta": meta,
                    "error": str(exc),
                }
    return snapshot


def write_snapshot(snapshot: dict, out_dir: Path = RAW_DIR) -> Path:
    """Persist a snapshot to ``data/raw/YYYY/MM/DD/HHMMSS.json``."""
    ts = datetime.fromisoformat(snapshot["fetched_at"])
    subdir = out_dir / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}"
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{ts.strftime('%H%M%S')}.json"
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    log.info("wrote %s", path)
    return path


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    try:
        snapshot = fetch_all()
        write_snapshot(snapshot)
    except Exception:
        log.exception("ingest failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
