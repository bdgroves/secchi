# ⚪⚫ secchi

### A modern Secchi disk for Lake Tahoe

**[→ Live dashboard](https://brooksgroves.com/secchi/)** · two agencies, 6.8 million observations, twenty months, updating hourly and watching itself

---

## Cold open

In 1865 the Papal Navy asked an astronomer a simple question: *how clear is the water?*

Angelo Secchi lowered a white plate over the side of a yacht in the Mediterranean and wrote down the depth at which it disappeared.

That's the instrument. A plate on a rope.

One hundred and sixty-one years later it is still the world standard for lake transparency, and it is still how Lake Tahoe's clarity gets reported every year. Twenty-two metres in the 1960s. About twenty-one now.

On **September 15, 2026**, the University of Nevada, Reno switched on the [Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/) — sondes in the water, loggers in the forest, cameras on the ridgelines. I saw it in the alumni newsletter and went to look at the API.

There was no documentation. There still isn't.

---

## What this found

Two things worth the whole build, both of which required assembling a record nobody had assembled.

### 1. Half of TEON's oxygen saturation is referenced to the wrong atmosphere

Lake Tahoe's surface sits at **1,898 m**, where air pressure is about **79.5 %** of sea level. Oxygen saturation depends on pressure, so a percentage referenced to sea level is wrong by roughly twenty points — enough to invert the reading.

Tested across **374,942 paired readings**, ten instruments, twenty months:

| | readings | fit to sea level | fit to lake pressure |
|---|---|---|---|
| **EXO sondes** | 109,626 | **0.004 mg/L** | 1.853 mg/L |
| **MiniDOT** | 265,316 | 2.097 mg/L | **0.142 mg/L** |

0.004 mg/L is the precision of the Weiss (1970) formula itself.

**The MiniDOTs are altitude-corrected. The EXO sondes are not.** Every one of the ten sites agrees with its instrument family:

| Instrument | Site | Published | At lake pressure |
|---|---|---|---|
| EXO | Sunnyside | 80.4 % | **101 %** |
| EXO | Glenbrook | 80.6 % | **101 %** |
| EXO | Blackwood 3 | 83.4 % | **105 %** |
| EXO | Meeks | 84.9 % | **107 %** |
| MiniDOT | Lake Forest | 101.6 % | 101 % |
| MiniDOT | tallac_lake | 103.8 % | 103 % |

I expected both fleets to share the error, and being wrong made the finding stronger. One fleet right and one wrong, in the same network, rules out a deliberate sea-level convention — that would have been applied to both. The MiniDOTs are the control, and they prove the correction is achievable in TEON's own pipeline.

It also validates the arithmetic against hardware: six devices doing this sum internally agree with ours to a fraction of a point.

Reported to TEON. `pixi run oxygen-check`.

### 2. Forty-three sensors are thirteen devices

At every forest station, the soil, air-temperature, tree-stress *and* stream-level endpoints return **projections of one Campbell logger table** — identical record UUIDs, identical battery voltage, identical row counts.

The arithmetic is exact. A full pull grabs 4,600 records; deduplicating on the source's own IDs collapses it to **1,600**. The live network is **2 lake sondes + 6 forest loggers + 5 cameras**.

Worth knowing before anyone writes a paper about forty-three independent measurement sites — and it later saved half a backfill, once the fetch path learned to collapse them too.

---

## Same storm, two watersheds

The question the project was built around.

Homewood and Glenbrook 5 sit **64 metres apart in latitude** on opposite shores. Both upland hillslope stations. One faces the Pacific; one sits in the lee of the Carson Range.

TEON's watershed layer — 60 catchments, 164 attributes each — puts numbers on it:

```
Homewood      (Madden Creek)      1,463 mm/yr
Glenbrook 5   (Glenbrook Creek)     689 mm/yr
                                   ──────────
                                       2.12×
```

Basin-wide the gradient reaches **3.03×**, 1,459 mm at Watson Creek down to 482 mm at Deadman Point. The map shades all sixty catchments by any of thirteen variables.

For most of the build this question was unanswerable: the two stations had a week of shared history and there had been no rain. Backfilling the telemetered fleet took it to **375 days**, covering a full winter.

`pixi run transect` detects wetting events in the soil moisture itself — soil wets fast and dries slowly, so the asymmetry is the signal — matches them across stations by onset time, and compares magnitude. It reports how strongly each shore responds to a shared event, and how many events only one shore saw at all.

**Why not use rainfall?** Because we can't. Blackwood 2 holds the network's only precipitation gauge, it has been dark since August, and the USGS store only covers September. Detecting events in the response rather than the forcing is the compromise, and the module says so rather than implying otherwise.

---

## The API had to be guessed

**Slugs are truncated to the leading concept.** `Air Temperature & Relative Humidity` is `/sensors/air-temperature`. `Soil Environmental Conditions` is, for reasons known only to the backend, `/sensors/soil-moisture`.

**Timestamps have three different field names.** EXO and the Campbell loggers use `TIMESTAMP`. HOBO uses `timestamp`. And MiniDOT uses **`Pacific Standard Time`** — a PME MiniDOT's export writes the *timezone* as the header of its time column, and TEON's loader kept it verbatim.

Assuming one name cost **1,490,116 rows** their timestamps in a single backfill. They landed in a `year=0000` partition that exists precisely so undated rows are filed rather than dropped, which is the only reason it was noticeable.

**Measurements have four naming conventions.** Campbell's `Air_Temp`, EXO's `Do_mgL`, HOBO's `conductivity`, MiniDOT's `Dissolved Oxygen Saturation`.

**And TEON publishes a visibility flag.** `/site-visibility/disabled` marks 4H Camp non-public. The dashboard honours it; so does the backfill, and if that endpoint can't be read the backfill fetches nothing rather than guessing.

`pixi run record-shape` exists so the next consumer doesn't have to discover all this the way we did.

---

## The ones you have to swim out to

Fourteen instruments across eight sites are self-logging — stored to memory, retrieved by boat and snorkel, roughly monthly.

TEON labels only two of them. The other twelve are MiniDOTs and HOBOs, which have no telemetry as a product category. All twelve stop on **2026-06-10**, the same day. Twelve simultaneous radio failures isn't plausible; one boat trip is.

Their records are the best in the network:

| Sonde | Telemetry | Complete |
|---|---|---|
| Blackwood 3 | manual | **98.8 %** |
| Meeks | manual | **98.8 %** |
| Glenbrook | live | 96.8 % |
| Sunnyside | live | 73.6 % |

Both manual sondes are short by **exactly 191 records** — 47.75 hours. An identical gap on two separate instruments is one service visit, not packet loss.

**The counter-intuitive part, measured:** telemetry buys *timeliness*, not *completeness*. Sunnyside has lost over a quarter of its record to the radio.

The dashboard gives them coverage cards rather than live cards — period of record held, what's outstanding, what the next collection would extend from — because a reading twelve weeks old is perfectly good data presented dishonestly if it sits in the live idiom.

---

## pH 1.8, or: we've seen this movie

Faults found, all live on the dashboard **with the caveats attached**:

**pH is dead fleetwide.** Glenbrook returns `0`. Sunnyside returns `null`.

**Sunnyside reads negative turbidity** — −2.109 FNU with a standard deviation of 0.042 across 48 readings. Too tight for noise; a mis-set zero point. The USGS gauge on Blackwood Creek reads +0.3 FNU from a different instrument and a different agency.

**A soil pH of 1.79** at Cave Rock in TEON's watershed layer. That is approximately battery acid and does not occur in Sierra granite — nodata cells averaged in as zeros. *Dante's Peak* had an acidified lake too.

**A Topographic Wetness Index of 833.9** against a 5–55 range everywhere else. Marlette Creek, whose catchment contains Marlette Lake.

**A camera filed a frame from the future** — six hours ahead of the snapshot containing it. Loggers on Pacific wall-clock, cameras on UTC.

Cataloguing these *is* the work. It's also much easier from outside, with no obligation to stand behind the numbers, than it is to operate the network. All of it has been reported back.

---

## The record

```
data/processed/observations/
    source=teon/year=2025/month=01/part.parquet    frozen
    ...
    source=teon/year=2026/month=09/part.parquet    the only file that churns
```

**6.8 million observations, January 2025 to now, 20 monthly partitions.**

Hive-partitioned because a parquet is rewritten whole on every update and git stores each version as a new blob. At 150 MB, hourly, that's 3.5 GB/day of history. Partitioned, historical months freeze and only the current one churns.

Four backfill stages, each fetching straight to parquet rather than through the raw buffer — an EXO record is ~2 KB of JSON and the full fleet would dump 2 GB into a directory sized for seven days.

`pixi run store-status` lists it. DuckDB queries it in place with a glob.

---

## It watches itself

Three workflows: **fetch** hourly, **pages** at :25, **watch** every six hours.

`pages` has its own schedule rather than triggering off fetch's commit, because GitHub deliberately does not create workflow runs from events triggered by the default `GITHUB_TOKEN`. That coupling looked right and never once fired.

`watch` diffs the inventory against a baseline and opens a GitHub issue on anything notable — a sensor resuming, a new sensor type, a visibility flag lifting, or a dormant sonde's record count jumping, which is the signature of a manual retrieval being uploaded.

Local runs are **read-only**. A local check that advanced the baseline would *consume* the change: you'd see the upload, the baseline would move, and CI would find nothing and never open the issue.

---

## The bugs were mostly mine

Worth a section, because the pattern is the most portable thing here.

Nineteen errors shipped or nearly shipped. Every one produced **plausible-looking output** rather than a crash:

| What broke | How it looked |
|---|---|
| Sensor-slug map keyed on the wrong names | Skipped all 23 sensors, **zero HTTP requests made** |
| `63158` for the outlet's elevation | Silently absent column |
| `statistic_id=00011` alone | Hid the TMDL load parameter |
| `statistic_id=00011,00006` | **HTTP 200, zero features, every gauge** |
| Bounding box instead of a watershed | 14 gauges draining to the Carson River |
| Web Mercator polygons vs WGS84 points | 0 of 28 stations matched, no error |
| 48-hour slope on a diurnal signal | Air temperature "rising 4.85" |
| A fully-clipped channel | Confident flat line at zero |
| Four string replaces that matched nothing | Printed "tasks added" |
| `GITHUB_TOKEN` pushes don't trigger workflows | Green cron, hours-old page |
| Three features deleted by stale-base copies | A whole map layer vanished |
| A presence check on the whole file | Assertion passed, import missing |
| Trend deltas printed in source units | `61.5 °F` with `falling 0.34` (°C) |
| Two writers on one baseline file | Merge conflict, and a consumed notification |
| A local import shadowing a module one | **`UnboundLocalError`, cron down for hours** |
| Backfill wrote non-canonical sensor types | **98.9 % of the record invisible to the dashboard** |
| Hardcoded `TIMESTAMP` field | 1,490,116 rows with no usable time |
| Cards filtered through an EXO variable list | Readings present in the payload, never drawn |
| Backfill ignored the visibility flag | Fetched a site TEON asked not be published |

### The pattern

Almost all of them are **something reporting success while doing nothing**. A query returning zero rows is indistinguishable from one that was never going to match. A green workflow is indistinguishable from a deployed page. A name in a file is indistinguishable from an imported one.

Three rules fell out, all in `docs/`:

- **When a query narrows results, check the count, not the syntax.**
- **Check the thing you care about, not a proxy that correlates with it.**
- **Before committing a generated file, ask who else writes it.**

And three test files that encode them. `test_capabilities_persist.py` is the interesting one: every *other* test checks internal consistency, and a consistent **subset** of the feature set passes all of them — so deleting a feature and its task together was invisible. It's a plain list of what must exist, and it caught a third deletion on its first run.

Nine probe commands exist for the same reason. They're a few dozen lines each and they've caught eight real bugs. The alternative isn't fewer bugs; it's the same bugs, shipped, producing numbers that look fine.

---

## By the numbers

```
   6,849,022   observations stored, Jan 2025 to now
     374,942   paired readings behind the oxygen finding
   1,490,116   rows that once landed with no timestamp, and were recovered
      68,000×  compression of the polygon layer, 7.8 MB -> 114 KB
         375   days of overlapping transect history
          60   stream catchments, 164 attributes each
          43   sensors listed by the API
          19   of my own bugs caught before or shortly after shipping
          13   actual physical devices
           8   data-quality faults found upstream, all reported back
        3.03×  rain-shadow gradient across the basin
        2.12×  the same gradient across two stations 64 m apart
     0.00 km   reprojection error, verified against published centroids
    0.004 mg/L the EXO fit to a sea-level atmosphere
           0   pH readings worth anything
```

---

## Running it

[pixi](https://pixi.sh) handles the environment.

```bash
pixi install
pixi run pipeline     # ingest both agencies, rebuild, prune
pixi run serve        # http://localhost:8000
```

| Task | What it does |
|---|---|
| `pipeline` | ingest-all → usgs → transform → prune |
| `backfill --stage …` | full history for one fleet, straight to parquet |
| `transect` | do the two shores respond differently to the same storm |
| `oxygen-check` | which atmosphere each instrument family references |
| `record-shape` | field names per sensor type — **run before any new backfill** |
| `catchment-join` | assign stations to catchments |
| `watch` | report upstream changes (read-only) |
| `store-status` | partitions, row counts, sizes |
| `probe` / `usgs-probe` / `usgs-discover` | resolve endpoints, write nothing |
| `reference-inspect` / `camera-probe` | CRS and extent / imagery reachability |

Needs a free [USGS API key](https://api.waterdata.usgs.gov/signup/) in `USGS_API_KEY`.

---

## What the disk can't see yet

- **3,468 camera frames** in a bucket named *Snow photos*, back to November 2025. `camera-probe` proved it refuses anonymous reads. A winter of snowpack from five angles, one email away.
- **718,127 records** at Blackwood 2 — precipitation and stream chemistry. The precipitation gauge is the forcing variable the transect most wants. The station has been dark since June, which is worth someone noticing.
- **Why Glenbrook 2 is wet.** 42 % soil moisture against 3.5 % at neighbouring upland stations. The catchment join gives it and Glenbrook 5 identical attributes, so catchment means can't explain it — that needs the source rasters sampled at each station point.
- **Ground truth for a clarity model.** TERC's Secchi record is in the [EDI repository](https://portal.edirepository.org/nis/mapbrowse?scope=edi&identifier=1340), versioned and DOI-bearing, back to 1968. Their 2025 report shows why any model must be **seasonal**: winter clarity is stable, summer is degrading, and 2025's summer average of 53.4 ft was the fifth poorest on record.

TERC also began, in 2025, assembling decades of clarity-driver data alongside Secchi depth — the same analysis this project's nowcast idea sketches, by the people with the instruments, the fifty-eight-year record and the funding.

So this isn't a novel scientific result. What it is: a fast, public, reproducible view over data otherwise scattered across two agencies and several undocumented endpoints, with its own problems stated on the face of it. That has real value, and it's a different kind than the science.

---

## Data & attribution

Sensor data from the **Tahoe Environmental Observatory Network** (Tahoe Institute for Global Sustainability, University of Nevada, Reno) and the **U.S. Geological Survey**. All provisional.

`secchi` honours TEON's `/site-visibility/disabled` flags in both display and ingest.

Clarity context from UC Davis TERC and the Lake Tahoe TMDL. If you use anything derived from this repo, cite the upstream sources, not this one.

---

## Part of

🌲 **Environmental Intelligence Lab** · [Brooks Labs](https://github.com/bdgroves)

MIT licensed. Built by [Brooks Groves](https://brooksgroves.com), GISP® — who did his undergraduate degree at the university that built the network this reads from, and found out about it from the alumni newsletter.

---

> *A plate on a rope, and a hundred and sixty-one years of wanting to know how deep you can see.*
