"""Move secchi into SQL Server and prove the copy matches.

    python -m secchi.cloud inventory   # docs/cloud/inventory.md + cloud/out/fingerprints.json
    python -m secchi.cloud export      # cloud/out/bcp/*.txt, ready for bcp
    python -m secchi.cloud load        # 01_schema.sql, bcp every file, 02_after_load.sql
    python -m secchi.cloud load --loader dotnet
                                       # the same, with C#'s SqlBulkCopy instead of bcp
                                       # (dotnet-batches: the first, DataTable version)
    python -m secchi.cloud reconcile   # SQL Server's fingerprints vs the store's
    python -m secchi.cloud all         # the four in order
    python -m secchi.cloud sync        # keep a copy current: reload only what differs,
                                       # refresh the quality views, check, log the run

The connection comes from the environment, never from a file in the repo:

    SECCHI_SQL_SERVER     localhost, or yourserver.database.windows.net
    SECCHI_SQL_DATABASE   the database name
    SECCHI_SQL_USER       a SQL login
    SECCHI_SQL_PASSWORD   its password
    SECCHI_SQL_TRUST_CERT 1 for a local server with a self-signed certificate

sqlcmd and bcp (Microsoft's ODBC command-line tools, version 18) must be on
PATH. They are the standard way to move rows in and out of SQL Server, and
using them keeps database drivers out of the pixi environment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from secchi.cloud.tables import (
    DIMENSIONS,
    REFERENCE_PLAN,
    TABLES,
    fingerprint_duck,
    fingerprint_tsql,
)
from secchi.config import PROCESSED_DIR, REFERENCE_DIR, REPO_ROOT

OUT = REPO_ROOT / "cloud" / "out"
BCP_DIR = OUT / "bcp"
SQL_DIR = REPO_ROOT / "cloud" / "sql"
INVENTORY_MD = REPO_ROOT / "docs" / "cloud" / "inventory.md"
FINGERPRINTS = OUT / "fingerprints.json"
DOTNET_CLI = REPO_ROOT / "cloud" / "dotnet" / "Secchi.Cloud.Cli"
DOTNET_DLL = DOTNET_CLI / "bin" / "Release" / "net8.0" / "secchi-cloud.dll"
RECONCILE_MD = OUT / "reconcile.md"

# Field separator in the bcp files. Export refuses to write any text that
# contains it (or a line break), rather than quoting: bcp's character
# format has no quoting, so a stray separator would shift every column
# after it.
SEP = "|"

# The files end lines with a bare line feed. Windows bcp reads "\n" as
# CR+LF and needs the hex form; Linux bcp doesn't take the hex form and reads
# "\n" as a line feed.
ROW_END = "0x0a" if os.name == "nt" else "\\n"

FOLDERS = {"obs": "observations", "usgs": "usgs_observations",
           "snotel": "snotel_observations", "smoke": "smoke_observations",
           "asos": "asos_observations", "assets": "assets"}


# ---------------------------------------------------------------------------
# DuckDB side
# ---------------------------------------------------------------------------

def ring_wkt(ring) -> str:
    return "(" + ", ".join(f"{x!r} {y!r}" for x, y, *_ in ring) + ")"


def geojson_wkt(geom: dict) -> str:
    """Polygon or MultiPolygon GeoJSON as WKT, coordinates as stored (lon lat)."""
    if geom["type"] == "Polygon":
        return "POLYGON (" + ", ".join(ring_wkt(r) for r in geom["coordinates"]) + ")"
    if geom["type"] == "MultiPolygon":
        return "MULTIPOLYGON (" + ", ".join(
            "(" + ", ".join(ring_wkt(r) for r in poly) + ")"
            for poly in geom["coordinates"]) + ")"
    raise ValueError(f"unsupported geometry {geom['type']}")


def watersheds_wgs84(path: Path = REFERENCE_DIR / "tahoe_watersheds.geojson") -> dict:
    """The catchments in degrees. The file is Web Mercator metres; this uses
    the same conversion as the catchment join, so both see one geometry."""
    from secchi.sources.reference import reproject_geojson

    return reproject_geojson(json.loads(path.read_text(encoding="utf-8")))


def watershed_rows() -> list[tuple]:
    data = watersheds_wgs84()
    rows = []
    for f in data["features"]:
        p = f["properties"]
        rows.append((int(p["Id"]), p.get("Name"),
                     float(p["Area_Km"]) if p.get("Area_Km") is not None else None,
                     geojson_wkt(f["geometry"])))
    return rows


def duck():
    """The query shell's connection, plus the watershed table."""
    import pandas as pd

    from secchi.query import connect

    con = connect()
    con.execute("SET enable_progress_bar = false")
    frame = pd.DataFrame(watershed_rows(),
                         columns=["watershed_id", "name", "area_km", "wkt"])
    con.register("_ws", frame)
    con.execute("CREATE TABLE watershed AS SELECT * FROM _ws")
    con.unregister("_ws")
    return con


def expected_site_watershed(con) -> dict[str, int | None]:
    """Which catchment each obs site is in, by secchi's own Python join."""
    from secchi.sources.reference import catchment_for_point

    polys = watersheds_wgs84()
    out = {}
    for site, lat, lng in con.sql("SELECT site, any_value(lat), any_value(lng) "
                                  "FROM obs GROUP BY site ORDER BY site").fetchall():
        f = catchment_for_point(lat, lng, polys)
        out[site] = int(f["properties"]["Id"]) if f else None
    return out


def store_fingerprints(con) -> list[dict]:
    rows = []
    for t in TABLES:
        rel = con.sql(fingerprint_duck(t))
        cols = rel.columns
        for r in rel.fetchall():
            rows.append(dict(zip(cols, r, strict=True)))
    return rows


def save_fingerprints(con) -> list[dict]:
    """Fingerprint the store and keep it where reconcile reads it."""
    prints = store_fingerprints(con)
    OUT.mkdir(parents=True, exist_ok=True)
    FINGERPRINTS.write_text(json.dumps(
        {"made": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "rows": prints,
         "site_watershed": expected_site_watershed(con)},
        indent=1, default=str), encoding="utf-8")
    return prints


def check_keys_ascii(con) -> None:
    """MD5 is taken over bytes. Both sides agree byte for byte only if the
    keys are plain ASCII, so check rather than assume."""
    for t in TABLES:
        if t.utf8:
            continue
        n = con.sql(f"SELECT count(*) FROM {t.view} "
                    f"WHERE NOT regexp_full_match({t.key_duck}, '[\\x20-\\x7e]*')").fetchall()[0][0]
        if n:
            raise SystemExit(f"{t.name}: {n} keys contain non-ASCII text; "
                             "the fingerprint would not match SQL Server's")


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def _size(folder: Path) -> tuple[int, int]:
    files = list(folder.rglob("part*.parquet"))
    return len(files), sum(f.stat().st_size for f in files)


def inventory(args) -> int:
    t0 = time.perf_counter()
    con = duck()
    check_keys_ascii(con)
    prints = save_fingerprints(con)

    by_table: dict[str, list[dict]] = {}
    for p in prints:
        by_table.setdefault(p["tbl"], []).append(p)

    today = dt.date.today().isoformat()
    lines = [
        "# secchi inventory",
        "",
        f"*Generated {today} by `pixi run cloud-inventory`. Regenerate rather than edit.*",
        "",
        "Step one of moving secchi into SQL Server and Azure SQL Database: everything",
        "that exists, how big it is, what feeds it, what reads it, and whether it",
        "moves. The month-by-month fingerprints written alongside",
        "(`cloud/out/fingerprints.json`) are what the database copy is checked",
        "against. See [docs/cloud.md](../cloud.md).",
        "",
        "## Tables that move",
        "",
        "| SQL table | Rows | Months | Time span | Store files | On disk | Fed by | Read by |",
        "|---|---:|---:|---|---:|---:|---|---|",
    ]
    total_rows = 0
    for t in TABLES:
        ps = by_table.get(t.name, [])
        n = sum(int(p["n_rows"]) for p in ps)
        total_rows += n
        mins = [p["ts_min"] for p in ps if p["ts_min"]]
        maxs = [p["ts_max"] for p in ps if p["ts_max"]]
        span = f"{min(mins)[:10]} to {max(maxs)[:10]}" if mins else "n/a"
        folder = FOLDERS.get(t.name)
        if folder:
            nf, nb = _size(PROCESSED_DIR / folder)
            files, size = str(nf), f"{nb / 1e6:,.1f} MB"
        else:
            files, size = "1 file", "reference"
        months = str(len(ps)) if t.monthly else "n/a"
        lines.append(f"| `secchi.{t.name}` | {n:,} | {months} | {span} | {files} | {size} "
                     f"| {t.fed_by} | {t.read_by} |")
    lines += ["", f"**{total_rows:,} rows in {len(TABLES)} tables.**", "",
              "## What each table is", ""]
    for t in TABLES:
        cols = con.sql(f"DESCRIBE {t.view}").fetchall()
        colnames = ", ".join(f"`{c[0]}` {c[1].lower()}" for c in cols
                             if c[0] not in ("year", "month"))
        lines += [f"**`secchi.{t.name}`**: {t.about}", "", f"Columns in the store: {colnames}.", ""]

    obs_dims = {k: con.sql(f"SELECT count(*) FROM ({v})").fetchall()[0][0]
                for k, v in DIMENSIONS.items()}
    lines += [
        "## Lookup tables",
        "",
        f"`secchi.obs` stores numbers instead of names: {obs_dims['site']} sites, "
        f"{obs_dims['sensor_type']} sensor types and {obs_dims['variable']} variables "
        "go into `secchi.site`, `secchi.sensor_type` and `secchi.variable`. "
        "`secchi.site` also gets a geography point for each station.",
        "",
        "## Reference files",
        "",
        "| File | Size | Plan |",
        "|---|---:|---|",
    ]
    for f in sorted(REFERENCE_DIR.iterdir()):
        if f.is_file():
            plan = REFERENCE_PLAN.get(f.name, "not yet decided")
            lines.append(f"| `{f.name}` | {f.stat().st_size / 1e3:,.1f} KB | {plan} |")
    lines += [
        "",
        "Not everything moves. Files that hold the app's own bookkeeping stay",
        "with the app; moving them would only make a second copy to keep in step.",
        "",
        "## Months, one line each",
        "",
        "| Table | Month | Rows | Distinct keys | First | Last |",
        "|---|---|---:|---:|---|---|",
    ]
    for p in prints:
        lines.append(f"| {p['tbl']} | {p['part']} | {int(p['n_rows']):,} | {int(p['n_keys']):,} "
                     f"| {(p['ts_min'] or '')[:19]} | {(p['ts_max'] or '')[:19]} |")
    INVENTORY_MD.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dup = [p for p in prints if int(p["n_rows"]) != int(p["n_keys"])]
    print(f"inventory: {total_rows:,} rows in {len(TABLES)} tables, {len(prints)} table-months, "
          f"{time.perf_counter() - t0:.1f}s")
    print(f"  wrote {INVENTORY_MD.relative_to(REPO_ROOT)} and {FINGERPRINTS.relative_to(REPO_ROOT)}")
    if dup:
        print(f"  note: {len(dup)} table-months have repeated keys: "
              + ", ".join(f"{p['tbl']} {p['part']}" for p in dup[:5]))
    return 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export(args) -> int:
    t0 = time.perf_counter()
    con = duck()
    check_keys_ascii(con)
    if BCP_DIR.exists():
        shutil.rmtree(BCP_DIR)
    BCP_DIR.mkdir(parents=True)
    for name, sql in DIMENSIONS.items():
        con.execute(f"CREATE TABLE dim_{name} AS {sql}")

    jobs = [(f"{name}", f"SELECT * FROM dim_{name} ORDER BY 1", False) for name in DIMENSIONS]
    jobs += [(t.name if t.name != "watershed" else "watershed_stage", t.export_sql, t.utf8)
             for t in TABLES]
    manifest = []
    for table, sql, utf8 in jobs:
        path = BCP_DIR / f"{table}.txt"
        n = write_bcp_file(con, table, sql, path, utf8)
        manifest.append({"table": table, "file": path.name, "rows": n, "utf8": utf8,
                         "bytes": path.stat().st_size})
        print(f"  {table:<16} {n:>12,} rows  {path.stat().st_size / 1e6:8.1f} MB")
    (BCP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"export: {len(manifest)} files in {BCP_DIR.relative_to(REPO_ROOT)}, "
          f"{time.perf_counter() - t0:.1f}s")
    return 0


def write_bcp_file(con, table: str, sql: str, path: Path, utf8: bool) -> int:
    """Write one query's rows as a bcp character file; return the row count.

    Refuses text bcp can't carry rather than quoting it (see SEP).
    """
    rel = con.sql(sql)
    text_cols = [c for c, ty in zip(rel.columns, rel.types, strict=True)
                 if str(ty) == "VARCHAR"]
    for c in text_cols:
        bad = con.sql(f"SELECT count(*) FROM ({sql}) WHERE \"{c}\" LIKE '%{SEP}%' "
                      f"OR \"{c}\" LIKE '%' || chr(10) || '%' "
                      f"OR \"{c}\" LIKE '%' || chr(13) || '%' "
                      + ("" if utf8 else
                         f"OR NOT regexp_full_match(\"{c}\", '[\\x20-\\x7e]*')")
                      ).fetchall()[0][0]
        if bad:
            raise SystemExit(f"{table}.{c}: {bad} values contain '{SEP}', a line break "
                             "or (in an ASCII-only table) non-ASCII text; bcp's "
                             "character format can't carry them")
    con.execute(f"COPY ({sql}) TO '{path.as_posix()}' "
                f"(FORMAT CSV, DELIMITER '{SEP}', HEADER false, QUOTE '', ESCAPE '', "
                f"NULLSTR '', TIMESTAMPFORMAT '%Y-%m-%d %H:%M:%S')")
    return con.sql(f"SELECT count(*) FROM ({sql})").fetchall()[0][0]


# ---------------------------------------------------------------------------
# SQL Server side
# ---------------------------------------------------------------------------

def _conn(tools: tuple[str, ...] = ("sqlcmd", "bcp")) -> dict:
    need = ["SECCHI_SQL_SERVER", "SECCHI_SQL_DATABASE", "SECCHI_SQL_USER", "SECCHI_SQL_PASSWORD"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise SystemExit("set " + ", ".join(missing) + " first (see docs/cloud.md)")
    for tool in tools:
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH (see docs/cloud.md)")
    return {"server": os.environ["SECCHI_SQL_SERVER"],
            "db": os.environ["SECCHI_SQL_DATABASE"],
            "user": os.environ["SECCHI_SQL_USER"],
            "password": os.environ["SECCHI_SQL_PASSWORD"],
            "trust": os.environ.get("SECCHI_SQL_TRUST_CERT") == "1"}


def sqlcmd(c: dict, *args: str, capture: bool = False) -> str:
    cmd = ["sqlcmd", "-S", c["server"], "-d", c["db"], "-U", c["user"], "-b", "-I"]
    if c["trust"]:
        cmd.append("-C")
    env = dict(os.environ, SQLCMDPASSWORD=c["password"])   # keeps it off the command line
    r = subprocess.run(cmd + list(args), env=env, text=True,
                       capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"sqlcmd failed ({r.returncode})")
    if not capture and r.stdout.strip():
        print(r.stdout.rstrip())
    return r.stdout


def load(args) -> int:
    loader = getattr(args, "loader", "bcp")
    c = _conn(("sqlcmd", "bcp") if loader == "bcp" else ("sqlcmd", "dotnet"))
    manifest = json.loads((BCP_DIR / "manifest.json").read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    print(f"load: {c['server']} / {c['db']}, with {loader}")
    sqlcmd(c, "-i", str(SQL_DIR / "01_schema.sql"))
    if loader == "bcp":
        for m in manifest:
            bcp_in(c, m["table"], BCP_DIR / m["file"], m["rows"], m.get("utf8", False))
    else:
        dotnet(["load"] + (["--batches"] if loader == "dotnet-batches" else []))
    print(f"load: rows copied in {time.perf_counter() - t0:.0f}s")
    sqlcmd(c, "-i", str(SQL_DIR / "02_after_load.sql"))
    apply_quality(c)
    print(f"load: done in {time.perf_counter() - t0:.0f}s")
    return 0


def dotnet(args: list[str]) -> None:
    """Run the C# tool (cloud/dotnet), which reads the same SECCHI_SQL_* variables.
    Build it first: dotnet build -c Release cloud/dotnet/Secchi.Cloud.Cli"""
    if not DOTNET_DLL.exists():
        raise SystemExit(f"{DOTNET_DLL.relative_to(REPO_ROOT)} not built; run "
                         "dotnet build -c Release cloud/dotnet/Secchi.Cloud.Cli")
    r = subprocess.run(["dotnet", str(DOTNET_DLL), *args], cwd=REPO_ROOT)
    if r.returncode != 0:
        raise SystemExit(f"secchi-cloud {' '.join(args)} failed ({r.returncode})")


def bcp_in(c: dict, table: str, path: Path, expected: int, utf8: bool) -> int:
    """Bulk-copy one file into secchi.<table>; stop unless every row arrived."""
    m = {"table": table, "rows": expected, "utf8": utf8}
    t1 = time.perf_counter()
    err = OUT / f"bcp-{m['table']}.err"
    cmd = ["bcp", f"secchi.{m['table']}", "in", str(path),
           "-S", c["server"], "-d", c["db"], "-U", c["user"], "-P", c["password"],
           "-c", "-t", SEP, "-r", ROW_END, "-b", "100000", "-h", "TABLOCK",
           "-m", "1", "-e", str(err)]
    if c["trust"]:
        cmd.append("-u")
    if m.get("utf8") and os.name == "nt":
        cmd += ["-C", "65001"]          # Windows bcp reads OEM text unless told; Linux is UTF-8
    r = subprocess.run(cmd, text=True, capture_output=True)
    copied = re.search(r"(\d+) rows copied", r.stdout)
    n = int(copied.group(1)) if copied else -1
    print(f"  {m['table']:<16} {n:>12,} rows  {time.perf_counter() - t1:6.1f}s")
    if r.returncode != 0 or n != m["rows"]:
        sys.stderr.write(r.stdout[-3000:].replace(c["password"], "***") + r.stderr)
        if err.exists():
            sys.stderr.write(err.read_text(errors="replace")[:3000])
        raise SystemExit(f"bcp {m['table']}: expected {m['rows']:,} rows, got {n:,}")
    return n


def server_fingerprints(c: dict) -> list[dict]:
    sql = "SET NOCOUNT ON;\n" + "\nUNION ALL\n".join(fingerprint_tsql(t) for t in TABLES) + ";"
    qfile = OUT / "fingerprint.sql"
    qfile.write_text(sql, encoding="utf-8")
    out = sqlcmd(c, "-i", str(qfile), "-h", "-1", "-W", "-s", SEP, capture=True)
    cols = ["tbl", "part", "n_rows", "n_keys", "key_hash", "ts_min", "ts_max",
            "v_min", "v_max", "v_sum", "v_null"]
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(SEP)
        if len(parts) != len(cols):
            continue
        rows.append({k: (None if v == "NULL" else v) for k, v in zip(cols, parts, strict=True)})
    return rows


def _ts(s):
    return dt.datetime.fromisoformat(s) if s else None


def _f(s):
    return float(s) if s is not None else None


def compare(store: list[dict], server: list[dict]) -> list[dict]:
    """One result per table-month. Everything must be equal.

    History (2026-10-21): the value check was first a sum of values rounded
    to 4 places, with an allowance of 0.0001 per row for rounding. The
    sync's sabotage test (cloud.yml) showed the allowance added up to 6 over
    a month of USGS readings, so a reading changed by 1 passed. Removing it
    showed the 4-place sums had never matched exactly: the two engines round
    binary floats to decimals differently in rare cases. The check is now a
    sum of floor(value x 1000), which both compute identically."""
    s_map = {(r["tbl"], r["part"]): r for r in store}
    d_map = {(r["tbl"], r["part"]): r for r in server}
    results = []
    for key in sorted(set(s_map) | set(d_map), key=lambda k: (k[0], k[1] or "")):
        a, b = s_map.get(key), d_map.get(key)
        problems = []
        if a is None or b is None:
            problems.append("missing in " + ("store" if a is None else "database"))
        else:
            for m in ("n_rows", "n_keys", "key_hash", "v_null"):
                if int(a[m] or 0) != int(b[m] or 0):
                    problems.append(f"{m} {a[m]} vs {b[m]}")
            for m in ("ts_min", "ts_max"):
                if _ts(a[m]) != _ts(b[m]):
                    problems.append(f"{m} {a[m]} vs {b[m]}")
            for m in ("v_min", "v_max"):
                if _f(a[m]) != _f(b[m]):
                    problems.append(f"{m} {a[m]} vs {b[m]}")
            sa, sb = Decimal(a["v_sum"] or 0), Decimal(b["v_sum"] or 0)
            if sa != sb:
                problems.append(f"v_sum {sa} vs {sb}")
        hard = [p for p in problems if not p.startswith("note:")]
        results.append({"tbl": key[0], "part": key[1],
                        "rows": int((a or b)["n_rows"]),
                        "ok": not hard, "problems": problems})
    return results


def compare_watersheds(expected: dict, c: dict) -> dict:
    """The spatial check: SQL Server's STContains against the Python join."""
    out = sqlcmd(c, "-Q", "SET NOCOUNT ON; SELECT site, ISNULL(CAST(watershed_id AS varchar(10)), "
                 "'NULL') FROM secchi.site_watershed ORDER BY site",
                 "-h", "-1", "-W", "-s", SEP, capture=True)
    got = {}
    for line in out.splitlines():
        if SEP in line:
            site, wid = line.strip().split(SEP)
            got[site] = None if wid == "NULL" else int(wid)
    diffs = {s: (expected.get(s), got.get(s)) for s in sorted(set(expected) | set(got))
             if expected.get(s) != got.get(s)}
    return {"sites": len(expected), "in_a_catchment": sum(v is not None for v in got.values()),
            "differences": diffs}


def reconcile(args) -> int:
    c = _conn()
    saved = json.loads(FINGERPRINTS.read_text(encoding="utf-8"))
    store = saved["rows"]
    t0 = time.perf_counter()
    server = server_fingerprints(c)
    results = compare(store, server)
    spatial = compare_watersheds(saved["site_watershed"], c)
    bad = [r for r in results if not r["ok"]]
    rows = sum(r["rows"] for r in results)
    lines = ["# Reconcile: store vs SQL Server", "",
             f"*{dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC, server `{c['server']}`.*", "",
             f"**{len(results) - len(bad)} of {len(results)} table-months match "
             f"({rows:,} rows).**", "",
             f"Spatial check: SQL Server places {spatial['in_a_catchment']} of {spatial['sites']} "
             f"TEON sites in a catchment; differences from secchi's own join: "
             f"{len(spatial['differences']) or 'none'}.", "",
             "| Table | Month | Rows | Result |", "|---|---|---:|---|"]
    for r in results:
        lines.append(f"| {r['tbl']} | {r['part']} | {r['rows']:,} | "
                     + ("MATCH" if r["ok"] else "**DIFFERENT**")
                     + ("" if not r["problems"] else ": " + "; ".join(r["problems"])) + " |")
    RECONCILE_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "reconcile.json").write_text(json.dumps({"months": results, "spatial": spatial},
                                                   indent=1), encoding="utf-8")
    print(f"reconcile: {len(results) - len(bad)} of {len(results)} table-months match, "
          f"{rows:,} rows, {time.perf_counter() - t0:.0f}s")
    print(f"  spatial: {spatial['in_a_catchment']} of {spatial['sites']} sites in a catchment, "
          f"{len(spatial['differences'])} differences from the Python join")
    for r in bad[:20]:
        print(f"  DIFFERENT {r['tbl']} {r['part']}: {'; '.join(r['problems'])}")
    for s, (want, got) in spatial["differences"].items():
        print(f"  DIFFERENT site {s}: Python says {want}, SQL Server says {got}")
    return 1 if bad or spatial["differences"] else 0


# ---------------------------------------------------------------------------
# sync: keep an existing copy current, and log every run
# ---------------------------------------------------------------------------

# More differing table-months than this and a full reload is simpler and
# about as fast.
MAX_MONTHS = 24

RUN_LOG_SQL = """SET NOCOUNT ON;
IF OBJECT_ID('secchi.run_log') IS NULL
CREATE TABLE secchi.run_log (
    run_id          int IDENTITY PRIMARY KEY,
    run_utc         datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    kind            varchar(20)  NOT NULL,     -- full, months, none
    detail          varchar(4000) NULL,        -- which table-months were reloaded
    rows_loaded     bigint NULL,
    months_checked  int NULL,
    months_matched  int NULL,
    flags_matched   bit NULL,
    result          varchar(20)  NOT NULL,     -- MATCH or DIFFERENT
    seconds         int NULL,
    commit_sha      varchar(40) NULL,
    free_vcore_s_remaining float NULL          -- filled by the workflow, from Azure Monitor
);"""


def _query(c: dict, sql: str) -> list[list[str]]:
    out = sqlcmd(c, "-Q", "SET NOCOUNT ON; " + sql, "-h", "-1", "-W", "-s", SEP, capture=True)
    return [line.strip().split(SEP) for line in out.splitlines() if line.strip()]


def _q(v) -> str:
    """A T-SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def month_bounds(part: str) -> tuple[str, str]:
    """'2026-02' -> ('2026-02-01', '2026-03-01')."""
    y, m = (int(x) for x in part.split("-"))
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}-01", f"{y2:04d}-{m2:02d}-01"


def plan_sync(results: list[dict]) -> tuple[str, list[dict]]:
    """Decide what a sync does from the reconcile results: nothing, reload
    some table-months, or reload everything."""
    diffs = [r for r in results if not r["ok"]]
    if not diffs:
        return "none", []
    if any(r["tbl"] == "watershed" for r in diffs) or len(diffs) > MAX_MONTHS:
        return "full", diffs
    return "months", diffs


def _sync_dimensions(c: dict, con) -> None:
    """Make DuckDB's lookup tables the server's, adding any new names to both.

    Ids must never change once loaded: the server's ids are the truth, and a
    new site or variable gets the next number.
    """
    for name, cols in (("site", "site_id, site, lat, lng"),
                       ("sensor_type", "sensor_type_id, sensor_type"),
                       ("variable", "variable_id, variable")):
        rows = _query(c, f"SELECT {cols} FROM secchi.{name} ORDER BY 1")
        have = {r[1]: int(r[0]) for r in rows}
        con.execute(f"DROP TABLE IF EXISTS dim_{name}")
        if name == "site":
            con.execute("CREATE TABLE dim_site (site_id INTEGER, site VARCHAR, lat DOUBLE, lng DOUBLE)")
            store = con.sql("SELECT site, any_value(lat), any_value(lng) FROM obs GROUP BY site").fetchall()
        else:
            con.execute(f"CREATE TABLE dim_{name} ({name}_id INTEGER, {name} VARCHAR)")
            store = [(r[0],) for r in con.sql(f"SELECT DISTINCT {name} FROM obs").fetchall()]
        for i, n in have.items():
            con.execute(f"INSERT INTO dim_{name} ({name}_id, {name}) VALUES (?, ?)", [n, i])
        next_id = max(have.values(), default=0) + 1
        for row in sorted(store):
            if row[0] in have:
                continue
            values = [next_id, *row]
            con.execute(f"INSERT INTO dim_{name} VALUES ({', '.join('?' * len(values))})", values)
            cols = "site_id, site, lat, lng" if name == "site" else f"{name}_id, {name}"
            sqlcmd(c, "-Q", f"INSERT INTO secchi.{name} ({cols}) "
                            f"VALUES ({', '.join(_q(v) for v in values)})")
            print(f"  new {name}: {row[0]!r} = {next_id}")
            next_id += 1
    sqlcmd(c, "-Q", "SET NOCOUNT ON; UPDATE secchi.site SET location = geography::Point(lat, lng, 4326) "
                    "WHERE location IS NULL")


def reload_parts(c: dict, con, diffs: list[dict]) -> tuple[int, list[str]]:
    """Delete and reload just the table-months that differ."""
    by_name = {t.name: t for t in TABLES}
    _sync_dimensions(c, con)
    slices = OUT / "slices"
    slices.mkdir(parents=True, exist_ok=True)
    total, done = 0, []
    for r in diffs:
        t = by_name[r["tbl"]]
        if r["part"] == "all":
            delete = f"DELETE FROM secchi.{t.name}"
            sql = t.export_sql
        else:
            lo, hi = month_bounds(r["part"])
            # datetimeoffset needs a time before the offset: '2026-09-01 +00:00'
            # doesn't convert, '2026-09-01 00:00:00 +00:00' does
            tz = " 00:00:00 +00:00" if t.name == "usgs" else ""
            delete = f"DELETE FROM secchi.{t.name} WHERE ts >= '{lo}{tz}' AND ts < '{hi}{tz}'"
            sql = (f"SELECT * FROM ({t.export_sql}) AS q "
                   f"WHERE substr(CAST(ts AS VARCHAR), 1, 7) = '{r['part']}'")
        path = slices / f"{t.name}-{r['part']}.txt"
        n = write_bcp_file(con, t.name, sql, path, t.utf8)
        sqlcmd(c, "-Q", "SET NOCOUNT ON; " + delete)
        if n:
            bcp_in(c, t.name, path, n, t.utf8)
        total += n
        done.append(f"{t.name} {r['part']} ({n:,})")
    return total, done


def apply_quality(c: dict) -> None:
    from secchi.cloud.quality import quality_sql

    path = OUT / "03_quality.sql"
    path.write_text(quality_sql(), encoding="utf-8")
    sqlcmd(c, "-i", str(path))


def check_flags(c: dict, con) -> dict:
    """SQL Server's flag counts against DuckDB's, flag by flag."""
    from secchi.cloud.quality import PORTED, flag_counts_duck, flag_counts_tsql

    duck_counts = flag_counts_duck(con)
    path = OUT / "flag_counts.sql"
    path.write_text(flag_counts_tsql(), encoding="utf-8")
    out = sqlcmd(c, "-i", str(path), "-h", "-1", "-W", "-s", SEP, capture=True)
    row = next(line.strip().split(SEP) for line in out.splitlines() if SEP in line)
    server = {f: int(v) for f, v in zip(PORTED, row, strict=True)}
    diffs = {f: (duck_counts[f], server[f]) for f in PORTED if duck_counts[f] != server[f]}
    print("  flags: " + ", ".join(f"{f} {server[f]:,}" for f in PORTED))
    for f, (a, b) in diffs.items():
        print(f"  DIFFERENT flag {f}: DuckDB {a:,}, SQL Server {b:,}")
    return {"duck": duck_counts, "server": server, "differences": diffs}


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
                              capture_output=True).stdout.strip() or None
    except OSError:
        return None


def sync(args) -> int:
    """Bring an existing copy up to date and prove it, then log the run.

    The reconcile decides the work: only table-months whose fingerprints
    differ are reloaded, so revised history is caught as well as new days.
    An empty database gets a full load.
    """
    c = _conn()
    t0 = time.perf_counter()
    con = duck()
    check_keys_ascii(con)
    store = save_fingerprints(con)
    sqlcmd(c, "-Q", RUN_LOG_SQL)
    exists = _query(c, "SELECT CASE WHEN OBJECT_ID('secchi.obs') IS NULL THEN 0 ELSE 1 END")
    if exists and exists[0][0] == "1":
        kind, diffs = plan_sync(compare(store, server_fingerprints(c)))
    else:
        kind, diffs = "full", []
    print(f"sync: {kind}" + (f", {len(diffs)} table-months differ" if diffs else ""))
    rows, detail = 0, []
    if kind == "full":
        export(args)
        load(args)
        rows = sum(m["rows"] for m in json.loads((BCP_DIR / "manifest.json").read_text()))
        detail = ["everything"]
    elif kind == "months":
        rows, detail = reload_parts(c, con, diffs)
        for d in detail:
            print(f"  reloaded {d}")
    apply_quality(c)
    rc = reconcile(args)
    flags = check_flags(c, con)
    results = json.loads((OUT / "reconcile.json").read_text())["months"]
    ok = rc == 0 and not flags["differences"]
    sqlcmd(c, "-Q", "SET NOCOUNT ON; INSERT INTO secchi.run_log (kind, detail, rows_loaded, months_checked, "
           "months_matched, flags_matched, result, seconds, commit_sha) VALUES ("
           + ", ".join(_q(v) for v in (
               kind, "; ".join(detail)[:4000] or None, rows, len(results),
               sum(r["ok"] for r in results), not flags["differences"],
               "MATCH" if ok else "DIFFERENT", int(time.perf_counter() - t0), _git_sha()))
           + ")")
    print(f"sync: {'MATCH' if ok else 'DIFFERENT'}, {rows:,} rows loaded, "
          f"{time.perf_counter() - t0:.0f}s")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m secchi.cloud", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["inventory", "export", "load", "reconcile", "sync", "all"])
    ap.add_argument("--loader", choices=["bcp", "dotnet", "dotnet-batches"], default="bcp",
                    help="load: bcp (default) or C#'s SqlBulkCopy (cloud/dotnet), streaming "
                         "or in DataTable batches")
    args = ap.parse_args(argv)
    steps = {"inventory": inventory, "export": export, "load": load, "reconcile": reconcile,
             "sync": sync}
    if args.step == "all":
        for name in ("inventory", "export", "load", "reconcile"):
            rc = steps[name](args)
            if rc:
                return rc
        return 0
    return steps[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
