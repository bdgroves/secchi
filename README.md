# ⚪⚫ secchi

### A modern Secchi disk for Lake Tahoe

**[→ Live dashboard](https://brooksgroves.com/secchi/)** · two agencies, 11.2 million observations, twenty-seven months, updating hourly and watching itself

---

## Cold open

In 1865 the Papal Navy asked an astronomer a simple question: *how clear is the water?*

Angelo Secchi lowered a white plate over the side of a yacht in the Mediterranean and wrote down the depth at which it disappeared.

That's the instrument. A plate on a rope.

One hundred and sixty-one years later it is still the world standard for lake transparency, and it is still how Lake Tahoe's clarity gets reported every year.

On **September 15, 2026**, the University of Nevada, Reno switched on the [Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/). I saw it in the alumni newsletter and went to look at the API.

There was no documentation. There still isn't.

---

## What this found

### 1. Half of TEON's oxygen saturation is referenced to the wrong atmosphere

Lake Tahoe's surface sits at **1,898 m**, where air pressure is about **79.5 %** of sea level. Saturation depends on pressure, so a percentage referenced to sea level is wrong by roughly twenty points — enough to invert the reading.

Tested across **374,942 paired readings**, ten instruments, twenty months:

| | readings | fit to sea level | fit to lake pressure |
|---|---|---|---|
| **EXO sondes** | 109,626 | **0.004 mg/L** | 1.853 mg/L |
| **MiniDOT** | 265,316 | 2.097 mg/L | **0.142 mg/L** |

0.004 mg/L is the precision of the Weiss (1970) formula itself.

**The MiniDOTs are altitude-corrected. The EXO sondes are not.** Every one of the ten sites agrees with its instrument family — EXO reading 80–85 % where the water is really at 101–107 %, MiniDOT agreeing with the corrected figure to within half a point.

I expected both fleets to share the error, and being wrong made it stronger: one fleet right and one wrong, in the same network, rules out a deliberate sea-level convention. The MiniDOTs are the control, and they prove the correction is achievable in TEON's own pipeline.

Reported upstream. `pixi run oxygen-check`.

### 2. Forty-three sensors are thirteen devices

At every forest station, the soil, air-temperature, tree-stress *and* stream-level endpoints return **projections of one Campbell logger table** — identical record UUIDs, identical battery voltage, identical row counts.

A full pull grabs 4,600 records; deduplicating collapses it to **1,600**. The live network is 2 lake sondes, 6 forest loggers and 5 cameras.

**But shared rows are not shared columns**, and I learned that the expensive way. Each endpoint is a *projection* of the logger table: soil returns soil moisture and temperature, air returns air temperature and humidity, tree returns the dendrometers. They share only record IDs and a couple of housekeeping fields. A backfill that fetched one endpoint and treated it as standing in for the rest silently discarded every forest station's air temperature, humidity and tree-stress history. The first query run in the new SQL shell showed air temperature at Homewood covering 7.5 days while soil covered a year.

---

## Same storm, two watersheds

Homewood and Glenbrook 5 sit **64 metres apart in latitude** on opposite shores. Their catchments receive **2.12×** different annual precipitation — basin-wide the gradient reaches **3.03×**.

`pixi run transect` detects wetting events in the soil moisture itself, matches them across stations, and compares response.

The overlap is about a year because Homewood is the youngest forest station, reporting only since September 2025.

**The result: total soil wetting runs at about the rainfall ratio.** Summed over every wetting event in the overlapping year, Homewood's soil took 146 points of wetting against Glenbrook 5's 63 — **2.32×**, against a catchment rainfall ratio of 2.12×. It's the one statistic built to be compared with rainfall, and it doesn't depend on how storms are paired between stations. It leans on a few big west-shore storms Glenbrook 5 never felt (+22.2 on 2025-10-02, +16.1 on 2025-12-17), so it's encouraging rather than settled.

**Storms mostly reach the west shore first: 15 of 18 shared events.** How long they take to cross can't be measured here. Detection runs on daily means, which it has to in order to reject the soil's daily cycle, so most storm onsets can only be placed to the day. I twice reported a lag in hours — +3.7 h, then +11.7 h — and called it the sturdiest finding. The first was biased short by a 12-hour matching window; the second was inflated by storms stamped at midnight. Only 6 of 18 pairs resolve to a real hour, spread from 4 to 37 hours.

**Getting here took five wrong answers, and every correction moved toward the null:** 3.31× from averaging ratios, 2.82× from counting the daily cycle as storms, 1.29× from matching at the wrong resolution, and the two lag figures. The number that survived is the one defined, before its value was known, to be compared against rainfall.

**What would settle it:** the 593,507 precipitation records now in the store, at a west-shore site, covering the same winter. That converts *"did both stations wet at the same time?"* — inferring storms from responses, where all four bugs lived — into *"how much did each wet after this much rain?"*

---

## Glenbrook 2: still unexplained

One station reads far wetter than its neighbours on the same hillslope, in the same catchment, a few hundred metres away.

**And the number I kept quoting was wrong.** I said 42 % against 3.4 % — a 12× gap — throughout earlier versions of this document. That compared a wet site to dry sites on one late-summer day. Over the full 354-day record:

| | 354-day mean |
|---|---|
| Glenbrook 2 | 46.0 % |
| Glenbrook 4 | 15.6 % |
| Glenbrook 5 | 10.9 % |

**2.9× to 4.2×**, not 12×. Still the wettest station by a wide margin; the headline was an artifact of when I happened to look.

Glenbrook 2 is the only terrestrial station with its own stream sensor, which made a cheap test look possible: does its soil drain at the creek's rate, or its own? Three attempts, none of which worked:

| Attempt | Result |
|---|---|
| Correlate soil against stream | Useless — everything correlates during a storm |
| Correlate during recession only | Useless — any two smooth declines correlate |
| Compare recession **rate** | Invalid on this record |

The third failed in an instructive way. It returned 2.9, 3.2, 3.1 and 2.9 days for a stream and three soils at completely different moisture levels. Four independent signals within 0.3 days of each other is the method measuring its own window: with recession runs about six days long, `tau ≈ run_length / log_range` returns ~3 days whatever the real drainage rate. Synthetic data with 30-day recessions separated a connected site from hillslope controls cleanly; real Tahoe storms arrive closer together.

`_recession_tau` now refuses to report when the runs are too short to contain the constant.

What the record *does* support is weak evidence **against** a stream connection: day-to-day, Glenbrook 2 tracks the creek (r = 0.107) *less* closely than the controls do (0.259, 0.256).

So: unexplained. Sampling the source rasters at each station point — soil depth, texture, aspect — is the honest next step, and it's real GIS work rather than another query.

---

## The API had to be guessed

**Slugs are truncated to the leading concept.** `Air Temperature & Relative Humidity` is `/sensors/air-temperature`. `Soil Environmental Conditions` is `/sensors/soil-moisture`.

**Timestamps have three field names.** EXO and the Campbell loggers use `TIMESTAMP`. HOBO uses `timestamp`. MiniDOT uses **`Pacific Standard Time`** — its export writes the *timezone* as the header of its time column and TEON's loader kept it verbatim. Assuming one name cost **1,490,116 rows** their timestamps.

**Measurements have four conventions.** `Air_Temp`, `Do_mgL`, `conductivity`, `Dissolved Oxygen Saturation`.

**And TEON publishes a visibility flag** at `/site-visibility/disabled`. Both the dashboard and the ingest honour it, and if the endpoint can't be read they fetch nothing rather than guessing.

`pixi run record-shape` exists so the next consumer doesn't discover this the way we did.

---

## The ones you have to swim out to

Fourteen instruments across eight sites are self-logging — read by boat and snorkel, roughly monthly. TEON labels only two; the rest are MiniDOTs and HOBOs, which have no telemetry as a product category, and all twelve stop on **2026-06-10**, the same day.

| Sonde | Telemetry | Complete |
|---|---|---|
| Blackwood 3 | manual | **98.8 %** |
| Meeks | manual | **98.8 %** |
| Glenbrook | live | 96.8 % |
| Sunnyside | live | 73.6 % |

Both manual sondes are short by **exactly 191 records** — 47.75 hours. One service visit, not packet loss.

**Telemetry buys timeliness, not completeness.** Sunnyside has lost over a quarter of its record to the radio.

They get coverage cards rather than live cards — period of record held, what's outstanding — and when new data appears upstream a **banner** goes up on the page saying how many records and which command fetches them. The watcher opens a GitHub issue at the same time.

---

## pH 1.8, or: we've seen this movie

**pH is dead fleetwide.** Glenbrook returns `0`. Sunnyside returns `null`.

**Sunnyside reads negative turbidity** — −2.109 FNU, σ 0.042, across 48 readings. A mis-set zero point. USGS on Blackwood Creek reads +0.3 FNU from a different instrument and agency.

**A soil pH of 1.79** at Cave Rock in TEON's watershed layer. Approximately battery acid; nodata averaged in as zeros.

**A Topographic Wetness Index of 833.9** against a 5–55 range. Marlette Creek, whose catchment contains a lake.

**A camera filed a frame from the future** — six hours ahead of the snapshot containing it.

**Instruments go quiet without the logger noticing.** At Glenbrook 5 the air temperature and humidity probe was offline from early June to mid-August 2025 while its soil sensors logged straight through — temperature and humidity always drop out together, the signature of one probe. The same station nearly vanished for June 2026, with 31 readings all month, and that month never came back upstream. Glenbrook 2 and Blackwood 2 are dark now, both on healthy batteries, and Glenbrook 1's battery channel has been frozen at exactly 11.45 V.

Cataloguing these *is* the work, and it's much easier from outside than operating the network. All reported back.

---

## The record

```
data/processed/observations/
    source=teon/year=2025/month=01/part.parquet    frozen
    ...
    source=teon/year=2026/month=09/part.parquet    the only file that churns
```

**11,151,846 observations, June 2024 to now** — twenty-seven months. Every reachable TEON record, across four backfill stages, plus USGS and 60 catchments. The network was built out progressively: Blackwood 2 first in June 2024, then UNR and the Glenbrook stations through late 2024, Glenbrook 2 in mid-2025, and Homewood last, in September 2025.

`pixi run query` opens a SQL shell over all of it, reading the parquet in place. A filtered aggregate over the whole store answers in under a tenth of a second.

Hive-partitioned because a parquet is rewritten whole on every update and git stores each version as a new blob. Historical months freeze; only the current one churns.

Backfills **append then compact** rather than read-merge-write. The first design was quadratic: 1.78 million observations meant 89 flushes each rewriting everything accumulated — **80 million row writes, 45× amplification, ~2 GB to store 45 MB** — and it filled the disk mid-run.

All writes are **atomic**. That failed run left truncated partitions and took about **76,000 records** with it: 28,535 at the nearshore sites, noticed at once because those sites have coverage cards, and about 47,800 at the forest stations and lake sondes, noticed a day later only because a SQL query compared what we held against TEON's counts. All recovered. Writes now go to a temp file and replace on success; compaction refuses to touch a partition it can't fully read.

---

## It watches itself

**fetch** hourly, **pages** at :25, **watch** every six hours.

`pages` has its own schedule rather than triggering off fetch's commit, because GitHub deliberately does not create workflow runs from events triggered by the default `GITHUB_TOKEN`. That coupling looked right and never once fired.

`watch` opens a GitHub issue on anything notable — a sensor resuming, a new sensor type, a dormant sonde's record count jumping. Local runs are **read-only**: a local check that advanced the baseline would *consume* the change before CI could report it.

---

## The bugs were mostly mine

Thirty errors shipped or nearly shipped. Every one produced **plausible-looking output** rather than a crash. The instructive ones:

| What broke | How it looked |
|---|---|
| Sensor-slug map keyed on wrong names | Skipped all 23 sensors, **zero HTTP requests** |
| `statistic_id=00011,00006` | **HTTP 200, zero features, every gauge** |
| Web Mercator polygons vs WGS84 points | 0 of 28 stations matched, no error |
| 48-hour slope on a diurnal signal | Air temperature "rising 4.85" |
| Six string replaces that matched nothing | Printed success |
| `GITHUB_TOKEN` pushes don't trigger workflows | Green cron, hours-old page |
| **Four** stale-base copies deleting features | A map layer, a timestamp parser, four CLI modes |
| A local import shadowing a module one | **`UnboundLocalError`, cron down for hours** |
| Backfill wrote non-canonical sensor types | **98.9 % of the record invisible** |
| Quadratic write amplification | **Filled the disk mid-backfill** |
| Non-atomic writes | **About 76,000 records lost**, all recovered |
| Fail-open defeating a fail-closed guard | A safety check that could never fire |
| Correlation as a discriminator, twice | Numbers that couldn't separate anything |
| Recession fit measuring its own window | Four signals, all ~3 days |
| A ratio quoted from one day's reading | 12× that was really 2.9–4.2× |
| Collapsed endpoints that shared rows but not columns | **Air temperature, humidity and tree-stress history discarded** |
| A fifth stale-base copy, this time of the backfill | The quadratic writer that filled the disk, silently back |
| A lag in hours read from day-resolution data | "+3.7 h, the sturdiest finding" — an artifact |
| A data-loss incident sized from the only sites with cards | 28,535 lost records that were really about 76,000 |

### The pattern

Almost all of them are **something reporting success while doing nothing**. Zero rows looks like no data. A green workflow looks like a deployed page. A name in a file looks like an imported one. A high correlation looks like a relationship.

Four rules fell out, all in `docs/`:

- **When a query narrows results, check the count, not the syntax.**
- **Check the thing you care about, not a proxy that correlates with it.**
- **Before committing a generated file, ask who else writes it.**
- **A safety check is only as strong as the weakest layer that can answer it.**

And three test files. `test_capabilities_persist.py` is the one that earns its place: every *other* test checks internal consistency, and a consistent **subset** of the feature set passes all of them. It's a plain list of what must exist, and it has caught two separate deletions — including four CLI modes in a bundle I was about to call finished.

Nine probe commands exist for the same reason. A few dozen lines each; eight real bugs between them.

---

## By the numbers

```
  11,151,846   observations stored, Jun 2024 to now
     374,942   paired readings behind the oxygen finding
   1,490,116   rows that once landed with no timestamp, recovered
      76,361   records lost to a non-atomic write, all recovered
         375   days of overlapping transect history
          60   stream catchments, 164 attributes each
          43   sensors listed by the API
          30   of my own bugs caught before or shortly after shipping
          13   actual physical devices
           8   data-quality faults found upstream, all reported
        3.03×  rain-shadow gradient across the basin
    0.004 mg/L the EXO fit to a sea-level atmosphere
     0.00 km   reprojection error, against published centroids
           0   pH readings worth anything
```

---

## Running it

```bash
pixi install
pixi run pipeline     # ingest both agencies, rebuild, prune
pixi run serve
```

| Task | What it does |
|---|---|
| `backfill --stage …` | full history for one fleet, straight to parquet |
| `transect` | do the two shores respond differently to the same storm |
| `glenbrook` | why is one station four times wetter than its neighbours |
| `oxygen-check` | which atmosphere each instrument family references |
| `record-shape` | field names per sensor type — **run before any new backfill** |
| `query` | SQL over the whole store, in place — see `docs/querying.md` |
| `catchment-join` | assign stations to catchments |
| `watch` | report upstream changes (read-only) |
| `store-status` | partitions, row counts, sizes |

Needs a free [USGS API key](https://api.waterdata.usgs.gov/signup/) in `USGS_API_KEY`.

---

## What the disk can't see yet

- **3,468 camera frames** in a bucket named *Snow photos*, back to November 2025. The bucket refuses anonymous reads. A winter of snowpack from five angles, one email away.
- **Why Glenbrook 2 is wet.** Needs rasters sampled at each station point.
- **The transect against rainfall.** The precipitation record is now in the store; the analysis hasn't been rebuilt on it.
- **Ground truth for a clarity model.** TERC's Secchi record is in the [EDI repository](https://portal.edirepository.org/nis/mapbrowse?scope=edi&identifier=1340), versioned and DOI-bearing, back to 1968. Their 2025 report shows why any model must be **seasonal**: winter clarity is stable, summer is degrading, and 2025's summer average of 53.4 ft was the fifth poorest on record.

TERC — which *is* UC Davis, not a separate organisation — also began in 2025 assembling decades of clarity-driver data alongside Secchi depth. That's the same analysis this project's nowcast idea sketches, by the people with the instruments, the fifty-eight-year record and the funding.

So this isn't a novel scientific result. What it is: a fast, public, reproducible view over data otherwise scattered across two agencies and several undocumented endpoints, with its own problems stated on the face of it.

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
