# Storage: do we have room, and is it time for DuckDB?

Short answers: **yes, room is fine.** **DuckDB, but not as storage.** And
the thing that actually needs fixing before any backfill is **churn, not
size.**

## The volume

Everything currently unreachable, if fully backfilled:

| Fleet | Records | ~Fields | Observations |
|---|---|---|---|
| Precipitation Gauge | 593,507 | 3 | 1,780,521 |
| MiniDot | 265,340 | 3 | 796,020 |
| HOBO | 214,378 | 2 | 428,756 |
| Stream Chemistry | 124,620 | 10 | 1,246,200 |
| **Total** | | | **4,251,497** |

At roughly 25 bytes/row in compressed parquet, that's about **100 MB**.
GitHub's soft limit is 1 GB. Size is genuinely not the problem.

## The problem is churn

A parquet file is rewritten whole on every update, and git stores each
version as a new blob — there is no delta compression on binary. So the
cost isn't the file, it's the file *times every commit*:

| Parquet size | Rewritten hourly |
|---|---|
| 3 MB (today) | 0.1 GB/day |
| 50 MB | 1.2 GB/day |
| 150 MB | **3.5 GB/day** |

Today it's invisible. After a backfill it would destroy the repository
inside a day. **The layout has to change before the backfill, not after.**

This is the same mistake as the original design, which committed every
fetch artifact forever and would have hit the limit in five days. Fixed
once by pruning raw and accumulating parquet; the accumulating parquet is
now the thing that needs fixing.

## The fix: partition, don't migrate

```
data/processed/observations/
    source=teon/year=2026/month=07/part.parquet    immutable
    source=teon/year=2026/month=08/part.parquet    immutable
    source=teon/year=2026/month=09/part.parquet    the only file that churns
    source=usgs/year=2026/month=09/part.parquet
```

Hive partitioning by source and month. Historical partitions are written
once and never touched again, so git stores each exactly once. Only the
current month rewrites hourly — about **8 MB** rather than 100.

And most of a backfill is historical by definition, so the bulk of it
lands as frozen partitions that cost one commit each.

Both pandas and polars read hive layouts natively. Nothing outside
`transform.py` needs to know.

## Where DuckDB fits

**Not as storage.** A `.duckdb` file has the same single-binary churn
problem as one big parquet, plus format lock-in. Putting one in git would
solve nothing.

**As a query layer over partitioned parquet, it's excellent:**

```sql
SELECT site, date_trunc('day', timestamp) AS day, avg(value)
FROM 'data/processed/observations/**/*.parquet'
WHERE variable = 'Do_mgL'
GROUP BY 1, 2
ORDER BY 2;
```

No server, no import step, no daemon. It reads hive partitions natively
and prunes by partition key, so a query filtered to one month touches one
file. The parquet stays readable by everything else.

That's the combination worth having: **partitioned parquet for storage,
DuckDB for asking questions.** It's a `pixi run query` away, not a
migration.

## What I'd rule out, and why

| Option | Why not |
|---|---|
| **Git LFS** | Bandwidth quotas, costs money, and the churn is unchanged — LFS still stores every version. |
| **A `.duckdb` file in git** | Same single-binary churn. Worse, since the file is larger than the equivalent parquet. |
| **Postgres / TimescaleDB** | A server to run, secure and pay for. Overkill for four million rows that fit in memory. |
| **S3 / Cloudflare R2** | The right answer at ten times this scale. Adds credentials, cost, and a dependency the project doesn't need yet — and loses the property that anyone can clone the repo and have all the data. |

That last point is worth weighting. The current design means `git clone`
gives you the complete record with no keys and no accounts. That's
genuinely valuable for a public science project, and worth preserving
until the numbers force otherwise.

## When to revisit

Move off GitHub when any of these becomes true:

- The repository passes **~700 MB** (approaching the 1 GB soft limit).
- Current-month churn exceeds **~50 MB/hour**.
- Clone time becomes a barrier for anyone who wants the data.

None is close today. Partitioning buys a long runway — long enough that
the decision can wait for evidence rather than anticipation.

## The order of operations

1. **Partition the parquet** — before any backfill.
2. **Write the backfill** with pagination and a raised page-size cap, run
   once, off the cron.
3. **Add `pixi run query`** as a DuckDB shell over the partitions.
4. Only then consider external storage, and only if the thresholds above
   are actually hit.


## Update, 2026-10-01: it churned anyway

The partitioning above froze history, but the repository still reached
**1.1 GB in its first two weeks**. Measured on one ordinary run:

| What a run added | Size |
|---|---|
| this month's observation file, rewritten whole | 2.9 MB |
| this month's USGS file, rewritten whole | 2.2 MB |
| every camera month file, rewritten with nothing new | 0.17 MB |
| raw downloads (git compresses text: 2.9 MB → 0.26 MB) | 0.26 MB |

Two faults behind it, both in `write_partitions`:

1. **It rewrote every partition it touched, even with no new rows.** The
   log said `+0 new` and the file was written anyway.
2. **It sorted with an unstable sort.** Rows sharing a timestamp came out
   in a different order each run, so identical data became a different
   file — and to git, a new 3 MB copy.

And one choice: the current month was a single file, so adding an hour
of readings meant storing the whole month again.

**The fix:** rows are sorted stably on timestamp and key, so the same
data is the same bytes; a file is compared with what's stored and left
alone if nothing changed; and from October 2026 (`DAILY_FROM`) each month
is one file per day. Months before stay single files — converting them
would store a second copy of history for nothing.

| A run now adds | Size |
|---|---|
| today's observation file | ≤ ~100 KB |
| today's USGS file | ≤ ~75 KB |
| camera files | nothing unless new frames |
| raw downloads | ~0.26 MB |
| **a run with no new data** | **nothing** |

About a fourteenth of before. At the irregular pace GitHub actually runs
the schedule, that's ~65 MB a month instead of ~1 GB; at a true hourly
pace, ~0.3 GB instead of ~4.

`tests/test_store_layout.py` pins all of this down, and the two churn
tests fail against the old store.
