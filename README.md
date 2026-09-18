# ⚪⚫ secchi

### A modern Secchi disk for Lake Tahoe

**[→ Live dashboard](https://brooksgroves.com/secchi/)** · two agencies, 33 instruments, updating hourly, entirely unattended

---

## Cold open

In 1865 the Papal Navy asked an astronomer a simple question: *how clear is the water?*

Angelo Secchi lowered a white plate over the side of a yacht in the Mediterranean and wrote down the depth at which it disappeared.

That's the instrument. A plate on a rope.

One hundred and sixty-one years later it is still the world standard for lake transparency, and it is still how Lake Tahoe's famous clarity gets reported every single year. Twenty-two metres in the 1960s. About twenty-one now. A number you get by dropping a disk in the water and watching it vanish.

On **September 15, 2026**, the University of Nevada, Reno switched on the [Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/) — sondes in the water, loggers in the forest, cameras on the ridgelines. For the first time you could watch clarity *assemble itself out of its causes*, every fifteen minutes.

There was no API documentation. There still isn't.

This is what two days of reading someone else's undocumented sensor network looks like.

---

## What this reads

```
   TEON                                    USGS
   ────                                    ────
   2  lake sondes      (YSI EXO)           10 stream & lake gauges
   6  forest loggers   (Campbell)          131 years of lake outflow
   5  field cameras                        66 years of Blackwood discharge
   60 stream catchments, 164 attributes    5 gauges with real-time turbidity
   10 sensor types, all resolved           the TMDL-regulated clarity pollutant
```

Hourly, into an append-only record. Nobody presses a button.

---

## The API had to be guessed

TEON publishes a JavaScript frontend and an undocumented REST backend on an AWS container. Everything below was reverse-engineered from network traffic.

**The slug convention.** Time-series endpoints don't use a sensor's display name — they use the label **truncated to its leading concept** and kebab-cased. `Air Temperature & Relative Humidity` is not `air-temperature-relative-humidity`; it's just **`air-temperature`**. `Tree stress and growth` is **`tree-stress`**. `Soil Environmental Conditions` is, for reasons known only to the backend, **`soil-moisture`**.

Ten sensor types, found by probing candidate slugs until something returned a 200. The convention held exactly once we'd seen enough of it: `{name}-sensor`, name not word-split. `Minidot` → `minidot-sensor`, which was the one candidate the first probe forgot to try.

**Forty-three sensors that are thirteen devices.** The inventory lists forty-three. They are not forty-three.

At every forest station, the soil, air-temperature, tree-stress *and* stream-level endpoints return **projections of a single Campbell logger table** — identical record UUIDs, identical battery voltage, identical panel temperature, identical row counts. One box, four names.

The arithmetic is exact. A full pull grabs 4,600 records; deduplicating on the source's own IDs collapses it to **1,600**. The real network is **2 lake sondes + 6 forest loggers + 5 cameras**. Useful to know before writing a paper about forty-three independent measurement sites.

**Whose data this isn't.** TEON publishes an inventory *and* a `/site-visibility/disabled` list. The 4H Camp sonde appears in the first and is suppressed in the second: deployed, reporting, and flagged non-public. Their frontend hides it. So does this one — the dashboard renders a card explaining *why* the site is blank rather than quietly scraping around the flag.

---

## The ones you have to swim out to

Two of the five lake sondes carry `Manual` in their IDs. Working out what that meant turned into the most satisfying piece of inference in the project.

TEON's own StoryMap gave it away eventually — *"Emily Carlson and Katie Senft ride and snorkel to their sensors every month, come rain, shine, and even snow in the winter months."* But the data said it first:

| Sonde | Telemetry | Records | Expected | Complete |
|---|---|---|---|---|
| Blackwood 3 | manual | 15,451 | 15,642 | **98.8 %** |
| Meeks | manual | 15,441 | 15,632 | **98.8 %** |
| 4H Camp | live | 52,443 | 54,344 | 96.5 % |
| Glenbrook | live | 46,271 | 47,811 | 96.8 % |
| Sunnyside | live | 38,562 | 52,422 | 73.6 % |

A self-logging instrument writes to memory and almost never misses. A telemetered one loses whatever the radio drops — Sunnyside has lost **over a quarter** of its expected record.

Better still: both manual sondes are short by **exactly 191 records**. 15,642 − 15,451 and 15,632 − 15,441 both equal 191, which is 47.75 hours. An identical two-day gap on two separate instruments isn't packet loss. That's one service visit with both out of the water at once.

**The counter-intuitive lesson, measured:** telemetry buys you *timeliness*, not *completeness*. If your question is "what is happening right now", telemeter. If it's "what happened over the last decade", a logger you visit quarterly may give you a better dataset for less money. Telemetry's real value is often knowing the station is still alive.

---

## pH 1.8, or: we've seen this movie

Every monitoring project has the moment where the instrument says something impossible and you have to work out whether it's the world or the wiring. Here's the current list — all of it live on the dashboard **with the caveats attached**, rather than quietly smoothed away.

**The pH probes are dead across the entire fleet.** Glenbrook returns exactly `0`. Sunnyside returns `null`. Not a plausible reading anywhere. TEON's StoryMap lists pH among the measured parameters, so this is a fault, not an unequipped channel. Hidden from display, flagged in the data dictionary.

**Sunnyside is reading negative turbidity.** Not noise — across 48 consecutive readings it sat at **−2.109 FNU with a standard deviation of 0.042**. That tight a spread around that negative a mean is a mis-set zero point. Add 2.11 and the site reads ~0.0 FNU, which is correct for clear alpine water. The USGS gauge on Blackwood Creek reads **+0.3 FNU** on the same measure, same units, different instrument, different agency — independent corroboration that the fault is Sunnyside's, not the lake's.

**And in TEON's own watershed data, a soil pH of 1.79.** Cave Rock catchment. Glenbrook Creek reads 2.61, Mill Creek 2.74. A soil pH of 1.8 is approximately battery acid and does not occur in Sierra granite; the enormous companion standard deviations are the signature of nodata cells averaged in as zeros. *Dante's Peak* had an acidified lake too, and it was also the thing that killed somebody. Excluded from display, with the reason recorded so nobody wires it back in.

**One Topographic Wetness Index of 833.9** against a 5–55 range everywhere else — Marlette Creek, whose catchment contains Marlette Lake. TWI diverges over standing water. Unfortunate, since TWI is one of the variables most worth having.

**The stream gauge is called `Uncalibrated_water_depth` and reads 0.001 m.** Their field name, not ours. Meanwhile the USGS gauge on the same creek reads **0.04 ft³/s** — two agencies, separate hardware, both saying the east-shore creek is essentially dry. That's real cross-validation, and it settles the question: the channel *is* dry, the datum isn't broken.

**A field camera filed a frame from the future.** Glenbrook 4's most recent capture was timestamped six hours *after* the snapshot containing it. TEON returns naive timestamps with no offset; the loggers appear to run on Pacific wall-clock and the cameras on UTC. Mixed conventions in one payload. Every timestamp this project emits now carries an explicit offset rather than leaving browsers to guess.

None of this is a knock on TEON. The network was two weeks old. Cataloguing the gremlins *is* the work — and finding faults from outside, with no obligation to stand behind the numbers, is a great deal easier than operating the thing.

---

## Same storm, two watersheds

The project's headline question: do two stations on opposite shores respond differently to the same weather?

Picking the pair took three attempts. Blackwood 2 was the obvious choice and is offline. Glenbrook 2 looked ideal until it turned out to be a **riparian microsite** — 42 % soil moisture against 3.5–11.5 % at every upland station around it, and the only station also carrying a stream gauge. Pairing it with Homewood would have measured *streambank versus hillslope*.

The answer is **Homewood and Glenbrook 5**: opposite shores, both upland, and **64 metres apart in latitude**. Effectively the same line across the map.

Then the catchment data arrived and made the point far better than any sensor could:

```
Homewood      (Madden Creek catchment)      1,463 mm/yr
Glenbrook 5   (Glenbrook Creek catchment)     689 mm/yr
                                             ──────────
                                                 2.12×
```

Two stations a stone's throw apart in latitude, in catchments receiving **twice** the annual precipitation. Basin-wide the gradient reaches **3.03×** — 1,459 mm at Watson Creek on the northwest shore down to 482 mm at Deadman Point on the east.

That's the Sierra rain shadow, measured, per catchment.

And here's the honest part: right now the two stations read **identical** soil moisture, 3.5 % each. Mid-September, no rain in weeks — there is nothing to detect. The earlier read was "nothing here." The catchment data says the opposite: the *climatology* isn't remotely similar, so the first atmospheric river should split them hard. Now there's a number to predict against instead of a hypothesis.

---

## The clarity chain

Late in the project a five-digit USGS parameter code turned up at Upper Truckee River that neither of us recognised. Rather than guess, we asked:

> **70369** — *Suspended sediment particles between 0.50 to 16.00 microns, water, unfiltered, computed by regression equation, counts per liter*

Then Lake Tahoe Info, verbatim: *"Fine sediment particles (0.5–16 µm) are primarily responsible for clarity loss in Lake Tahoe."*

**The size class matches the regulation exactly.**

Fine sediment under 16 µm is, per EPA, the main pollutant degrading Tahoe's deep-water clarity — roughly **two-thirds** of the lake's impairment. Lake Tahoe is Clean Water Act 303(d)-listed for it. The TMDL requires a **65 % reduction** to restore Secchi depth to **97.4 ft by 2076**.

So parameter 70369, at the lake's largest tributary, is the **regulated clarity pollutant, measured continuously**. Its companion `70372` is the same thing as a daily *load* — which is the unit the TMDL actually writes its allocations in.

This project is named after a disk on a rope. The regulatory target is stated in Secchi depth. 70369 is the thing that controls it.

What we now hold in one pipeline:

| Process | Measurement |
|---|---|
| The regulated pollutant | `70369` / `70372`, Upper Truckee |
| Independent continuous proxy | turbidity at 5 tributaries |
| The algal half of the mechanism | chlorophyll + phycocyanin, lake sondes |
| Sediment supply | catchment precipitation, slope, burn history |
| Long baseline | 131 years of outflow, 66 of Blackwood discharge |

Both sediment parameters are **regression surrogates**, per USGS's own definitions — not laboratory particle counts. That caveat travels with them everywhere it appears.

---

## The bugs were mostly mine

Worth a section, because the process that caught them is the most portable thing here.

Nine errors shipped or nearly shipped during this build. Every one produced **plausible-looking output** rather than a crash:

| What broke | How it looked | How it was caught |
|---|---|---|
| Sensor-slug map keyed on the wrong names | Skipped all 23 sensors, **zero HTTP requests made** | A probe that asked which slugs resolve |
| `63158` for the outlet's elevation | Silently absent column | Probe reported "configured but not reported" |
| `statistic_id=00011` alone | Hid the TMDL load parameter | Reading probe output instead of assuming |
| `statistic_id=00011,00006` | **HTTP 200, zero features, every gauge** | An empty-looking CI log |
| Bounding box instead of a watershed | 14 gauges draining to the Carson River | Discovery report flagged unconfigured stations |
| Web Mercator polygons vs WGS84 points | 0 of 28 stations matched, no error | A total miss is systematic, not scattered |
| 48-hour slope on a diurnal signal | Air temperature "rising 4.85" | Fixture built with a known-flat trend |
| A fully-clipped channel | Confident flat line at zero | Asking what the clipping was hiding |
| A string replace that matched nothing | Printed "tasks added" | `pixi task list` showing five missing |

The pattern in almost all of them: **a filter that removed more than intended, and returned success.** A query returning zero rows is indistinguishable from one that was never going to match. Neither raises.

So the operating rule, now written into `docs/silent-failures.md`: **when a query narrows results, check the count, not the syntax.**

The probe commands — `probe`, `usgs-probe`, `usgs-discover`, `usgs-params`, `reference-inspect`, `camera-probe`, `watch` — exist for this. They're a few dozen lines each and they've caught six real bugs. The alternative isn't fewer bugs; it's the same bugs, shipped, producing numbers that look fine.

---

## By the numbers

```
   2,635,395   records catalogued upstream
   1,197,845   in dormant sensor types, now reachable
          60   stream catchments, 164 attributes each
          43   sensors listed by the API
          13   actual physical devices
          10   sensor types, all slugs resolved
           9   of my own bugs caught before or shortly after shipping
           6   data-quality faults found in the upstream networks
           3.03×  rain-shadow precipitation gradient across the basin
        0.00 km  reprojection error, verified against published centroids
           0   pH readings worth anything
```

---

## Running it

[pixi](https://pixi.sh) handles the environment — conda-forge underneath, one lockfile per platform.

```bash
pixi install
pixi run pipeline     # ingest both agencies, rebuild, prune
pixi run serve        # http://localhost:8000
```

| Task | What it does |
|---|---|
| `pipeline` | ingest-all → usgs → transform → prune |
| `ingest-all` / `usgs` | TEON sensors / USGS gauges |
| `reference` | cache the catchment polygons and attributes |
| `catchment-join` | assign each station to its catchment |
| `watch` | report what changed upstream |
| `probe` / `usgs-probe` | resolve every endpoint, write nothing |
| `usgs-discover` | find every gauge in the basin |
| `usgs-params` | look up a parameter code properly |
| `reference-inspect` | CRS, extent and property keys of the polygon file |
| `camera-probe` | test whether camera imagery is reachable |
| `transform` / `serve` | rebuild the dashboard data / serve it |

Needs a free [USGS API key](https://api.waterdata.usgs.gov/signup/) in `USGS_API_KEY` — 50 requests/hour without one, 1,000 with.

---

## How it stays honest

```
   TEON + USGS APIs
         │
         ▼
   data/raw/**.json          a 7-day working buffer, pruned
         │
         ▼
   data/processed/*.parquet  THE DURABLE RECORD — accumulates,
         │                   deduped on the sources' own IDs
         ▼
   web/assets/latest.json    built at deploy time, never committed
         │
         ▼
   GitHub Pages → brooksgroves.com/secchi
```

Three workflows: **fetch** hourly, **pages** on push, **watch** every six hours — which opens a GitHub issue when the manual sondes upload, when the dormant fleet resumes, if 4H Camp is un-hidden, or if TEON's missing fourth monitoring domain ever appears.

Repo growth is bounded at roughly 160 MB steady state. The first design committed every fetch artifact forever and would have hit GitHub's limit in five days.

Full endpoint map, record schemas, unit notes and open questions: **[`docs/`](docs/)**.

---

## What the disk can't see yet

- **3,392 camera frames** in a bucket named *Snow photos*, going back to November 2025. `camera-probe` proved the bucket refuses anonymous reads. A winter of Tahoe snowpack from five angles, one email away.
- **~930,000 dormant records** — slugs confirmed, reachable, waiting on a deliberate paginated backfill. Includes the precipitation gauge, which is the forcing variable the transect wants most.
- **Why Glenbrook 2 is wet.** The catchment join gives it and Glenbrook 5 identical attributes while they read 42 % and 3.5 % soil moisture. Catchment means explain *between*-catchment variation, not *within*. That needs the source rasters sampled at each station point.
- **Ground truth for a clarity model.** TERC's Secchi record lives in the [EDI repository](https://portal.edirepository.org/nis/mapbrowse?scope=edi&identifier=1340) as a versioned, DOI-bearing package. Their 2025 report also shows why any model here must be **seasonal**: winter clarity is stable, summer clarity is degrading, and 2025's summer average of 53.4 ft was the fifth poorest on record. An annual mean averages away the only part of the signal that's moving.

---

## Data & attribution

Sensor data from the **Tahoe Environmental Observatory Network** (Tahoe Institute for Global Sustainability, University of Nevada, Reno) and the **U.S. Geological Survey**. All provisional.

`secchi` honours TEON's own `/site-visibility/disabled` flags — a site the observatory marks non-public is neither ingested nor displayed.

Clarity context from UC Davis TERC and the Lake Tahoe TMDL. If you use anything derived from this repo, cite the upstream sources, not this one.

---

## Part of

🌲 **Environmental Intelligence Lab** · [Brooks Labs](https://github.com/bdgroves)

MIT licensed. Built by [Brooks Groves](https://brooksgroves.com), GISP® — who did his undergraduate degree at the university that built the network this reads from.

---

> *A plate on a rope, and a hundred and sixty-one years of wanting to know how deep you can see.*
