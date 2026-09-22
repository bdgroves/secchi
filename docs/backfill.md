# Backfilling the history

About **1.2 million records** sit upstream that the hourly cron never
fetches. The cron targets live sensors and caps each at 200 records — the
right behaviour for keeping a dashboard current, and useless for history.

## Three stages, by value per megabyte

| Stage | Contents | Records | ~Parquet |
|---|---|---|---|
| `manual` | Blackwood 3, Meeks — hand-collected lake sondes | 30,892 | 10 MB |
| `nearshore` | MiniDot ×6, HOBO ×6 | 479,718 | 34 MB |
| `blackwood` | Precipitation, Stream Chemistry at Blackwood 2 | 718,127 | 103 MB |

**`manual` first**, and not only because it's smallest. Those two sondes
have the best records in the network — 98.8 % complete against 73.6–96.8 %
for the telemetered ones, because a self-logging instrument has no radio
link to drop packets. They're also both west-shore, on the wet side of
the 3× precipitation gradient, where telemetered coverage is thinnest.

**`nearshore` is the nearshore programme.** TEON's StoryMap describes ten
nearshore water-quality stations; the API exposes five EXO sites. The gap
is these twelve instruments across six shared sites.

**`blackwood` is the forcing variable.** The precipitation gauge is the
largest dataset in the network and the thing the transect has never had —
right now we measure watershed *response* but not the storm driving it.

## Run it

```bash
pixi run backfill --stage manual --dry-run    # what would happen
pixi run backfill --stage manual              # do it
pixi run transform                            # rebuild the dashboard
pixi run store-status                         # what's stored now
```

`--site "Meeks"` restricts to one sensor. `--page-size` raises the
request size once someone has checked what TEON accepts; the default 200
is conservative because only 50 has been verified in production.

## Two design decisions

**It writes straight to parquet, never to `data/raw/`.** An EXO record is
roughly 2 KB of JSON, so routing the full dormant fleet through the raw
buffer would dump about **2 GB** into a directory designed to hold seven
days of hourly snapshots.

**It streams rather than accumulates.** `TeonClient.fetch_sensor` holds
every record in memory before returning — fine for 200, not for 593,507.
A new `iter_sensor_pages` generator yields pages instead, and the backfill
flushes every 20,000 observations, so memory stays flat.

## Re-running is safe

The store deduplicates on the source's own record ids, so an interrupted
run is simply re-run. No resume state to corrupt, and finished sensors
cost only their first page. Verified: a second identical run added
**0 rows**.

## Why partitioning had to come first

A parquet file is rewritten whole on every update, and git stores each
version as a new blob.

| Store size | Rewritten hourly |
|---|---|
| 3 MB (before) | 0.1 GB/day |
| 150 MB (after all three stages) | **3.5 GB/day** |

So `data/processed/` is now hive-partitioned:

```
observations/
    source=teon/year=2026/month=07/part.parquet    frozen
    source=teon/year=2026/month=08/part.parquet    frozen
    source=teon/year=2026/month=09/part.parquet    the only file that churns
```

Historical partitions are written once and never touched. A backfill is
*mostly* historical by definition, so most of it lands frozen, costing
one commit each.

Verified: writing to August left June and July byte-identical.

### Migration is automatic and reversible

The first `transform` after this lands splits any existing single-file
parquet into partitions and renames the original to
`.parquet.premigration` rather than deleting it. A migration that
destroys its input is one you can only run once, and only correctly.
Re-running is a no-op.

## What to expect from stage 1

30,892 records → roughly **432,000 observations** spanning January to
July 2026, in seven monthly partitions.

On the dashboard, Blackwood 3 and Meeks stop being pins with nothing
behind them: their sparklines get a real season of west-shore lake
chemistry, and the `unpulled_records` gap closes so the pin returns from
ember to the hollow hand-collected ring.

## Querying it afterwards

Readable by pandas and polars directly, and DuckDB queries it in place:

```sql
SELECT site, date_trunc('month', timestamp) AS month, avg(value)
FROM 'data/processed/observations/**/*.parquet'
WHERE variable = 'Do_mgL'
GROUP BY 1, 2 ORDER BY 2;
```

DuckDB prunes by partition key, so a query filtered to one month opens
one file.
