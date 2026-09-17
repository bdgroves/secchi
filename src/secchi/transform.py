"""Reshape raw TEON snapshots into a tidy time series for analysis and display.

Two outputs:

- ``data/processed/readings.parquet`` — long-format history for notebooks and
  modeling, one row per (station, variable, timestamp).
- ``data/processed/latest.json`` — the most recent reading per station,
  shaped for direct consumption by the web dashboard so the frontend never
  has to touch pandas or parquet.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from secchi.config import PROCESSED_DIR, RAW_DIR, WEB_DIR

log = logging.getLogger("secchi.transform")


def load_raw_snapshots(raw_dir: Path = RAW_DIR) -> list[dict]:
    """Load every raw snapshot JSON on disk."""
    snapshots: list[dict] = []
    for path in sorted(raw_dir.rglob("*.json")):
        try:
            snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            log.warning("skipping malformed %s: %s", path, exc)
    return snapshots


def to_long_frame(snapshots: list[dict]) -> pd.DataFrame:
    """Flatten snapshots into a long DataFrame.

    Expected columns: ``fetched_at``, ``station``, ``variable``, ``value``.
    Adjust the readings-extraction block once the real TEON response shape is
    known.
    """
    rows: list[dict] = []
    for snap in snapshots:
        fetched_at = snap.get("fetched_at")
        for station_id, station in snap.get("stations", {}).items():
            readings = station.get("readings") or {}
            # TODO: replace with the real shape once confirmed.
            variables = readings.get("variables", {}) if isinstance(readings, dict) else {}
            for var_name, value in variables.items():
                rows.append(
                    {
                        "fetched_at": fetched_at,
                        "station": station_id,
                        "variable": var_name,
                        "value": value,
                    }
                )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True)
    return df


def write_processed(df: pd.DataFrame, out_dir: Path = PROCESSED_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / "readings.parquet"
    df.to_parquet(parquet_path, index=False)
    log.info("wrote %s (%d rows)", parquet_path, len(df))


def write_latest(snapshots: list[dict], web_dir: Path = WEB_DIR) -> None:
    """Copy the most recent snapshot to ``web/assets/latest.json``."""
    if not snapshots:
        log.info("no snapshots — skipping latest.json")
        return
    latest = max(snapshots, key=lambda s: s.get("fetched_at", ""))
    assets = web_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")
    log.info("wrote %s", assets / "latest.json")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    snapshots = load_raw_snapshots()
    df = to_long_frame(snapshots)
    write_processed(df)
    write_latest(snapshots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
