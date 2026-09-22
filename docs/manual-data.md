# The manually collected data

Two of the five lake sondes — **Blackwood 3** and **Meeks** — are
self-logging. They publish nothing until Emily Carlson and Katie Senft
snorkel out, retrieve them, download the memory and upload it. Per TEON's
StoryMap that happens roughly **monthly**, in all weather.

When it lands, about two months of 15-minute data appears at once.

## How to see it

**No special endpoint.** They publish through the same
`/sensors/exo-sensor` route as the telemetered sondes. The records simply
extend when an upload happens. Nothing to build for access.

## How we'll know — and the two gaps that were in the way

### Gap 1: the watcher could have missed it

The watcher compares sensor **state** — live, quiet, dormant. If an upload
delivers records whose newest timestamp is still older than the 30-day
dormancy threshold, the state stays `dormant` and a state-only diff
reports nothing.

Fixed by also comparing **record count**, but only for sensors that
weren't live. That distinction matters: a live sensor's count moves every
hour, and comparing it would make every run look like a change and drown
the report. A dormant sensor's count should never move at all, so a jump
is unambiguous.

Threshold is 50 records — far above any plausible trickle, far below a
real batch. A retrieval delivers thousands; each of these sondes holds
about 15,450 for a single deployment.

Tested against the exact scenario: Meeks going from 15,441 to 22,180
records while *remaining* below the live window. Reported as notable;
Glenbrook's routine hourly growth in the same diff correctly ignored.

A decrease is also reported, at lower severity — records disappearing
means a reload, a correction, or a bug upstream, and all three are worth
knowing.

### Gap 2: the cron won't fetch it

`ingest-all` targets sensors reporting within `LIVE_WINDOW_HOURS` (24).
Dormant sondes aren't in that list, so the hourly job won't pull the new
data even once we know it exists.

And `DEFAULT_INGEST_PAGE_SIZE` caps each sensor at 200 records per run, so
even including them would fetch only the newest 200 of several thousand.

**This is deliberate for now.** A retrieval is a one-off event a few times
a year; wiring the hourly cron to handle it would mean either doubling
every run's request count or building pagination into the routine path.
The right response is a **deliberate backfill**, triggered when the
watcher says an upload has landed.

## What to do when the issue appears

The watcher will open a GitHub issue reading something like:

> **dormant sensor received an upload**: `lake/EXO/Meeks` — record count
> 15,441 → 22,180 (+6,739), newest 2026-08-28T09:15:00

At that point:

1. Run the backfill for that sensor with a raised page size — this needs
   writing; see `docs/storage.md` for why it should wait until the parquet
   is partitioned.
2. `pixi run transform` to fold it in. The accumulator dedupes on record
   UUIDs, so re-fetching overlapping data is harmless.
3. The sonde's history on the dashboard extends by however long it was in
   the water.

## Why this data is worth the trouble

The self-logging sondes have the **best records in the network** — 98.8 %
complete against 73.6–96.8 % for the telemetered ones, because there's no
radio link to drop packets.

They're also the west-shore sites. Blackwood 3 and Meeks sit in the wet
half of the 3× precipitation gradient, where TEON's telemetered coverage
is thinnest and where the Blackwood terrestrial station has been offline
since June.
