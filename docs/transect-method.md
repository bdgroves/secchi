# Detecting wetting events, and three attempts to do it right

The transect asks whether two stations 64 m apart in latitude, on
opposite shores, respond differently to the same storm. Answering it
needs a way to say *a storm happened here*.

There is no rain gauge to ask. Blackwood 2 holds the network's only
precipitation record and it has been dark since August, and the USGS
store only covers September. So events are detected in the **soil
moisture response** rather than the forcing.

That is a reasonable compromise and it has one serious pitfall, which
took three attempts to get past.

## Soil moisture has a strong daily cycle

Roughly half a percentage point, peaking mid-afternoon. It comes from
evapotranspiration drawdown and overnight recovery, plus some
temperature sensitivity in the dielectric measurement.

Any detector that looks at short-window rises will find it, every day,
forever.

### Attempt 1: hourly rises, 18-hour cooldown

Produced runs like this:

    2025-12-05 20:00  +4.25      2026-03-08 17:00  +2.70
    2025-12-06 14:00  +2.03      2026-03-10 16:00  +1.67
    2025-12-07 15:00  +1.10      2026-03-11 16:00  +1.80
    2025-12-08 15:00  +1.60      2026-03-13 15:00  +2.10
    2025-12-09 14:00  +1.50      2026-03-15 14:00  +3.10
    2025-12-10 13:00  +0.80      2026-03-16 11:00  +4.00

Consecutive days, same time of afternoon, small magnitudes. The
18-hour cooldown was shorter than the 24-hour cycle, so every day
registered as a fresh event.

On synthetic data with a planted 0.5-point cycle and three real storms:
**45 events detected, 3 real.**

### Attempt 2: 30-hour cooldown, plus a persistence test

The cooldown now guaranteed one cycle couldn't produce two events. And
a storm leaves soil wet for days while a diurnal swing returns to
baseline within hours — so require the moisture still be elevated 24 h
later.

**28 events.** Better, and still wrong, in an instructive way:

> **A periodic signal sampled exactly one period later looks perfectly
> persistent.**

Checking 24 hours after a peak in a 24-hour cycle lands on the next
peak. The test confirmed exactly what it was meant to reject.

### Attempt 3: detect on daily means

A daily mean cancels a daily cycle **by construction**. A storm raises
the daily mean; a diurnal swing cannot.

**3 events. Exactly the three planted, to the correct day.**

This is the same fix the sparklines needed on the project's first day,
when a 48-hour linear slope reported air temperature *"rising 4.85"* —
it was measuring where in the cycle the window happened to start and
end. The fix then was a day-over-day comparison. I reintroduced the bug
here in a different shape and had to rediscover it.

## Keeping the lag

Detecting on daily means would cost the west-to-east lag, which is the
best validation the method has: storms cross the basin from the
Pacific, so a positive lag says two stations responded to one system
rather than coincidentally.

So detection runs on daily means and **onset is then refined against
the hourly series** within the detected day — the first sample that has
climbed a quarter of the way up. Detection is robust; timing keeps its
resolution.

## Current parameters

| | |
|---|---|
| `WETTING_THRESHOLD_VWC` | 0.003 stored = **0.3 points** displayed |
| `DAILY_RISE_WINDOW_DAYS` | 3 — rise measured against a 3-day trough |
| `DAILY_COOLDOWN_DAYS` | 3 — one storm is one event |
| `SYNCHRONY_WINDOW` | 12 h — how close two onsets must be to pair |

`Soil_VWC` is **stored as a fraction** and displayed ×100. A card
reading "3.4 %" is `0.034` in the parquet. The first draft used a
threshold of `0.3`, which would have needed a thirty-point rise and
never fired.

## What still can't be claimed

- An event cannot be attributed to a named storm.
- A rise could be snowmelt, irrigation or disturbance. Synchrony across
  20 km makes precipitation the parsimonious reading, not proof.
- Detecting on daily means may merge two storms a day apart.

`pixi run backfill --stage blackwood` would bring in 593,507
precipitation records at a west-shore site, turning "both stations
wetted at the same time" into "both wetted after this much rain". That
is the fix worth making before treating any of these numbers as final.
