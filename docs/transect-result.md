# The transect: where it stands

Homewood and Glenbrook 5 sit 64 metres apart in latitude on opposite
shores. Their catchments receive **2.12×** different annual rainfall.
Does that reach the soil?

**Yes, at roughly the rainfall ratio in total, and storms mostly reach
the west shore first.** How long they take to cross isn't measurable at
this resolution.

## The result, on the complete record

376 days of overlap, limited by Homewood, the youngest forest station
(reporting since September 2025).

| | West (Homewood) | East (Glenbrook 5) | Ratio |
|---|---|---|---|
| wetting events | 27 | 21 | 1.29 |
| events only one shore saw | 9 | 3 | 3.00 |
| **total wetting, all events** | **146 pts** | **63 pts** | **2.32** |
| catchment rainfall | 1,463 mm | 689 mm | 2.12 |

Of the 18 storms both stations felt: geometric mean response ratio 1.73,
ratio of totals 1.51, and 6 of 18 favoured the east.

**The total wetting ratio is the headline.** It sums every event at each
station, so it doesn't depend on how events are paired, and it's the one
statistic built to be compared with rainfall. Over the year Homewood's
soil took about 2.3 times Glenbrook 5's wetting, against a 2.1 times
rainfall difference.

It leans on a few large storms — Homewood +22.2 on 2025-10-02 and +16.1
on 2025-12-17, neither felt at Glenbrook 5 — so it's encouraging, not
settled.

## Direction, and a correction about lag

**15 of 18 shared storms reached the west shore first** (2 reached the
east first, 1 the same hour). Storms arrive from the Pacific; this is
the robust timing result.

The lag **in hours** is not measurable here, and earlier versions of
this document were wrong to lead with it:

| Version | Reported lag | Why it wasn't a travel time |
|---|---|---|
| 12-hour matching window | +3.7 to +4.3 h | The window excluded every longer lag, biasing it short |
| day-level matching | +11.7 h | Storms starting the evening before are stamped at midnight; many lags came out at exactly 24 h |

Detection runs on daily means — necessarily, to reject the soil's daily
cycle — so most storm onsets can only be placed to the day. Only 6 of 18
pairs have both onsets resolved to a real hour, and those range from +4
to +37 hours. That is too few and too spread to state a crossing time.

The report now leads with direction and prints the hour figure only for
resolved pairs, with its spread and a warning.

## What changed since the last run, and why

Event counts are identical to the run before the data recovery (27 and
21), so the recovered forest rows didn't add or remove storms. What
changed is pairing: 18 storms now match instead of 7. That comes from
the fix that matches at the resolution detection works at (the same
day, give or take one), which hadn't been run until now.

## What would settle the size of the effect

The rain gauge record at Blackwood 2 — 593,507 readings, west shore,
same winter — converts "both stations wetted at once" into "each wetted
this much after this much rain". That, rather than further work on the
soil-only method, is the next step for the transect.
