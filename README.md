# ⚪⚫ secchi

### A modern Secchi disk for Lake Tahoe

**[→ Live dashboard](https://brooksgroves.com/secchi/)** · two agencies, 33 instruments, 60 catchments, updating hourly and watching itself

---

## Cold open

In 1865 the Papal Navy asked an astronomer a simple question: *how clear is the water?*

Angelo Secchi lowered a white plate over the side of a yacht in the Mediterranean and wrote down the depth at which it disappeared.

That's the instrument. A plate on a rope.

One hundred and sixty-one years later it is still the world standard for lake transparency, and it is still how Lake Tahoe's famous clarity gets reported every single year. Twenty-two metres in the 1960s. About twenty-one now. A number you get by dropping a disk in the water and watching it vanish.

On **September 15, 2026**, the University of Nevada, Reno switched on the [Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/) — sondes in the water, loggers in the forest, cameras on the ridgelines. For the first time you could watch clarity *assemble itself out of its causes*, every fifteen minutes.

There was no API documentation. There still isn't.

This is what reading someone else's undocumented sensor network looks like.

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

Hourly, into an append-only record. Nobody presses a button, and it opens a GitHub issue when the upstream network changes.

---

## The API had to be guessed

TEON publishes a JavaScript frontend and an undocumented REST backend on an AWS container. Everything below was reverse-engineered from network traffic.

**The slug convention.** Time-series endpoints don't use a sensor's display name — they use the label **truncated to its leading concept** and kebab-cased. `Air Temperature & Relative Humidity` is not `air-temperature-relative-humidity`; it's just **`air-temperature`**. `Tree stress and growth` is **`tree-stress`**. `Soil Environmental Conditions` is, for reasons known only to the backend, **`soil-moisture`**.

Ten sensor types, found by probing candidate slugs until something returned a 200.

**Forty-three sensors that are thirteen devices.** The inventory lists forty-three. They are not forty-three.

At every forest station, the soil, air-temperature, tree-stress *and* stream-level endpoints return **projections of a single Campbell logger table** — identical record UUIDs, identical battery voltage, identical row counts. One box, four names.

The arithmetic is exact. A full pull grabs 4,600 records; deduplicating on the source's own IDs collapses it to **1,600**. The real network is **2 lake sondes + 6 forest loggers + 5 cameras**.

If you build an API over a logger network, be explicit about the difference between a *logger*, a *sensor* and a *channel*. Consumers will otherwise mistake your endpoint count for your instrument count, and the error inflates by a factor of three.

**Whose data this isn't.** TEON publishes an inventory *and* a `/site-visibility/disabled` list. The 4H Camp sonde appears in the first and is suppressed in the second. Their frontend hides it. So does this one — the dashboard renders a card explaining *why* the site is blank rather than quietly scraping around the flag.

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

**The counter-intuitive lesson, measured:** telemetry buys you *timeliness*, not *completeness*. If your question is "what is happening right now", telemeter. If it's "what happened over the last decade", a logger you visit quarterly may give you a better dataset for less money. Telemetry's real value is often knowing the station is alive.

---

## The oxygen was telling a different story than it looked

The single most consequential finding here, and it took 1,068 readings to establish.

TEON publishes dissolved oxygen two ways: concentration in mg/L, and percent saturation. Saturation depends on barometric pressure — and **Lake Tahoe's surface sits at 1,898 m, about 79.5 % of sea-level pressure.**

So: is the published percentage referenced to sea level, or to the lake?

Rather than assume, `pixi run oxygen-check` tests both hypotheses against every reading carrying temperature, concentration and percentage on the same timestamp:

| Hypothesis | Mean absolute error |
|---|---|
| percentage referenced to **sea level** | **0.004 mg/L** |
| percentage referenced to lake altitude | 1.627 mg/L |

0.004 mg/L is the precision of the Weiss (1970) saturation formula itself. Not a close call.

| Site | Temp | mg/L | Published | At lake pressure |
|---|---|---|---|---|
| Sunnyside | 16.3 °C | 7.93 | 80.9 % | **102 %** |
| Glenbrook | 16.5 °C | 7.98 | 81.7 % | **103 %** |

**81 % reads as oxygen-stressed water. 102 % of what the water can actually hold means photosynthesis is outrunning respiration.** Opposite ecological conclusions from identical numbers.

And a real result falls out: both nearshore sites are slightly supersaturated — net-photosynthetic at the surface. Expected for a sunlit oligotrophic lake in September, but measured rather than assumed, and invisible in the published figure.

The dashboard shows **three** oxygen readings and never overwrites theirs: concentration, TEON's published saturation, and a locally-referenced figure derived from *their* concentration and temperature — so it depends on their measurements, not their saturation assumption. Derived values carry a visually distinct `computed from …` label.

Is it a bug? Probably a barometric setting, since the field standard (APHA, and USGS parameter `00301`) references local pressure. But sea-level referencing could be deliberate for cross-site comparability. Without seeing their sonde configuration, the honest statement is narrower: **the number doesn't mean what a reader at Tahoe would assume it means.**

---

## pH 1.8, or: we've seen this movie

Every monitoring project has the moment where the instrument says something impossible. Here's the rest of the list — all of it live on the dashboard **with the caveats attached**, rather than quietly smoothed away.

**The pH probes are dead across the entire fleet.** Glenbrook returns exactly `0`. Sunnyside returns `null`. TEON's StoryMap lists pH among the measured parameters, so this is a fault, not an unequipped channel.

**Sunnyside is reading negative turbidity.** Across 48 consecutive readings it sat at **−2.109 FNU with a standard deviation of 0.042** — a mis-set zero point, not noise. The USGS gauge on Blackwood Creek reads **+0.3 FNU** on the same measure from a different instrument and a different agency. Independent corroboration that the fault is Sunnyside's, not the lake's.

**And in TEON's own watershed data, a soil pH of 1.79.** Cave Rock catchment. A soil pH of 1.8 is approximately battery acid and does not occur in Sierra granite; the enormous companion standard deviations are the signature of nodata cells averaged in as zeros. *Dante's Peak* had an acidified lake too, and it was also the thing that killed somebody — but this one is a raster artifact, excluded from display with the reason recorded.

**One Topographic Wetness Index of 833.9** against a 5–55 range everywhere else. Marlette Creek, whose catchment contains Marlette Lake. TWI diverges over standing water.

**The stream gauge is called `Uncalibrated_water_depth` and reads 0.001 m.** Their field name, and admirably honest. Meanwhile the USGS gauge on the same creek reads **0.05 ft³/s** — two agencies, separate hardware, both saying the east-shore creek is essentially dry.

**A field camera filed a frame from the future.** Glenbrook 4's most recent capture was timestamped six hours *after* the snapshot containing it. Naive timestamps, loggers apparently on Pacific wall-clock and cameras on UTC.

None of this is a knock on TEON. The network was two weeks old, cataloguing the gremlins *is* the work, and finding faults from outside — with no obligation to stand behind the numbers — is a great deal easier than operating the thing. All of it has been reported back to them.

---

## Same storm, two watersheds

The project's headline question: do two stations on opposite shores respond differently to the same weather?

Picking the pair took three attempts. Blackwood 2 was the obvious choice and is offline. Glenbrook 2 looked ideal until it turned out to be a **riparian microsite** — 42 % soil moisture against 3.5–11.5 % at every upland station around it. Pairing it with Homewood would have measured *streambank versus hillslope*.

The answer is **Homewood and Glenbrook 5**: opposite shores, both upland, **64 metres apart in latitude**.

Then TEON's watershed layer arrived — 60 catchments with 164 climate and landscape attributes each — and made the point better than any sensor could:

```
Homewood      (Madden Creek catchment)      1,463 mm/yr
Glenbrook 5   (Glenbrook Creek catchment)     689 mm/yr
                                             ──────────
                                                 2.12×
```

Two stations a stone's throw apart in latitude, in catchments receiving **twice** the annual precipitation. Basin-wide the gradient reaches **3.03×** — 1,459 mm at Watson Creek on the northwest shore down to 482 mm at Deadman Point on the east.

That's the Sierra rain shadow, measured, per catchment. The map shades all 60 by any of thirteen variables, and you can watch it.

And the honest part: right now those two stations read **identical** soil moisture. Mid-September, no rain in weeks, nothing to detect. The *climatology* isn't remotely similar though — so when the first atmospheric river lands they should diverge sharply, and there's a number to predict against.

### Getting the catchments onto the map

Two problems worth recording.

**The polygon file is 7.8 MB**, which is not something to ship on every page load. Reprojected, simplified with Ramer–Douglas–Peucker at ~40 m, trimmed from 164 properties to the 29 displayed, coordinates rounded to ~1 m: **74,978 vertices → 3,434, and 7.8 MB → 114 KB.** A 68× reduction, below what a basin-scale map can resolve anyway.

**The file is in Web Mercator, not WGS84** — metres, not degrees, despite RFC 7946. Point-in-polygon matched **0 of 28 stations** with no error at all. The reprojection is verified against ground truth the file ships itself: each catchment states its centroid in degrees while storing geometry in metres, and the computed centroids agree to **0.00 km** across all 60.

---

## The clarity chain

A five-digit USGS parameter code turned up at Upper Truckee River that neither of us recognised. Rather than guess, we asked:

> **70369** — *Suspended sediment particles between 0.50 to 16.00 microns, water, unfiltered, computed by regression equation, counts per liter*

Then Lake Tahoe Info, verbatim: *"Fine sediment particles (0.5–16 µm) are primarily responsible for clarity loss in Lake Tahoe."*

**The size class matches the regulation exactly.**

Fine sediment under 16 µm is, per EPA, the main pollutant degrading Tahoe's deep-water clarity — roughly **two-thirds** of the lake's impairment. The TMDL requires a **65 % reduction** to restore Secchi depth to **97.4 ft by 2076**.

So parameter 70369, at the lake's largest tributary, is the **regulated clarity pollutant, measured continuously**. Its companion `70372` is the same thing as a daily *load* — the unit the TMDL actually writes its allocations in.

Both are **regression surrogates**, per USGS's own definitions, not laboratory particle counts. That caveat travels with them everywhere they appear.

---

## It watches itself

Several things this project cares about will change without announcement. The manual sondes publish nothing until someone snorkels out. MiniDot and HOBO both stopped around 10 June, which looks seasonal. One sonde sits behind a visibility flag that could lift. And TEON's StoryMap describes *four* monitoring domains while the API exposes three — if ponds and wet meadows appear, that's a large new dataset.

So `watch` runs every six hours, diffs the inventory against a stored baseline, and **opens a GitHub issue** when something notable moves. It has already caught a camera resuming without anyone asking.

Two design decisions that matter:

**Record counts are excluded for live sensors, and compared for dormant ones.** A live sensor's count moves hourly and comparing it would make every run a false alarm. A dormant sensor's should never move at all — so a jump of several thousand is the unmistakable signature of a manual sonde being retrieved and uploaded.

**Local runs are read-only.** If a local check advanced the baseline it would *consume* the change: you'd see the upload, the baseline would move, and CI would then find nothing and never open the issue. Looking for the notification would destroy it. Only CI advances the baseline, via `watch-update`.

---

## The bugs were mostly mine

Worth a section, because the process that caught them is the most portable thing here.

Fourteen errors shipped or nearly shipped. Every one produced **plausible-looking output** rather than a crash:

| What broke | How it looked | How it was caught |
|---|---|---|
| Sensor-slug map keyed on the wrong names | Skipped all 23 sensors, **zero HTTP requests made** | A probe that asked which slugs resolve |
| `63158` for the outlet's elevation | Silently absent column | Probe: "configured but not reported" |
| `statistic_id=00011` alone | Hid the TMDL load parameter | Reading probe output |
| `statistic_id=00011,00006` | **HTTP 200, zero features, every gauge** | An empty-looking CI log |
| Bounding box instead of a watershed | 14 gauges draining to the Carson River | Discovery report |
| Web Mercator polygons vs WGS84 points | 0 of 28 stations matched, no error | A total miss is systematic |
| 48-hour slope on a diurnal signal | Air temperature "rising 4.85" | Fixture with a known-flat trend |
| A fully-clipped channel | Confident flat line at zero | Asking what clipping hid |
| A string replace that matched nothing | Printed "tasks added" | `pixi task list` |
| `GITHUB_TOKEN` pushes don't trigger workflows | Green cron, hours-old page | Noticing the page was stale |
| A bundle built from a stale base | Deleted a whole feature silently | Diffing the function sets |
| Presence check on the whole file | Assertion passed, import missing | `NameError` at runtime |
| Trend deltas printed in source units | `61.5 °F` with `falling 0.34` (°C) | Auditing every formatted value |
| Two writers on one baseline file | Merge conflict, and a consumed notification | The conflict |

### The pattern

Almost all of those are **something reporting success while doing nothing.** A query returning zero rows is indistinguishable from one that was never going to match. A green workflow is indistinguishable from a deployed page. A name appearing in a file is indistinguishable from it being imported.

Two rules fell out, both written into `docs/`:

- **When a query narrows results, check the count, not the syntax.**
- **Check the thing you care about, not a proxy that correlates with it.**

Seven probe commands exist for this — `probe`, `usgs-probe`, `usgs-discover`, `usgs-params`, `reference-inspect`, `camera-probe`, `oxygen-check`. A few dozen lines each, and they caught seven real bugs between them. The alternative isn't fewer bugs; it's the same bugs, shipped, producing numbers that look fine.

---

## By the numbers

```
   2,635,395   records catalogued upstream
   1,197,845   in dormant sensor types, now reachable
      68,000×  compression of the polygon layer, 7.8 MB -> 114 KB
       1,068   readings proving the DO saturation reference
          60   stream catchments, 164 attributes each
          43   sensors listed by the API
          14   of my own bugs caught before or shortly after shipping
          13   actual physical devices
          10   sensor types, all slugs resolved
           7   data-quality faults found upstream, all reported back
        3.03×  rain-shadow gradient across the basin
        2.12×  the same gradient across two stations 64 m apart
     0.00 km   reprojection error, verified against published centroids
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
| `reference` / `catchment-join` | cache catchment polygons / assign stations to them |
| `watch` | report upstream changes (read-only) |
| `oxygen-check` | test whether DO saturation is altitude-corrected |
| `probe` / `usgs-probe` | resolve every endpoint, write nothing |
| `usgs-discover` / `usgs-params` | find every basin gauge / look up a parameter code |
| `reference-inspect` | CRS, extent and property keys of the polygon file |
| `camera-probe` | test whether camera imagery is reachable |
| `transform` / `serve` | rebuild dashboard data / serve it |

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
   web/assets/*.json         built at deploy time, never committed
         │
         ▼
   GitHub Pages → brooksgroves.com/secchi
```

Three workflows: **fetch** hourly, **pages** at :25 past, **watch** every six hours.

`pages` has its own schedule rather than triggering off fetch's commit, because GitHub deliberately does not create workflow runs from events triggered by the default `GITHUB_TOKEN`. That coupling looked correct and never once fired.

Repo growth is bounded at roughly 160 MB steady state. The first design committed every fetch artifact forever and would have hit GitHub's limit in five days.

Full endpoint map, record schemas, unit notes, the storage analysis and open questions: **[`docs/`](docs/)**.

---

## What the disk can't see yet

- **3,468 camera frames** in a bucket named *Snow photos*, going back to November 2025. `camera-probe` proved the bucket refuses anonymous reads. A winter of Tahoe snowpack from five angles, one email away.
- **~1.2 million dormant records** — slugs confirmed, reachable, waiting on a deliberate paginated backfill. Includes the precipitation gauge, the forcing variable the transect wants most. **The parquet needs partitioning first**: a monolithic file rewritten hourly at that size would add 3.5 GB/day to git history. See [`docs/storage.md`](docs/storage.md).
- **Why Glenbrook 2 is wet.** The catchment join gives it and Glenbrook 5 identical attributes while they read 42 % and 3.5 % soil moisture. Catchment means explain *between*-catchment variation, not *within*. That needs the source rasters sampled at each station point.
- **Ground truth for a clarity model.** TERC's Secchi record lives in the [EDI repository](https://portal.edirepository.org/nis/mapbrowse?scope=edi&identifier=1340) as a versioned, DOI-bearing package going back to 1968. Their 2025 report also shows why any model here has to be **seasonal**: winter clarity is stable, summer clarity is degrading, and 2025's summer average of 53.4 ft was the fifth poorest on record.

One thing worth stating plainly. TERC's 2025 report says they have *begun lining up decades of data on the potential drivers of clarity, alongside Secchi depth*, and that in 2026 they're deploying new imaging technology to visualise particle aggregation. That's the same analysis the "clarity nowcast" idea sketches, by the people with the instruments, the fifty-eight-year record and the funding.

This project isn't a novel scientific result. What it is: a fast, public, reproducible view over data otherwise scattered across two agencies and several undocumented endpoints, with its own data-quality problems stated on the face of it. That has real value. It's a different kind of value than the science.

---

## Data & attribution

Sensor data from the **Tahoe Environmental Observatory Network** (Tahoe Institute for Global Sustainability, University of Nevada, Reno) and the **U.S. Geological Survey**. All provisional.

`secchi` honours TEON's own `/site-visibility/disabled` flags — a site the observatory marks non-public is neither ingested nor displayed.

Clarity context from UC Davis TERC and the Lake Tahoe TMDL. If you use anything derived from this repo, cite the upstream sources, not this one.

---

## Part of

🌲 **Environmental Intelligence Lab** · [Brooks Labs](https://github.com/bdgroves)

MIT licensed. Built by [Brooks Groves](https://brooksgroves.com), GISP® — who did his undergraduate degree at the university that built the network this reads from, and found out about it from the alumni newsletter.

---

> *A plate on a rope, and a hundred and sixty-one years of wanting to know how deep you can see.*
