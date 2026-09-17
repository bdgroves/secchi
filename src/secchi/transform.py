"""Reshape raw TEON snapshots for analysis and display.

Three outputs:

- ``data/processed/observations.parquet`` — long-format history, one row
  per (site, sensor_type, variable, timestamp), deduped on the raw record
  UUID that TEON assigns to every observation.
- ``data/processed/latest_wide.parquet`` — the most recent observation
  per (site, sensor_type) as a wide row, useful for quick reporting.
- ``web/assets/latest.json`` — the compact snapshot the dashboard reads.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from secchi.config import (
    EXO_VARIABLES,
    LIVE_EXO_SITES,
    PROCESSED_DIR,
    RAW_DIR,
    SITE_METADATA,
    WEB_DIR,
)

log = logging.getLogger("secchi.transform")

# Non-measurement columns in TEON EXO records — everything else is a variable.
_META_COLS = frozenset({"uuid", "site", "longitude", "latitude", "TIMESTAMP",
                        "kor_site_name", "TSS"})


def load_teon_snapshots(raw_dir: Path = RAW_DIR) -> Iterable[dict]:
    """Iterate over every ``teon/*/*/YYYY/MM/DD/*.json`` snapshot on disk."""
    teon_root = raw_dir / "teon"
    if not teon_root.exists():
        return
    for path in sorted(teon_root.rglob("*.json")):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("skipping malformed %s: %s", path, exc)


def to_long_frame(snapshots: Iterable[dict]) -> pd.DataFrame:
    """Flatten snapshots into a deduped long DataFrame."""
    rows: list[dict] = []
    for snap in snapshots:
        sensor_type = snap.get("sensor_type", "")
        for rec in snap.get("records", []):
            base = {
                "uuid": rec.get("uuid"),
                "site": rec.get("site"),
                "sensor_type": sensor_type,
                "timestamp": rec.get("TIMESTAMP"),
                "lat": rec.get("latitude"),
                "lng": rec.get("longitude"),
            }
            for col, value in rec.items():
                if col in _META_COLS:
                    continue
                rows.append({**base, "variable": col, "value": value})

    if not rows:
        return pd.DataFrame(columns=["uuid", "site", "sensor_type", "timestamp",
                                     "lat", "lng", "variable", "value"])

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    # Same observation may appear across snapshots — the record UUID is stable.
    df = df.drop_duplicates(subset=["uuid", "variable"]).reset_index(drop=True)
    return df


def to_latest_wide(df_long: pd.DataFrame) -> pd.DataFrame:
    """Most recent observation per (site, sensor_type) as a wide row."""
    if df_long.empty:
        return df_long
    latest_ts = (df_long.groupby(["site", "sensor_type"])["timestamp"]
                 .transform("max"))
    latest = df_long[df_long["timestamp"] == latest_ts]
    wide = latest.pivot_table(
        index=["site", "sensor_type", "timestamp", "lat", "lng"],
        columns="variable",
        values="value",
        aggfunc="first",
    ).reset_index()
    wide.columns.name = None
    return wide


def build_dashboard_snapshot(df_wide: pd.DataFrame) -> dict:
    """Shape ``latest_wide`` for the web dashboard's expected schema."""
    generated_at = pd.Timestamp.now(tz="UTC").isoformat()
    sites: dict[str, dict] = {}

    for site in LIVE_EXO_SITES:
        row = df_wide[(df_wide["site"] == site) & (df_wide["sensor_type"] == "ExoSensor")]
        if row.empty:
            continue
        r = row.iloc[0]

        readings: dict[str, dict] = {}
        for var, meta in EXO_VARIABLES.items():
            if meta.get("hide") or var not in row.columns:
                continue
            value = r[var]
            if pd.isna(value):
                continue
            value = float(value)
            if "clip_low" in meta and value < meta["clip_low"]:
                value = meta["clip_low"]
            readings[var] = {
                "value": round(value, 3),
                "label": meta["label"],
                "units": meta["units"],
            }

        site_meta = SITE_METADATA.get(site, {})
        sites[site] = {
            "sensor_type": "EXO",
            "shore": site_meta.get("shore"),
            "coordinates": {"lat": float(r["lat"]), "lng": float(r["lng"])},
            "observed_at": pd.Timestamp(r["timestamp"]).isoformat(),
            "readings": readings,
        }

    return {
        "generated_at": generated_at,
        "source": "TEON",
        "sites": sites,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    df_long = to_long_frame(load_teon_snapshots())
    log.info("loaded %d observations across %d unique records",
             len(df_long), df_long["uuid"].nunique() if not df_long.empty else 0)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    obs_path = PROCESSED_DIR / "observations.parquet"
    df_long.to_parquet(obs_path, index=False)
    log.info("wrote %s", obs_path)

    df_wide = to_latest_wide(df_long)
    wide_path = PROCESSED_DIR / "latest_wide.parquet"
    df_wide.to_parquet(wide_path, index=False)
    log.info("wrote %s", wide_path)

    snapshot = build_dashboard_snapshot(df_wide)
    assets = WEB_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    latest_path = assets / "latest.json"
    latest_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s (%d sites)", latest_path, len(snapshot["sites"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
