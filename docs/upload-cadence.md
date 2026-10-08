# How often TEON data arrives

Every telemetered TEON station **measures every 15 minutes but uploads twice
a day**. It stores 12 hours of readings and sends them as one batch, about two
hours after the last reading in it. Each station keeps its own clock
(logger clock, PST all year, so add an hour in summer; the batch's last
reading shown):

| Station | Batch ends around |
|---|---|
| Sunnyside (lake) | 01:00 / 13:00 |
| 4H Camp (lake) | 02:00 / 14:00 |
| Glenbrook (lake) | 03:00 / 15:00 |
| Glenbrook 1 | 01:30 / 13:30 |
| Glenbrook 4 | 01:45 / 13:45 |
| Glenbrook 5 | 05:45 / 17:45 |
| UNR Tahoe Campus | 08:15 / 20:15 |
| Homewood | 10:15 / 22:15 |

Found 2026-10-05 from the watcher's raw snapshots: each station's newest
reading moves in 12-hour steps, never in between. The pattern goes back to
the earliest watcher record (2026-09-18), so it's how the network works, not
a recent change.

## What it means here

- **A station's latest reading can be up to ~14 hours old and still be on
  time.** `LIVE_WINDOW_HOURS = 24` already allows for that; don't tighten it
  below ~16 h or healthy stations will flicker to "quiet".
- **The freshest reading anywhere is usually a few hours old**, because the
  batches are staggered around the clock.
- **Hourly fetches are about right.** With eight staggered batches a day,
  fetching hourly picks each one up within an hour. Fetching more often gains
  nothing.
- **No external trigger.** GitHub's scheduler alone gives about 4–6
  snapshots a day, which adds a few hours of lag on top of the batching. A
  Cloudflare cron to fire the workflows on time was written, then dropped
  (2026-10-05): it could trim that lag but can't make data arrive faster than
  the stations send it. It was first pitched as keeping the page close to
  live, which overstated it. It's in git history (`ops/hourly-trigger/`).
- Blackwood 2's whole 99-day outage arriving at once (see the README) is the
  same mechanism at a larger scale: the logger kept recording and sent it all
  when the link came back.
