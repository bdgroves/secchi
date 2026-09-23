# Respecting the visibility flag

TEON publishes an inventory **and** a `/site-visibility/disabled` list.
The 4H Camp sonde appears in the first and is suppressed in the second:
deployed, reporting, and explicitly flagged non-public.

This project has honoured that from the start — in the *display* layer.
The dashboard renders a card explaining why the site is blank rather than
quietly showing data the observatory asked to be withheld.

**The backfill never looked at the flag.** `select_targets` filtered by
stage and by collection method and nothing else, so a `live` run pulled
**52,875 records** from 4H Camp.

## Why this matters even though nothing was displayed

The data came from a public endpoint and isn't secret. Anyone can fetch
it. But TEON went to the trouble of publishing a separate list saying
*don't show this one*, and the meaningful response to that is not to
hold it.

The store is committed to a public repository. Not displaying data you've
published in a git history is a distinction without a difference.

So: remove it, and make sure the path that fetched it can't again.

## The fix

**`select_targets` reads `/site-visibility/disabled` first** and skips
any matching site, logging what it skipped so the exclusion is visible
rather than silent.

If that endpoint can't be read, the backfill **returns no targets** and
says so. Failing closed is right here: the cost of fetching nothing is a
re-run, and the cost of guessing wrong is ingesting data someone asked
you not to.

**`pixi run purge-hidden`** removes stored rows for any flagged site,
across every partition, rewriting only the partitions that actually
contain it.

## A correction to what was here

This section used to say the same run "fetched 684,366 duplicate records
— 49 % of 1,410,562", because a forest station's soil, air, tree and
stream-level endpoints share record ids and row counts. The fix was to
collapse them: fetch one, treat it as standing in for the rest.

**That was wrong, and it lost data.** The endpoints share *rows*, not
*columns*. Soil returns soil moisture and temperature; air returns air
temperature and humidity; tree returns the dendrometers. Collapsing them
discarded every forest station's air temperature, humidity and
tree-stress history. See [`endpoint-projections.md`](endpoint-projections.md).

The collapse has been removed. The backfill now skips sensors already
complete in the store instead, which recovers most of the saving without
throwing anything away.

## What to run

    pixi run purge-hidden                     # remove the 4H Camp rows
    pixi run backfill --stage live --dry-run  # confirm it's excluded now
