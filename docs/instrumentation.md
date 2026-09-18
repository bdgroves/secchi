# Instrumentation

What TEON actually deploys, how each class of instrument works, and what it
would take to stand up something similar somewhere else — Walker Lake, an
alpine watershed, anywhere with a question and a budget.

## Confidence levels used below

Reverse-engineering hardware from an API takes some care, so each claim is
tagged:

- **Named** — the instrument family appears verbatim in TEON's API.
- **Signature** — inferred from field naming conventions or data
  structure that are distinctive to one vendor's ecosystem.
- **Typical** — the class of instrument that produces these measurements;
  the specific model is *not* established, and alternatives are noted.

---

## The lake: YSI EXO multiparameter sonde

**Named.** The API's sensor type is literally `EXO`, and the field set
matches an EXO sonde exactly.

A multiparameter sonde is a single submerged tube carrying several probes
around a central wiper. It reports on one timestamp, every 15 minutes here:

| Channel | Probe type | How it works |
|---|---|---|
| Temperature | thermistor | Resistance changes with temperature. The reference channel — almost every other measurement needs temperature compensation. |
| Conductivity → salinity, TDS | four-electrode cell | Measures the water's ability to carry current. Salinity and total dissolved solids are computed from it, which is why all three move together. |
| Dissolved oxygen | optical (luminescent) | A dye is excited by light; oxygen quenches its luminescence, and the decay time gives concentration. Optical DO doesn't consume oxygen the way older membrane probes did, so it's far better suited to long deployments. |
| Turbidity | 90° nephelometer | Shines near-infrared light and measures scatter at 90°. Reported in FNU. **This is the channel with the Sunnyside zero-offset fault.** |
| Chlorophyll-a | fluorometer | Excites around 470 nm, measures emission around 685 nm. Measures *fluorescence*, a proxy for algal biomass — not chlorophyll directly. |
| Phycocyanin | fluorometer | Different excitation/emission pair, targeting cyanobacteria specifically. |
| pH | glass electrode | Potential difference across a glass membrane. **The shortest-lived probe on the sonde** and the first to fail — which is exactly what TEON's fleetwide pH failure looks like. |
| Depth | pressure transducer | Water column pressure above the sensor. Doubles as a check that the sonde is where you left it. |

**What you must plan for: biofouling.** Algae grows over optical windows
within weeks in productive water. An unwiped fluorometer or nephelometer
drifts, and the drift looks like real environmental change. This is why
TEON's team snorkels to every nearshore sonde **monthly**, per their own
story map — retrieve, clean, download, recalibrate against certified
standards, redeploy. Budget that labour from day one; it is the dominant
operating cost of a sonde network, not the hardware.

**Cost order of magnitude:** a configured multiparameter sonde is the most
expensive item in a network like this by a wide margin — think a used car
rather than a laptop, per unit, depending on which probes you fit.

---

## The lake, cheaper: MiniDOT and HOBO loggers

**Named.** API sensor types `Minidot` and `Hobo`.

These are the other half of TEON's nearshore network — twelve instruments
across six sites (Camp Richardson, Lake Forest, Lakeside, Incline, Tahoe
Keys, Tallac), each with 35,000–47,000 observations. They are *dormant in
the API* as of September 2026 but hold the largest nearshore archive in
the network.

**PME MiniDOT** logs dissolved oxygen and temperature only. Self-contained,
battery-powered, no telemetry — you retrieve it and download over USB.

**Onset HOBO** is a family of small data loggers; in this deployment,
temperature.

**Why this matters for replication.** A full multiparameter sonde per site
is usually unaffordable. Single-purpose loggers cost one to two orders of
magnitude less, need no wiper, and tolerate long unattended deployments.
TEON's own design is the lesson: a **small number of full sondes as
reference stations, surrounded by many cheap loggers for spatial
coverage.** Five EXO sondes tell you what the water is doing in detail;
twelve MiniDOT/HOBO units tell you whether that detail generalises around
the shoreline.

If you're starting a lake program on a real budget, start here, not with
sondes.

---

## The land: Campbell Scientific dataloggers

**Signature, and a strong one.** Three independent tells:

1. The S3 bucket is named `teon-loggernet-data-storage`. **LoggerNet** is
   Campbell Scientific's datalogger support software.
2. Field names follow Campbell's CRBasic aggregate convention exactly:
   `BattV_Avg`, `BattV_Min`, `PTemp_C_Avg`. The `_Avg` / `_Min` suffixes
   are how Campbell DataTables name aggregated columns.
3. `PTemp_C_Avg` is *panel temperature* — the logger enclosure's own
   internal temperature, a Campbell diagnostic staple. No third-party
   sensor reports this; the logger reports it about itself.

A datalogger is the hub: a low-power microcontroller in a weatherproof
enclosure, running a program on a fixed schedule, reading attached
sensors, timestamping, writing to internal memory, and reporting battery
and panel temperature so you can tell a dead sensor from a dead station.

**The single most useful structural finding in this project** comes from
this architecture: TEON's API serves **one logger table under three or four
different sensor names.** At a terrestrial station, the `soil-moisture`,
`air-temperature`, `tree-stress` and `stream-level` endpoints return
identical record UUIDs, identical `BattV_Avg`, identical row counts. The
inventory's "43 sensors" is really about **13 physical devices**. If you
build an API over a logger network, be explicit about the difference
between a logger, a sensor and a channel — consumers will otherwise
mistake your endpoint count for your instrument count.

### What hangs off those loggers

**Soil: volumetric water content, electrical conductivity, temperature, at
two depths.** *Typical* — this triple-output-per-depth pattern is the
signature of a capacitance/TDR soil probe such as the METER TEROS 12 or
Campbell's CS65x family. VWC is measured via the soil's dielectric
permittivity (water has a far higher dielectric constant than soil or air,
so permittivity tracks water content). Raw values arrive as a fraction —
`0.057` means 5.7 % by volume.

TEON's schema has slots for five depths (`Soil_VWC` through `Soil_VWC_5`)
but only two are populated anywhere. If you design a schema, expect this:
provision for the deepest install you might ever do, accept that most
sites will null most columns.

**Air temperature and relative humidity.** *Typical* — a combined
temperature/RH probe inside a radiation shield. The shield matters more
than people expect: unshielded air temperature in mountain sun reads
several degrees high. If you replicate this, an aspirated or well-designed
naturally-ventilated shield is not optional.

**Tree stress and growth: eight band dendrometers per station.** *Typical*
— a band dendrometer is a metal or invar band around the trunk connected
to a displacement sensor, resolving circumference change at micrometre
scale. TEON reports `Tree_1_diameter_change` … `Tree_8_diameter_change`
with values around 1,000–3,000 (units inferred as µm from magnitude, not
documented).

This is a genuinely clever measurement and worth understanding: stem
diameter changes on **two timescales at once.** Seasonally it grows as the
tree adds wood. Daily it *swells and shrinks* as the tree's water content
rises overnight and falls during transpiration. So a dendrometer is
simultaneously a growth record and a **water-stress record** — the daily
shrink-swell amplitude tells you how hard the tree is working for water.
For drought monitoring this is much more direct than inferring stress from
soil moisture alone.

**Precipitation.** *Typical* — a tipping-bucket gauge, most likely. The
Blackwood 2 gauge has **593,507 records**, the largest single dataset in
the network, consistent with event-driven logging (a record per tip)
rather than fixed-interval sampling. Note for cold climates: a plain
tipping bucket under-catches snow badly. Weighing gauges or heated/
shielded designs exist for a reason, and at Tahoe elevations that choice
determines whether your winter precipitation record means anything.

**Stream level.** *Typical* — a submerged pressure transducer. TEON's field
is named `Uncalibrated_water_depth`, which is admirably honest: a pressure
reading becomes a *stage* only after you survey the sensor against a
datum. Contrast USGS's `63160 Stream level, NAVD88` on the same creek,
which is referenced to a national vertical datum and therefore comparable
between sites and across years. **If you deploy stream sensors, survey them
to a real datum.** Otherwise you have relative numbers that can never be
compared to anyone else's.

**Field cameras.** 30-minute intervals, writing JPEGs into S3 under paths
like `.../Glenbrook 2 - Terrestrial/Snow photos/`. Roughly 3,378 frames
across five stations. The folder name says the purpose: snowpack. A camera
is the cheapest possible snow-depth-and-presence sensor, and it also gives
you visual ground truth when a number looks wrong — which, as this project
keeps demonstrating, is often.

---

## Telemetry: the decision that shapes everything

TEON runs both models side by side, which makes the trade-off unusually
legible in the data:

| | Self-logging | Telemetered |
|---|---|---|
| Data arrives | when you retrieve it | continuously |
| Record completeness (observed) | **98.8 %** | 73.6 – 96.8 % |
| Infrastructure | none | power, radio/cellular, receiver |
| Failure mode | silent until next visit | visible immediately |
| Cost per site | low | higher, ongoing |

Those completeness figures are measured, not assumed — Blackwood 3 and
Meeks (self-logging) sit at 98.8 %, while Sunnyside (telemetered) has lost
**over a quarter** of its expected record to transmission gaps.

That is the counter-intuitive part worth carrying into any new design:
**telemetry gets you timeliness, not completeness.** A logger with no radio
writes to memory and almost never misses. A telemetered station misses
whatever the radio link drops. If your question is "what is happening right
now", telemeter. If your question is "what happened over the last decade",
a self-logging instrument you visit quarterly may give you a *better*
dataset for less money.

The other half of the trade: a self-logging sensor that floods, gets
buried, or has its battery die is **silently dead until someone walks out
there.** Telemetry's real value is often not the data — it's knowing the
station is alive.

---

## If you were building this at Walker Lake

Walker Lake is a different problem from Tahoe — terminal, hypersaline and
falling, rather than oligotrophic and famously clear. What carries over and
what doesn't:

**Carries over directly**
- Reference-plus-coverage design: a couple of full sondes, many cheap loggers.
- Campbell-class loggers for anything on land. Well documented, repairable,
  enormous community.
- Survey every water-level sensor to a real vertical datum. For a lake
  whose defining story *is* falling water level, this is the whole ballgame.
- Cameras. Cheap, and they answer "is that number real" better than
  anything else.
- A public API from day one, with an explicit distinction between logger,
  sensor and channel.

**Needs rethinking**
- **Salinity range.** Walker Lake's conductivity is orders of magnitude
  above Tahoe's ~94 µS/cm. Standard freshwater conductivity cells and the
  salinity algorithms behind them are out of range; you need marine or
  hypersaline-rated probes and a defensible conversion.
- **Turbidity vs. clarity.** Tahoe's question is transparency measured in
  tens of metres. Walker's is suspended sediment and algal density in a
  shallow, wind-mixed, much more productive system. Different sensor
  ranges, and Secchi depth may not be the headline metric at all.
- **Chemistry.** Terminal lakes concentrate dissolved constituents. pH,
  alkalinity and specific ion chemistry matter far more than at Tahoe, and
  pH is exactly the probe most prone to failure — plan redundancy and
  frequent calibration, or use discrete lab samples for the chemistry and
  sondes only for the physics.
- **Inflow.** Walker's story is the Walker River and upstream diversion.
  A gauge network on the river with real datums, plus the existing USGS
  record, probably matters more than in-lake instrumentation.
- **Biofouling may be lower** in hypersaline water, which could relax the
  monthly-visit burden. Worth testing rather than assuming.

**The transferable lesson from this project**, which has nothing to do with
hardware: the API design and the metadata discipline determined whether
the data was usable. TEON's network is good. Finding out that "43 sensors"
meant 13 devices, that pH was dead everywhere, that one turbidity channel
had a −2.1 FNU offset, and that timestamps mixed two timezone conventions
— all of that took a day of probing precisely *because* none of it was
documented. Publish a data dictionary, publish your calibration events,
declare your timezone, and distinguish provisional from approved values
the way USGS does. Those cost almost nothing and they are the difference
between data and a puzzle.

---

## Sources

- TEON API structure, field names and record counts: observed directly,
  September 2026. See [`data-dictionary.md`](data-dictionary.md).
- Monthly snorkel-and-retrieve cycle, the ten nearshore stations, the four
  monitoring domains, and the smoke-shadow/algal-bloom mechanism: TEON's
  ArcGIS StoryMap, "Eyes on the health of the Tahoe Basin", July 2025.
- Telemetry completeness figures: computed from inventory record counts
  against deployment windows.
- Instrument operating principles: general environmental-monitoring
  practice. Specific model attributions are marked *Typical* above and
  are **not** established from TEON documentation.
