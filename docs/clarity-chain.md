# The clarity chain

Parameter `70369` closed the loop on what this project is actually
measuring, so it's worth writing the chain down explicitly.

## What sets Secchi depth

Lake Tahoe's transparency is controlled by two processes:

- **Fine sediment particles scatter light.** Inorganic particles smaller
  than 16 µm — about a third the width of a human hair — are, per EPA,
  "the main pollutant that is degrading deep-water clarity", accounting for
  roughly **two-thirds** of the lake's impairment.
- **Algae absorb light.** Free-floating phytoplankton, fed by nitrogen and
  phosphorus, account for most of the remaining third.

Lake Tahoe is Clean Water Act 303(d)-listed as impaired for nitrogen,
phosphorus and sediment on exactly that basis. The TMDL requires a **65 %
reduction in fine sediment particles** (plus 10 % N, 35 % P) to restore
annual average Secchi depth to **97.4 ft (29.7 m) by 2076**, with an
interim "Clarity Challenge" of **78 ft by 2031**.

Worth noting: as of the 2022 TMDL performance report, clarity was **not
improving** despite achieved pollutant reductions.

## What we can now measure against that

| Process | Our measurement | Source |
|---|---|---|
| Fine sediment, the regulated fraction | `70369` — particles 0.5–16 µm, count/L | USGS, Upper Truckee River |
| Sediment proxy, continuous | `63680` turbidity, FNU | USGS, 5 tributaries |
| Algal biomass | `Chl_a` fluorescence, µg/L | TEON, 3 lake sondes |
| Cyanobacteria specifically | `phycocyanin`, µg/L | TEON, 3 lake sondes |
| Sediment delivery driver | discharge `00060` | USGS, 8 tributaries |
| Catchment sediment supply | `PrecipAvg`, `TotalFire`, `ImpervAvg`, `SlopeAvg` | TEON watershed layer |
| Historical sediment load | `80154`/`80155`, 1974–1992 | USGS, Blackwood |

**The size class match is exact.** `70369` covers 0.5–16 µm; Lake Tahoe
Info describes the clarity-responsible fraction as 0.5–16 µm. This is not a
loose proxy — it is the regulated pollutant, measured continuously, at the
lake's largest tributary.

## The caveat that matters

USGS's own definition says `70369` is **"computed by regression
equation"**. It is a surrogate, almost certainly derived from turbidity at
the same gauge, not a laboratory particle count. Consequences:

- It inherits turbidity's measurement error.
- The regression is site-specific, so values are not freely comparable
  between gauges.
- It should be labelled as modelled rather than measured wherever it's
  displayed.

That doesn't diminish it. Continuous FSP surrogates are how the TMDL is
actually tracked — the alternative is discrete lab samples at a fraction of
the temporal resolution.

## Why this reframes the project

`secchi` is named after a disk on a rope that measures transparency. The
TMDL's target is stated in Secchi depth. And we now hold, in one pipeline:

- the **regulated pollutant** that controls that depth (`70369`)
- an independent **continuous proxy** for it at five tributaries (`63680`)
- the **algal** half of the mechanism from three in-lake sondes (`Chl_a`)
- the **catchment characteristics** that govern sediment supply
- and **131 years** of outflow plus 66 years of Blackwood discharge for
  context

The original "clarity nowcast" idea — predict Secchi depth from live
sensors — stops being speculative at that point. The inputs the TMDL's own
clarity model uses are, in surrogate form, on hand.

What's missing to actually close it: **historical Secchi measurements as
ground truth.** UC Davis TERC publishes the annual clarity record. Without
it there's nothing to calibrate or validate against, and that's the next
real dependency.
