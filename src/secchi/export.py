"""Export the store as files people can download: Parquet, CSV, a manifest.

    pixi run export                       # preview bundle in exports/
    pixi run export --only lake_exo       # one dataset, for a quick look
    pixi run export --release --teon-permission "who, when, how"

What it writes (one flat directory, so it maps 1:1 onto GitHub Release
assets): per dataset, one Parquet file for everything and one gzipped CSV
per calendar year; plus README.md, SOURCES.md, CITATION.cff,
manifest.json and SHA256SUMS. ``exports/`` is gitignored: the store is
already committed, and a second committed copy of 12 million rows would
be the same mistake the partitioned layout was built to avoid.

Rules this module holds itself to
---------------------------------
**Raw values are never changed.** Anything suspect is *flagged* in a
``quality_flag`` column, not corrected or dropped. The flags are
inferred here from the data itself, and the README says so.

**TEON's visibility flag is honoured, and fails closed.** The list is
fetched live; if it cannot be read the export refuses. ``--offline-visibility``
falls back to the watcher's baseline, and only while that is fresh.

**A release needs a recorded permission.** TEON said (2026-10-06) it is
comfortable with the project being shared publicly. Bulk redistribution of
its data as downloadable files is a different step, so ``--release``
refuses unless ``--teon-permission`` says who allowed it. Without it the
bundle is labelled a preview, in the manifest and the README.

**Every count is checked against the files actually written**, re-read
from disk, not against the query that produced them: Parquet rows, CSV
rows, the three observation datasets against the whole visible store, and
no hidden site anywhere in the output.

Why the duplicate flags exist
-----------------------------
Until 2026-10-07 the store deduplicated on record ids only, and the
September 2026 backfills stored 289,804 readings twice under *different*
ids (mostly forest stations, November 2025 to February 2026). They were
removed from the store that day and the store now drops identical repeat
readings itself (``store.READING_KEY``). The flags stay as a check: an
identical repeat should no longer appear, and ``conflicting_duplicate``
marks the 149 moments where two copies disagree on the value, which are
kept because neither is provably wrong.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from secchi.config import PROCESSED_DIR, REFERENCE_DIR, REPO_ROOT
from secchi.sources.teon import VisibilityUnavailable, slugify_site
from secchi.store import DST_CLOCK_SENSORS
from secchi.sources.watch import BASELINE_FILE, IMPOSSIBLE

log = logging.getLogger(__name__)

EXPORT_DIR = REPO_ROOT / "exports"
FLAGS_VERSION = 3   # 2: negative_chlorophyll, negative_phycocyanin; 3: repeated_hour (2026-10-08)
BASELINE_MAX_AGE_HOURS = 48

LAKE_SENSORS = ("ExoSensor", "MiniDotSensor", "HoboSensor")

# The EXO channels that are optical sensors. At 4H Camp in July 2026 these
# were filed under each other's names while temperature, conductivity and
# depth stayed correct (docs/dissolved-oxygen.md, teon-note-2026-09-28.md).
OPTICAL_CHANNELS = ("Do_mgL", "Do_percent", "Chl_a", "phycocyanin", "Turbidity", "TSS")

# Flag name -> what it means. The README is generated from this, so a flag
# cannot exist without a definition.
QUALITY_FLAGS = {
    "impossible_range": (
        "Value outside what the instrument could physically read in Lake "
        "Tahoe (water temperature outside -2 to 35 C, oxygen above 20 mg/L, "
        "oxygen saturation outside 50-150 %, pH outside 0-14). The same "
        "rules the watcher uses."),
    "channel_scramble": (
        "At this exact timestamp the sonde's channels were filed under "
        "the wrong names: oxygen concentration above 20 mg/L (saturation "
        "in the concentration field) flags the optical channels; water "
        "temperature above 35 C (saturation in the temperature field) "
        "flags every channel except battery. Inferred by secchi, not "
        "stated by TEON. The readings are probably intact and mislabelled, "
        "but are not remapped here."),
    "exo_sat_sea_level": (
        "EXO oxygen saturation (Do_percent). secchi's test (docs/"
        "dissolved-oxygen.md) found the EXO sondes reference saturation to "
        "sea-level pressure, so at Tahoe's altitude it reads about 20 "
        "points below the true saturation. Do_mgL is a measured "
        "concentration and is not affected. Reported to TEON; not "
        "confirmed by them."),
    "negative_turbidity": (
        "EXO turbidity below zero: not a real measurement, a zero-point "
        "offset. Common, not rare: every EXO sonde in the store reads "
        "below zero for much of its record, and at Sunnyside and 4H Camp "
        "the offset changes at service visits. Treat turbidity as relative "
        "until the zero point is understood."),
    "negative_chlorophyll": (
        "EXO chlorophyll (Chl_a) below zero: a zero-point offset, not a real "
        "concentration. Every EXO sonde does it, Sunnyside most (83 % of its "
        "record). Readings near zero mean very little chlorophyll, but the "
        "exact level isn't known; treat it as relative."),
    "negative_phycocyanin": (
        "EXO phycocyanin (blue-green algae pigment) below zero: a zero-point "
        "offset, as for chlorophyll and turbidity. Every EXO sonde does it "
        "some of the time (12-36 % of readings)."),
    "ph_zero": (
        "EXO pH exactly 0: a dead channel. pH works only on the "
        "hand-collected sondes."),
    "repeated_hour": (
        "MiniDOT clocks follow daylight saving, so when clocks fall back "
        "(01:00-01:59 on the first Sunday of November) the same timestamps "
        "happen twice: two real readings an hour apart share each one. "
        "Both are kept; nothing in the record says which came first."),
    "duplicate_reading": (
        "Another row has the same site, sensor, variable and timestamp, "
        "under a different record id, with the same value. Keep one: "
        "QUALIFY row_number() OVER (PARTITION BY site, sensor_type, "
        "variable, timestamp ORDER BY uuid) = 1"),
    "conflicting_duplicate": (
        "As duplicate_reading, but the values differ. The cause has not "
        "been established (some are two readings within the same minute); "
        "not safe to drop either without looking."),
}

DATASETS = [
    dict(name="lake_exo", table="observations", teon=True,
         where="sensor_type = 'ExoSensor'",
         description="Lake EXO sondes: telemetered (4H Camp, Glenbrook, "
                     "Sunnyside) and hand-collected (Blackwood 3, Meeks)."),
    dict(name="lake_nearshore", table="observations", teon=True,
         where="sensor_type IN ('MiniDotSensor', 'HoboSensor')",
         description="Nearshore self-logging MiniDOT (oxygen, temperature) "
                     "and HOBO (temperature) loggers, read by boat."),
    dict(name="forest", table="observations", teon=True,
         where="(sensor_type IS NULL OR sensor_type NOT IN "
               "('ExoSensor', 'MiniDotSensor', 'HoboSensor'))",
         description="Forest stations: soil, air, trees, stream level and "
                     "chemistry, precipitation gauge. Soil_VWC is a "
                     "fraction (0.034 = 3.4 %)."),
    dict(name="usgs", table="usgs_observations", teon=False,
         description="USGS stream gauges in the Tahoe basin."),
    dict(name="snotel", table="snotel_observations", teon=False,
         description="NRCS SNOTEL: Ward Creek #3, Rubicon #2, Marlette Lake "
                     "and the Snow Lab station."),
    dict(name="smoke", table="smoke_observations", teon=False,
         description="NOAA Hazard Mapping System smoke over the lake, daily."),
    dict(name="asos", table="asos_observations", teon=False,
         description="National Weather Service daily weather at South Lake "
                     "Tahoe and Truckee airports."),
]

# Reference CSVs copied as they are. `cleared` is whether the source's
# terms are confirmed to allow redistribution; an unconfirmed one is left
# out and says so in the manifest rather than quietly shipping.
REFERENCE_FILES = [
    dict(file="terc_secchi.csv", cleared=True,
         note="UC Davis TERC Secchi depth record, EDI package edi.1340."),
    dict(file="cssl_snow_climatology.csv", cleared=False,
         note="UC Berkeley Central Sierra Snow Laboratory snowfall by water "
              "year. Redistribution terms not confirmed; get it from the "
              "source."),
]


class ExportRefused(RuntimeError):
    """The export will not run, and says why. Never a silent fallback."""


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------

def load_disabled(*, live: bool = True, reference_dir: Path = REFERENCE_DIR,
                  client_factory=None, now: datetime | None = None,
                  max_age_hours: int = BASELINE_MAX_AGE_HOURS) -> tuple[set[str], str]:
    """Site slugs TEON hides, and where that answer came from.

    Raises ExportRefused if the list cannot be established. "Could not
    read it" is never treated as "nothing is hidden".
    """
    if live:
        if client_factory is None:
            from secchi.sources.teon import TeonClient
            client_factory = TeonClient
        try:
            with client_factory() as client:
                slugs = client.disabled_sites()
        except VisibilityUnavailable as exc:
            raise ExportRefused(
                f"{exc}. Not exporting without TEON's visibility list. "
                f"If you are offline, --offline-visibility uses the "
                f"watcher's baseline while it is under "
                f"{max_age_hours} h old.") from exc
        return {slugify_site(s) for s in slugs}, "live"

    path = Path(reference_dir) / BASELINE_FILE
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
        slugs = baseline["disabled_sites"]
        captured = datetime.fromisoformat(baseline["captured_at"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ExportRefused(
            f"cannot read the visibility list from {path}: {exc}") from exc
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age = now - captured
    if age > timedelta(hours=max_age_hours):
        raise ExportRefused(
            f"the baseline's visibility list is {age.total_seconds() / 3600:.0f} h "
            f"old (limit {max_age_hours} h). Pull, or export with a live "
            f"connection.")
    return {slugify_site(s) for s in slugs}, f"baseline captured {captured:%Y-%m-%d %H:%M} UTC"


def _visible_predicate(disabled: set[str]) -> str:
    if not disabled:
        return "TRUE"
    quoted = ", ".join("'" + s.replace("'", "''") + "'" for s in sorted(disabled))
    return f"lower(replace(site, ' ', '')) NOT IN ({quoted})"


def _hidden_predicate(disabled: set[str]) -> str:
    quoted = ", ".join("'" + s.replace("'", "''") + "'" for s in sorted(disabled))
    return f"lower(replace(site, ' ', '')) IN ({quoted})"


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def _glob(processed_dir: Path, table: str) -> str | None:
    root = Path(processed_dir) / table
    if not root.exists() or not any(root.rglob("part*.parquet")):
        return None
    return (root / "**" / "part*.parquet").as_posix()


def _read(glob: str) -> str:
    # union_by_name: not every partition has every column (USGS `approval`).
    return f"read_parquet('{glob}', hive_partitioning = true, union_by_name = true)"


def _impossible_sql() -> str:
    parts = [
        f"(o.sensor_type = '{inst}' AND o.variable = '{var}' "
        f"AND (o.value < {lo} OR o.value > {hi}) "
        f"AND NOT (o.variable = 'pH' AND o.value = 0))"
        for inst, var, lo, hi in IMPOSSIBLE]
    return " OR ".join(parts)


def _optical_list() -> str:
    return ", ".join(f"'{c}'" for c in OPTICAL_CHANNELS)


def _prepare_observations(con, glob: str, disabled: set[str]) -> None:
    """Create ``obs_flagged``: the visible TEON store plus quality_flag.

    Triggers and duplicates are materialised once (small tables) and
    joined, rather than computed per row over 12 million rows.
    """
    visible = _visible_predicate(disabled)
    con.execute(f"""
        CREATE TEMP VIEW obs_visible AS
        SELECT uuid, source, site, sensor_type, timestamp, lat, lng,
               variable, value
        FROM {_read(glob)}
        WHERE {visible}""")

    # The hour a daylight-saving clock repeats: its two readings per
    # timestamp are real, an hour apart, not duplicates (store.py).
    dst = ", ".join(f"'{s}'" for s in sorted(DST_CLOCK_SENSORS))
    fall_back = (f"(o.sensor_type IN ({dst}) AND month(o.timestamp) = 11 "
                 f"AND dayofweek(o.timestamp) = 0 AND day(o.timestamp) <= 7 "
                 f"AND hour(o.timestamp) = 1)")

    # Exact duplicate keys under different record ids.
    con.execute(f"""
        CREATE TEMP TABLE dup_keys AS
        SELECT site, sensor_type, variable, timestamp,
               min(value) = max(value) AS same
        FROM obs_visible o
        WHERE NOT {fall_back}
        GROUP BY site, sensor_type, variable, timestamp
        HAVING count(*) > 1""")

    # Timestamps at which an EXO sonde's channels were filed under the
    # wrong names. The trigger is the signature of the swap itself: oxygen
    # *saturation* (~85) arriving in the concentration field, or oxygen
    # saturation in the water-temperature field. Not "any impossible
    # oxygen reading": a first version also triggered on saturation below
    # 50 %, which wrongly marked 4H Camp's October 2025 readings, a
    # service visit at Blackwood 3, and isolated zeros as scrambles.
    # Per-reading, not a guessed window, so 4H Camp's normal day in the
    # middle of its week stays unflagged.
    con.execute("""
        CREATE TEMP TABLE scramble_optical AS
        SELECT DISTINCT site, timestamp FROM obs_visible
        WHERE sensor_type = 'ExoSensor'
          AND variable = 'Do_mgL' AND value > 20""")
    con.execute("""
        CREATE TEMP TABLE scramble_full AS
        SELECT DISTINCT site, timestamp FROM obs_visible
        WHERE sensor_type = 'ExoSensor'
          AND variable = 'Temp' AND value > 35""")

    con.execute(f"""
        CREATE TEMP VIEW obs_flagged AS
        SELECT o.uuid, o.source, o.site, o.sensor_type, o.timestamp,
               o.lat, o.lng, o.variable, o.value,
               nullif(concat_ws(';',
                 CASE WHEN {_impossible_sql()} THEN 'impossible_range' END,
                 CASE WHEN o.sensor_type = 'ExoSensor'
                       AND ((f.site IS NOT NULL AND o.variable <> 'BattV_Min')
                         OR (t.site IS NOT NULL
                             AND o.variable IN ({_optical_list()})))
                      THEN 'channel_scramble' END,
                 CASE WHEN o.sensor_type = 'ExoSensor'
                       AND o.variable = 'Do_percent'
                      THEN 'exo_sat_sea_level' END,
                 CASE WHEN o.sensor_type = 'ExoSensor'
                       AND o.variable = 'Turbidity' AND o.value < 0
                      THEN 'negative_turbidity' END,
                 CASE WHEN o.sensor_type = 'ExoSensor'
                       AND o.variable = 'Chl_a' AND o.value < 0
                      THEN 'negative_chlorophyll' END,
                 CASE WHEN o.sensor_type = 'ExoSensor'
                       AND o.variable = 'phycocyanin' AND o.value < 0
                      THEN 'negative_phycocyanin' END,
                 CASE WHEN o.sensor_type = 'ExoSensor'
                       AND o.variable = 'pH' AND o.value = 0
                      THEN 'ph_zero' END,
                 CASE WHEN {fall_back} THEN 'repeated_hour' END,
                 CASE WHEN d.site IS NOT NULL THEN
                      CASE WHEN d.same THEN 'duplicate_reading'
                           ELSE 'conflicting_duplicate' END END
               ), '') AS quality_flag
        FROM obs_visible o
        LEFT JOIN dup_keys d
          ON d.site = o.site AND d.sensor_type = o.sensor_type
         AND d.variable = o.variable AND d.timestamp = o.timestamp
        LEFT JOIN scramble_optical t
          ON t.site = o.site AND t.timestamp = o.timestamp
        LEFT JOIN scramble_full f
          ON f.site = o.site AND f.timestamp = o.timestamp""")


def _dataset_select(spec: dict, glob: str | None) -> str:
    """The SELECT that defines one dataset, in a fixed row order."""
    if spec["table"] == "observations":
        return (f"SELECT * FROM obs_flagged WHERE {spec['where']} "
                f"ORDER BY site, sensor_type, variable, timestamp, uuid")
    return (f"SELECT * EXCLUDE (year, month) FROM {_read(glob)} ORDER BY ALL")


# ---------------------------------------------------------------------------
# Writing and checking
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _copy(con, select_sql: str, path: Path, options: str) -> None:
    """COPY to a temp name, then replace: a failed run leaves no half file."""
    if "'" in path.as_posix():
        raise ExportRefused(f"refusing a path containing a quote: {path}")
    tmp = path.with_name(path.name + ".tmp")
    try:
        con.execute(f"COPY ({select_sql}) TO '{tmp.as_posix()}' ({options})")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _count_parquet(con, path: Path) -> int:
    return con.execute(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]


def _count_csv(con, path: Path) -> int:
    return con.execute(
        f"SELECT count(*) FROM read_csv('{path.as_posix()}', header = true, "
        f"all_varchar = true)").fetchone()[0]


def _columns(con, path: Path) -> list[dict]:
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()
    return [{"name": r[0], "type": r[1]} for r in rows]


def _file_entry(path: Path, kind: str, rows: int, year: int | None = None) -> dict:
    entry = {"name": path.name, "format": kind, "rows": rows,
             "bytes": path.stat().st_size, "sha256": _sha256(path)}
    if year is not None:
        entry["year"] = year
    return entry


def _write_dataset(con, spec: dict, glob: str | None, bundle: Path,
                   disabled: set[str]) -> dict:
    name = spec["name"]
    select = _dataset_select(spec, glob)
    expected = con.execute(f"SELECT count(*) FROM ({select})").fetchone()[0]

    pq = bundle / f"{name}.parquet"
    _copy(con, select, pq, "FORMAT parquet, COMPRESSION zstd")
    parquet_rows = _count_parquet(con, pq)
    if parquet_rows != expected:
        raise ExportRefused(
            f"{name}: wrote {parquet_rows:,} Parquet rows but the query "
            f"returns {expected:,}")

    if disabled and "site" in {c["name"] for c in _columns(con, pq)}:
        leaked = con.execute(
            f"SELECT count(*) FROM read_parquet('{pq.as_posix()}') "
            f"WHERE {_hidden_predicate(disabled)}").fetchone()[0]
        if leaked:
            raise ExportRefused(
                f"{name}: {leaked:,} rows from a site TEON hides reached the "
                f"output. Nothing published.")

    files = [_file_entry(pq, "parquet", parquet_rows)]

    years = [r[0] for r in con.execute(
        f"SELECT DISTINCT year(timestamp) AS y FROM read_parquet('{pq.as_posix()}') "
        f"ORDER BY y").fetchall() if r[0] is not None]
    csv_total = 0
    for y in years:
        csv = bundle / f"{name}_{y}.csv.gz"
        _copy(con,
              f"SELECT * FROM read_parquet('{pq.as_posix()}') WHERE year(timestamp) = {y}",
              csv, "FORMAT csv, HEADER, COMPRESSION gzip")
        n = _count_csv(con, csv)
        expect_y = con.execute(
            f"SELECT count(*) FROM read_parquet('{pq.as_posix()}') "
            f"WHERE year(timestamp) = {y}").fetchone()[0]
        if n != expect_y:
            raise ExportRefused(f"{name} {y}: CSV has {n:,} rows, Parquet {expect_y:,}")
        csv_total += n
        files.append(_file_entry(csv, "csv.gz", n, year=y))
    undated = con.execute(
        f"SELECT count(*) FROM read_parquet('{pq.as_posix()}') "
        f"WHERE timestamp IS NULL").fetchone()[0]
    if csv_total + undated != parquet_rows:
        raise ExportRefused(
            f"{name}: CSV files hold {csv_total:,} rows plus {undated:,} undated, "
            f"Parquet holds {parquet_rows:,}")

    span = con.execute(
        f"SELECT min(timestamp)::VARCHAR, max(timestamp)::VARCHAR, count(DISTINCT site) "
        f"FROM read_parquet('{pq.as_posix()}')").fetchone()
    entry = {
        "name": name, "description": spec["description"],
        "source_table": spec["table"], "teon": spec["teon"],
        "rows": parquet_rows, "sites": span[2],
        "first": span[0], "last": span[1],
        "columns": _columns(con, pq), "files": files,
    }
    if any(c["name"] == "quality_flag" for c in entry["columns"]):
        counts = con.execute(
            f"SELECT f, count(*) FROM (SELECT unnest(string_split(quality_flag, ';')) AS f "
            f"FROM read_parquet('{pq.as_posix()}') WHERE quality_flag IS NOT NULL) "
            f"GROUP BY f ORDER BY 2 DESC").fetchall()
        entry["quality_flag_counts"] = {f: n for f, n in counts}
    return entry


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

DISCLAIMER = """\
**These data are provisional.** They are raw or lightly processed readings \
from instruments in the field and are subject to instrument performance, \
maintenance cycles and field conditions. They have not been reviewed or \
approved by TEON or any other agency, and their sources may revise, correct \
or withdraw them at any time. Re-download before relying on a number.

**This is not an official product** of the Tahoe Environmental Observatory \
Network, the Tahoe Institute for Global Sustainability, the University of \
Nevada, Reno, the U.S. Forest Service, the Tahoe Science Advisory Council, \
the U.S. Geological Survey, USDA NRCS, NOAA, the National Weather Service \
or UC Davis TERC. No endorsement by any of them is stated or implied.

**The quality flags are not quality control.** They are inferred by secchi \
from the data. A reading without a flag has not been certified; it is only \
one nothing here has caught. Flagged readings are kept exactly as stored.

**Provided as is, without warranty of any kind**, express or implied, \
including accuracy, completeness, timeliness or fitness for a particular \
purpose. Do not use these data for safety-critical, regulatory, legal or \
operational decisions. Anyone relying on them does so at their own risk.

**Terms.** Each source's own terms govern its data (see SOURCES.md). The \
MIT licence on the secchi code does not apply to the data. Cite the \
upstream sources, not this bundle."""

REDISTRIBUTION_PREVIEW = ("PREVIEW: permission from TEON to redistribute its "
                          "data as files is not recorded. Do not publish.")

TEON_ACK = ("Sensor data from the Tahoe Environmental Observatory Network "
            "(Tahoe Institute for Global Sustainability, University of Nevada, "
            "Reno, with the U.S. Forest Service Pacific Southwest Research "
            "Station and the Tahoe Science Advisory Council).")


def _render_readme(m: dict) -> str:
    cleared = m["redistribution"]["status"] == "cleared"
    banner = "" if cleared else (
        f"> **{REDISTRIBUTION_PREVIEW}**\n\n")
    tag = m["release_tag"]
    base = f"https://github.com/bdgroves/secchi/releases/download/{tag}"
    lines = [
        f"# secchi data, through {m['data_through'][:10]}\n",
        banner,
        "Open data behind <https://brooksgroves.com/secchi/>, built from "
        "the project's store by `pixi run export`. **All data is "
        "provisional.** Nothing here is an official TEON, USGS or other "
        "agency product; cite the upstream sources (SOURCES.md), not this "
        "bundle.\n",
        "## Disclaimer\n",
        DISCLAIMER + "\n",
        "## Files\n",
        "| dataset | rows | sites | first | last | what it is |",
        "|---|---:|---:|---|---|---|",
    ]
    for d in m["datasets"]:
        lines.append(
            f"| `{d['name']}` | {d['rows']:,} | {d['sites']} | "
            f"{(d['first'] or '')[:10]} | {(d['last'] or '')[:10]} | "
            f"{d['description']} |")
    lines += [
        "",
        "Each dataset is `<name>.parquet` (everything) and "
        "`<name>_<year>.csv.gz` (one per calendar year, for Excel and "
        "quick looks). `SHA256SUMS` checks every file; `manifest.json` "
        "lists row counts, columns and date ranges.\n",
        "## Read it\n",
        "```python\nimport duckdb\n"
        f"duckdb.sql(\"SELECT site, avg(value) FROM '{base}/lake_exo.parquet' "
        "WHERE variable = 'Temp' AND quality_flag IS NULL GROUP BY site\")\n```\n",
        "```python\nimport pandas as pd\n"
        f"df = pd.read_parquet('{base}/lake_exo.parquet')\n```\n",
        "```r\nlibrary(arrow)\n"
        f"df <- read_parquet('{base}/lake_exo.parquet')\n```\n",
        "## Columns (TEON datasets)\n",
        "`uuid` record id from TEON, shared by the variables of one record; "
        "`source`; `site`; `sensor_type`; `timestamp`; `lat`, `lng`; "
        "`variable`; `value`; `quality_flag`. One row per reading per "
        "variable (long format).\n",
        "- **Timestamps are the loggers' clocks, with no zone written**: "
        "Pacific Standard Time (UTC-8) all year, even in summer, except "
        "the MiniDOT loggers in `lake_nearshore`, which follow Pacific "
        "daylight time. USGS times carry their own offset.\n"
        "- **`Soil_VWC` is a fraction** (0.034 = 3.4 %).\n"
        "- Four naming conventions coexist: EXO `Temp`/`Do_mgL`/`Chl_a`, "
        "MiniDOT `Temperature`/`Dissolved Oxygen`, HOBO `temperature`, "
        "Campbell loggers `Air_Temp`/`Soil_VWC`/`BattV_Avg`.\n"
        "- Units are not stored per row. See "
        "<https://github.com/bdgroves/secchi/blob/main/docs/data-dictionary.md>.\n",
        "## Quality flags\n",
        "**Values are exactly as stored. Nothing was corrected or removed.** "
        "`quality_flag` is empty for readings with nothing noted, or one or "
        "more of these separated by `;`. They are inferred by secchi from "
        "the data and are not TEON's own quality control.\n",
        "| flag | meaning |", "|---|---|",
    ]
    counts: dict[str, int] = {}
    for d in m["datasets"]:
        for k, v in (d.get("quality_flag_counts") or {}).items():
            counts[k] = counts.get(k, 0) + v
    for flag, text in QUALITY_FLAGS.items():
        n = counts.get(flag)
        suffix = f" ({n:,} rows)" if n is not None else ""
        lines.append(f"| `{flag}`{suffix} | {text.replace('|', '/')} |")
    lines += [
        "",
        "Flags exist only on the TEON datasets. A reading with no flag is "
        "not certified good; it is just one nothing here has caught.\n",
        "## Sites TEON hides\n",
        f"This bundle leaves out any site TEON's `/site-visibility/disabled` "
        f"list hides (checked: {m['visibility']['source']}; "
        f"{len(m['visibility']['hidden_slugs'])} hidden).\n",
        "## Cite\n",
        "See `CITATION.cff` for this bundle and `SOURCES.md` for the "
        "upstream sources, which come first.\n",
    ]
    return "\n".join(lines)


def _render_sources(m: dict, terc: dict | None) -> str:
    cleared = m["redistribution"]["status"] == "cleared"
    perm = m["redistribution"].get("teon_permission") or "none recorded"
    terc_rev = (f" Revision {terc['newest_seen']}."
                if terc and terc.get("newest_seen") else "")
    out = [
        "# Sources and terms\n",
        "Cite these, not secchi.\n",
        DISCLAIMER + "\n",
        "## TEON (`lake_exo`, `lake_nearshore`, `forest`)\n",
        TEON_ACK + " TEON asks that its website, "
        "<https://tahoeenvironmentalobservatorynetwork.org/>, be "
        "acknowledged in derived products. The data is raw or lightly "
        "processed and subject to instrument performance, maintenance "
        "cycles and field conditions.\n",
        f"Redistribution as files: **{m['redistribution']['status']}**. "
        f"Permission recorded: {perm}.\n",
    ]
    if not cleared:
        out.append(f"> {REDISTRIBUTION_PREVIEW}\n")
    out += [
        "## USGS (`usgs`)\n",
        "U.S. Geological Survey stream-gauge data via the Water Data OGC "
        "APIs. USGS data is generally in the public domain; credit USGS and "
        "note the `approval` column (provisional or approved).\n",
        "## NRCS SNOTEL (`snotel`)\n",
        "USDA Natural Resources Conservation Service SNOTEL network.\n",
        "## NOAA HMS (`smoke`)\n",
        "NOAA Hazard Mapping System smoke analysis; the smoke figure is "
        "secchi's overlap of the analysed plumes with the lake.\n",
        "## NWS airports (`asos`)\n",
        "National Weather Service daily climate record at South Lake Tahoe "
        "and Truckee, via NOAA's Applied Climate Information System.\n",
        "## Reference files\n",
    ]
    for r in m["reference"]["included"]:
        extra = (f"{terc_rev} Licence CC BY 4.0." if r["name"] == "terc_secchi.csv" else "")
        out.append(f"- `{r['name']}`: {r['note']}{extra}")
    for r in m["reference"]["excluded"]:
        out.append(f"- `{r['name']}` is **not included**: {r['note']}")
    out.append("")
    return "\n".join(out)


def _render_citation(m: dict) -> str:
    day = m["data_through"][:10]
    return (
        "cff-version: 1.2.0\n"
        "message: \"Cite the upstream sources in SOURCES.md first. If this "
        "bundle was what you used, cite it as below.\"\n"
        f"title: \"secchi data bundle, through {day}\"\n"
        f"version: \"{day}\"\n"
        f"date-released: \"{m['generated_at'][:10]}\"\n"
        "type: dataset\n"
        "authors:\n"
        "  - family-names: Groves\n"
        "    given-names: Brooks\n"
        "repository-code: \"https://github.com/bdgroves/secchi\"\n"
        "url: \"https://brooksgroves.com/secchi/\"\n"
        "abstract: \"Provisional Lake Tahoe sensor data from TEON, with USGS, "
        "SNOTEL, NOAA and NWS context, exported from the secchi store with "
        "inferred quality flags. Not an official product of any agency.\"\n")


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------

def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def build_bundle(*, processed_dir: Path = PROCESSED_DIR,
                 reference_dir: Path = REFERENCE_DIR,
                 out_root: Path = EXPORT_DIR,
                 disabled: set[str], visibility_source: str,
                 release: bool = False, teon_permission: str | None = None,
                 only: list[str] | None = None,
                 now: datetime | None = None) -> Path:
    """Write the bundle and return its directory. Raises ExportRefused."""
    import duckdb

    if release and not (teon_permission or "").strip():
        raise ExportRefused(
            "--release needs --teon-permission saying who at TEON allowed "
            "redistributing its data as files, and when. TEON's 2026-10-06 "
            "reply covers sharing the project, not bulk files; ask, then "
            "record the answer.")
    if release and only:
        raise ExportRefused("--release exports everything; drop --only.")

    specs = [s for s in DATASETS if not only or s["name"] in only]
    unknown = set(only or []) - {s["name"] for s in DATASETS}
    if unknown:
        raise ExportRefused(f"unknown dataset(s): {sorted(unknown)}")
    if not specs:
        raise ExportRefused("nothing to export")

    now = now or datetime.now(timezone.utc)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    building = out_root / ".building"
    if building.exists():
        shutil.rmtree(building)
    building.mkdir()

    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{(building / '.duckdb-tmp').as_posix()}'")
    try:
        obs_glob = _glob(processed_dir, "observations")
        if obs_glob is None:
            raise ExportRefused(f"no observations under {processed_dir}")
        _prepare_observations(con, obs_glob, disabled)

        entries = []
        for spec in specs:
            glob = None if spec["table"] == "observations" else _glob(processed_dir, spec["table"])
            if spec["table"] != "observations" and glob is None:
                log.warning("skipping %s: no data under %s", spec["name"], spec["table"])
                continue
            log.info("exporting %s", spec["name"])
            entries.append(_write_dataset(con, spec, glob, building, disabled))

        if not only:
            total = con.execute("SELECT count(*) FROM obs_visible").fetchone()[0]
            got = sum(e["rows"] for e in entries if e["teon"])
            if got != total:
                raise ExportRefused(
                    f"the three TEON datasets hold {got:,} rows but the visible "
                    f"store holds {total:,}: a sensor type fell between them")

        data_through = con.execute(
            "SELECT max(timestamp)::VARCHAR FROM obs_visible").fetchone()[0]
    finally:
        con.close()
        shutil.rmtree(building / ".duckdb-tmp", ignore_errors=True)

    if data_through is None:
        raise ExportRefused("the store has no readings")

    # Reference files.
    included, excluded = [], []
    for ref in REFERENCE_FILES:
        src = Path(reference_dir) / ref["file"]
        if not ref["cleared"]:
            excluded.append({"name": ref["file"], "note": ref["note"]})
        elif src.exists():
            dest = building / ref["file"]
            shutil.copyfile(src, dest)
            included.append({"name": ref["file"], "note": ref["note"],
                             "bytes": dest.stat().st_size, "sha256": _sha256(dest)})
        else:
            excluded.append({"name": ref["file"], "note": "file missing from the repo"})
    terc = None
    tp = Path(reference_dir) / "terc_package.json"
    if tp.exists():
        try:
            terc = json.loads(tp.read_text(encoding="utf-8"))
        except ValueError:
            terc = None

    day = data_through[:10]
    manifest = {
        "bundle": f"secchi-data-{day}",
        "release_tag": f"data-{day}",
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "data_through": data_through,
        "timestamps": "naive logger time: " + "Pacific Standard Time (UTC-8) all year, except MiniDOT, which follows Pacific daylight time",
        "secchi_commit": _git_commit(),
        "quality_flags_version": FLAGS_VERSION,
        "partial": bool(only),
        "visibility": {"source": visibility_source,
                       "hidden_slugs": sorted(disabled)},
        "redistribution": (
            {"status": "cleared", "teon_permission": teon_permission.strip()}
            if release else
            {"status": "preview", "note": REDISTRIBUTION_PREVIEW,
             "teon_permission": None}),
        "disclaimer": DISCLAIMER,
        "datasets": entries,
        "reference": {"included": included, "excluded": excluded},
    }

    (building / "README.md").write_text(_render_readme(manifest), encoding="utf-8")
    (building / "SOURCES.md").write_text(_render_sources(manifest, terc), encoding="utf-8")
    (building / "DISCLAIMER.md").write_text(
        "# Disclaimer\n\n" + DISCLAIMER + "\n", encoding="utf-8")
    (building / "CITATION.cff").write_text(_render_citation(manifest), encoding="utf-8")
    (building / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    sums = [f"{_sha256(p)}  {p.name}" for p in sorted(building.iterdir())
            if p.is_file() and p.name != "SHA256SUMS"]
    (building / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    final = out_root / manifest["bundle"]
    if final.exists():
        shutil.rmtree(final)
    building.rename(final)
    return final


def verify_bundle(bundle: Path) -> list[str]:
    """Re-check a bundle on disk against its own SHA256SUMS. Returns problems."""
    problems = []
    sums = Path(bundle) / "SHA256SUMS"
    if not sums.exists():
        return ["SHA256SUMS missing"]
    listed = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        listed.add(name)
        path = Path(bundle) / name
        if not path.exists():
            problems.append(f"{name}: missing")
        elif _sha256(path) != digest:
            problems.append(f"{name}: checksum differs")
    for p in Path(bundle).iterdir():
        if p.is_file() and p.name != "SHA256SUMS" and p.name not in listed:
            problems.append(f"{p.name}: not in SHA256SUMS")
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="secchi export", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=EXPORT_DIR,
                    help="where bundles go (default: exports/, gitignored)")
    ap.add_argument("--only", nargs="+", metavar="DATASET",
                    help=f"just these: {', '.join(s['name'] for s in DATASETS)}")
    ap.add_argument("--offline-visibility", action="store_true",
                    help=f"use the watcher's baseline instead of asking TEON "
                         f"(refused if older than {BASELINE_MAX_AGE_HOURS} h)")
    ap.add_argument("--release", action="store_true",
                    help="mark the bundle cleared for redistribution; needs --teon-permission")
    ap.add_argument("--teon-permission", metavar="TEXT",
                    help="who at TEON allowed redistribution, and when")
    ap.add_argument("--verify", type=Path, metavar="DIR",
                    help="check an existing bundle against its SHA256SUMS and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.verify:
        problems = verify_bundle(args.verify)
        for p in problems:
            print(f"  PROBLEM  {p}")
        print("bundle verifies" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    try:
        disabled, source = load_disabled(live=not args.offline_visibility)
        bundle = build_bundle(out_root=args.out, disabled=disabled,
                              visibility_source=source, release=args.release,
                              teon_permission=args.teon_permission, only=args.only)
    except ExportRefused as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 2

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    print(f"\n  {bundle}")
    print(f"  data through {manifest['data_through']}   "
          f"visibility: {source}   {manifest['redistribution']['status']}")
    for d in manifest["datasets"]:
        size = sum(f["bytes"] for f in d["files"]) / 1e6
        print(f"    {d['name']:<16}{d['rows']:>12,} rows  {len(d['files']):>2} files  {size:>8.1f} MB")
        for k, v in (d.get("quality_flag_counts") or {}).items():
            print(f"        {k:<24}{v:>10,}")
    if manifest["redistribution"]["status"] != "cleared":
        print(f"\n  {REDISTRIBUTION_PREVIEW}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
