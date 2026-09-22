# What the record holds, and the hole in the middle of it

After the nearshore backfill the store spans **January 2025 to September
2026** — 1.95 million rows across 20 monthly partitions, 33 MB.

```
2025-01   36,284  █████████
2025-02   67,200  █████████████████
2025-06  103,680  ██████████████████████████
2025-10  101,184  █████████████████████████
2026-01   96,444  ████████████████████████
2026-03  165,064  █████████████████████████████████████████
2026-05  169,324  ██████████████████████████████████████████
2026-06   95,744  ████████████████████████
2026-07   18,721  █████
2026-08        0
2026-09   74,232  ██████████████████
```

## Three things that shape tells you

**The record starts January 2025.** MiniDot and HOBO have been logging
for twenty months — a far longer baseline than anything else here except
the USGS gauges. TEON went public in September 2026, so this is a year
and a half of data that predates the public network.

**February to May 2026 nearly doubles**, 96k to 169k rows a month. Either
more instruments came online or sampling got denser. Worth identifying
which, because it changes what a year-over-year comparison means.

**August 2026 is empty**, and the reason is the interesting part:

| | |
|---|---|
| 10 Jun | MiniDot + HOBO retrieved → June partial |
| 9 Jul | manual EXO sondes retrieved → July is only those two |
| Aug | nothing in the water, **and we weren't running yet** |
| 18 Sep | our cron starts |

## The gap that exposes

August is empty even though Glenbrook, Sunnyside and six forest loggers
were telemetering the whole time. We simply weren't there to ask.

| Live sensor | Records upstream |
|---|---|
| EXO Glenbrook | 46,703 |
| EXO Sunnyside | 38,946 |
| Glenbrook 1 logger | 58,362 |
| Glenbrook 2 logger | 40,978 |
| Glenbrook 4 logger | 71,410 |
| Glenbrook 5 logger | 63,436 |
| Homewood logger | 36,133 |
| UNR Tahoe logger | 70,891 |
| **Total** | **426,859** |

We hold roughly 5,000 of those. The hourly cron only ever fetches the
newest 200 records per sensor, so **everything before we started running
is missing** — for the sensors the project cares about most, including
both live lake sondes and the entire transect.

## The fourth stage

`--stage live` is the mirror of the hand-collected stages: telemetered
instruments whose history we've never pulled.

```bash
pixi run backfill --stage live --dry-run
pixi run backfill --stage live
```

`_is_manual` now mirrors the transform's rule exactly — TEON's label *or*
instrument class — so the stages can't disagree about which fleet a
sensor belongs to. A `live` stage that accidentally included MiniDot
would re-fetch half a million records for nothing. Verified: the four
stages partition the fleet with **no overlap**.

## Why it matters more than the record count suggests

The transect is Homewood versus Glenbrook 5, and right now we hold about
a week of each. The whole question — do two stations 64 m apart in
latitude respond differently to the same storm — needs storms, and there
haven't been any since we started watching.

**Those loggers have been recording through the entire 2025–26 winter.**
Backfilling them means the transect question can be answered from history
tonight rather than waiting for the first atmospheric river.

Same for the lake sondes: a full seasonal cycle of chlorophyll and
turbidity at Glenbrook and Sunnyside, instead of the fortnight we have.

## After it lands

Two checks worth running, both now possible for the first time:

**`pixi run oxygen-check`** across the MiniDOT fleet. Their
`Dissolved Oxygen Saturation` is computed the same way as the EXO
`Do_percent` that turned out sea-level referenced. If it's wrong too,
that's two independent instrument fleets making the same error — which
says something about how the numbers are assembled rather than about one
misconfigured sonde.

**The PST question.** MiniDOT's timestamp column is literally named
`Pacific Standard Time`. If those records carry a fixed −08:00 offset
they don't shift for daylight saving, and we're an hour out all summer.
The fleet ran through March 2026, so the backfill now contains a DST
boundary — comparing a MiniDOT against a co-located HOBO across that date
settles it.
