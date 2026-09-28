# Dissolved oxygen, altitude, and a finding that changed shape

Lake Tahoe's surface sits at about **1,898 m**, where air pressure is
roughly **79.5 %** of sea level. Oxygen saturation depends on pressure,
so a percentage referenced to the wrong atmosphere is wrong by about
twenty points — enough to invert the ecological reading.

Two instrument families in TEON's network report saturation. **One gets
it right and one doesn't.**

## The measurement

| Site | Instrument | Published | At lake pressure | Gap |
|---|---|---|---|---|
| Camp Richardson | MiniDOT | 102.96 % | 102.6 % | −0.4 |
| Lake Forest | MiniDOT | 101.60 % | 101.2 % | −0.4 |
| Lakeside | MiniDOT | 99.91 % | 99.4 % | −0.5 |
| incline_lake | MiniDOT | 104.03 % | 103.6 % | −0.4 |
| tahoe_keys_lake | MiniDOT | 101.89 % | 101.5 % | −0.4 |
| tallac_lake | MiniDOT | 103.78 % | 103.4 % | −0.4 |
| Blackwood 3 | EXO | 83.40 % | 104.9 % | **+21.5** |
| Meeks | EXO | 84.90 % | 106.9 % | **+22.0** |
| Sunnyside | EXO | 82.70 % | 104.0 % | **+21.3** |
| Glenbrook | EXO | 86.00 % | 108.2 % | **+22.2** |

**MiniDOT mean gap: −0.41 points. EXO mean gap: +21.75 points.**

## What that means

I expected both fleets to share the error — a MiniDOT computes
saturation the same way, from temperature and a configured pressure, so
the same misconfiguration was plausible. That prediction was wrong, and
being wrong made the finding stronger.

The MiniDOTs **are** altitude-corrected. Their own figure agrees with a
saturation computed independently from their concentration and
temperature at lake pressure, to within half a point, at all six sites.

So within one network, one instrument family is right and the other is
not. That rules out the most charitable explanation — a deliberate
sea-level convention adopted for cross-site comparability — because it
would have been applied to both. It points at EXO sonde configuration,
and TEON's own MiniDOTs are the proof the correction is achievable.

## It also validates our arithmetic

Six independent devices, each doing this calculation internally, agree
with ours to 0.4 points. That checks the Weiss (1970) saturation formula
and the standard-atmosphere pressure model against hardware.

Which matters for the claim about the EXO sondes: **the 104–108 % we
report isn't our model being wrong.** It's the number those instruments
should be publishing.

The residual −0.4 is expected and small: we use the standard atmosphere
while the instrument uses whatever pressure it was configured with, and
real barometric pressure varies with weather by a few percent.

## What the dashboard shows

Three oxygen readings on each lake card, and TEON's value is never
overwritten:

| Reading | Source |
|---|---|
| **DO** — mg/L | as published |
| **DO sat** — % | as published |
| **DO sat (local)** — % | derived here from concentration and temperature |

On the MiniDOT cards the last two now agree, which is itself the
evidence. On the EXO cards they differ by twenty points.

Derived readings carry a `computed from …` label so a number of ours is
never mistaken for one of theirs.

## Method

Saturation uses **Weiss (1970)**, verified against published freshwater
tables to 0.004 mg/L. Pressure uses the International Standard
Atmosphere. Salinity is ignored — Tahoe runs about 0.04 ppt, which moves
saturation by well under a tenth of a percent.

`pixi run oxygen-check` tests both hypotheses against every reading
carrying temperature, concentration and percentage on the same
timestamp, and reports which fits.

## Update, 2026-09-28: 4H Camp, and a check that let one sonde blur a fleet

TEON made 4H Camp public on 2026-09-26, adding a fifth EXO sonde. It is
referenced to **sea level** like the other four: month by month its
effective reference pressure matches Glenbrook's to three decimals.

But adding it moved the EXO fleet's fit from **0.004 to 0.242 mg/L**. The
cause wasn't configuration: for about six days in July 2026, ending
2026-07-24, its optical channels were filed under the wrong names — 564
readings. Averaged over those readings against the rest of the month:

| Field | During | Normally | Which is really |
|---|---|---|---|
| `Do_mgL` | 85.83 | 7.83 | the saturation (normally 85.01) |
| `Do_percent` | 0.65 | 85.01 | the chlorophyll (normally 0.67) |
| `Turbidity` | 7.74 | −2.37 | the concentration (normally 7.83) |

Temperature, conductivity, TDS, salinity and depth were unaffected, and pH
reported 89.4 once — another saturation value in the wrong place. This
looks like sensors moved between ports at a service visit with the parser
keeping the old order. If so, the readings are intact and could be
remapped upstream.

An earlier draft of this note said the concentration "stayed normal",
inferred from the ratio exploding. It read 85 mg/L. Both fields were wrong.

Blackwood 3's 22 set-aside readings are a different case: internally
consistent low oxygen (5.6 mg/L, 48 %) over about four hours in April 2026,
with warmer water, disturbed optical channels and a freshly charged
battery — a sonde being handled at a service visit.

The check averaged errors across the whole fleet, so one sonde's bad week
blurred every EXO verdict. It also dropped saturation ≤ 0 **silently**
while keeping near-zero positives like 0.01 %.

Now readings outside 50–150 % saturation are set aside, **counted and
reported by site and month**, and each site gets its own fit alongside the
fleet's.
