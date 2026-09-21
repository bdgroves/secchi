# Dissolved oxygen, and why altitude matters

Oxygen is the one lake measurement TEON publishes two ways, and the two
are not interchangeable.

| Field | Units | Question it answers |
|---|---|---|
| `Do_mgL` | mg/L | How much oxygen is dissolved. What a fish experiences. |
| `Do_percent` | % | How much relative to what this water *can* hold. Which way the biology is pushing. |

Above 100 % saturation means photosynthesis is outrunning respiration.
Below means the reverse. It's the more ecologically informative of the
two — and it's the one that depends on barometric pressure.

## The problem

Oxygen saturation falls with pressure, and **Lake Tahoe's surface sits at
about 1,898 m** — roughly **79.5 %** of sea-level pressure, 604 mmHg
against 760.

That is a large correction:

| Temperature | Saturation at sea level | Saturation at Tahoe |
|---|---|---|
| 4 °C | 13.11 mg/L | 10.42 mg/L |
| 8 °C | 11.84 mg/L | 9.41 mg/L |
| 12 °C | 10.78 mg/L | 8.56 mg/L |
| 16.4 °C | 9.79 mg/L | 7.78 mg/L |
| 20 °C | 9.09 mg/L | 7.22 mg/L |

Large enough to **invert the reading**. Water reported at 81 % saturated
against a sea-level atmosphere is slightly *super*saturated at Tahoe's
altitude. One says the lake is oxygen-poor; the other says photosynthesis
is running ahead of respiration, which is what you'd expect in a sunlit
oligotrophic lake.

A YSI EXO computes saturation from temperature, salinity and barometric
pressure. If the sonde has no barometer, or was configured with a default
sea-level pressure, the published percentage is referenced to the wrong
atmosphere.

## The answer, from the data

`pixi run oxygen-check`, 2026-09-18, across **1,068 readings** carrying
temperature, concentration and percentage on the same timestamp:

| Hypothesis | Mean absolute error |
|---|---|
| percentage referenced to **sea level** | **0.004 mg/L** |
| percentage referenced to lake altitude | 1.627 mg/L |

0.004 mg/L is the precision of the Weiss formula itself. This is not a
close call.

**TEON's `Do_percent` is referenced to a sea-level atmosphere.** What that
means in practice:

| Site | Temp | Concentration | Published | At lake pressure |
|---|---|---|---|---|
| Sunnyside | 16.3 °C | 7.93 mg/L | 80.9 % | **102 %** |
| Glenbrook | 16.5 °C | 7.98 mg/L | 81.7 % | **103 %** |

81 % reads as mildly oxygen-stressed water. 102 % of what the water can
actually hold at 1,898 m means photosynthesis is running ahead of
respiration. **Opposite ecological conclusions from one dataset.**

And a real result falls out of it: both nearshore sites are slightly
supersaturated, so both are net-photosynthetic at the surface. Expected
for a sunlit oligotrophic lake in September — but measured rather than
assumed, and invisible in the published percentage.

### Bug or convention?

Probably a configuration issue, but I can't see their sonde setup so I
won't call it a bug.

The field standard for DO percent saturation — APHA, and USGS's own
parameter `00301` — references **local** barometric pressure. An EXO
computes saturation from temperature, salinity and a pressure value; if
that value isn't set to local, sea-level referencing is what you get by
default.

What can be said without seeing their configuration: the number does not
mean what a reader at Tahoe would assume it means.

## How the dashboard handles it

Three oxygen readings on each lake card, and the distinction is deliberate:

| Reading | Source |
|---|---|
| **DO** — mg/L | TEON, as published |
| **DO sat** — % | TEON, as published (sea-level referenced) |
| **DO sat (local)** — % | **derived here** from their concentration and temperature |

TEON's published value is never overwritten. The local figure is computed
from `Do_mgL` and `Temp` rather than by rescaling their percentage, so it
depends on their *measurements* and not on their saturation assumption.

Derived readings carry a visually distinct `computed from …` label, so a
number of ours is never mistaken for a number of theirs.

## The test

`pixi run oxygen-check` doesn't assume either way. For every reading that
carries temperature, concentration *and* percentage on the same timestamp,
it computes what the concentration would be under each hypothesis and
reports which matches the value TEON actually published.

The hypotheses are about 1.6 mg/L apart at Tahoe temperatures — far beyond
instrument noise — so the data decides cleanly. Verified against synthetic
records built each way; the check identifies both correctly with zero error
on the matching hypothesis.

If the two are closer than 0.3 mg/L the check reports **inconclusive**
rather than guessing.

## The physics

Saturation uses **Weiss (1970)**, the standard formulation, verified
against published freshwater tables:

| Temp | Computed | Published | Error |
|---|---|---|---|
| 0 °C | 14.62 | 14.62 | 0.001 |
| 10 °C | 11.29 | 11.29 | 0.002 |
| 20 °C | 9.09 | 9.09 | 0.002 |
| 30 °C | 7.56 | 7.56 | 0.001 |

Worst error 0.004 mg/L.

Salinity is ignored deliberately — Tahoe runs about 0.04 ppt, which moves
saturation by well under a tenth of a percent.

Pressure uses the International Standard Atmosphere. **Real barometric
pressure varies with weather by a few percent**, which is smaller than the
altitude effect but not nothing, so an altitude-referenced percentage
computed this way is approximate. A sonde with a working barometer would
be better than our correction; the point of the check is to find out
whether one is in play.

## The display change

The lake cards originally showed oxygen as a **percentage only** — the
concentration was ingested and never rendered. That hid the discrepancy
completely: with only one of the two numbers visible there's nothing to
compare.

Both now appear, concentration first. `EXO_CARD_VARIABLES` is
`("Temp", "Do_mgL", "Do_percent", "Chl_a", "Turbidity")`.

## The USGS cross-check

Three tributary gauges — Blackwood, General and Trout Creek — publish
`00300` dissolved oxygen in mg/L. Different agency, different instruments,
and **concentration only**, so no saturation assumption is embedded.

That makes them a useful sanity check on the lake sondes' concentrations,
in the same way Blackwood's turbidity cross-checked Sunnyside's −2.11 FNU
offset. Stream water isn't lake water, but an implausible lake
concentration would stand out against creek values measured the same day
at similar temperatures.

## What this doesn't settle

Whether TEON *intends* the percentage to be sea-level referenced. Some
programmes report against a standard atmosphere deliberately, for
comparability across sites at different elevations. That's a defensible
choice — it just needs stating, because a reader who assumes local
referencing draws the opposite conclusion about the lake.

If the check says uncorrected, the honest presentation is to show both the
published percentage and the altitude-referenced one, labelled, rather than
silently replacing theirs with ours.
