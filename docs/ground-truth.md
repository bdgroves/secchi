# Ground truth: the TERC Secchi record

Every other stream in this project is a **predictor** of clarity. This is
clarity itself. Without it there is nothing to calibrate or validate
against, and no amount of additional sensor data substitutes.

## Where it lives

Not in a PDF. UC Davis TERC publishes the individual readings to the
**Environmental Data Initiative** repository:

    https://portal.edirepository.org/nis/mapbrowse?scope=edi&identifier=1340

EDI runs the PASTA+ stack and exposes a documented REST API. Packages
there are **immutable and versioned** — each revision gets a DOI and a
package ID of the form `scope.identifier.revision`, so `edi.1340.N` is a
citable, reproducible pin. That matters a great deal if a model is ever
calibrated against it: "trained on edi.1340.7" is a real statement.

`pixi run terc-discover` walks the package and reports revisions,
entities, headers and sample rows.

## The record

Measured since **1968** — nearly sixty years, with a 10-inch white disk
lowered until it vanishes.

| Year | Annual average |
|---|---|
| 2025 | 69.2 ft |
| 2024 | 62.3 ft (19.0 m, 95 % CI 17.0–20.2 m) |
| 2023 | 68.2 ft |

**Target: 97.4 ft (29.7 m)** — the average recorded 1967–1971, adopted as
TRPA threshold standard WQ1 and the TMDL's numeric target.

In 2025 TERC took **20 readings at the long-term index station (LTP)** and
**12 at the mid-lake index station**. So the calibration target is a few
dozen readings a year, not a continuous series — which constrains what a
nowcast can honestly claim.

## The structural fact that should shape any model

TERC reports winter and summer separately, because they behave
differently:

| 2025 | Average |
|---|---|
| Winter | 68.9 ft (21.0 m) — *within the normal winter range* |
| Summer | **53.4 ft (16.3 m) — fifth poorest summer on record** |

Winter clarity is **stable**. Summer clarity shows **significant
variability and long-term degradation**. The annual mean averages that
structure away.

So: **a clarity model built on this record should be seasonal.** Fitting a
single annual relationship would hide the only part of the signal that is
actually moving. TERC attributes the summer decline to elevated algal
growth and accumulation of fine particles during warm, stratified months —
which is precisely the pair of mechanisms we already measure.

Extremes within 2025 for scale: greatest single reading **103 ft (31.4 m)**
on 7 March, likely following deep winter mixing bringing clear
low-particle water up from depth; lowest winter reading **56.6 ft (17.3 m)**
on 4 January.

## Precision, and what it bounds

Jassby et al. (1999) put the method's precision at **±0.027 m** between two
observers. That is remarkably tight for a rope-and-disk measurement, and
it sets a floor on how much precision a model calibrated against this can
legitimately claim.

## TERC confirms our clarity chain — and is doing the same thing

From the 2025 report, verbatim in substance: past research indicates fine
particles in the upper waters are the main factor governing lake clarity,
and clarity in 2025 correlated with a seasonal peak of fine particles.

That independently corroborates the chain in
[`clarity-chain.md`](clarity-chain.md), built from the USGS parameter
definitions.

It also comes with a caution worth stating plainly. In 2025 TERC began
*"lining up decades of data on the potential drivers of clarity, alongside
Secchi depth, to better examine which drivers of water clarity may shift
from year to year"* — sediment from streams and atmosphere, and the
interplay of phytoplankton and picoplankton. In 2026 they are deploying
new imaging technology to visualise particle aggregation.

**That is the same analysis this project's nowcast idea describes, being
done by the people with the instruments, the record and the funding.**
Worth knowing before framing `secchi` as filling a gap. What this project
can honestly be is a fast, public, reproducible view over data that is
otherwise scattered across two agencies and several undocumented
endpoints — which has real value — rather than a novel scientific result.

## Implementation notes

`src/secchi/sources/terc.py` is written from EDI's published API docs;
this environment cannot reach `pasta.lternet.edu`. Two consequences:

- **`terc-discover` is the verification step.** It prints the real headers
  and sample rows so the column hints can be replaced with exact names.
- **Column identification is heuristic for now.** `_guess_columns` matches
  substrings, longest hint first, and returns `None` rather than picking
  something wrong — a mis-identified depth column silently produces
  plausible garbage, which is worse than a visible gap.

One case the guesser will fail on, deliberately: a package that splits the
date across `year` / `month` / `day` columns. It reports no date column
rather than inventing one. If `terc-discover` shows that layout, the
parser needs a small addition.

The metre-to-foot conversion is verified against TERC's own published
figures: 31.4 m → 103.0 ft, 21.0 m → 68.9 ft, 16.3 m → 53.5 ft. All match.
