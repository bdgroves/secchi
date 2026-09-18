"""Reshape raw TEON snapshots for analysis and display.

Outputs:

- ``data/processed/observations.parquet`` — long-format numeric history,
  one row per (site, sensor_type, variable, timestamp), deduped on the
  record UUID TEON assigns to every observation.
- ``data/processed/assets.parquet`` — non-numeric records (field-camera
  image references), kept separate because they can't share a float column.
- ``data/processed/latest_wide.parquet`` — most recent observation per
  (site, sensor_type) as a wide row.
- ``web/assets/latest.json`` — the snapshot the dashboard reads, including
  visibility flags and a summarized inventory so the page has no
  cross-origin dependencies.

A note on shared loggers: at the terrestrial stations, the air-temperature,
soil-moisture and tree-stress endpoints return three projections of one
Campbell logger table — identical record uuid, identical BattV_Avg and
PTemp_C_Avg, identical row counts. Deduping on (uuid, variable) collapses
the shared diagnostic channels to a single row, which is the behaviour we
want. The ``sensor_type`` recorded for a shared channel is whichever
snapshot the transform reached first; that's cosmetic, since the value is
the same either way.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from secchi.config import (
    ASSET_FIELDS,
    DIAGNOSTIC_FIELDS,
    LIVE_EXO_SITES,
    LIVE_WINDOW_HOURS,
    PROCESSED_DIR,
    RAW_DIR,
    RECORD_META_FIELDS,
    SENSOR_VARIABLES,
    SITE_METADATA,
    USGS_GAUGES,
    USGS_PARAMETERS,
    USGS_STATISTIC_INSTANTANEOUS,
    USGS_STATISTICS,
    TEON_TIMEZONE,
    TERRESTRIAL_STATIONS,
    TRANSECT_ALIGN_TOLERANCE_MINUTES,
    TRANSECT_PAIR,
    WEB_DIR,
)

log = logging.getLogger("secchi.transform")

_CATEGORY_ORDER = {"lake": 0, "stream": 1, "terrestrial": 2}

# Which variables each dashboard card surfaces, in display order.
EXO_CARD_VARIABLES = ("Temp", "Chl_a", "Do_percent", "Turbidity")
STATION_CARD_VARIABLES = ("Air_Temp", "RH", "Soil_VWC", "Soil_T")
# The transect compares meteorology and soil state only. Stream depth and
# dendrometers are deliberately excluded: they exist at some stations and
# not others, so including them would make the two columns structurally
# different rather than comparable.
TRANSECT_VARIABLES = ("Air_Temp", "RH", "Soil_VWC", "Soil_T")


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


def load_usgs_snapshots(raw_dir: Path = RAW_DIR) -> Iterable[dict]:
    """Iterate over every ``usgs/*/*/YYYY/MM/DD/*.json`` snapshot on disk."""
    root = raw_dir / "usgs"
    if not root.exists():
        return
    for path in sorted(root.rglob("*.json")):
        try:
            yield json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("skipping malformed %s: %s", path, exc)


def usgs_to_long(snapshots: Iterable[dict]) -> pd.DataFrame:
    """Flatten USGS GeoJSON snapshots into the same long format as TEON.

    USGS returns one feature per observation, so each becomes one row.
    Two USGS-only columns come along: ``approval_status`` (Provisional vs.
    Director-approved) and ``qualifier``. TEON publishes no equivalent, so
    they're null for TEON rows — worth keeping rather than discarding, since
    knowing a value is provisional changes how much weight it carries.
    """
    from secchi.sources.usgs import flatten_features

    rows: list[dict] = []
    for snap in snapshots:
        for obs in flatten_features(snap):
            value = obs.get("value")
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue

            code = obs.get("parameter_code") or ""
            stat = obs.get("statistic_id") or ""
            site_number = obs.get("site_number") or ""
            gauge = USGS_GAUGES.get(site_number, {})
            rows.append({
                "uuid": (obs.get("feature_id")
                         or f"{site_number}-{code}-{stat}-{obs.get('time')}"),
                "source": "USGS",
                # Site label mirrors TEON's human-readable naming so the two
                # sources can share downstream grouping.
                "site": gauge.get("name") or obs.get("site_name") or f"USGS {site_number}",
                "site_number": site_number,
                "sensor_type": "UsgsGauge",
                "timestamp": obs.get("time"),
                "lat": obs.get("lat"),
                "lng": obs.get("lng"),
                "variable": code,
                # A parameter code alone does not identify a series: one
                # gauge publishes the same parameter as instantaneous, daily
                # max, min, mean and median. The statistic is part of the
                # identity, so it's part of the dedupe key and part of what
                # the card builder groups on.
                "statistic_id": stat,
                "statistic": USGS_STATISTICS.get(stat, stat),
                "value": numeric,
                "unit_of_measure": obs.get("unit_of_measure"),
                "approval_status": obs.get("approval_status"),
                "qualifier": obs.get("qualifier"),
            })

    cols = ["uuid", "source", "site", "site_number", "sensor_type", "timestamp",
            "lat", "lng", "variable", "statistic_id", "statistic", "value",
            "unit_of_measure", "approval_status", "qualifier"]
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    if not df.empty:
        # USGS timestamps are RFC 3339 with real offsets — no guessing,
        # unlike TEON. Normalize to UTC so both sources compare cleanly.
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
        df = df.drop_duplicates(
            subset=["uuid", "variable", "statistic_id"]).reset_index(drop=True)
    return df


def build_usgs_cards(df_usgs: pd.DataFrame) -> dict:
    """Latest reading per parameter per gauge, shaped like the other cards."""
    if df_usgs.empty:
        return {}
    gauges: dict[str, dict] = {}
    for site_number, grp in df_usgs.groupby("site_number"):
        meta = USGS_GAUGES.get(site_number, {})
        readings: dict[str, dict] = {}
        group_cols = ["variable", "statistic_id"] if "statistic_id" in grp.columns \
            else ["variable"]
        for key, sub in grp.groupby(group_cols):
            code, stat = (key if isinstance(key, tuple) else (key, ""))
            latest = sub.loc[sub["timestamp"].idxmax()]
            pmeta = USGS_PARAMETERS.get(code, {})
            label = pmeta.get("label", code)
            # Only annotate when it's NOT the instantaneous series, so the
            # common case stays clean but an aggregate can never be mistaken
            # for a live reading.
            if stat and stat != USGS_STATISTIC_INSTANTANEOUS:
                label = f"{label} ({USGS_STATISTICS.get(stat, stat)})"
            entry = {
                "value": round(float(latest["value"]), 3),
                "label": label,
                "units": pmeta.get("units") or latest.get("unit_of_measure") or "",
                "parameter_code": code,
                "statistic_id": stat,
                "observed_at": pd.Timestamp(latest["timestamp"]).isoformat(),
            }
            if latest.get("approval_status"):
                entry["approval_status"] = latest["approval_status"]
            if pmeta.get("note"):
                entry["note"] = pmeta["note"]
            readings[f"{code}:{stat}" if stat else code] = entry

        if not readings:
            continue
        newest = grp["timestamp"].max()
        gauges[site_number] = {
            "site_number": site_number,
            "name": meta.get("name") or grp["site"].iloc[0],
            "shore": meta.get("shore"),
            "coordinates": {"lat": _first_float(grp["lat"]), "lng": _first_float(grp["lng"])},
            "observed_at": pd.Timestamp(newest).isoformat(),
            "readings": readings,
        }
    return gauges


def _first_float(series: pd.Series) -> float | None:
    vals = series.dropna()
    return float(vals.iloc[0]) if not vals.empty else None


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
# Flattening
# ---------------------------------------------------------------------------

def to_frames(snapshots: Iterable[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flatten snapshots into (numeric observations, assets).

    Numeric and non-numeric measurements are separated because parquet
    needs one type per column: a field-camera ``image`` value like
    ``s3://bucket/path.jpg`` can't live in the same column as a float.
    Null measurements are dropped — several channels (soil depths 3–5,
    pH, TSS) are present in the schema but null everywhere, and carrying
    empty rows for them adds nothing.
    """
    obs_rows: list[dict] = []
    asset_rows: list[dict] = []
    skipped_non_numeric: dict[str, int] = {}

    for snap in snapshots:
        sensor_type = snap.get("sensor_type", "")
        # TEON reports how many records exist upstream; keep it so asset
        # counts reflect the real archive rather than our ingest page size.
        total_available = snap.get("total_available")
        for rec in snap.get("records", []):
            base = {
                "uuid": rec.get("uuid"),
                "source": "TEON",
                "site": rec.get("site"),
                "sensor_type": sensor_type,
                "timestamp": rec.get("TIMESTAMP"),
                "lat": rec.get("latitude"),
                "lng": rec.get("longitude"),
            }

            for field, value in rec.items():
                if field in RECORD_META_FIELDS:
                    continue

                if field in ASSET_FIELDS:
                    if value is not None:
                        asset_rows.append({**base, "kind": field, "ref": str(value),
                                           "total_available": total_available})
                    continue

                if value is None:
                    continue

                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    # An undeclared string field. Count it and move on so a
                    # schema change upstream shows up in the log rather than
                    # crashing the parquet write.
                    skipped_non_numeric[field] = skipped_non_numeric.get(field, 0) + 1
                    continue

                obs_rows.append({**base, "variable": field, "value": numeric})

    if skipped_non_numeric:
        log.warning("skipped non-numeric values in undeclared fields: %s",
                    ", ".join(f"{k} (×{v})" for k, v in sorted(skipped_non_numeric.items())))

    obs_cols = ["uuid", "source", "site", "sensor_type", "timestamp",
                "lat", "lng", "variable", "value"]
    asset_cols = ["uuid", "source", "site", "sensor_type", "timestamp", "lat", "lng",
                  "kind", "ref", "total_available"]

    df_obs = pd.DataFrame(obs_rows, columns=obs_cols) if obs_rows else pd.DataFrame(columns=obs_cols)
    df_assets = (pd.DataFrame(asset_rows, columns=asset_cols)
                 if asset_rows else pd.DataFrame(columns=asset_cols))

    for df in (df_obs, df_assets):
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    if not df_obs.empty:
        # The record uuid is stable, so this collapses both re-fetched
        # snapshots and the shared-logger duplication described in the
        # module docstring.
        #
        # `site` is in the key purely as defensive hardening. The
        # shared-logger case we want to collapse is always within one site,
        # so including site never blocks it — but it does make a uuid
        # collision across two different sites harmless instead of silently
        # discarding one site's observations. TEON emits UUID4s, so this
        # shouldn't be reachable; it costs nothing to be certain.
        df_obs = df_obs.drop_duplicates(
            subset=["uuid", "site", "variable"]).reset_index(drop=True)
    if not df_assets.empty:
        df_assets = df_assets.drop_duplicates(
            subset=["uuid", "site", "kind"]).reset_index(drop=True)

    return df_obs, df_assets


def to_latest_wide(df_long: pd.DataFrame) -> pd.DataFrame:
    """Most recent observation per (site, sensor_type) as a wide row."""
    if df_long.empty:
        return df_long
    latest_ts = df_long.groupby(["site", "sensor_type"])["timestamp"].transform("max")
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

def _iso_local(ts) -> str | None:
    """Render a TEON timestamp with an explicit UTC offset.

    TEON's own strings are naive wall-clock; emitting them unchanged makes
    the browser parse them as the *viewer's* local time, which is wrong for
    anyone outside Pacific. Localizing here means the JSON carries the
    offset and every client agrees on the instant.
    """
    if ts is None or pd.isna(ts):
        return None
    t = pd.Timestamp(ts)
    if t.tz is None:
        try:
            t = t.tz_localize(TEON_TIMEZONE)
        except Exception:
            t = t.tz_localize("UTC")
    return t.isoformat()


def _format_reading(sensor_type: str, field: str, raw: float) -> dict | None:
    """Apply scale/clip/label metadata to one raw value."""
    meta = SENSOR_VARIABLES.get(sensor_type, {}).get(field)
    if meta is None or meta.get("hide"):
        return None
    value = float(raw)
    if "scale" in meta:
        value *= meta["scale"]
    if "clip_low" in meta and value < meta["clip_low"]:
        value = meta["clip_low"]
    out = {
        "value": round(value, 3),
        "label": meta["label"],
        "units": meta["units"],
    }
    if "note" in meta:
        out["note"] = meta["note"]
    return out


def _readings_for(row: pd.Series, columns, sensor_type: str, fields: Iterable[str]) -> dict:
    readings: dict[str, dict] = {}
    for field in fields:
        if field not in columns:
            continue
        raw = row[field]
        if pd.isna(raw):
            continue
        formatted = _format_reading(sensor_type, field, raw)
        if formatted is not None:
            readings[field] = formatted
    return readings


def build_lake_cards(df_wide: pd.DataFrame) -> dict:
    """Card payload for the lake EXO sondes."""
    sites: dict[str, dict] = {}
    for site in LIVE_EXO_SITES:
        row = df_wide[(df_wide["site"] == site) & (df_wide["sensor_type"] == "ExoSensor")]
        if row.empty:
            continue
        r = row.iloc[0]
        meta = SITE_METADATA.get(site, {})
        sites[site] = {
            "sensor_type": "EXO",
            "shore": meta.get("shore"),
            "coordinates": {"lat": float(r["lat"]), "lng": float(r["lng"])},
            "observed_at": _iso_local(r["timestamp"]),
            "readings": _readings_for(r, row.columns, "ExoSensor", EXO_CARD_VARIABLES),
        }
    return sites


def build_station_cards(df_wide: pd.DataFrame) -> dict:
    """Card payload for the terrestrial logger stations.

    Each station's air-temperature and soil-moisture readings come from the
    same logger table, so we merge across those sensor types into one card
    rather than showing the same station twice.
    """
    stations: dict[str, dict] = {}
    for site in TERRESTRIAL_STATIONS:
        rows = df_wide[df_wide["site"] == site]
        if rows.empty:
            continue

        readings: dict[str, dict] = {}
        observed: list[pd.Timestamp] = []
        coords: tuple[float, float] | None = None
        trees: list[float] = []

        for _, r in rows.iterrows():
            sensor_type = r["sensor_type"]
            readings.update(_readings_for(r, rows.columns, sensor_type, STATION_CARD_VARIABLES))

            # Dendrometers: summarize eight channels rather than listing them.
            if sensor_type == "TreeStressAndGrowth":
                for n in range(1, 9):
                    field = f"Tree_{n}_diameter_change"
                    if field in rows.columns and not pd.isna(r[field]):
                        trees.append(float(r[field]))

            # Stream level lives on its own logger at some sites.
            if sensor_type == "StreamLevel":
                formatted = _readings_for(r, rows.columns, sensor_type,
                                          ("Uncalibrated_water_depth",))
                readings.update(formatted)

            if not pd.isna(r["timestamp"]):
                observed.append(pd.Timestamp(r["timestamp"]))
            if coords is None and not pd.isna(r["lat"]):
                coords = (float(r["lat"]), float(r["lng"]))

        if not readings and not trees:
            continue

        meta = SITE_METADATA.get(site, {})
        card: dict = {
            "shore": meta.get("shore"),
            "coordinates": ({"lat": coords[0], "lng": coords[1]} if coords else None),
            "observed_at": _iso_local(max(observed)) if observed else None,
            "readings": readings,
        }
        if trees:
            card["dendrometers"] = {
                "count": len(trees),
                "mean_change": round(sum(trees) / len(trees), 1),
                "units": "µm",
            }
        if "note" in meta:
            card["note"] = meta["note"]
        stations[site] = card
    return stations


def build_transect(df_long: pd.DataFrame) -> dict | None:
    """The east–west pair, compared at a timestamp both sites share.

    Works from the full long-format history rather than each site's latest
    row. Air temperature and humidity swing hard across a diurnal cycle, so
    comparing Homewood's 10:15 reading against Glenbrook 5's 05:45 reading
    would show a time-of-day artifact as a geographic signal — which is
    exactly what the first version of this function did.

    Finds the most recent timestamp present at both sites (within
    :data:`TRANSECT_ALIGN_TOLERANCE_MINUTES`) and reads both sides there.
    Returns ``None`` if the two histories don't overlap at all.
    """
    west_site, east_site = TRANSECT_PAIR
    if df_long.empty:
        return None

    pair = df_long[
        df_long["site"].isin(TRANSECT_PAIR)
        & df_long["variable"].isin(TRANSECT_VARIABLES)
        & df_long["timestamp"].notna()
    ]
    if pair.empty:
        return None

    west_times = set(pair.loc[pair["site"] == west_site, "timestamp"])
    east_times = set(pair.loc[pair["site"] == east_site, "timestamp"])
    if not west_times or not east_times:
        return None

    exact = west_times & east_times
    if exact:
        w_time = e_time = max(exact)
        offset_minutes = 0.0
    else:
        # No shared instant on the grid. Take the latest west reading that
        # has an east reading within tolerance.
        tolerance = pd.Timedelta(minutes=TRANSECT_ALIGN_TOLERANCE_MINUTES)
        best: tuple[pd.Timestamp, pd.Timestamp] | None = None
        for wt in sorted(west_times, reverse=True):
            candidates = [et for et in east_times if abs(et - wt) <= tolerance]
            if candidates:
                best = (wt, min(candidates, key=lambda et: abs(et - wt)))
                break
        if best is None:
            log.warning("transect: %s and %s histories do not overlap within %d min",
                        west_site, east_site, TRANSECT_ALIGN_TOLERANCE_MINUTES)
            return None
        w_time, e_time = best
        offset_minutes = abs((e_time - w_time).total_seconds()) / 60.0

    def side(site: str, at: pd.Timestamp) -> dict:
        rows = pair[(pair["site"] == site) & (pair["timestamp"] == at)]
        readings: dict[str, dict] = {}
        for _, r in rows.iterrows():
            formatted = _format_reading(r["sensor_type"], r["variable"], r["value"])
            if formatted is not None:
                readings[r["variable"]] = formatted
        meta = SITE_METADATA.get(site, {})
        return {
            "site": site,
            "shore": meta.get("shore"),
            "coordinates": {"lat": meta.get("lat"), "lng": meta.get("lng")},
            "observed_at": _iso_local(at),
            "readings": readings,
        }

    west = side(west_site, w_time)
    east = side(east_site, e_time)
    if not west["readings"] or not east["readings"]:
        return None

    # How much shared history exists, for context on the dashboard.
    overlap_start = max(min(west_times), min(east_times))
    overlap_end = min(max(west_times), max(east_times))
    overlap_hours = max(0.0, (overlap_end - overlap_start).total_seconds() / 3600.0)

    return {
        "aligned_at": _iso_local(w_time),
        "offset_minutes": round(offset_minutes, 1),
        "overlap_hours": round(overlap_hours, 1),
        "latitude_offset_m": _latitude_offset_m(west_site, east_site),
        "west": west,
        "east": east,
    }


def _latitude_offset_m(a: str, b: str) -> int | None:
    """North–south separation of two sites, in metres.

    The transect's whole claim is that these two stations sit on the same
    line of latitude, so it's worth stating the number rather than asserting
    it. One degree of latitude is ~111.32 km everywhere.
    """
    lat_a = SITE_METADATA.get(a, {}).get("lat")
    lat_b = SITE_METADATA.get(b, {}).get("lat")
    if lat_a is None or lat_b is None:
        return None
    return round(abs(lat_a - lat_b) * 111_320)


def build_asset_summary(df_assets: pd.DataFrame) -> list[dict]:
    """Per-site field-camera summary.

    Image refs are ``s3://`` URIs into TEON's LoggerNet bucket, which
    aren't publicly resolvable over HTTP. We record the count and latest
    capture so the page can report that imagery exists, and leave the
    display of actual photos until an HTTP-accessible URL form is known.
    """
    if df_assets.empty:
        return []
    out: list[dict] = []
    for (site, kind), grp in df_assets.groupby(["site", "kind"]):
        latest = grp.loc[grp["timestamp"].idxmax()]
        upstream = None
        if "total_available" in grp.columns:
            vals = grp["total_available"].dropna()
            if not vals.empty:
                upstream = int(vals.max())
        out.append({
            "site": site,
            "kind": kind,
            # Frames we hold locally, vs. frames TEON says exist. These
            # differ because each ingest pulls a fixed page depth.
            "held": int(len(grp)),
            "upstream_total": upstream,
            "latest_at": _iso_local(latest["timestamp"]),
            "latest_ref": str(latest["ref"]),
            "resolvable": False,
        })
    out.sort(key=lambda r: r["site"])
    return out


def build_inventory_summary(inventory: dict | None) -> list[dict]:
    """Flatten the TEON inventory into a display-ready list."""
    if not inventory:
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - LIVE_WINDOW_HOURS * 3600

    rows: list[dict] = []
    for category, sensor_types in (inventory.get("locations") or {}).items():
        for sensor_type, sensors in (sensor_types or {}).items():
            for s in sensors:
                last_iso = s.get("last_update")
                last_dt = _parse_iso(last_iso)
                rows.append({
                    "category": category,
                    "sensor_type": sensor_type,
                    "sensor_type_display": _display_type(sensor_type),
                    "site": s.get("site"),
                    "lat": s.get("lat"),
                    "lng": s.get("lng"),
                    "first_update": _iso_local(pd.Timestamp(s["first_update"]))
                                    if s.get("first_update") else None,
                    "last_update": _iso_local(pd.Timestamp(last_iso)) if last_iso else None,
                    "data_count": s.get("data_count"),
                    "is_live": bool(last_dt and last_dt.timestamp() >= cutoff),
                    "is_manual": "Manual" in (s.get("id") or ""),
                })

    rows.sort(key=lambda r: (
        _CATEGORY_ORDER.get(r["category"], 99),
        r["sensor_type"],
        not r["is_live"],
        r["site"] or "",
    ))
    return rows


def _display_type(sensor_type: str) -> str:
    """TEON's inventory keys are already display-ready; prettify a few."""
    return {"Minidot": "MiniDot", "Hobo": "HOBO"}.get(sensor_type, sensor_type)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a TEON timestamp, stamping the configured zone if naive."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed
    try:
        from zoneinfo import ZoneInfo
        return parsed.replace(tzinfo=ZoneInfo(TEON_TIMEZONE))
    except Exception:
        return parsed.replace(tzinfo=timezone.utc)


def build_dashboard_snapshot(df_wide: pd.DataFrame,
                             df_long: pd.DataFrame,
                             df_assets: pd.DataFrame,
                             df_usgs: pd.DataFrame,
                             inventory: dict | None,
                             disabled: list[str]) -> dict:
    """Compose the payload the dashboard reads."""
    return {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "sources": ["TEON"] + (["USGS"] if not df_usgs.empty else []),
        "sites": build_lake_cards(df_wide),
        "stations": build_station_cards(df_wide),
        "transect": build_transect(df_long),
        "cameras": build_asset_summary(df_assets),
        "gauges": build_usgs_cards(df_usgs),
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

    df_obs, df_assets = to_frames(load_teon_snapshots())
    log.info("TEON: %d numeric observations (%d unique records), %d asset refs",
             len(df_obs),
             df_obs["uuid"].nunique() if not df_obs.empty else 0,
             len(df_assets))

    df_usgs = usgs_to_long(load_usgs_snapshots())
    if not df_usgs.empty:
        log.info("USGS: %d observations across %d gauge(s)",
                 len(df_usgs), df_usgs["site_number"].nunique())

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    obs_path = PROCESSED_DIR / "observations.parquet"
    df_obs.to_parquet(obs_path, index=False)
    log.info("wrote %s", obs_path)

    assets_path = PROCESSED_DIR / "assets.parquet"
    df_assets.to_parquet(assets_path, index=False)
    log.info("wrote %s", assets_path)

    usgs_path = PROCESSED_DIR / "usgs_observations.parquet"
    df_usgs.to_parquet(usgs_path, index=False)
    log.info("wrote %s", usgs_path)

    df_wide = to_latest_wide(df_obs)
    wide_path = PROCESSED_DIR / "latest_wide.parquet"
    df_wide.to_parquet(wide_path, index=False)
    log.info("wrote %s", wide_path)

    inventory = load_latest_json(RAW_DIR, "inventory")
    visibility = load_latest_json(RAW_DIR, "visibility") or {}
    disabled = visibility.get("disabled", [])

    snapshot = build_dashboard_snapshot(df_wide, df_obs, df_assets, df_usgs,
                                        inventory, disabled)
    assets_dir = WEB_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    latest_path = assets_dir / "latest.json"
    latest_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("wrote %s (%d lake, %d stations, %d cameras, %d gauges, "
             "%d inventory, %d disabled)",
             latest_path,
             len(snapshot["sites"]), len(snapshot["stations"]),
             len(snapshot["cameras"]), len(snapshot["gauges"]),
             len(snapshot["inventory"]), len(snapshot["disabled"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
