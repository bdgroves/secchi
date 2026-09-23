"""Ask questions of the whole record in SQL, without writing a script.

    pixi run query                              # interactive
    pixi run query "SELECT count(*) FROM obs"   # one question, then exit

DuckDB reads the partitioned parquet store **in place** — nothing is
imported, copied or converted, and the store stays exactly as the
pipeline writes it. On a 7.5-million-row store, a filtered aggregate
answers in well under a second, because DuckDB opens only the monthly
partitions the query touches and only the columns it names. Loading the
same data into pandas takes ~650 MB of memory before you've asked
anything.

Tables available on start
-------------------------

``obs``         every TEON observation — one row per reading per variable.
                Columns: uuid, source, site, sensor_type, timestamp, lat,
                lng, variable, value, plus ``year`` and ``month`` read from
                the partition folders.
``usgs``        the USGS gauge observations.
``assets``      camera frame references.
``stations``    one row per mapped location, with the catchment it sits in.
``catchments``  the 60 catchments and their attributes.

Two things DuckDB does silently here, worth knowing
---------------------------------------------------

1. **The folder name overrides the file's ``source`` column.** Each file
   holds ``source = 'TEON'`` and sits under a folder named
   ``source=teon``; DuckDB reports ``'teon'``. Harmless — both mean the
   same thing — but it is the folder you're seeing.

2. **The partition folders are typed inconsistently.** ``year=2026``
   reads as a number but ``month=06`` as text, because of the leading
   zero — so ``month = 9`` works while ``month >= 8`` raises an error.
   This module declares both as integers.

3. **Columns present in only some months would be dropped** unless the
   read uses ``union_by_name``. USGS rows carry an ``approval`` column
   that not every partition has. This module always sets it, so nothing
   disappears; a hand-written ``read_parquet`` elsewhere should too.

None of these raises an error on its own. That's why they're written down.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from secchi.config import PROCESSED_DIR, REFERENCE_DIR, WEB_DIR

# Rows printed before truncating. `.save` writes the full result.
MAX_ROWS = 40

EXAMPLES = [
    ("What's in the store, by sensor type",
     """SELECT sensor_type, count(*) AS readings,
       min(timestamp) AS first, max(timestamp) AS last
FROM obs GROUP BY sensor_type ORDER BY readings DESC;"""),

    ("Monthly mean lake temperature at each EXO site",
     """SELECT site, year, month, round(avg(value), 2) AS temp_c
FROM obs
WHERE sensor_type = 'ExoSensor' AND variable = 'Temp'
GROUP BY ALL ORDER BY site, year, month;"""),

    ("Nearshore dissolved oxygen by month (MiniDOT)",
     """SELECT site, date_trunc('month', timestamp) AS month,
       round(avg(value), 2) AS do_mgl
FROM obs
WHERE sensor_type = 'MiniDotSensor' AND variable = 'Dissolved Oxygen'
GROUP BY ALL ORDER BY site, month;"""),

    ("Logger battery over the last week of data, lowest floor first",
     """SELECT site, round(min(value), 2) AS floor_v,
       round(max(value), 2) AS peak_v
FROM obs
WHERE variable = 'BattV_Avg'
  AND timestamp > (SELECT max(timestamp) FROM obs
                   WHERE variable = 'BattV_Avg') - INTERVAL 7 DAY
GROUP BY site ORDER BY floor_v;"""),

    ("Mean soil moisture by catchment",
     """SELECT s.catchment, round(avg(o.value) * 100, 1) AS soil_pct,
       count(*) AS readings
FROM obs o JOIN stations s USING (site)
WHERE o.variable = 'Soil_VWC'
GROUP BY s.catchment ORDER BY soil_pct DESC;"""),

    ("What columns a table has",
     "DESCRIBE catchments;"),
]

HELP = """\
  Type SQL ending in ;   (it can span several lines)

  .tables              list the tables
  .examples            show example queries
  .run N               run example N
  .save FILE.csv       write the last result, in full, to a CSV file
  .help                this message
  .quit                leave (Ctrl+D or Ctrl+Z also work)
"""


def _glob(name: str) -> str | None:
    """A read_parquet glob for one partitioned table, or None if empty."""
    root = PROCESSED_DIR / name
    if not root.exists() or not any(root.rglob("part*.parquet")):
        return None
    return (root / "**" / "part*.parquet").as_posix()


def _catchments_frame():
    """The catchment attribute table, parsed the way the join parses it."""
    import pandas as pd
    from secchi.sources.reference import _attribute_index, load_reference

    ref = load_reference(REFERENCE_DIR)
    index = _attribute_index(ref.get("attributes") or {})
    if not index:
        return None
    return pd.DataFrame(list(index.values()))


def _stations_frame():
    """Mapped locations with their catchment, from the dashboard snapshot.

    Uses the same point-in-polygon join as ``pixi run catchment-join``,
    so a site's catchment here is the one it gets everywhere else.
    """
    import pandas as pd
    from secchi.sources.reference import join_stations, load_reference

    snap_path = WEB_DIR / "assets" / "latest.json"
    if not snap_path.exists():
        return None
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    points = (snap.get("map") or {}).get("points") or []
    if not points:
        return None

    ref = load_reference(REFERENCE_DIR)
    joined = join_stations(points, ref) if ref.get("polygons") else {}

    rows = []
    for p in points:
        site = p.get("site")
        rows.append({
            "site": site,
            "source": p.get("source"),
            "category": p.get("category"),
            "lat": p.get("lat"),
            "lng": p.get("lng"),
            "collection": p.get("collection"),
            "is_live": p.get("is_live"),
            "sensor_types": ", ".join(p.get("sensor_types") or []),
            "catchment": (joined.get(site) or {}).get("catchment"),
        })
    return pd.DataFrame(rows)


def connect(verbose: bool = False):
    """A DuckDB connection with every table set up.

    Usable from a notebook as well as the shell::

        from secchi.query import connect
        con = connect()
        con.sql("SELECT count(*) FROM obs").df()
    """
    import duckdb

    con = duckdb.connect()
    made: list[str] = []
    skipped: list[str] = []

    for view, folder in (("obs", "observations"),
                         ("usgs", "usgs_observations"),
                         ("assets", "assets")):
        g = _glob(folder)
        if g is None:
            skipped.append(f"{view} (no data in data/processed/{folder})")
            continue
        # union_by_name: without it, a column present in only some
        # partitions is silently dropped. See the module docstring.
        # hive_types: the folders are named month=06, and DuckDB reads a
        # zero-padded value as TEXT while reading year=2026 as a number.
        # Left alone, `month = 9` happens to work but `month >= 8` raises
        # a type error. Declaring both as integers makes them behave.
        con.execute(
            f"CREATE VIEW {view} AS SELECT * FROM read_parquet("
            f"'{g}', hive_partitioning = true, union_by_name = true, "
            f"hive_types = {{'year': INTEGER, 'month': INTEGER}})")
        made.append(view)

    for name, builder in (("stations", _stations_frame),
                          ("catchments", _catchments_frame)):
        try:
            frame = builder()
        except Exception as exc:                    # never block the shell
            frame = None
            skipped.append(f"{name} ({type(exc).__name__}: {exc})")
        if frame is None or frame.empty:
            if not any(s.startswith(name) for s in skipped):
                skipped.append(f"{name} (run `pixi run transform` and "
                               f"`pixi run reference` first)")
            continue
        con.register(f"_{name}_df", frame)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}_df")
        con.unregister(f"_{name}_df")
        made.append(name)

    if verbose:
        # Only what's MISSING — the shell lists the tables itself.
        for s in skipped:
            print(f"  skipped: {s}")
    return con


def _show(con, sql: str):
    """Run one statement, print the result, and return it as a frame."""
    t0 = time.perf_counter()
    rel = con.sql(sql)
    if rel is None:                        # DDL and the like return nothing
        print(f"  ok ({time.perf_counter() - t0:.2f}s)")
        return None
    df = rel.df()
    elapsed = time.perf_counter() - t0
    if df.empty:
        print(f"  (no rows, {elapsed:.2f}s)")
        return df
    with_limit = df.head(MAX_ROWS)
    import pandas as pd
    with pd.option_context("display.max_columns", 50, "display.width", 200,
                           "display.max_colwidth", 60):
        print(with_limit.to_string(index=False))
    tail = (f", showing {MAX_ROWS} — `.save file.csv` for all"
            if len(df) > MAX_ROWS else "")
    print(f"  ({len(df):,} rows, {elapsed:.2f}s{tail})")
    return df


def _repl(con) -> int:
    try:
        import readline  # noqa: F401 — line editing where available
    except ImportError:
        pass

    print("  secchi query shell · DuckDB over the partitioned store")
    connect_summary = con.sql("SELECT table_name FROM information_schema.tables "
                              "ORDER BY table_name").fetchall()
    print(f"  tables: {', '.join(t[0] for t in connect_summary)}")
    print("  .help for commands, .examples for ideas\n")

    buffer: list[str] = []
    last = None
    while True:
        try:
            line = input("secchi> " if not buffer else "   ...> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        stripped = line.strip()
        if not buffer and stripped.startswith("."):
            cmd, _, arg = stripped.partition(" ")
            arg = arg.strip()
            if cmd in (".quit", ".exit", ".q"):
                return 0
            if cmd == ".help":
                print(HELP)
            elif cmd == ".tables":
                for (t,) in con.sql("SELECT table_name FROM "
                                    "information_schema.tables "
                                    "ORDER BY table_name").fetchall():
                    print(f"  {t}")
            elif cmd == ".examples":
                for i, (title, sql) in enumerate(EXAMPLES, 1):
                    print(f"\n  [{i}] {title}\n")
                    print("      " + sql.replace("\n", "\n      "))
                print("\n  .run N to run one\n")
            elif cmd == ".run":
                try:
                    title, sql = EXAMPLES[int(arg) - 1]
                except (ValueError, IndexError):
                    print(f"  pick 1-{len(EXAMPLES)}")
                    continue
                print(f"  {title}\n")
                try:
                    last = _show(con, sql)
                except Exception as exc:
                    print(f"  error: {exc}")
            elif cmd == ".save":
                if last is None:
                    print("  nothing to save yet")
                elif not arg:
                    print("  usage: .save results.csv")
                else:
                    Path(arg).write_text(last.to_csv(index=False),
                                         encoding="utf-8")
                    print(f"  wrote {len(last):,} rows to {arg}")
            else:
                print(f"  unknown command {cmd} — .help")
            continue

        if not stripped and not buffer:
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            sql = "\n".join(buffer)
            buffer = []
            try:
                last = _show(con, sql)
            except Exception as exc:
                print(f"  error: {exc}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        con = connect(verbose=not argv)
    except ImportError:
        print("  DuckDB isn't installed in this environment — run "
              "`pixi install` and try again.")
        return 1

    if argv:
        sql = " ".join(argv)
        try:
            _show(con, sql)
        except Exception as exc:
            print(f"  error: {exc}")
            return 1
        return 0
    return _repl(con)


if __name__ == "__main__":
    raise SystemExit(main())
