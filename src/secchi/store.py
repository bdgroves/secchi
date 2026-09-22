"""A partitioned parquet store, so the repository stops growing without bound.

The problem this solves is **churn**, not size. A single parquet file is
rewritten whole on every update, and git stores each version as a new
blob — there is no delta compression on binary. At today's 3 MB that is
invisible. After a backfill it would be fatal:

    3 MB rewritten hourly   ->   0.1 GB/day of git history
  150 MB rewritten hourly   ->   3.5 GB/day

Hive partitioning fixes it by making most of the data immutable:

    data/processed/observations/
        source=teon/year=2026/month=07/part.parquet   written once, frozen
        source=teon/year=2026/month=08/part.parquet   written once, frozen
        source=teon/year=2026/month=09/part.parquet   the only file that churns

Historical partitions are never touched again, so git stores each exactly
once. And a backfill is *mostly* historical by definition, so the bulk of
it lands as frozen partitions costing one commit each.

Deliberately **not** using ``DataFrame.to_parquet(partition_cols=...)``.
That appends a new randomly-named file to each partition on every write,
which accumulates thousands of fragments and defeats the whole point.
This module reads, merges, deduplicates and replaces one file per
partition instead.

Both pandas and polars read this layout natively, and DuckDB can query it
in place with a glob — no import step, no server.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable

import pandas as pd

log = logging.getLogger("secchi.store")

PARTITION_FILE = "part.parquet"


def partition_path(root: Path, source: str, year: int, month: int) -> Path:
    """Hive-style path for one partition."""
    return root / f"source={source}" / f"year={year:04d}" / f"month={month:02d}"


def _partition_keys(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Attach the partition columns derived from each row's timestamp.

    Rows with no usable timestamp go to a dedicated ``year=0000`` bucket
    rather than being dropped. Losing data silently because a date failed
    to parse is exactly the class of failure this project keeps finding.
    """
    out = df.copy()
    ts = pd.to_datetime(out["timestamp"], errors="coerce", utc=True)
    out["_year"] = ts.dt.year.fillna(0).astype(int)
    out["_month"] = ts.dt.month.fillna(0).astype(int)
    out["_source"] = source
    undated = int((out["_year"] == 0).sum())
    if undated:
        log.warning("%d row(s) have no parseable timestamp; filing under "
                    "year=0000 rather than discarding them", undated)
    return out


def read_partitions(root: Path,
                    columns: list[str] | None = None) -> pd.DataFrame:
    """Read every partition under ``root`` into one frame.

    Returns an empty frame if nothing is stored yet, so callers don't
    need to special-case a fresh checkout.
    """
    if not root.exists():
        return pd.DataFrame()
    files = sorted(root.rglob(PARTITION_FILE))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        try:
            frames.append(pd.read_parquet(f, columns=columns))
        except Exception as exc:
            log.warning("could not read %s (%s); skipping", f, exc)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def write_partitions(df: pd.DataFrame,
                     root: Path,
                     source: str,
                     dedupe_on: list[str]) -> dict:
    """Merge rows into the store, rewriting only the partitions they touch.

    For each (year, month) present in ``df``: read the existing partition
    if there is one, concatenate, deduplicate on ``dedupe_on``, and write
    the file back. Partitions with no incoming rows are not opened, let
    alone rewritten — which is the entire point.

    New rows are placed first so a re-fetch of the same observation keeps
    the freshest copy, matching the behaviour of the previous monolithic
    accumulator.
    """
    if df.empty:
        return {"partitions_touched": 0, "rows_written": 0, "rows_added": 0}

    keyed = _partition_keys(df, source)
    touched = 0
    total_rows = 0
    total_added = 0

    for (year, month), group in keyed.groupby(["_year", "_month"], sort=True):
        target = partition_path(root, source, int(year), int(month))
        target.mkdir(parents=True, exist_ok=True)
        path = target / PARTITION_FILE

        incoming = group.drop(columns=["_year", "_month", "_source"])
        before = 0
        if path.exists():
            try:
                existing = pd.read_parquet(path)
                before = len(existing)
                missing = set(dedupe_on) - set(existing.columns)
                if missing:
                    log.warning("%s is missing %s; replacing it", path,
                                ", ".join(sorted(missing)))
                else:
                    incoming = pd.concat([incoming, existing], ignore_index=True)
            except Exception as exc:
                log.warning("could not read %s (%s); replacing it", path, exc)

        merged = incoming.drop_duplicates(subset=dedupe_on).reset_index(drop=True)
        if "timestamp" in merged.columns:
            merged = merged.sort_values("timestamp").reset_index(drop=True)

        merged.to_parquet(path, index=False)
        touched += 1
        total_rows += len(merged)
        total_added += max(0, len(merged) - before)

    log.info("%s: %d partition(s) touched, %d rows stored (+%d new)",
             root.name, touched, total_rows, total_added)
    return {"partitions_touched": touched,
            "rows_written": total_rows,
            "rows_added": total_added}


def partition_summary(root: Path) -> list[dict]:
    """One row per partition: path, size and row count. For reporting."""
    if not root.exists():
        return []
    out = []
    for f in sorted(root.rglob(PARTITION_FILE)):
        rel = f.relative_to(root)
        try:
            # Row count from the file's own metadata — no need to read
            # any column data. `read_parquet(columns=[])` looks like it
            # should work and returns a zero-row frame instead.
            import pyarrow.parquet as pq
            n = pq.ParquetFile(f).metadata.num_rows
        except Exception:
            n = -1
        out.append({
            "partition": str(rel.parent).replace("\\", "/"),
            "rows": n,
            "kb": round(f.stat().st_size / 1024, 1),
        })
    return out


def migrate_monolith(monolith: Path,
                     root: Path,
                     source: str,
                     dedupe_on: list[str],
                     keep_backup: bool = True) -> dict:
    """Split an existing single-file parquet into partitions.

    One-time. The original is renamed rather than deleted — a migration
    that destroys its input is a migration you can only run once, and
    only correctly.
    """
    if not monolith.exists():
        return {"migrated": False, "reason": "no monolithic file to migrate"}
    if root.exists() and any(root.rglob(PARTITION_FILE)):
        return {"migrated": False, "reason": "partitions already exist"}

    df = pd.read_parquet(monolith)
    log.info("migrating %s (%d rows) into partitions", monolith.name, len(df))
    result = write_partitions(df, root, source, dedupe_on)

    if keep_backup:
        backup = monolith.with_suffix(".parquet.premigration")
        shutil.move(str(monolith), str(backup))
        log.info("original preserved as %s", backup.name)
    result["migrated"] = True
    result["source_rows"] = len(df)
    return result
