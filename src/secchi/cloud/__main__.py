"""Move secchi into SQL Server and prove the copy matches.

    python -m secchi.cloud inventory   # docs/cloud/inventory.md + cloud/out/fingerprints.json
    python -m secchi.cloud export      # cloud/out/bcp/*.txt, ready for bcp
    python -m secchi.cloud load        # 01_schema.sql, bcp every file, 02_after_load.sql
    python -m secchi.cloud reconcile   # SQL Server's fingerprints vs the store's
    python -m secchi.cloud all         # the four in order

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
RECONCILE_MD = OUT / "reconcile.md"

# Field separator in the bcp files. Export refuses to write any text that
# contains it (or a line break), rather than quoting: bcp's character
# format has no quoting, so a stray separator would shift every column
# after it.
SEP = "|"

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


def check_keys_ascii(con) -> None:
    """MD5 is taken over bytes. Both sides agree byte for byte only if the
    keys are plain ASCII, so check rather than assume."""
    for t in TABLES:
        if t.utf8:
            continue
        n = con.sql(f"SELECT count(*) FROM {t.view} "
                    f"WHERE NOT regexp_full_match({t.key_duck}, '[\\x20-\\x7e]*')").fetchone()[0]
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
    prints = store_fingerprints(con)
    OUT.mkdir(parents=True, exist_ok=True)
    FINGERPRINTS.write_text(json.dumps(
        {"made": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "rows": prints,
         "site_watershed": expected_site_watershed(con)},
        indent=1, default=str), encoding="utf-8")

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

    obs_dims = {k: con.sql(f"SELECT count(*) FROM ({v})").fetchone()[0]
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
        rel = con.sql(sql)
        text_cols = [c for c, ty in zip(rel.columns, rel.types, strict=True)
                     if str(ty) == "VARCHAR"]
        for c in text_cols:
            bad = con.sql(f"SELECT count(*) FROM ({sql}) WHERE \"{c}\" LIKE '%{SEP}%' "
                          f"OR \"{c}\" LIKE '%' || chr(10) || '%' "
                          f"OR \"{c}\" LIKE '%' || chr(13) || '%' "
                          + ("" if utf8 else
                             f"OR NOT regexp_full_match(\"{c}\", '[\\x20-\\x7e]*')")
                          ).fetchone()[0]
            if bad:
                raise SystemExit(f"{table}.{c}: {bad} values contain '{SEP}', a line break "
                                 "or (in an ASCII-only table) non-ASCII text; bcp's "
                                 "character format can't carry them")
        path = BCP_DIR / f"{table}.txt"
        con.execute(f"COPY ({sql}) TO '{path.as_posix()}' "
                    f"(FORMAT CSV, DELIMITER '{SEP}', HEADER false, QUOTE '', ESCAPE '', "
                    f"NULLSTR '', TIMESTAMPFORMAT '%Y-%m-%d %H:%M:%S')")
        n = con.sql(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        manifest.append({"table": table, "file": path.name, "rows": n, "utf8": utf8,
                         "bytes": path.stat().st_size})
        print(f"  {table:<16} {n:>12,} rows  {path.stat().st_size / 1e6:8.1f} MB")
    (BCP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"export: {len(manifest)} files in {BCP_DIR.relative_to(REPO_ROOT)}, "
          f"{time.perf_counter() - t0:.1f}s")
    return 0


# ---------------------------------------------------------------------------
# SQL Server side
# ---------------------------------------------------------------------------

def _conn() -> dict:
    need = ["SECCHI_SQL_SERVER", "SECCHI_SQL_DATABASE", "SECCHI_SQL_USER", "SECCHI_SQL_PASSWORD"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise SystemExit("set " + ", ".join(missing) + " first (see docs/cloud.md)")
    for tool in ("sqlcmd", "bcp"):
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
    c = _conn()
    manifest = json.loads((BCP_DIR / "manifest.json").read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    print(f"load: {c['server']} / {c['db']}")
    sqlcmd(c, "-i", str(SQL_DIR / "01_schema.sql"))
    for m in manifest:
        t1 = time.perf_counter()
        err = OUT / f"bcp-{m['table']}.err"
        cmd = ["bcp", f"secchi.{m['table']}", "in", str(BCP_DIR / m["file"]),
               "-S", c["server"], "-d", c["db"], "-U", c["user"], "-P", c["password"],
               "-c", "-t", SEP, "-r", "0x0a", "-b", "100000", "-h", "TABLOCK",
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
    sqlcmd(c, "-i", str(SQL_DIR / "02_after_load.sql"))
    print(f"load: done in {time.perf_counter() - t0:.0f}s")
    return 0


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
    """One result per table-month. Counts, keys, times and min/max must be
    equal; the 4-place sums may differ by rounding at most 0.0001 per row
    (expected: 0)."""
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
            if abs(sa - sb) > Decimal("0.0001") * int(a["n_rows"]):
                problems.append(f"v_sum {sa} vs {sb}")
            elif sa != sb:
                problems.append(f"note: v_sum differs by {sa - sb} (within rounding)")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m secchi.cloud", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["inventory", "export", "load", "reconcile", "all"])
    args = ap.parse_args(argv)
    steps = {"inventory": inventory, "export": export, "load": load, "reconcile": reconcile}
    if args.step == "all":
        for name in ("inventory", "export", "load", "reconcile"):
            rc = steps[name](args)
            if rc:
                return rc
        return 0
    return steps[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
