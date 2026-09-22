# Why the backfill filled the disk

`pixi run backfill --stage blackwood` died at 44.6 % with:

    [Errno 28] No space left on device

The disk wasn't small. The repo is 3.8 GB, 3.3 of it the pixi
environment, `.git` only 215 MB, and C: had 5.85 GB free afterwards.
Nothing was bloated.

The backfill wrote several gigabytes to store 45 MB.

## The cause

`write_partitions` reads the existing partition, concatenates the new
rows, deduplicates, and writes the whole thing back. That is correct for
the hourly cron, which adds a few thousand rows to a partition of
similar size.

It is **quadratic** for a backfill. The precipitation gauge holds
593,507 records across three fields — 1,780,521 observations. Flushing
every 20,000 rows means 89 flushes, and each one rewrites everything
accumulated so far:

| | |
|---|---|
| observations to store | 1,780,521 |
| flushes | 89 |
| rows actually written | **80,100,000** |
| amplification | **45×** |

About **2 GB written** to store 45 MB, and parquet writes through a temp
file, so peak usage is higher again.

It never showed before because every earlier fleet was small enough. The
nearshore backfill's largest partition was a fifth the size.

## The fix: append, then compact

Two new functions in `store.py`:

- **`append_partitions`** writes each flush as a new `part-00001.parquet`
  alongside the canonical `part.parquet`, without reading anything.
- **`compact_partitions`** merges each partition's part files once,
  deduplicates, and removes the parts.

Two passes over the data instead of ninety:

| | rows written |
|---|---|
| read-merge-write | 80,100,000 |
| append then compact | 3,561,042 |
| | **~89 MB instead of ~2 GB** |

`read_partitions` globs `part*.parquet`, so the store is readable
between append and compaction — an interrupted backfill leaves
duplicates, not a hole, and compaction resolves them.

Compaction writes the canonical file **before** deleting the parts, for
the same reason.

## What didn't change

The hourly cron still uses `write_partitions`. Read-merge-write is the
right shape there: a few thousand new rows into a partition of similar
size, with deduplication applied immediately so the store is always
canonical. Only the backfill needed the other pattern.

`CHUNK_ROWS` also rose from 20,000 to 100,000, which is safe now that a
flush is a plain append and makes compaction cheaper by producing fewer,
larger part files.

## Re-running

Safe, and cheap. The store deduplicates on the source's own record ids,
so the 320,000 records already fetched cost only their first page on a
second run.

    pixi run backfill --stage blackwood
    pixi run transform

## The general lesson

This is the same shape as the original repo-growth problem, which
committed every fetch artifact forever and would have hit GitHub's limit
in five days. Both were fine at the scale they were written for and
failed an order of magnitude up.

**A write pattern that is correct for incremental updates is usually
wrong for a bulk load.** Worth asking, of anything that reads before it
writes, what happens when the thing being written is much larger than
the thing being added.
