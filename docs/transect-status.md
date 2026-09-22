# The transect: where this actually stands

Four methodological bugs, four different headline numbers, in one
afternoon. That pattern is the finding.

## The numbers I have given, in order

| Attempt | Headline | What was wrong |
|---|---|---|
| 1 | west responds **3.31×** harder | Arithmetic mean of ratios — wrong operation for a multiplicative quantity. Three outliers carried it while 5 of 15 events favoured the east. |
| 2 | **2.82×** more events on the west | Diurnal cycle counted as a storm every afternoon. 18 h cooldown < 24 h period. |
| 3 | **1.29×** more events, **1.54×** magnitude | Matching used a 12 h window while detection ran on daily means. One storm crossing midnight became two one-sided events — 13 times. |
| 4 | *pending* | — |

Every correction moved the number **down**, toward the null. That
direction is worth noticing: each bug made the rain shadow look stronger
than the data supports.

## What that tells us

Not that the analysis is unsalvageable — each fix was correct and the
method is better for them. But four methodological choices have each
moved the headline by more than the effect being measured.

**When the analyst's choices move the answer more than the physics does,
the data is under-determining the question.**

The stable findings are the ones that don't depend on thresholds:

- **Lag: +3.7 to +4.3 h, west to east**, in every version. It depends
  only on which station moved first.
- **Catchment precipitation ratio: 2.12×**, which comes from TEON's
  watershed layer, not from us.
- **The largest events are west-shore**: +22.16 on 2025-10-02, +16.05 on
  12-17, +9.40 on 11-16.

The ratios — frequency, magnitude, total — have moved every time I
touched the detector, and should be treated as provisional.

## What would fix it properly

```bash
pixi run backfill --stage blackwood
```

**593,507 precipitation records** at Blackwood 2, a west-shore site.

That changes the question from *"did both stations wet at the same
time?"* — which requires inferring storms from responses, and is where
every one of these bugs lived — to *"how much did each station wet after
this much rain?"*, which is a measurement.

With a rain gauge the event list stops being a modelling choice. Storms
are identified from precipitation, and soil moisture is the response
rather than both the evidence and the conclusion.

**That is the next step, and I would not trust another ratio from this
analysis until it's done.** Blackwood 2 has been dark since June, so
this is history rather than a live feed — but it covers the same winter
the transect stations recorded.

## What can be said now

The west shore is wetter, wets earlier, and takes the largest events.
The precipitation gradient does reach the soil.

By how much is not yet settled by this data, and the honest range across
my four attempts is somewhere between 1.3× and 2×, against a
climatological 2.12×.
