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
import os
import shutil
from pathlib import Path
from typing import Iterable

import pandas as pd

log = logging.getLogger("secchi.store")

PARTITION_FILE = "part.parquet"

# Files a partition may contain. Append mode writes "part-<n>.parquet"
# alongside the canonical "part.parquet"; compaction merges them back.
PARTITION_GLOB = "part*.parquet"


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    """Write a parquet file atomically.

    ``DataFrame.to_parquet`` writes in place. When the disk filled during
    a backfill on 2026-09-22, that left a TRUNCATED part.parquet and took
    everything previously in those partitions with it — about 13 % of the
    nearshore record, 28,535 rows across three sites.

    Writing to a sibling temp file and replacing only on success means a
    failed write leaves the old file untouched. ``Path.replace`` is
    atomic on the same filesystem, on Windows as well as POSIX.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
    finally:
        # A failed write leaves the temp file behind; clear it so the
        # next run doesn't trip over a partial.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


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
    files = sorted(root.rglob(PARTITION_GLOB))
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


def append_partitions(df: pd.DataFrame,
                      root: Path,
                      source: str) -> dict:
    """Write rows as NEW part files, without reading what's there.

    :func:`write_partitions` reads the existing partition, concatenates,
    deduplicates and writes it all back. That's right for the hourly
    cron, which adds a few thousand rows to a partition of similar size.

    It is quadratic for a backfill. The precipitation gauge holds
    1,780,521 observations; flushing every 20,000 rows meant 89 flushes,
    each rewriting everything accumulated so far — **80 million row
    writes for 1.8 million rows of data, a 45x amplification.** About
    2 GB written to store 45 MB, and it filled the disk mid-run.

    So: append without reading, then compact once at the end. Two passes
    over the data instead of ninety.
    """
    if df.empty:
        return {"partitions_touched": 0, "rows_written": 0}

    keyed = _partition_keys(df, source)
    touched = 0
    written = 0

    for (year, month), group in keyed.groupby(["_year", "_month"], sort=True):
        target = partition_path(root, source, int(year), int(month))
        target.mkdir(parents=True, exist_ok=True)

        # A counter rather than a uuid, so the files sort predictably and
        # a half-finished run is legible on disk.
        existing = len(list(target.glob("part-*.parquet")))
        path = target / f"part-{existing + 1:05d}.parquet"

        chunk = group.drop(columns=["_year", "_month", "_source"])
        _write_atomic(chunk, path)
        touched += 1
        written += len(chunk)

    return {"partitions_touched": touched, "rows_written": written}


def compact_partitions(root: Path, dedupe_on: list[str]) -> dict:
    """Merge every partition's part files into one, deduplicating.

    The other half of append-then-compact. Only partitions holding more
    than one file are touched, so running it twice costs almost nothing.
    """
    if not root.exists():
        return {"compacted": 0, "rows": 0}

    compacted = 0
    total_rows = 0
    removed_files = 0
    skipped = 0

    # Directories holding at least one part file.
    dirs = {f.parent for f in root.rglob(PARTITION_GLOB)}
    for target in sorted(dirs):
        files = sorted(target.glob(PARTITION_GLOB))
        if len(files) <= 1:
            # Already a single file; nothing to merge. Rename it to the
            # canonical name if a bare append created part-00001 only.
            if len(files) == 1 and files[0].name != PARTITION_FILE:
                files[0].replace(target / PARTITION_FILE)
            continue

        frames = []
        unreadable: list[Path] = []
        for f in files:
            try:
                frames.append(pd.read_parquet(f))
            except Exception as exc:
                log.error("could not read %s (%s)", f, exc)
                unreadable.append(f)
        if not frames:
            continue

        if unreadable:
            # REFUSE to compact. The previous version skipped an
            # unreadable file and then deleted it along with the rest,
            # turning a truncated file into a permanently lost one.
            #
            # Leaving the partition alone keeps whatever is still
            # readable and makes the damage visible, which is the right
            # trade when the alternative is silent loss.
            log.error("refusing to compact %s: %d unreadable file(s). "
                      "Re-run the backfill for this period — the store "
                      "deduplicates, so it is safe.",
                      target.relative_to(root), len(unreadable))
            skipped += 1
            continue

        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(subset=dedupe_on).reset_index(drop=True)
        if "timestamp" in merged.columns:
            merged = merged.sort_values("timestamp").reset_index(drop=True)

        # Write the canonical file first, then remove the parts, so an
        # interruption leaves duplicates rather than a hole.
        _write_atomic(merged, target / PARTITION_FILE)
        for f in files:
            if f.name != PARTITION_FILE:
                f.unlink()
                removed_files += 1

        compacted += 1
        total_rows += len(merged)

    if compacted:
        log.info("compacted %d partition(s) to %s rows, removed %d part file(s)",
                 compacted, f"{total_rows:,}", removed_files)
    if skipped:
        log.error("%d partition(s) left uncompacted because of unreadable "
                  "files — see the errors above", skipped)
    return {"compacted": compacted, "rows": total_rows,
            "files_removed": removed_files, "skipped": skipped}


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

        _write_atomic(merged, path)
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
    for f in sorted(root.rglob(PARTITION_GLOB)):
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


def drop_partition(root: Path, source: str, year: int, month: int) -> dict:
    """Delete one partition.

    Exists for the undated bucket. On 2026-09-22 a nearshore backfill
    filed 1,490,116 rows under ``year=0000`` because the timestamp key
    differed by sensor type — 24 MB of rows that can't be plotted,
    ordered or windowed. Once the key is fixed the right move is to drop
    that partition and re-run the backfill, not to try repairing rows
    that never had a usable time.
    """
    target = partition_path(root, source, year, month)
    path = target / PARTITION_FILE
    if not path.exists():
        return {"dropped": False, "reason": "partition does not exist"}

    try:
        import pyarrow.parquet as pq
        rows = pq.ParquetFile(path).metadata.num_rows
    except Exception:
        rows = -1
    kb = path.stat().st_size / 1024

    path.unlink()
    # Tidy the now-empty directories, but never above `root`.
    for parent in (target, target.parent):
        try:
            if parent != root and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            break

    log.info("dropped %s (%s rows, %.1f KB)",
             target.relative_to(root), f"{rows:,}" if rows >= 0 else "?", kb)
    return {"dropped": True, "rows": rows, "kb": round(kb, 1)}


def purge_site(root: Path, site: str) -> dict:
    """Remove every row for one site from every partition.

    Written because a `live` backfill pulled 52,875 records from 4H Camp,
    the site TEON flags non-public via /site-visibility/disabled. The
    dashboard honoured the flag; the backfill never checked it.

    The data was fetched from a public endpoint and isn't secret, but the
    observatory asked that it not be published and this store is
    committed to a public repository. Honouring the request means
    removing it, not just declining to display it.

    Only partitions actually containing the site are rewritten.
    """
    if not root.exists():
        return {"purged": False, "reason": "no store"}

    removed = 0
    touched = 0
    emptied = 0
    for path in sorted(root.rglob(PARTITION_GLOB)):
        try:
            df = pd.read_parquet(path)
        except Exception as exc:
            log.warning("could not read %s (%s); skipping", path, exc)
            continue
        if "site" not in df.columns:
            continue
        mask = df["site"] == site
        n = int(mask.sum())
        if not n:
            continue

        kept = df[~mask]
        removed += n
        touched += 1
        if kept.empty:
            path.unlink()
            emptied += 1
        else:
            _write_atomic(kept, path)

    log.info("purged %s: %d row(s) from %d partition(s)%s",
             site, removed, touched,
             f", {emptied} partition(s) now empty and removed" if emptied else "")
    return {"purged": True, "site": site, "rows_removed": removed,
            "partitions_touched": touched}


def repair_sensor_types(root: Path, mapping: dict[str, str]) -> dict:
    """Rewrite non-canonical sensor_type values in place.

    The backfill wrote raw inventory keys ("EXO", "Minidot", "Hobo")
    while the hourly ingest wrote canonical ones ("ExoSensor",
    "MiniDotSensor", "HoboSensor"), so the store held the same
    instrument under two names and every downstream filter missed half
    its data.

    Rewriting beats re-fetching: three million rows are already correct
    apart from one column, and a re-run would mean hours of requests to
    arrive at the same place. Only partitions actually containing a
    stale value are rewritten.
    """
    if not root.exists():
        return {"repaired": False, "reason": "no store"}

    changed_rows = 0
    touched = 0
    seen: dict[str, int] = {}

    for path in sorted(root.rglob(PARTITION_GLOB)):
        try:
            df = pd.read_parquet(path)
        except Exception as exc:
            log.warning("could not read %s (%s); skipping", path, exc)
            continue
        if "sensor_type" not in df.columns:
            continue

        mask = df["sensor_type"].isin(mapping)
        n = int(mask.sum())
        if not n:
            continue
        for value in df.loc[mask, "sensor_type"].unique():
            seen[value] = seen.get(value, 0) + int((df["sensor_type"] == value).sum())

        df.loc[mask, "sensor_type"] = df.loc[mask, "sensor_type"].map(mapping)
        _write_atomic(df, path)
        changed_rows += n
        touched += 1

    for old, count in sorted(seen.items()):
        log.info("  %s -> %s  (%s rows)", old, mapping[old], f"{count:,}")
    log.info("repaired %s row(s) across %d partition(s)",
             f"{changed_rows:,}", touched)
    return {"repaired": True, "rows": changed_rows, "partitions": touched,
            "values": seen}


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
    if root.exists() and any(root.rglob(PARTITION_GLOB)):
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
