# Glenbrook 2: three methods, no answer

One terrestrial station reads far wetter than its neighbours on the same
hillslope, in the same catchment, a few hundred metres away. The
catchment join can't explain it — all three Glenbrook stations carry
identical attributes, because catchment means describe variation
*between* catchments.

Glenbrook 2 is the only terrestrial station with its own stream sensor,
so a cheap test looked possible. It wasn't.

## First, a correction

I quoted **42 % against 3.4 %** — a 12× gap — repeatedly. That compared
a wet site to dry sites on one late-summer day.

| | 354-day mean |
|---|---|
| Glenbrook 2 | 46.0 % |
| Glenbrook 4 | 15.6 % |
| Glenbrook 5 | 10.9 % |

**2.9× to 4.2×.** Still the wettest station by a wide margin, and the
headline number was an artifact of when I looked.

## Three attempts

### 1. Correlate soil moisture against stream level

| site | r |
|---|---|
| Glenbrook 2 | 0.826 |
| Glenbrook 4 | 0.815 |
| Glenbrook 5 | 0.853 |

Useless. Rain raises the creek and wets every soil at once, so
everything correlates and a control scores higher than the site of
interest.

### 2. Correlate during recession only

The reasoning was sound — both respond to rain, so look at what happens
after. But any two smooth declines correlate. On synthetic data a
directly stream-driven site scored 0.967 against controls at 0.843 and
0.869: all high, margin meaningless.

### 3. Compare recession *rate*

The right idea, and invalid on this record:

| | time constant | vs stream |
|---|---|---|
| the creek | 2.9 d | — |
| Glenbrook 2 | 3.2 d | 1.08 |
| Glenbrook 4 | 3.1 d | 1.06 |
| Glenbrook 5 | 2.9 d | 1.00 |

**Four independent signals within 0.3 days of each other.** A stream and
three soils at completely different moisture levels do not share a
drainage rate; the method is measuring its own window.

With recession runs about six days long and a fit spanning roughly two
e-folds, `tau ≈ run_length / log_range` returns ~3 days whatever the
real rate. The synthetic test had 30-day recessions, so the fit had room
to separate a connected site (1.10×) from hillslope controls (1.59×,
1.61×). Real Tahoe storms arrive closer together than that.

`_recession_tau` now reports a validity check: when the median run
length isn't at least 3× the fitted constant, it says the estimate can't
discriminate instead of printing a number.

## What the record does support

Day-to-day change against the creek:

| site | r |
|---|---|
| **Glenbrook 2** | **0.107** |
| Glenbrook 4 | 0.259 |
| Glenbrook 5 | 0.256 |

Glenbrook 2 tracks the creek *less* closely than the hillslope controls.
Weak evidence **against** a stream connection, not for one.

## Verdict

*(Originally:)* Unexplained. Sampling the source rasters at each station
point — soil depth, texture, aspect, distance to the channel — is the
honest next step.

## Update, 2026-10-02: probably explained — a saturated canyon floor

The ground at each station, from the national soil survey (NRCS SSURGO via
Soil Data Access), USGS 3DEP elevation and USGS NHD streams, sampled on a
GitHub runner. Saved in `data/reference/station_ground.json`.

| Station | Soil moisture, Aug 2025-Aug 2026 (10th-90th pct) | Mapped soil, drainage | Water table (min) | Nearest stream | Height above ground 300 m around |
|---|---|---|---|---|---|
| **Glenbrook 2** | **46.6 % (42-49)** | Cryorthents-Xerorthents-Tahoe complex, **poorly drained**, frequently flooded | **15 cm** | **11 m, Glenbrook Creek (perennial)** | **-77 m** |
| Glenbrook 1 | 31.9 % (12-42) | same map unit, poorly drained | 15 cm | 17 m, Glenbrook Creek | -8 m |
| Glenbrook 4 | 18.3 % (7-27) | Caverock sandy loam, somewhat excessively drained, bedrock 67 cm | — | 152 m (intermittent) | +20 m |
| UNR Tahoe Campus | 15.1 % (6-26) | Inville gravelly coarse sandy loam, well drained | — | 25 m (intermittent) | -4 m |
| Homewood | 14.5 % (4-21) | Watsonlake gravelly sandy loam, well drained | — | 170 m (intermittent) | +52 m |
| Blackwood 2 | 14.4 % (4-21) | Tahoe complex, gravelly, poorly drained | 10 cm | 18 m, Blackwood Creek (perennial) | -12 m |
| Glenbrook 5 | 12.0 % (4-18) | Shakespeare silt loam, well drained | 122 cm | 4 m (intermittent) | -6 m |

What it says:

- **Glenbrook 2 is saturated, all year.** Its moisture spans 42-49 % from
  the 10th to the 90th percentile, where every other station swings by
  15-30 points. A soil at capacity can't respond to rain. That also
  re-reads the one result above that held up: Glenbrook 2 tracking the
  creek *least* day to day (0.107) was called "weak evidence against a
  stream connection". **That reading was wrong.** A saturated soil
  can't follow anything; the low correlation fits saturation, whatever
  supplies the water.
- **Its position is unlike any other station's**: 11 m from a perennial
  creek at the floor of the Glenbrook Creek canyon, 77 m below the ground
  300 m around it (the next lowest is Blackwood 2 at -12 m), on a mapped
  poorly drained, frequently flooded soil with the water table as shallow
  as 15 cm. Water from the slopes converges there.
- **Glenbrook 1, on the same map unit beside the same creek, is the
  second-wettest station** (31.9 %), which fits.
- **Blackwood 2 doesn't fit the soil map**: mapped poorly drained, 18 m
  from Blackwood Creek, but reads dry (14.4 %). Its map unit is the
  gravelly variant, and it sits only slightly below its surroundings. So
  the soil map alone doesn't decide; the canyon-floor position is what
  sets Glenbrook 2 apart. (Soil survey map units are drawn at about
  1:24,000; a station can sit on a different patch than its polygon
  says.)

**Verdict: probably explained** — a riparian site on a canyon floor,
saturated year-round. Consistent across five independent facts; not
proven. A site photo or TEON's site description would settle it.

## The general lesson

Correlation was the wrong tool twice, and a rate estimate was invalid a
third time, all while returning confident-looking numbers.

**A statistic that can't distinguish your hypothesis from the null isn't
weak evidence — it's no evidence.** Worth checking what a method returns
on data where you know the answer, and worth noticing when independent
signals produce suspiciously similar values.
