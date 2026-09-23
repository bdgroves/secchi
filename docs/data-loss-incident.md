# Data loss, 2026-09-22

**28,535 rows lost** across three nearshore sites. Recoverable — the
records are still in TEON's API — but they were lost from the store, and
the mechanism is worth writing down.

## What was lost

| Site | Before | After | Lost | |
|---|---|---|---|---|
| Camp Richardson | 88,214 | 77,086 | 11,128 | 12.6 % |
| Lake Forest | 85,997 | 74,879 | 11,118 | 12.9 % |
| Lakeside | 46,184 | 39,895 | 6,289 | 13.6 % |

Near-identical fractions at three independent sites — not random
corruption, but whole monthly partitions losing their contents. Roughly
two of seventeen months' worth.

Blackwood 3 and Meeks were **untouched**. They're EXO sites, in
different partitions.

## What happened

The `blackwood` backfill ran on the old code path and died with
`[Errno 28] No space left on device`, 44.6 % through.

`write_partitions` wrote directly over the partition file:

```python
merged.to_parquet(path, index=False)      # path = part.parquet
```

When the disk filled mid-write, that left a **truncated part.parquet** —
and everything previously in that partition went with it.

Blackwood 2's precipitation record spans the same monthly partitions as
the nearshore sites. The partitions being rewritten when it died were
partitions holding Camp Richardson, Lake Forest and Lakeside data.

## The compaction would have compounded it

```python
except Exception as exc:
    log.warning("could not read %s; skipping", f)
...
merged.to_parquet(target / PARTITION_FILE)
for f in files:
    f.unlink()                 # including the one it couldn't read
```

Skip a file you can't read, then delete it. A truncated file becomes a
permanently lost one.

## Two fixes

**Writes are atomic.** `_write_atomic` writes to a sibling `.tmp` and
`Path.replace`s it into position, which is atomic on the same filesystem
on Windows and POSIX alike. A failed write now leaves the previous file
untouched, and the temp file is cleaned up.

Every write in `store.py` goes through it — `write_partitions`,
`append_partitions`, `compact_partitions` and `purge_site`.

**Compaction refuses to proceed on an unreadable partition.** It logs an
error naming the file, leaves everything in place, and reports the count
in its result. Keeping damaged-but-readable data and making the damage
visible beats silently tidying it away.

Verified: a deliberately truncated part file now leaves the partition
untouched, the good data intact, and the corrupt file on disk to be
noticed.

## Recovery

```bash
pixi run backfill --stage nearshore
pixi run transform
```

The store deduplicates on the source's own record ids, so the 191,000
surviving rows cost only their first page and only the gaps are
refetched.

## What this says about the design

Committing the parquet was the right call for repo growth, and
partitioning was right for churn. But both decisions made the store the
**single copy** of assembled data, and a single copy needs its writes to
be atomic. That wasn't true until now.

`data/raw/` holds only seven days, so it was never a fallback for
history. The real fallback is that TEON still has everything — which is
lucky rather than designed. A backfill that depended on a source having
gone away would have been a different conversation.

## Update, 2026-09-23: it was about three times bigger

The table above lists only the three nearshore sites, because those were
the only sites with coverage cards, and a coverage card is what noticed
the loss.

The truncated partitions held **every** site's data for those months.
The next day, the new SQL shell compared records held against TEON's
counts and found the forest stations and lake sondes short too:

| Site | Missing |
|---|---|
| Glenbrook 4 soil | 8,772 |
| Glenbrook 5 soil | 7,435 |
| Glenbrook 1 soil | 5,956 |
| Glenbrook 2 soil | 5,932 |
| Blackwood 2 soil | 5,652 |
| UNR Tahoe Campus soil | 5,255 |
| Glenbrook EXO | 4,421 |
| Homewood soil | 2,880 |
| Sunnyside EXO | 1,523 |

About **47,800 more records**, for roughly **76,000 in total**. Blackwood
3 and Meeks were complete: their data sits in months the failed run
never rewrote.

All recovered by a `live` backfill, verified to within 6 records of
TEON's counts at every site.

The lesson is about coverage of the checks, not the storage. The loss
was detectable everywhere; it was only *detected* where something was
looking. The backlog check now compares held against upstream for
telemetered stations too, so the same loss would appear on the banner.
