"""The files behind the page's custom download: small, flagged, on the site.

    pixi run webdata            # writes web/data/ (gitignored), after transform

The page's "Download the data" form lets someone pick a dataset, stations,
variables and a time window (last 7 days, 3 months, a custom range...) and
get exactly those rows as a CSV, zipped with a README that carries the
disclaimer. It runs entirely in the browser, so it needs the data where the
browser can read it: GitHub release assets send no CORS header
(docs/export.md), so the files live on the site itself.

What it writes
--------------
``web/data/<dataset>/<site-slug>/<YYYY-MM>.csv.gz``, one file per dataset,
site and month, the same rows and columns as the export bundle's CSV
(``quality_flag`` included), in a fixed order. ``web/data/catalog.json``
lists every file with its row count and size, and every site's variables
with labels and units, so the form is built without fetching any data.

The rules are the export's, by reusing its code rather than restating it:
the quality flags come from ``export._prepare_observations``, the datasets
from ``export.DATASETS``, the disclaimer from ``export.DISCLAIMER``.

**TEON's visibility list fails closed.** If it can't be read, the TEON
datasets are left out of the catalog (with the reason) rather than served
unfiltered; the Beyond TEON datasets still build. After writing, every file
is read back: row counts per file must match the query, and no hidden site
may appear anywhere.

**Raw values are never changed.** ``Soil_VWC`` stays a fraction; the
catalog's units say so.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from secchi import export
from secchi.config import (PROCESSED_DIR, REFERENCE_DIR, SENSOR_VARIABLES,
                           SITE_LABELS, USGS_PARAMETERS, WEB_DIR)

log = logging.getLogger(__name__)

WEB_DATA_DIR = WEB_DIR / "data"
CATALOG_VERSION = 1

# Who allowed redistribution, as Brooks recorded it. The export's
# --teon-permission wants the same fact; keep them saying the same thing.
REDISTRIBUTION = {
    "status": "cleared",
    "recorded": "2026-10-07",
    "statement": ("Sudeep Chandra (TEON lead, University of Nevada, Reno), email "
                  "to Brooks Groves, 2026-10-06: \"we are comfortable with you "
                  "sharing this publicly\". Shared with provisional-data language, "
                  "as Brooks recorded on 2026-10-07."),
}

# How each dataset is grouped on the page. TEON first; everything else is
# context and sits under "Beyond TEON", as it does everywhere on the page.
GROUP = {"lake_exo": "TEON", "lake_nearshore": "TEON", "forest": "TEON"}
LABELS = {
    "lake_exo": "Lake sondes (EXO)",
    "lake_nearshore": "Nearshore loggers (MiniDOT, HOBO)",
    "forest": "Forest stations",
    "usgs": "USGS stream gauges",
    "snotel": "SNOTEL",
    "smoke": "Wildfire smoke (NOAA HMS)",
    "asos": "Airport weather",
}

# Who to credit, per dataset: the same wording as the export's SOURCES.md.
TEON_SOURCE = (export.TEON_ACK + " TEON asks that its website, "
               "https://tahoeenvironmentalobservatorynetwork.org/, be acknowledged "
               "in derived products.")
SOURCES = {
    "lake_exo": TEON_SOURCE, "lake_nearshore": TEON_SOURCE, "forest": TEON_SOURCE,
    "usgs": ("U.S. Geological Survey stream-gauge data via the Water Data OGC APIs. "
             "USGS data is generally in the public domain; credit USGS and note "
             "the approval_status column (provisional or approved)."),
    "snotel": "USDA Natural Resources Conservation Service SNOTEL network.",
    "smoke": ("NOAA Hazard Mapping System smoke analysis; the smoke figure is "
              "secchi's overlap of the analysed plumes with the lake."),
    "asos": ("National Weather Service daily climate record at South Lake Tahoe "
             "and Truckee, via NOAA's Applied Climate Information System."),
}


def _download_disclaimer() -> str:
    """export.DISCLAIMER, pointed at what a custom download actually contains."""
    text = export.DISCLAIMER
    for old, new in (("(see SOURCES.md)", "(see SOURCE below)"),
                     ("not this bundle.", "not this download.")):
        if old not in text:
            raise RuntimeError(f"export.DISCLAIMER changed; update webdata ({old!r})")
        text = text.replace(old, new)
    return text


# Columns the store carries for bookkeeping that a download doesn't need.
DROP_COLUMNS = {"year", "month"}


def _slug(site: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(site).lower()).strip("-")
    return s or "site"


def _units(sensor_type: str, variable: str, row_unit: str | None) -> tuple[str, str]:
    """(label, units) for one variable, units as stored, not as displayed."""
    if row_unit:
        return variable, row_unit
    meta = (SENSOR_VARIABLES.get(sensor_type) or {}).get(variable)
    if meta:
        units = meta.get("units", "")
        if meta.get("scale") == 100.0 and units == "%":
            units = "fraction (0.034 = 3.4 %)"
        return meta.get("label", variable), units
    p = USGS_PARAMETERS.get(variable)
    if p:
        return p.get("label", variable), p.get("units", "")
    if variable.startswith("BattV") or variable == "Battery":
        return "Logger battery", "V"
    return variable, ""


def _select(con, spec: dict, glob: str | None) -> str | None:
    """The dataset's rows, export's columns, plus the two partition keys."""
    if spec["table"] == "observations":
        base = f"SELECT * FROM obs_flagged WHERE {spec['where']}"
    else:
        if glob is None:
            return None
        base = f"SELECT * FROM {export._read(glob)}"
    cols = [r[0] for r in con.execute(f"DESCRIBE {base}").fetchall()]
    keep = ", ".join(f'"{c}"' for c in cols if c not in DROP_COLUMNS)
    return (f"SELECT {keep}, strftime(timestamp, '%Y-%m') AS _month "
            f"FROM ({base}) WHERE timestamp IS NOT NULL")


def _write_dataset(con, spec: dict, select: str, out: Path,
                   disabled: set[str]) -> dict:
    name = spec["name"]
    con.execute(f"CREATE OR REPLACE TEMP TABLE ds AS {select}")
    sites = [r[0] for r in con.execute(
        "SELECT DISTINCT site FROM ds WHERE site IS NOT NULL ORDER BY 1").fetchall()]
    slugs: dict[str, str] = {}
    for s in sites:
        slug = _slug(s)
        if slug in slugs.values():
            raise export.ExportRefused(f"{name}: two sites share the slug {slug!r}")
        slugs[s] = slug
    con.register("_slugs", __import__("pandas").DataFrame(
        {"site": list(slugs), "_slug": list(slugs.values())}))
    con.execute("CREATE OR REPLACE TEMP TABLE ds2 AS SELECT ds.*, _slugs._slug "
                "FROM ds JOIN _slugs USING (site)")
    con.unregister("_slugs")

    ddir = out / name
    tmp = out / f".{name}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    con.execute(
        f"COPY (SELECT * FROM ds2 ORDER BY _slug, _month, sensor_type, variable, "
        f"timestamp, uuid) TO '{tmp.as_posix()}' "
        f"(FORMAT csv, HEADER, COMPRESSION gzip, PARTITION_BY (_slug, _month), "
        f"OVERWRITE_OR_IGNORE)")

    # Flatten DuckDB's hive folders to <slug>/<YYYY-MM>.csv.gz.
    expected = {(s, m): n for s, m, n in con.execute(
        "SELECT _slug, _month, count(*) FROM ds2 GROUP BY ALL").fetchall()}
    written: dict[tuple[str, str], Path] = {}
    for f in tmp.rglob("*.csv.gz"):
        parts = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in f.relative_to(tmp).parts[:-1]}
        key = (parts["_slug"], parts["_month"])
        if key in written:
            raise export.ExportRefused(f"{name}: more than one file for {key}")
        dest = tmp / key[0] / f"{key[1]}.csv.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.replace(dest)
        written[key] = dest
    for d in sorted((p for p in tmp.rglob("*") if p.is_dir()), reverse=True):
        if "=" in d.name:
            shutil.rmtree(d, ignore_errors=True)
    if set(written) != set(expected):
        raise export.ExportRefused(
            f"{name}: files written for {len(written)} site-months, expected {len(expected)}")

    # Read every file back: counts, and no hidden site.
    back = con.execute(
        f"SELECT filename, count(*), count(DISTINCT site), "
        f"bool_or({export._hidden_predicate(disabled) if disabled else 'FALSE'}) "
        f"FROM read_csv('{(tmp / '*' / '*.csv.gz').as_posix()}', header = true, "
        f"all_varchar = true, filename = true) GROUP BY filename").fetchall()
    got = {Path(fn).relative_to(tmp).as_posix(): (n, leak) for fn, n, _, leak in back}
    for (slug, month), n in expected.items():
        rel = f"{slug}/{month}.csv.gz"
        if rel not in got:
            raise export.ExportRefused(f"{name}: {rel} could not be read back")
        if got[rel][0] != n:
            raise export.ExportRefused(
                f"{name}: {rel} holds {got[rel][0]:,} rows, the query {n:,}")
        if got[rel][1]:
            raise export.ExportRefused(
                f"{name}: {rel} contains a site TEON hides. Nothing written.")

    if ddir.exists():
        shutil.rmtree(ddir)
    tmp.replace(ddir)

    has_unit = any(c == "unit" for c, in con.execute(
        "SELECT column_name FROM (DESCRIBE ds)").fetchall())
    unit_expr = "any_value(unit)" if has_unit else "NULL"
    var_rows = con.execute(
        f"SELECT site, sensor_type, variable, count(*), {unit_expr} "
        f"FROM ds GROUP BY site, sensor_type, variable ORDER BY ALL").fetchall()
    spans = {s: (a, b) for s, a, b in con.execute(
        "SELECT site, min(timestamp)::VARCHAR, max(timestamp)::VARCHAR "
        "FROM ds GROUP BY site").fetchall()}

    site_entries = []
    for site, slug in slugs.items():
        variables: dict[str, dict] = {}
        for s, st, var, n, unit in var_rows:
            if s != site:
                continue
            label, units = _units(st, var, unit)
            v = variables.setdefault(var, {"name": var, "label": label,
                                           "units": units, "rows": 0})
            v["rows"] += n
        files = []
        for (sl, month), path in sorted(written.items()):
            if sl != slug:
                continue
            files.append({"month": month,
                          "path": f"data/{name}/{slug}/{month}.csv.gz",
                          "rows": expected[(sl, month)],
                          "bytes": (ddir / slug / f"{month}.csv.gz").stat().st_size})
        site_entries.append({
            "site": site, "label": SITE_LABELS.get(site, site), "slug": slug,
            "first": spans[site][0], "last": spans[site][1],
            "variables": sorted(variables.values(), key=lambda v: v["label"].lower()),
            "files": files,
        })

    header = [c for c, in con.execute(
        "SELECT column_name FROM (DESCRIBE ds)").fetchall() if c != "_month"]
    first = min(e["first"] for e in site_entries) if site_entries else None
    last = max(e["last"] for e in site_entries) if site_entries else None
    return {
        "name": name, "label": LABELS.get(name, name),
        "group": GROUP.get(name, "Beyond TEON"),
        "description": spec["description"], "teon": spec["teon"],
        "source": SOURCES.get(name, ""),
        "columns": header, "first": first, "last": last,
        "rows": sum(expected.values()),
        "bytes": sum(f["bytes"] for e in site_entries for f in e["files"]),
        "sites": site_entries,
    }


def build(*, out: Path = WEB_DATA_DIR, processed_dir: Path = PROCESSED_DIR,
          reference_dir: Path = REFERENCE_DIR, disabled: set[str] | None = None,
          visibility_source: str | None = None, live: bool = True,
          now: datetime | None = None) -> dict:
    """Write web/data/ and its catalog. Returns the catalog."""
    import duckdb

    now = now or datetime.now(timezone.utc)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    # USGS timestamps carry a zone; show them in Pacific like everything else.
    con.execute("SET TimeZone = 'America/Los_Angeles'")
    # One file per site-month: by default DuckDB keeps 100 partition files
    # open and starts a second file for a partition it had to close.
    for setting in ("partitioned_write_max_open_files = 100000",
                    "partitioned_write_flush_threshold = 100000000"):
        try:
            con.execute(f"SET {setting}")
        except duckdb.Error:                 # older DuckDB: the check below still holds
            log.warning("webdata: could not SET %s", setting)

    left_out: dict[str, str] = {}
    teon_ok = True
    if disabled is None:
        try:
            disabled, visibility_source = export.load_disabled(
                live=live, reference_dir=reference_dir, now=now)
        except export.ExportRefused as exc:
            teon_ok = False
            disabled = set()
            visibility_source = None
            reason = f"TEON's list of hidden sites couldn't be read ({exc})"
            log.warning("webdata: %s; leaving the TEON datasets out", reason)
            for spec in export.DATASETS:
                if spec["teon"]:
                    left_out[spec["name"]] = reason

    obs_glob = export._glob(processed_dir, "observations")
    if teon_ok and obs_glob:
        export._prepare_observations(con, obs_glob, disabled)

    datasets = []
    for spec in export.DATASETS:
        name = spec["name"]
        if name in left_out:
            shutil.rmtree(out / name, ignore_errors=True)
            continue
        glob = obs_glob if spec["table"] == "observations" else \
            export._glob(processed_dir, spec["table"])
        select = _select(con, spec, glob) if (glob or spec["table"] == "observations") and \
            (spec["table"] != "observations" or obs_glob) else None
        if select is None:
            left_out[name] = "no data in the store"
            continue
        datasets.append(_write_dataset(con, spec, select, out, disabled))
        log.info("webdata: %s, %s rows, %.1f MB", name, f"{datasets[-1]['rows']:,}",
                 datasets[-1]["bytes"] / 1e6)

    flags = {k: v for k, v in export.QUALITY_FLAGS.items()}
    catalog = {
        "version": CATALOG_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "timestamps": ("Naive Pacific local time (no zone, daylight saving not "
                       "marked), except USGS, which carries its UTC offset."),
        "visibility": visibility_source,
        "redistribution": REDISTRIBUTION,
        "disclaimer": _download_disclaimer(),
        "teon_acknowledgement": export.TEON_ACK,
        "quality_flags": flags,
        "data_dictionary": "https://github.com/bdgroves/secchi/blob/main/docs/data-dictionary.md",
        "datasets": datasets,
        "left_out": left_out,
    }
    tmp = out / "catalog.json.tmp"
    tmp.write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out / "catalog.json")
    return catalog


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--offline-visibility", action="store_true",
                   help="read TEON's hidden-site list from the watcher's baseline "
                        "(only while under 48 h old) instead of asking TEON")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cat = build(live=not args.offline_visibility)
    total = sum(d["bytes"] for d in cat["datasets"])
    files = sum(len(s["files"]) for d in cat["datasets"] for s in d["sites"])
    print(f"web/data: {len(cat['datasets'])} datasets, {files} files, {total / 1e6:.0f} MB")
    for name, why in cat["left_out"].items():
        print(f"  left out {name}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
