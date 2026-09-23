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

Unexplained. Sampling the source rasters at each station point — soil
depth, texture, aspect, distance to the channel — is the honest next
step, and it's real GIS work rather than another query over data we
already hold.

## The general lesson

Correlation was the wrong tool twice, and a rate estimate was invalid a
third time, all while returning confident-looking numbers.

**A statistic that can't distinguish your hypothesis from the null isn't
weak evidence — it's no evidence.** Worth checking what a method returns
on data where you know the answer, and worth noticing when independent
signals produce suspiciously similar values.
