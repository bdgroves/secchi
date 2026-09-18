# Watching upstream

Both networks are actively being built out, and the changes worth knowing
about arrive without announcement. `pixi run watch`, and the six-hourly
`watch.yml` workflow, notice them.

## What it's actually waiting for

| Change | Why it matters |
|---|---|
| **The manual sondes upload** | Blackwood 3 and Meeks publish nothing until someone snorkels out, retrieves them and uploads. When that happens their record jumps ~2 months at once, through the endpoint we already poll. Nothing to build — but worth knowing the day it lands. |
| **MiniDot / HOBO resume** | Both stopped around 2026-06-10, the manual sondes on 2026-07-09. That clustering looks like a summer haul-out rather than failure, so they are probably back in the water. Twelve nearshore instruments, ~480,000 records. |
| **4H Camp un-hidden** | TEON's `/site-visibility/disabled` currently hides it. We respect that flag; if it lifts, a third lake sonde becomes available. |
| **The aquatic domain appears** | TEON's StoryMap describes four monitoring domains — nearshore water quality, terrestrial, stream, and *aquatic* (ponds, wet meadows, upland lakes). The API exposes only three. If the fourth shows up, that's a substantial new dataset. |
| **A live sensor goes quiet** | Reported, but at lower severity. Useful context rather than something to interrupt anyone for. |

## How it decides

`watch` reduces the inventory to the facts worth comparing: which sensors
exist, whether each is `live` / `quiet` / `dormant`, which sensor types and
categories are present, and which sites are hidden.

**Record counts are deliberately excluded from the comparison.** They
change every hour, so including them would make every single run look like
a change and the notifications would be worthless within a day.

State thresholds:

- **live** — reported within `LIVE_WINDOW_HOURS` (24)
- **quiet** — within 30 days
- **dormant** — longer than that

The gap between live and dormant is deliberate. If "not live" immediately
meant "dormant", a single missed hourly run would flip a sensor's state
and generate a spurious notification.

## Severity, and what interrupts you

| Severity | Examples | Effect |
|---|---|---|
| **notable** | new sensor type, new category, sensor resumed, site un-hidden, new sensor appeared | Opens a GitHub issue |
| **info** | sensor went quiet, sensor removed, other state changes | Logged only |

"Sensor resumed" is notable because it means data that was unreachable is
now flowing. "Sensor went quiet" is info because it's usually seasonal and
there's nothing to act on.

Exit codes carry the signal so the workflow can branch without parsing
text: `0` no notable changes, `2` notable changes found, `1` the check
itself failed.

## The baseline

`data/reference/watch_baseline.json`, committed so the comparison survives
a fresh CI checkout. The first run establishes it and reports nothing —
there's nothing to compare against yet.

The workflow updates and commits it each run, with the same three-attempt
rebase-and-retry the fetch workflow uses.

## Cadence

Six-hourly, offset 17 minutes past to avoid colliding with the hourly
fetch. The things being watched move on the scale of weeks, so hourly
would be noise; six-hourly still catches a sonde retrieval the same day.

## What it can't see

- **Camera imagery becoming public.** The S3 bucket policy is invisible
  from outside; `camera-probe` has to be run to test it. Worth re-running
  occasionally by hand.
- **The EDI Secchi API unblocking.** Same situation — `terc-discover`
  tests it, the watcher doesn't.
- **New USGS gauges or parameters.** `usgs-discover` covers gauges and
  `usgs-probe` covers parameters, but neither is wired into the watcher
  yet. Both are cheap; folding them in would be a reasonable next step,
  and would have caught the fine-sediment series appearing.
