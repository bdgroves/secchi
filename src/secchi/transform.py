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
    DEFAULT_UNIT_SYSTEM,
    SPARKLINE_MIN_POINTS,
    SPARKLINE_POINTS,
    SPARKLINE_WINDOW_HOURS,
    TREND_SIGNIFICANCE,
    UNIT_CONVERSIONS,
    MANUAL_COLLECTION_TYPES,
    TEON_TIMESTAMP_FIELDS,
    SENSOR_TYPE_CANONICAL,
    OFFLINE_STATION_MIN_TYPES,
    USGS_DATUMS,
    WATERSHED_DISPLAY_VARIABLES,
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
# Oxygen is shown BOTH ways on purpose. Concentration (mg/L) and percent
# saturation answer different questions, and at Tahoe's altitude the
# relationship between them is not the textbook one — see
# docs/dissolved-oxygen.md. Showing only the percentage, as this did
# originally, hides the discrepancy entirely.
EXO_CARD_VARIABLES = ("Temp", "Do_mgL", "Do_percent", "Chl_a", "Turbidity")
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


def build_usgs_series(df_usgs: pd.DataFrame, site_number: str) -> dict:
    """Sparkline series per (parameter, statistic) for one USGS gauge."""
    if df_usgs.empty:
        return {}
    sub = df_usgs[(df_usgs["site_number"] == site_number) & df_usgs["timestamp"].notna()]
    if sub.empty:
        return {}
    newest = sub["timestamp"].max()
    sub = sub[sub["timestamp"] >= newest - pd.Timedelta(hours=SPARKLINE_WINDOW_HOURS)]

    out: dict[str, dict] = {}
    group_cols = ["variable", "statistic_id"] if "statistic_id" in sub.columns else ["variable"]
    for key, rows in sub.groupby(group_cols):
        code, stat = (key if isinstance(key, tuple) else (key, ""))
        rows = rows.sort_values("timestamp")
        vals = [float(v) for v in rows["value"].tolist()]
        entry: dict = {"points": _downsample(vals)}
        ages = [(newest - t).total_seconds() / 3600 for t in rows["timestamp"]]
        trend = _trend(vals, ages)
        if trend:
            entry["trend"] = trend
        span_h = (rows["timestamp"].max() - rows["timestamp"].min()).total_seconds() / 3600
        entry["span_hours"] = round(span_h, 1)
        out[f"{code}:{stat}" if stat else code] = entry
    return out


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
            units = pmeta.get("units") or latest.get("unit_of_measure") or ""
            value = round(float(latest["value"]), 3)
            entry = {
                "value": value,
                "label": label,
                "units": units,
                "system": _unit_system(units),
                "parameter_code": code,
                "statistic_id": stat,
                "observed_at": pd.Timestamp(latest["timestamp"]).isoformat(),
            }
            alt = _alt_unit(value, units)
            if alt:
                entry["alt"] = alt

            # A gage height on a local datum is not a usable number on its
            # own. Lake Tahoe's recorder sits 6,220.00 ft above the USBR
            # reference, so 7.07 ft means a lake surface at 6,227.07 ft —
            # which is what every agency quotes and what can be compared to
            # the natural rim and the legal maximum.
            datum = USGS_DATUMS.get(site_number)
            if datum and datum.get("parameter_code") == code:
                absolute = value + datum["offset_ft"]
                derived = {
                    "value": round(absolute, 2),
                    "label": datum.get("label", "Elevation"),
                    "units": units,
                    "system": _unit_system(units),
                    "datum": datum.get("datum"),
                    "derived_from": code,
                }
                alt_abs = _alt_unit(absolute, units)
                if alt_abs:
                    derived["alt"] = alt_abs
                refs = datum.get("reference_levels") or {}
                if refs:
                    derived["vs"] = {
                        name: round(absolute - level, 2)
                        for name, level in refs.items()
                    }
                    derived["reference_levels"] = refs
                readings[f"{code}:{stat}:datum"] = derived
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
            # lake / outlet / tributary — drives how the dashboard groups
            # these, since ten flat cards would swamp the page.
            "role": meta.get("role", "tributary"),
            "note": meta.get("note"),
            "has_turbidity": "63680" in (meta.get("parameters") or ()),
            "coordinates": {"lat": _first_float(grp["lat"]), "lng": _first_float(grp["lng"])},
            "observed_at": pd.Timestamp(newest).isoformat(),
            "readings": readings,
        }
        series = build_usgs_series(df_usgs, site_number)
        if series:
            gauges[site_number]["series"] = series
    return gauges


def _first_float(series: pd.Series) -> float | None:
    vals = series.dropna()
    return float(vals.iloc[0]) if not vals.empty else None


def _load_existing(path: Path, required: Iterable[str]) -> pd.DataFrame:
    """Read a previously written parquet, or return an empty frame.

    This is what makes the parquet the durable record rather than a
    disposable derivative: each run appends to what is already there. Raw
    snapshots can then be pruned on a retention window without losing
    history, which is the difference between a repo that grows without
    bound and one that reaches a steady state.

    A schema change is handled by discarding the old file rather than
    failing — the alternative is a crash loop that needs manual cleanup,
    and the raw buffer can rebuild recent history.
    """
    if not path.exists():
        return pd.DataFrame()
    try:
        existing = pd.read_parquet(path)
    except Exception as exc:
        log.warning("could not read %s (%s); rebuilding from raw", path.name, exc)
        return pd.DataFrame()
    missing = set(required) - set(existing.columns)
    if missing:
        log.warning("%s is missing %s; rebuilding from raw",
                    path.name, ", ".join(sorted(missing)))
        return pd.DataFrame()
    return existing


def _accumulate(new: pd.DataFrame, path: Path, dedupe_on: list[str]) -> pd.DataFrame:
    """Merge newly-parsed rows into the stored record and dedupe.

    New rows are put first so that a re-fetch of the same observation keeps
    the freshest copy — relevant if TEON ever corrects a value in place.
    """
    if new.empty:
        return _load_existing(path, dedupe_on)
    existing = _load_existing(path, dedupe_on)
    if existing.empty:
        combined = new
    else:
        combined = pd.concat([new, existing], ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=dedupe_on).reset_index(drop=True)
    if "timestamp" in combined.columns:
        combined = combined.sort_values("timestamp").reset_index(drop=True)
    log.info("%s: %d new + %d stored -> %d unique (%d duplicates collapsed)",
             path.name, len(new), len(existing), len(combined), before - len(combined))
    return combined


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

# Sensor types whose timestamp key has already been resolved, so the
# lookup is logged once rather than per record.
_TS_FIELD_SEEN: dict[str, str] = {}


def _record_timestamp(rec: dict,
                      sensor_type: str = "",
                      seen: dict[str, str] | None = None) -> str | None:
    """The observation time, whatever this sensor type calls it.

    Tries each name in :data:`TEON_TIMESTAMP_FIELDS`. Hardcoding
    ``TIMESTAMP`` cost 1,490,116 MiniDot and HOBO rows their timestamps
    on 2026-09-22 — filed under year=0000, taking 24 MB and answering
    nothing, because no sparkline, trend or coverage window can be built
    without a time.
    """
    for key in TEON_TIMESTAMP_FIELDS:
        value = rec.get(key)
        if value:
            if seen is not None and sensor_type:
                seen.setdefault(sensor_type, key)
            return value
    if seen is not None and sensor_type:
        seen.setdefault(sensor_type, "(none found)")
    return None


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
    resolved_ts_field: dict[str, str] = {}

    for snap in snapshots:
        # Canonicalise HERE, the one place every TEON row passes through.
        # The hourly ingest already passed canonical keys; the backfill
        # passed raw inventory keys ("EXO" vs "ExoSensor"), so the store
        # ended up holding the same instrument under two names and every
        # downstream filter silently missed half its data.
        raw_type = snap.get("sensor_type", "")
        sensor_type = SENSOR_TYPE_CANONICAL.get(raw_type, raw_type)
        # TEON reports how many records exist upstream; keep it so asset
        # counts reflect the real archive rather than our ingest page size.
        total_available = snap.get("total_available")
        for rec in snap.get("records", []):
            base = {
                "uuid": rec.get("uuid"),
                "source": "TEON",
                # Fall back to the site the snapshot was requested for. Real
                # TEON records carry their own `site`, but a record without
                # one would otherwise become invisible to every per-site
                # query, card, banner and completeness check — which is how
                # a test with site-less records found this.
                "site": rec.get("site") or snap.get("site"),
                "sensor_type": sensor_type,
                "timestamp": _record_timestamp(rec, sensor_type,
                                               resolved_ts_field),
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

    for stype, key in sorted(resolved_ts_field.items()):
        if key != "TIMESTAMP":
            log.warning("%s uses %r as its timestamp field, not 'TIMESTAMP' "
                        "— add it to TEON_TIMESTAMP_FIELDS if missing",
                        stype, key)

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


def _alt_unit(value: float, units: str) -> dict | None:
    """The same value expressed in the other measurement system.

    Returns ``None`` for dimensionless or system-neutral units. Emitting
    both representations here means the dashboard toggles between them
    without duplicating conversion logic in JavaScript, and the stored
    source value is never overwritten.
    """
    conv = UNIT_CONVERSIONS.get(units)
    if not conv or not conv.get("to"):
        return None
    converted = value * conv["factor"] + conv.get("offset", 0.0)
    return {
        "value": round(converted, 3),
        "units": conv["to"],
        "system": "imperial" if conv["system"] == "metric" else "metric",
    }


def _unit_system(units: str) -> str:
    conv = UNIT_CONVERSIONS.get(units)
    return conv.get("system", "both") if conv else "both"


def _trend(values: list[float], hours: list[float]) -> dict | None:
    """Day-over-day change across a window of samples.

    Compares the mean of the most recent 24 hours against the mean of the
    24 hours before it, rather than fitting a slope across the whole
    window. This matters because almost everything here has a strong
    diurnal cycle: air temperature swings 14 °C in a day, so a
    least-squares slope over 48 hours mostly reports where in the cycle
    the window happens to begin and end. Comparing whole days cancels the
    cycle and leaves actual change.

    ``hours`` is each sample's age in hours, newest = 0.

    Falls back to a linear slope when there isn't a full 36 hours yet, and
    flags that case as provisional so the dashboard can hedge.
    """
    n = len(values)
    if n < SPARKLINE_MIN_POINTS:
        return None

    lo, hi = min(values), max(values)
    span = hi - lo
    mean_all = sum(values) / n

    recent = [v for v, h in zip(values, hours) if h <= 24]
    prior = [v for v, h in zip(values, hours) if 24 < h <= 48]

    provisional = False
    if len(recent) >= 4 and len(prior) >= 4:
        change = sum(recent) / len(recent) - sum(prior) / len(prior)
        basis = "day-over-day"
    else:
        # Not enough history for a clean day comparison. Use a slope, but
        # say so — for a diurnal variable this number is unreliable.
        mean_x = (n - 1) / 2
        denom = sum((i - mean_x) ** 2 for i in range(n))
        slope = (sum((i - mean_x) * (v - mean_all) for i, v in enumerate(values)) / denom
                 if denom else 0.0)
        change = slope * (n - 1)
        basis = "slope"
        provisional = True

    if span <= 0 or abs(change) < span * TREND_SIGNIFICANCE:
        direction = "steady"
    else:
        direction = "rising" if change > 0 else "falling"

    out = {
        "direction": direction,
        "change": round(change, 3),
        "basis": basis,
        "min": round(lo, 3),
        "max": round(hi, 3),
        "mean": round(mean_all, 3),
        "samples": n,
        "window_hours": SPARKLINE_WINDOW_HOURS,
    }
    if provisional:
        out["provisional"] = True
    return out


def _downsample(values: list[float], target: int = SPARKLINE_POINTS) -> list[float]:
    """Thin a series to at most ``target`` points, preserving both ends."""
    n = len(values)
    if n <= target:
        return [round(v, 3) for v in values]
    step = (n - 1) / (target - 1)
    return [round(values[min(int(round(i * step)), n - 1)], 3) for i in range(target)]


def build_series(df_long: pd.DataFrame,
                 site: str,
                 sensor_type: str | None,
                 fields: Iterable[str]) -> dict:
    """Recent history per variable for one site, for inline sparklines.

    Answers the question a single number can't: is this reading drifting or
    holding? Turbidity sitting at a steady 0.3 FNU and turbidity climbing
    through 0.3 are the same snapshot and completely different stories.
    """
    if df_long.empty:
        return {}

    sel = df_long["site"] == site
    if sensor_type:
        sel &= df_long["sensor_type"] == sensor_type
    sub = df_long[sel & df_long["timestamp"].notna()]
    if sub.empty:
        return {}

    newest = sub["timestamp"].max()
    cutoff = newest - pd.Timedelta(hours=SPARKLINE_WINDOW_HOURS)
    sub = sub[sub["timestamp"] >= cutoff]

    out: dict[str, dict] = {}
    for field in fields:
        rows = sub[sub["variable"] == field].sort_values("timestamp")
        if rows.empty:
            continue
        raw = [float(v) for v in rows["value"].tolist()]

        meta = SENSOR_VARIABLES.get(sensor_type or "", {}).get(field, {})
        scale = meta.get("scale")
        clip = meta.get("clip_low")
        vals, clipped_count = [], 0
        for v in raw:
            if scale:
                v *= scale
            if clip is not None and v < clip:
                v = clip
                clipped_count += 1
            vals.append(v)

        entry: dict = {"points": _downsample(vals)}

        # A series where every sample sat below the floor is not a stable
        # measurement — it's a broken channel that clipping has flattened
        # to a confident-looking line. Say so rather than draw it.
        if clip is not None and clipped_count == len(vals) and len(vals) > 0:
            entry["all_clipped"] = True
            entry["raw_mean"] = round(sum(raw) / len(raw), 3)
        elif clipped_count:
            entry["clipped_count"] = clipped_count

        ages = [(newest - t).total_seconds() / 3600 for t in rows["timestamp"]]
        trend = _trend(vals, ages)
        if trend:
            entry["trend"] = trend
        # Timespan actually covered, which may be shorter than the window
        # if the sensor only recently came online.
        span_h = (rows["timestamp"].max() - rows["timestamp"].min()).total_seconds() / 3600
        entry["span_hours"] = round(span_h, 1)
        out[field] = entry
    return out


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
        "system": _unit_system(meta["units"]),
    }
    alt = _alt_unit(value, meta["units"])
    if alt:
        out["alt"] = alt
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


def _local_do_saturation(readings: dict) -> dict | None:
    """Oxygen saturation referenced to the lake's actual air pressure.

    TEON publishes ``Do_percent`` against a SEA-LEVEL atmosphere. Confirmed
    from the data on 2026-09-18 by `pixi run oxygen-check`: across 1,068
    readings carrying temperature, concentration and percentage together,
    the sea-level hypothesis fit with a mean error of 0.004 mg/L against
    1.627 for the altitude-corrected one. 0.004 is the precision of the
    Weiss formula itself, so this is not close.

    That matters because Lake Tahoe's surface is at ~1,898 m, where air
    pressure is about 79.5 % of sea level. A reading of 81 % against a
    sea-level atmosphere is roughly 102 % of what the water can actually
    hold there — the difference between "oxygen-stressed" and "slightly
    supersaturated, net photosynthetic". Opposite conclusions.

    This derives the local-pressure figure from concentration and
    temperature rather than rescaling their percentage, so it depends on
    their measurements and not on their saturation assumption. Their
    published value is left untouched; this is added alongside it.
    """
    from secchi.analysis.oxygen import do_saturation_local

    # Field names differ by vendor: EXO uses Do_mgL/Temp, MiniDOT uses
    # "Dissolved Oxygen"/"Temperature". Both compute saturation from a
    # configured barometric pressure, so both are candidates for the
    # sea-level referencing problem and both deserve the local figure.
    mgl = ((readings.get("Do_mgL") or readings.get("Dissolved Oxygen") or {})
           .get("value"))
    temp = ((readings.get("Temp") or readings.get("Temperature") or {})
            .get("value"))
    if mgl is None or temp is None:
        return None
    if not (0 < float(temp) < 40) or float(mgl) <= 0:
        return None

    sat = do_saturation_local(float(temp))
    if sat <= 0:
        return None
    pct = float(mgl) / sat * 100

    return {
        "value": round(pct, 1),
        "label": "DO sat (local)",
        "units": "%",
        "system": "both",
        "derived_from": "Do_mgL + Temp",
        "note": "Referenced to air pressure at the lake surface (~1,898 m, "
                "79.5 % of sea level). TEON's own Do_percent is referenced "
                "to a sea-level atmosphere — see docs/dissolved-oxygen.md.",
    }


def build_lake_cards(df_wide: pd.DataFrame, df_long: pd.DataFrame | None = None) -> dict:
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
        local_do = _local_do_saturation(sites[site]["readings"])
        if local_do:
            sites[site]["readings"]["Do_percent_local"] = local_do
        if df_long is not None:
            series = build_series(df_long, site, "ExoSensor", EXO_CARD_VARIABLES)
            if series:
                sites[site]["series"] = series
    return sites


def build_station_cards(df_wide: pd.DataFrame, df_long: pd.DataFrame | None = None) -> dict:
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
        if df_long is not None:
            # Station readings come off several sensor types on one logger,
            # so gather series per type and merge.
            series: dict = {}
            for stype in ("AirTemperatureRelativeHumidity",
                          "SoilEnvironmentalConditions", "StreamLevel"):
                series.update(build_series(df_long, site, stype, STATION_CARD_VARIABLES
                                           + ("Uncalibrated_water_depth",)))
            if series:
                card["series"] = series
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
                    # Two ways a sensor is hand-collected: TEON says so
                    # in the id, or the instrument class has no telemetry.
                    # See MANUAL_COLLECTION_TYPES for the reasoning.
                    "is_manual": ("Manual" in (s.get("id") or "")
                                  or sensor_type in MANUAL_COLLECTION_TYPES),
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


def _slugify_site(site: str) -> str:
    """Match TEON's site-slug convention in /site-visibility/disabled:
    lowercase with spaces removed ("4H Camp" -> "4hcamp")."""
    return (site or "").lower().replace(" ", "")


def count_held_records(df_long: pd.DataFrame) -> dict[str, int]:
    """Unique records actually in our store, keyed by site.

    The map's colour comes from TEON's inventory, which reports what
    EXISTS upstream. The data comes from ingest, which only targets live
    sensors. Those two can disagree badly: when a manual sonde is
    retrieved and uploaded, the inventory jumps by thousands of records
    while we have pulled none of them.

    Without this the pin would turn green on the strength of data we
    don't have — worse than leaving it grey, because it looks answered.
    """
    if df_long.empty or "uuid" not in df_long.columns:
        return {}
    return (df_long.groupby("site")["uuid"].nunique().to_dict())


def build_map_points(inventory: list[dict],
                     lake: dict,
                     stations: dict,
                     gauges: dict,
                     disabled: list[str],
                     held: dict[str, int] | None = None) -> dict:
    """Everything with a coordinate, shaped for the map layer.

    One entry per physical location rather than per sensor, because the
    inventory lists three-to-four endpoints for a single terrestrial logger
    (see the shared-logger note at the top of this module) and plotting
    those as separate markers would triple-stack pins on one tripod.

    Each point carries the sensor types present there, the freshest
    last_update across them, total record count, and — where we have it —
    the latest readings already formatted for display.
    """
    disabled_set = set(disabled)
    points: dict[tuple, dict] = {}

    for row in inventory:
        lat, lng = row.get("lat"), row.get("lng")
        if lat is None or lng is None:
            continue
        site = row.get("site") or "?"
        # Key on rounded coordinates: one marker per physical location.
        key = (round(float(lat), 5), round(float(lng), 5))
        pt = points.setdefault(key, {
            "site": site,
            "lat": float(lat),
            "lng": float(lng),
            "category": row.get("category"),
            "source": "TEON",
            "sensor_types": [],
            "records": 0,
            "last_update": None,
            "is_live": False,
            "is_manual": False,
            "disabled": _slugify_site(site) in disabled_set,
            # Records upstream vs records we hold. A gap means there is
            # data published that we haven't ingested — which is exactly
            # the state a manual sonde enters the moment it's uploaded.
            "held_records": (held or {}).get(site, 0),
        })
        stype = row.get("sensor_type_display") or row.get("sensor_type")
        if stype and stype not in pt["sensor_types"]:
            pt["sensor_types"].append(stype)
        # MAX, not sum. At a terrestrial station the soil, air, tree and
        # stream endpoints are projections of one logger table and report
        # overlapping counts — summing them triples the total (Glenbrook 5
        # would read 188,868 instead of 62,956). Max is the honest figure
        # for "observations recorded at this location".
        pt["records"] = max(pt["records"], row.get("data_count") or 0)
        pt["is_live"] = pt["is_live"] or bool(row.get("is_live"))
        pt["is_manual"] = pt["is_manual"] or bool(row.get("is_manual"))
        last = row.get("last_update")
        if last and (pt["last_update"] is None or last > pt["last_update"]):
            pt["last_update"] = last
        # A location carrying both lake and terrestrial gear is labelled by
        # whichever category sorts first, for consistent marker styling.
        if row.get("category") and (
            _CATEGORY_ORDER.get(row["category"], 99)
            < _CATEGORY_ORDER.get(pt["category"], 99)
        ):
            pt["category"] = row["category"]

    # Attach formatted readings from the card builders where the site matches.
    for name, card in {**lake, **stations}.items():
        for pt in points.values():
            if pt["site"] == name:
                pt["readings"] = card.get("readings", {})
                if card.get("shore"):
                    pt["shore"] = card["shore"]
                if card.get("dendrometers"):
                    pt["dendrometers"] = card["dendrometers"]
                break

    out = list(points.values())

    # USGS gauges are a separate source and get their own marker styling.
    for num, g in (gauges or {}).items():
        coords = g.get("coordinates") or {}
        if coords.get("lat") is None or coords.get("lng") is None:
            continue
        out.append({
            "site": g.get("name") or f"USGS {num}",
            "lat": coords["lat"],
            "lng": coords["lng"],
            "category": "gauge",
            "source": "USGS",
            "site_number": num,
            "sensor_types": ["USGS gauge"],
            "records": 0,
            "last_update": g.get("observed_at"),
            "is_live": True,
            "is_manual": False,
            "disabled": False,
            "shore": g.get("shore"),
            "readings": g.get("readings", {}),
        })

    # Three ways a location can be not-live, and they mean different
    # things. Conflating them is what made the map call a working
    # hand-collected sonde "dormant".
    for pt in out:
        if pt.get("is_manual"):
            pt["collection"] = "by hand"
            gap = (pt.get("records") or 0) - (pt.get("held_records") or 0)
            pt["unpulled_records"] = max(0, gap)
        elif (not pt.get("is_live")
              and not pt.get("disabled")
              and len(pt.get("sensor_types") or []) >= OFFLINE_STATION_MIN_TYPES):
            # Every sensor type at this location is dark. One instrument
            # quiet is a channel; the whole station quiet is the station.
            pt["collection"] = "offline"
        else:
            pt["collection"] = "telemetered"

    out.sort(key=lambda r: (_CATEGORY_ORDER.get(r["category"], 99), r["site"]))
    return {"points": out}


def _sensor_type_key(inventory_row: dict) -> str:
    """The key the transform stores rows under, from an inventory row.

    Uses the RAW `sensor_type`, not `sensor_type_display`.
    `_display_type` prettifies for humans — "Minidot" becomes "MiniDot",
    "Hobo" becomes "HOBO" — while SENSOR_TYPE_CANONICAL is keyed on the
    raw names. Looking up the prettified form missed every time and fell
    through to a value matching nothing in the store.
    """
    raw = inventory_row.get("sensor_type") or ""
    if raw in SENSOR_TYPE_CANONICAL:
        return SENSOR_TYPE_CANONICAL[raw]
    display = inventory_row.get("sensor_type_display") or raw
    return SENSOR_TYPE_CANONICAL.get(display, display.replace(" ", ""))


# Short, human names for the instrument families, used when two devices
# at one site report the same quantity.
INSTRUMENT_SHORT_NAMES = {
    "MiniDotSensor": "MiniDOT",
    "HoboSensor": "HOBO",
    "ExoSensor": "EXO",
}


def _card_variables(sensor_type_key: str) -> tuple[str, ...]:
    """Which variables a card shows for a given sensor type.

    Derived from SENSOR_VARIABLES rather than hardcoded, because the
    first version passed "ExoSensor" for every hand-collected site. The
    six nearshore sites are MiniDOTs and HOBOs whose fields
    ("Dissolved Oxygen", "conductivity") don't exist in the EXO config,
    so every reading formatted to None and those cards showed coverage
    and nothing else.
    """
    if sensor_type_key == "ExoSensor":
        return EXO_CARD_VARIABLES
    declared = SENSOR_VARIABLES.get(sensor_type_key) or {}
    return tuple(name for name, meta in declared.items()
                 if not (meta or {}).get("hidden"))


def build_manual_sonde_cards(df_wide: pd.DataFrame,
                             df_long: pd.DataFrame,
                             inventory: list[dict]) -> dict:
    """Cards for the hand-collected sites, built around COVERAGE.

    These are deliberately not the shape of a live card. A live card
    answers "what is the lake doing right now"; these answer:

      where have we been  — the period of record actually held
      what is outstanding — records published but not pulled, and so
                            what the next collection would extend from

    A self-logging instrument's newest reading can be months old and
    still be perfectly good data. Presenting it in the live-card idiom,
    beside readings from an hour ago, would imply a currency it hasn't
    got — so the period of record is the headline.

    One site can carry SEVERAL hand-collected instruments. Each nearshore
    site has both a MiniDOT and a HOBO, and the first version keyed the
    inventory by site alone, so `upstream` reflected one instrument while
    `held` counted rows from both — producing "held 88,214 of 47,226" and
    a coverage bar past 100 %.
    """
    # Group by SITE, keeping every hand-collected sensor at it.
    by_site: dict[str, list[dict]] = {}
    for row in inventory:
        if row.get("is_manual"):
            by_site.setdefault(row["site"], []).append(row)
    if not by_site:
        return {}

    out: dict[str, dict] = {}
    for site, rows in by_site.items():
        held_rows = df_long[(df_long["site"] == site)
                            & df_long["timestamp"].notna()] \
            if not df_long.empty else pd.DataFrame()

        instruments = sorted({(r.get("sensor_type_display")
                               or r.get("sensor_type") or "?") for r in rows})
        card: dict = {
            "instruments": instruments,
            "sensor_type": " + ".join(instruments),
            "shore": (SITE_METADATA.get(site) or {}).get("shore"),
            "collection": "by hand",
            # Sum across every hand-collected instrument at the site, so
            # this is comparable with the held count below.
            "upstream_records": sum(r.get("data_count") or 0 for r in rows),
            "held_records": int(held_rows["uuid"].nunique())
                            if not held_rows.empty else 0,
            "last_upstream": max((r.get("last_update") or "") for r in rows) or None,
        }
        card["unpulled_records"] = max(
            0, card["upstream_records"] - card["held_records"])

        if not held_rows.empty:
            first, last = held_rows["timestamp"].min(), held_rows["timestamp"].max()
            card["coverage"] = {
                "first": _iso_local(first),
                "last": _iso_local(last),
                "days": round((last - first).total_seconds() / 86400, 1),
            }

            # Readings from the newest record, per instrument, using each
            # one's OWN field config rather than assuming EXO.
            readings: dict = {}
            series: dict = {}
            for stype_key in sorted({_sensor_type_key(r) for r in rows}):
                subset = held_rows[held_rows["sensor_type"] == stype_key]
                if subset.empty:
                    continue
                variables = _card_variables(stype_key)
                newest = subset[subset["timestamp"] == subset["timestamp"].max()]
                for _, r in newest.iterrows():
                    # Only the card variables. Iterating every stored
                    # variable put Battery on the card despite its
                    # hidden flag — the flag filters _card_variables and
                    # nothing was applying it here.
                    if r["variable"] not in variables:
                        continue
                    formatted = _format_reading(stype_key, r["variable"], r["value"])
                    if formatted:
                        # A site with two instruments can produce two
                        # readings labelled "Water temp" — MiniDOT's
                        # "Temperature" and HOBO's "temperature". Same
                        # quantity, different device; say which.
                        formatted = {**formatted, "instrument": stype_key}
                        readings[r["variable"]] = formatted
                series.update(build_series(df_long, site, stype_key, variables) or {})

            if readings:
                # The order to render them in. The frontend used to filter
                # every card through LAKE_VARS — a hardcoded EXO list — so
                # a MiniDOT's "Dissolved Oxygen" and a HOBO's
                # "conductivity" were in the payload and never drawn. Only
                # Do_percent_local appeared, because it happens to be an
                # EXO variable name.
                #
                # Temperature first, then oxygen, then everything else:
                # the order a limnologist reads them in.
                def _rank(var: str) -> tuple[int, str]:
                    low = var.lower()
                    if "temp" in low:
                        return (0, var)
                    if "oxygen" in low or low.startswith("do_"):
                        return (1, var)
                    return (2, var)

                card["variable_order"] = sorted(readings, key=_rank)

                # Disambiguate labels that collide across instruments.
                from collections import Counter
                label_counts = Counter(r["label"] for r in readings.values())
                for var, r in readings.items():
                    if label_counts[r["label"]] > 1:
                        pretty = INSTRUMENT_SHORT_NAMES.get(
                            r.get("instrument", ""), r.get("instrument", ""))
                        r["label"] = f"{r['label']} ({pretty})"

                local_do = _local_do_saturation(readings)
                if local_do:
                    readings["Do_percent_local"] = local_do
                    # Sits immediately after the published saturation it
                    # corrects, not at the end where the comparison is lost.
                    order = card["variable_order"]
                    anchor = next((v for v in order
                                   if "saturation" in v.lower()
                                   or v == "Do_percent"), None)
                    if anchor and anchor in order:
                        order.insert(order.index(anchor) + 1, "Do_percent_local")
                    else:
                        order.append("Do_percent_local")
                card["readings"] = readings
            if series:
                card["series"] = series
        else:
            card["coverage"] = None

        out[site] = card

    return dict(sorted(out.items()))


# A gap smaller than this is noise, not a backlog. The hourly job fetches
# each sensor's newest 200 records, so anything it could close it already
# has; what's left after it runs is either a handful of unparseable or
# duplicate records (up to ~14 seen in practice) or a real backlog of
# thousands. 100 sits comfortably between.
BACKLOG_THRESHOLD = 100


def _stages_for(raw_sensor_type: str, is_manual: bool) -> list[str]:
    """Which backfill stage(s) cover this sensor, from backfill.STAGES.

    Derived rather than hardcoded. The first banner said
    "pixi run backfill --stage manual" for every hand-collected site —
    but six of the eight are MiniDOT and HOBO sites, which live in the
    "nearshore" stage, so that command would have fetched nothing for
    them. Asking STAGES keeps the banner honest if a stage changes.
    """
    from secchi.backfill import STAGES          # lazy: avoids import cycles
    out = []
    for name, stage in STAGES.items():
        if raw_sensor_type not in stage.sensor_types:
            continue
        if stage.manual_only and not is_manual:
            continue
        if getattr(stage, "telemetered_only", False) and is_manual:
            continue
        out.append(name)
    return out


def build_backlog_entries(inventory: list[dict],
                          df_long: pd.DataFrame,
                          disabled: list[str]) -> list[dict]:
    """Telemetered sites with published records the hourly job can't reach.

    The case this exists for: a station comes back after a long outage and
    its logger uploads the backlog it kept while the radio was down.
    Blackwood 2 went dark in June; if it returns, ~9,000 readings could
    appear at once. The hourly job only fetches the newest 200 per sensor,
    so it would pick up the last two days and leave the rest behind with
    nothing on the page saying so.

    The comparison is upstream count against held records, per DEVICE.
    That distinction matters. A forest station's soil, air and tree
    endpoints are one logger reporting identical counts, and the backfill
    stored the history under only ONE of them. Comparing air's upstream
    count against air's held rows would report a 40,000-record backlog at
    every forest station. So sensors are grouped by upstream count, the
    same way the backfill identified shared loggers, and "held" is the
    largest held count within each group — wherever the history landed.

    Excluded, because each would false-alarm permanently: hidden sites
    (never ingested, so their gap is their entire record), cameras (their
    records live in the assets table, not observations), and hand-collected
    sensors (which have their own banner entry).
    """
    if df_long is None or df_long.empty or not inventory:
        return []

    held = (df_long.groupby(["site", "sensor_type"])["uuid"].nunique()
            .to_dict())
    disabled_set = set(disabled or [])

    by_site: dict[str, list[tuple[dict, str]]] = {}
    for row in inventory:
        site = row.get("site")
        if not site or row.get("is_manual"):
            continue
        if _slugify_site(site) in disabled_set:
            continue
        raw = row.get("sensor_type") or ""
        canon = SENSOR_TYPE_CANONICAL.get(raw, raw)
        if canon == "FieldCamera":
            continue
        by_site.setdefault(site, []).append((row, canon))

    out: list[dict] = []
    for site, rows in by_site.items():
        groups: dict[int, list[tuple[dict, str]]] = {}
        for row, canon in rows:
            groups.setdefault(int(row.get("data_count") or 0), []).append((row, canon))

        gap_total = 0
        stages: set[str] = set()
        instruments: set[str] = set()
        for count, members in groups.items():
            if count <= 0:
                continue
            have = max(held.get((site, canon), 0) for _, canon in members)
            gap = count - have
            if gap > BACKLOG_THRESHOLD:
                gap_total += gap
                for row, _ in members:
                    stages.update(_stages_for(row.get("sensor_type") or "", False))
                    instruments.add(row.get("sensor_type_display")
                                    or row.get("sensor_type") or "?")
        if gap_total:
            out.append({
                "site": site,
                "kind": "backlog",
                "records": gap_total,
                "instruments": sorted(instruments),
                "live": any(r.get("is_live") for r, _ in rows),
                "commands": [f"pixi run backfill --stage {st}"
                             for st in sorted(stages)],
            })
    return out


def build_upload_alert(manual_cards: dict,
                       inventory: list[dict] | None = None,
                       df_long: pd.DataFrame | None = None,
                       disabled: list[str] | None = None) -> dict | None:
    """Banner payload: published records we haven't pulled, and how to.

    Two cases, one banner:

      manual   a hand-collected site has been visited and uploaded — a
               dive happened, and there is fresh history to fetch
      backlog  a telemetered station has published more than the hourly
               job can reach, typically after coming back from an outage

    Each entry carries its own backfill command(s), derived from the
    stage definitions. Returns None when everything is current, so the
    banner disappears rather than becoming furniture.
    """
    entries: list[dict] = []

    for site, card in (manual_cards or {}).items():
        gap = card.get("unpulled_records") or 0
        if gap <= 0:
            continue
        stages: set[str] = set()
        for row in (inventory or []):
            if row.get("site") == site and row.get("is_manual"):
                stages.update(_stages_for(row.get("sensor_type") or "", True))
        entries.append({
            "site": site,
            "kind": "manual",
            "records": gap,
            "instruments": card.get("instruments") or [],
            "held_through": (card.get("coverage") or {}).get("last"),
            "commands": [f"pixi run backfill --stage {st}"
                         for st in sorted(stages)],
        })

    entries.extend(build_backlog_entries(inventory or [], df_long, disabled or []))

    if not entries:
        return None
    entries.sort(key=lambda e: -e["records"])
    commands = sorted({c for e in entries for c in e["commands"]})
    return {
        "sites": entries,
        "total_records": sum(e["records"] for e in entries),
        "commands": commands,
    }


def build_manual_sondes(inventory: list[dict]) -> list[dict]:
    """One entry per hand-collected SITE, not per sensor.

    Keyed per inventory row, this produced "Camp Richardson — newest
    record 3 mo ago. Camp Richardson — newest record 3 mo ago." because
    each nearshore site carries both a MiniDOT and a HOBO. It also
    called all fourteen of them EXO sites, which only two are.
    """
    by_site: dict[str, dict] = {}
    for row in inventory:
        if not row.get("is_manual"):
            continue
        site = row.get("site")
        instrument = (row.get("sensor_type_display")
                      or row.get("sensor_type") or "?")
        entry = by_site.setdefault(site, {
            "site": site,
            "shore": row.get("shore"),
            "instruments": [],
            "last_update": None,
            "records": 0,
        })
        if instrument not in entry["instruments"]:
            entry["instruments"].append(instrument)
        entry["records"] += row.get("data_count") or 0
        last = row.get("last_update")
        if last and (entry["last_update"] is None or last > entry["last_update"]):
            entry["last_update"] = last

    out = sorted(by_site.values(), key=lambda r: r["site"] or "")
    for entry in out:
        entry["instruments"].sort()
    return out


def build_transect_line(transect: dict | None) -> dict | None:
    """The transect as a drawable line, so the map can show the pairing.

    The claim is that these two stations sit on the same line of latitude
    across the lake. Drawing it is the most direct way to let someone
    check that by eye.
    """
    if not transect:
        return None
    w, e = transect.get("west") or {}, transect.get("east") or {}
    wc, ec = w.get("coordinates") or {}, e.get("coordinates") or {}
    if None in (wc.get("lat"), wc.get("lng"), ec.get("lat"), ec.get("lng")):
        return None
    return {
        "west": {"site": w.get("site"), "lat": wc["lat"], "lng": wc["lng"]},
        "east": {"site": e.get("site"), "lat": ec["lat"], "lng": ec["lng"]},
        "latitude_offset_m": transect.get("latitude_offset_m"),
    }


def write_web_watersheds(web_dir: Path = WEB_DIR) -> dict | None:
    """Write a browser-sized copy of the catchment polygons.

    The cached file is 7.8 MB in Web Mercator with 164 properties per
    feature. This reprojects it to WGS84 (Leaflet expects lon/lat),
    simplifies the geometry, trims to the display variables, and writes
    ``web/assets/watersheds.geojson``.

    Returns a summary for the snapshot, or None if the reference data
    hasn't been cached yet — the dashboard then just omits the layer
    rather than failing.
    """
    from secchi.sources.reference import load_reference, reproject_geojson
    from secchi.sources.simplify import simplify_collection

    ref = load_reference()
    polygons = ref.get("polygons")
    if not polygons:
        log.info("no cached catchment polygons — skipping the map layer. "
                 "Run `pixi run reference` to enable it.")
        return None

    wgs = reproject_geojson(polygons)
    reduced = simplify_collection(wgs, list(WATERSHED_DISPLAY_VARIABLES))

    assets = web_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    out_path = assets / "watersheds.geojson"
    # Compact separators: this file is machine-read, and indentation would
    # add roughly a third to the payload for no benefit.
    out_path.write_text(json.dumps(reduced, separators=(",", ":")), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    log.info("wrote %s (%d features, %.0f KB)", out_path,
             len(reduced.get("features", [])), size_kb)

    # Per-variable ranges, so the dashboard can scale its colours without
    # reading every feature twice in JavaScript.
    ranges: dict[str, dict] = {}
    for var in WATERSHED_DISPLAY_VARIABLES:
        vals = []
        for feat in reduced.get("features", []):
            v = (feat.get("properties") or {}).get(var)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        if len(vals) >= 2:
            ranges[var] = {"min": round(min(vals), 4), "max": round(max(vals), 4)}

    return {
        "path": "assets/watersheds.geojson",
        "features": len(reduced.get("features", [])),
        "size_kb": round(size_kb, 1),
        "ranges": ranges,
    }


def build_dashboard_snapshot(df_wide: pd.DataFrame,
                             df_long: pd.DataFrame,
                             df_assets: pd.DataFrame,
                             df_usgs: pd.DataFrame,
                             inventory: dict | None,
                             disabled: list[str],
                             watersheds: dict | None = None) -> dict:
    """Compose the payload the dashboard reads."""
    lake = build_lake_cards(df_wide, df_long)
    stations = build_station_cards(df_wide, df_long)
    gauges = build_usgs_cards(df_usgs)
    transect = build_transect(df_long)
    inv = build_inventory_summary(inventory)
    # Built once. It was being built twice — once for the cards, once for
    # the banner — which over 7.5 million rows is real, wasted time.
    manual_cards = build_manual_sonde_cards(df_wide, df_long, inv)
    return {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "sources": ["TEON"] + (["USGS"] if not df_usgs.empty else []),
        "default_unit_system": DEFAULT_UNIT_SYSTEM,
        "sparkline_window_hours": SPARKLINE_WINDOW_HOURS,
        "sites": lake,
        "stations": stations,
        "transect": transect,
        "cameras": build_asset_summary(df_assets),
        "gauges": gauges,
        "disabled": sorted(disabled),
        "inventory": inv,
        "map": build_map_points(inv, lake, stations, gauges, sorted(disabled),
                                count_held_records(df_long)),
        "transect_line": build_transect_line(transect),
        "watersheds": watersheds,
        "manual_sondes": build_manual_sondes(inv),
        "manual_cards": manual_cards,
        "upload_alert": build_upload_alert(manual_cards, inv, df_long,
                                           sorted(disabled)),
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
    log.info("TEON: %d numeric observations parsed from raw "
             "(%d unique records), %d asset refs",
             len(df_obs),
             df_obs["uuid"].nunique() if not df_obs.empty else 0,
             len(df_assets))

    df_usgs = usgs_to_long(load_usgs_snapshots())
    if not df_usgs.empty:
        log.info("USGS: %d observations parsed from raw across %d gauge(s)",
                 len(df_usgs), df_usgs["site_number"].nunique())

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Accumulate into a PARTITIONED store rather than one file per source.
    # A monolithic parquet is rewritten whole on every update and git
    # stores each version as a new blob, so at backfill scale (~150 MB)
    # the hourly cron would add 3.5 GB/day of history. Partitioned by
    # month, only the current month churns. See docs/storage.md.
    from secchi.store import migrate_monolith, read_partitions, write_partitions

    obs_root = PROCESSED_DIR / "observations"
    assets_root = PROCESSED_DIR / "assets"
    usgs_root = PROCESSED_DIR / "usgs_observations"

    # One-time: split any existing single-file parquet into partitions.
    # Keeps a .premigration backup rather than destroying its input.
    for monolith, root, source, keys in (
        (PROCESSED_DIR / "observations.parquet", obs_root, "teon",
         ["uuid", "site", "variable"]),
        (PROCESSED_DIR / "assets.parquet", assets_root, "teon",
         ["uuid", "site", "kind"]),
        (PROCESSED_DIR / "usgs_observations.parquet", usgs_root, "usgs",
         ["uuid", "variable", "statistic_id"]),
    ):
        outcome = migrate_monolith(monolith, root, source, keys)
        if outcome.get("migrated"):
            log.info("migrated %s -> %s (%d rows)", monolith.name,
                     root.name, outcome.get("source_rows", 0))

    write_partitions(df_obs, obs_root, "teon", ["uuid", "site", "variable"])
    write_partitions(df_assets, assets_root, "teon", ["uuid", "site", "kind"])
    write_partitions(df_usgs, usgs_root, "usgs",
                     ["uuid", "variable", "statistic_id"])

    # Downstream builders need the FULL record, not just this run's rows —
    # sparklines and trends read back over 48 hours, and a backfill may
    # have added years.
    df_obs = read_partitions(obs_root)
    df_assets = read_partitions(assets_root)
    df_usgs = read_partitions(usgs_root)
    log.info("record now holds %d TEON, %d asset, %d USGS rows",
             len(df_obs), len(df_assets), len(df_usgs))

    df_wide = to_latest_wide(df_obs)
    wide_path = PROCESSED_DIR / "latest_wide.parquet"
    df_wide.to_parquet(wide_path, index=False)
    log.info("wrote %s", wide_path)

    inventory = load_latest_json(RAW_DIR, "inventory")
    visibility = load_latest_json(RAW_DIR, "visibility") or {}
    disabled = visibility.get("disabled", [])

    watersheds = write_web_watersheds()

    snapshot = build_dashboard_snapshot(df_wide, df_obs, df_assets, df_usgs,
                                        inventory, disabled, watersheds)
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
    log.info("map: %d plotted locations from %d inventory rows",
             len(snapshot["map"]["points"]), len(snapshot["inventory"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
