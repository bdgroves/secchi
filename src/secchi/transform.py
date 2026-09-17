"""Reshape raw TEON snapshots for analysis and display.

Three outputs:

- ``data/processed/observations.parquet`` — long-format history, one row
  per (site, sensor_type, variable, timestamp), deduped on the raw record
  UUID that TEON assigns to every observation.
- ``data/processed/latest_wide.parquet`` — the most recent observation
  per (site, sensor_type) as a wide row.
- ``web/assets/latest.json`` — the compact snapshot the dashboard reads.
  Includes the current TEON visibility flags and a summarized inventory,
  so the dashboard has no cross-origin dependencies.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from secchi.config import (
    EXO_VARIABLES,
    LIVE_EXO_SITES,
    LIVE_WINDOW_HOURS,
    PROCESSED_DIR,
    RAW_DIR,
    SITE_METADATA,
    WEB_DIR,
)

log = logging.getLogger("secchi.transform")

# Non-measurement columns in TEON EXO records — everything else is a variable.
_META_COLS = frozenset({"uuid", "site", "longitude", "latitude", "TIMESTAMP",
                        "kor_site_name", "TSS"})

_CATEGORY_ORDER = {"lake": 0, "stream": 1, "terrestrial": 2}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

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


def load_latest_json(raw_dir: Path, subdir: str) -> dict | None:
    """Return the most recent JSON snapshot in ``data/raw/<subdir>/``."""
    root = raw_dir / subdir
    if not root.exists():
        return None
    files = sorted(root.rglob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("malformed latest %s: %s", files[-1], exc)
        return None


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Dashboard snapshot
# ---------------------------------------------------------------------------

def build_site_cards(df_wide: pd.DataFrame) -> dict:
    """Card-shaped payload for the three anchor EXO sites."""
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
    return sites


def build_inventory_summary(inventory: dict | None) -> list[dict]:
    """Flatten the TEON inventory into a display-ready list.

    Each entry: ``{category, sensor_type, sensor_type_display, site, lat,
    lng, last_update, first_update, data_count, is_live, is_manual}``.
    Sorted for stable rendering: category → sensor type → site name.
    Non-recent sensors are kept but flagged, so the dashboard can show the
    full network including seasonally-parked or offline gear.
    """
    if not inventory:
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - LIVE_WINDOW_HOURS * 3600

    rows: list[dict] = []
    for category, sensor_types in (inventory.get("locations") or {}).items():
        for sensor_type, sensors in (sensor_types or {}).items():
            for s in sensors:
                last_iso = s.get("last_update")
                last_dt = _parse_iso(last_iso)
                is_live = bool(last_dt and last_dt.timestamp() >= cutoff)
                # "Manual" telemetry is inferred from the id prefix TEON
                # uses (``ExoSensorManual_...``).
                is_manual = "Manual" in (s.get("id") or "")
                rows.append({
                    "category": category,
                    "sensor_type": sensor_type,
                    "sensor_type_display": _display_type(sensor_type),
                    "site": s.get("site"),
                    "lat": s.get("lat"),
                    "lng": s.get("lng"),
                    "first_update": s.get("first_update"),
                    "last_update": last_iso,
                    "data_count": s.get("data_count"),
                    "is_live": is_live,
                    "is_manual": is_manual,
                })

    rows.sort(key=lambda r: (
        _CATEGORY_ORDER.get(r["category"], 99),
        r["sensor_type"],
        not r["is_live"],   # live first
        r["site"] or "",
    ))
    return rows


def _display_type(sensor_type: str) -> str:
    """TEON's inventory keys are already display-ready (e.g. ``"EXO"``,
    ``"Soil Environmental Conditions"``). Return as-is; kept as a shim for
    the small handful of aliases we do want to prettify."""
    return {
        "EXO": "EXO",
        "Minidot": "MiniDot",
        "Hobo": "HOBO",
    }.get(sensor_type, sensor_type)


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_dashboard_snapshot(df_wide: pd.DataFrame,
                             inventory: dict | None,
                             disabled: list[str]) -> dict:
    """Compose the payload the dashboard reads."""
    return {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "source": "TEON",
        "sites": build_site_cards(df_wide),
        "disabled": sorted(disabled),
        "inventory": build_inventory_summary(inventory),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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

    inventory = load_latest_json(RAW_DIR, "inventory")
    visibility = load_latest_json(RAW_DIR, "visibility") or {}
    disabled = visibility.get("disabled", [])

    snapshot = build_dashboard_snapshot(df_wide, inventory, disabled)
    assets = WEB_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    latest_path = assets / "latest.json"
    latest_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s (%d sites, %d inventory rows, %d disabled)",
             latest_path, len(snapshot["sites"]),
             len(snapshot["inventory"]), len(snapshot["disabled"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
