# When a station goes dark

Two of TEON's seven terrestrial stations have stopped: **Blackwood 2**
in June, **Glenbrook 2** on 2026-09-22. Both took every channel with
them — air, soil, tree stress, stream level, camera — so the cause is
something shared, not a sensor fault.

## Neither was a flat battery

Campbell loggers report `BattV_Avg`, their own supply voltage. The real
record says:

| Station | Final mean | Final min | Floor | 30-day trend |
|---|---|---|---|---|
| Blackwood 2 | 12.98 V | 12.91 V | 12.84 V | −0.039 V/wk |
| Glenbrook 2 | 12.75 V | 12.72 V | 12.58 V | −0.039 V/wk |

**Both were on healthy power right up to the moment they stopped.** A
12 V lead-acid supply at 12.8–13.0 V is mid-range on charge, nowhere
near the ~11.5 V where a logger stops writing.

So the cause is **telemetry, the logger, or physical damage** — not
power. Which is the better outcome, because a logger that lost only its
radio has been recording the whole time. Blackwood 2 alone would be
three months of west-shore precipitation we currently treat as lost.

You'll know which when it comes back: if the gap backfills, it was
telemetry.

## The first version of this got it wrong

It reported *"Blackwood 2: power failure with a visible run-up"* on a
battery sitting at 12.84 V. It triggered on the **trend** while ignoring
the **level**.

At −0.039 V/week from 12.84 V, the battery had **eight months** of
headroom. That slope is drift or seasonal variation, not a failure in
progress. The same slope at 11.6 V would matter a great deal.

Fixed two ways:

- **Level before trend.** A healthy final voltage rules out power
  whatever the slope was doing.
- **Trend expressed as headroom.** `weeks_to_floor` projects the
  current floor forward at the observed slope, and the flag fires when
  that is under twelve weeks. A slope with no headroom behind it means
  nothing.

## And three false alarms

The same run flagged Glenbrook 1, 2 and 5 as at risk. All three were
artifacts:

**Glenbrook 5** — flagged on a 30-day floor of 10.90 V while trending
**up** at +0.276 V/week. It had a bad spell and recovered; the flag was
reading history as a forecast. The "current floor" is now the last
**seven** days.

**Glenbrook 2** — "declining" at a 12.58 V floor. The same level-blind
trigger.

**Glenbrook 1** — mean, minimum and 30-day floor all exactly **11.45 V**.
Three statistics landing on one number means there is essentially one
distinct value in the window. That's a data question, not a battery
question, and it's still open.

## Reading the output

The **overnight floor** carries more signal than the mean: a battery
recovering to 13 V each afternoon but touching 11.6 V at dawn is in
more trouble than one sitting flat at 12.2 V. The floor is what stops
the logger.

Thresholds are advisory — comfortable above 12.0 V, at risk below
11.5 V — with the real figures depending on chemistry, temperature and
shared load.

## Will it happen to others?

On the corrected logic, **no station currently shows a declining supply
with limited headroom**. Whatever took Blackwood 2 and Glenbrook 2 is
not visible in the remaining five.

That's a more useful answer than the alarm the first version raised, and
it took being wrong to get there.

## Instruments go quiet on their own, too

Not every outage takes the whole station. At **Glenbrook 5**, the air
temperature and humidity probe was offline from early June to mid-August
2025 while the soil sensors logged straight through:

| Month | Soil readings | Air readings |
|---|---|---|
| June 2025 | 2,466 | 458 |
| July 2025 | 2,142 | 0 |
| August 2025 | 2,975 | 1,291 |

Temperature and humidity counts are identical at every station, so they
always drop out together — one combined probe, not two sensors. Glenbrook
2 and 4 have similar shortfalls; all three are the stations carrying tree
dendrometers, which may or may not be a clue.

## Does an outage backfill when the station comes back?

Glenbrook 5 answers that once. It nearly vanished for **June 2026** — 31
readings all month, soil and air alike — and came back in July. TEON's
record count for the station matches ours, so that month never came back
upstream.

One example, not a rule. A station that lost only its radio would still
have a full card to upload. But it's a reason not to count on Glenbrook 2
and Blackwood 2 filling in their gaps, even though both went dark on
healthy batteries.

## Glenbrook 4: both outcomes at one station

The watcher's Issue #4 (2026-09-25) caught Glenbrook 4 going quiet and
coming back, with its record count jumping by 96 — exactly one day of
15-minute readings — in a six-hour window where a healthy station adds 24.
The hole query shows no gap for 2026-09-23 to 09-24: **every reading from
the silent period came back.** The logger kept recording while it couldn't
transmit, and TEON's collection picked up the backlog once the link returned.

The same station lost about **68 days** to earlier outages that never came
back — TEON's own count matches ours, so the readings don't exist upstream.
The battery in the 24 hours before each one tells them apart:

| Went quiet | Length | Battery floor, day before | Reading |
|---|---|---|---|
| 2024-12-03 | 3 days | 8.1 V | power failure |
| 2025-02-02 | 8 days | 6.8 V | power failure |
| 2025-03-14 | 8 days | 7.9 V | power failure |
| 2025-05-12 | 18 days | 11.6 V | borderline |
| 2025-08-11 | 7 days | 11.9 V | borderline |
| 2025-09-08 | 1 day | 12.2 V | not power |
| 2025-10-15 | 21 days | 12.4 V | not power |
| 2026-09-23 | 1 day | ~12.9 V | not power — **recovered** |

**Winter 2024–25 was power.** A 12 V battery at 7–8 V is deeply drained;
the logger shut down and recorded nothing, which is why nothing came back.
Short days, low sun and snow on the panel at ~1,900 m would do it.

**The fall 2025 outages weren't power**, yet still never came back. The
21-day one began on 2025-10-15, the day the season's first storm reached the
lake in the transect data. Water ingress or lightning are possible; the
timing is the only evidence.

The battery floor before each outage rises steadily through 2025. Either the
power system was upgraded around spring 2025 or it's partly seasonal. The
monthly battery floor for winter 2025–26 would settle it: still near 12 V
means an upgrade, a sag that held means seasonal.
