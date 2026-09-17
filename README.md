# ⚪⚫ secchi

### A modern Secchi disk for Lake Tahoe

**[→ Live dashboard](https://brooksgroves.com/secchi/)** · updating hourly from a sensor network that went public two days before this repo existed

---

## Cold open

In 1865, an Italian astronomer named Angelo Secchi was handed a problem by the Papal Navy: *how clear is the water?* He lowered a white disk over the side of a yacht in the Mediterranean and wrote down the depth at which it vanished.

That's it. That's the instrument. A plate on a rope.

One hundred and sixty-one years later it is still the standard measure of lake transparency on Earth, and it is still how Lake Tahoe's famous clarity gets reported every single year. Twenty-two metres in the 1960s. Roughly seventeen now. A number you get by dropping a disk in the water and watching it disappear.

On **September 15, 2026**, the University of Nevada, Reno switched on the [Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/) — sondes in the water, loggers in the forest, cameras on the ridgelines, all of it streaming. For the first time you can watch clarity *assemble itself* out of its causes in near-real time: the chlorophyll bloom, the turbidity pulse, the soil that did or didn't hold last night's rain.

On **September 17, 2026**, this repo started pulling all of it.

`secchi` is the disk, rebuilt as a pipeline.

---

## The instrument

```
                         ╭───────────────╮
                         │  ███████      │   ← 15-min telemetry
                         │  ███████      │      temp · chlorophyll
                         │       ███████ │      DO · turbidity
                         │       ███████ │      conductivity · phycocyanin
                         ╰───────┬───────╯
                                 │
                            ~3 m of rope
                                 │
                            Lake Tahoe
```

Two YSI EXO multiparameter sondes are reporting from opposite shores of the lake right now — **Sunnyside** on the west, **Glenbrook** on the east — dropping a full water-chemistry panel every fifteen minutes. Six Campbell Scientific loggers sit up in the watersheds reading soil moisture at two depths, air temperature, humidity, and the swelling and shrinking of instrumented tree trunks. Five field cameras watch the snow.

`secchi` archives every reading, reshapes it, and puts it on a page.

---

## What we had to figure out first

TEON is two days old. There is no API documentation. There is a JavaScript frontend and a REST backend on an AWS container, and everything below was reverse-engineered from network traffic and a lot of educated guessing.

**The slug convention.** Time-series endpoints don't use the sensor's display name. They use the label *truncated to its leading concept*, kebab-cased. `Air Temperature & Relative Humidity` is not `air-temperature-relative-humidity` — it's just **`air-temperature`**. `Tree stress and growth` is **`tree-stress`**. `Soil Environmental Conditions` is, for reasons known only to the backend, **`soil-moisture`**. Six endpoints found by probing candidate slugs until something returned a 200:

| Display name | Endpoint |
|---|---|
| EXO | `/api/sensors/exo-sensor` |
| Air Temperature & Relative Humidity | `/api/sensors/air-temperature` |
| Soil Environmental Conditions | `/api/sensors/soil-moisture` |
| Tree stress and growth | `/api/sensors/tree-stress` |
| Stream Level | `/api/sensors/stream-level` |
| Field Camera | `/api/sensors/field-camera` |

`pixi run probe` re-resolves all of them and prints a report. Run it whenever TEON ships a change.

**The 43 sensors that are actually 13 devices.** The inventory endpoint lists forty-three sensors. They are not forty-three sensors.

At every forest station, the soil, air-temperature, tree-stress *and* stream-level endpoints return **three-to-four projections of one logger table** — identical record UUIDs, identical battery voltage, identical panel temperature, identical row counts. One Campbell unit is being served under four different names.

The arithmetic is exact. A full pull grabs 4,600 records. Deduplicating on `(uuid, variable)` collapses it to **1,600 unique observations**: 400 from the two lake sondes, 1,200 from six terrestrial loggers, and every field-camera frame living entirely in a separate asset table. 2.25× redundancy in the API, gone in storage.

So the real network is **2 lake sondes + 6 forest loggers + 5 cameras**. Good to know before you go writing a paper about forty-three independent measurement sites.

**Where the photos live.** The cameras return `s3://teon-loggernet-data-storage/Glenbrook 2 - Terrestrial/Snow photos/…`. Which tells you the whole backend stack — Campbell LoggerNet writing into an S3 bucket — and also that a browser can't render any of it. 3,378 frames exist upstream, 1,000 pulled so far, zero displayable. Yet.

**Whose data this isn't.** TEON publishes a `/api/sensors/locations` inventory *and* a `/api/site-visibility/disabled` list. The 4H Camp sonde appears in the first and is suppressed in the second: deployed, reporting, and flagged non-public by the observatory. Their own frontend hides it. So does this one. The dashboard renders a card explaining *why* the site is blank rather than quietly scraping around the flag.

---

## We got cows

Every good monitoring project has the moment where the instrument tells you something absurd and you have to work out whether it's the world or the wiring. Here's the current list, all of it live on the dashboard with caveats attached rather than silently smoothed away.

**The pH probe is dead across the entire fleet.** Glenbrook returns exactly `0`. Sunnyside returns `null`. Not a plausible reading anywhere. Hidden from display, flagged in the data dictionary, and almost certainly the reason TEON ships a `/api/calibration/events` endpoint.

**Sunnyside is reading negative turbidity.** Down to **−1.98 FNU**. Optical sensors do dip slightly below zero at their detection floor in genuinely clear water, and Tahoe is genuinely clear — but −1.98 is an order of magnitude past noise. That's a calibration offset. Clipped to zero for display, marked *calibration suspect* on the card.

**Glenbrook 2's soil is soaking wet and nothing around it is.** Volumetric water content across the eastern cluster, mid-September, all within about a kilometre of each other:

```
Glenbrook 2   ████████████████████████████████████████  42.0 %
Glenbrook 1   ███████████                                11.5 %
Glenbrook 4   ██████                                      6.4 %
Glenbrook 5   ███                                         3.5 %
Homewood      ███                                         3.5 %   (west shore)
```

Regional climate cannot produce a 12× difference over a kilometre. A microsite can. And Glenbrook 2 is — not coincidentally — the one station that also carries the **stream level** sensor. It's sitting in a riparian zone. That single fact invalidated the first version of this project's headline comparison (see the roadmap).

**The stream gauge is labelled `Uncalibrated_water_depth` and reads 0.001 m.** Upstream field name, not ours. Either the channel is dry or the datum correction hasn't been applied. Treated as relative, never absolute.

**A field camera filed a frame from the future.** Glenbrook 4's most recent capture is timestamped six hours *after* the snapshot that contains it. TEON returns naive timestamps with no offset, the loggers appear to be on Pacific wall-clock, and the cameras appear to be on UTC. Mixed conventions in one payload. Unresolved, tracked below, and the reason every timestamp in `latest.json` now carries an explicit offset instead of leaving browsers to guess.

None of this is a knock on TEON. The network is two days old and visibly mid-build. Cataloguing the gremlins *is* the work.

---

## By the numbers

```
  22,471   numeric observations archived
   1,600   unique records after dedupe
   3,378   camera frames upstream (1,000 pulled)
      43   sensors listed by the API
      13   actual physical devices
       6   endpoint slugs reverse-engineered
       1   site TEON asked us not to show   (we don't)
       5   data-quality gremlins catalogued
       0   pH readings worth anything
```

Snapshots land in `data/raw/` hourly via GitHub Actions and are never overwritten — the append-only record is the point.

---

## Roadmap

### Next up

**Fix the transect.** The dashboard leads with a west-vs-east watershed comparison, and right now it's **measuring the wrong thing**. Two problems, both mine: it pairs each site's *latest* reading, and those readings can be six hours apart — so a diurnal temperature swing gets displayed as a geographic signal. And the east anchor is Glenbrook 2, the riparian outlier above. Needs timestamp-aligned binning and an upland anchor (Glenbrook 5 vs Homewood is the honest pairing, and the honest current answer is that in September both sides are equally bone dry).

**Settle the timezone.** Loggers read as Pacific local, cameras read as UTC, and the only hard evidence is a frame from the future. Everything derives from one constant (`TEON_TIMEZONE`), so this is a one-line fix once we know — but we need to know.

**Camera frame counts** currently display the ingest page size, not `total_available`. Small, dumb, visible.

### Then

**Snowpack time-lapse.** 3,378 frames sitting in a bucket named *Snow photos*, going back to November 2025. Needs an HTTPS form of those `s3://` refs or a small proxy. This is the single highest-payoff item on the list — a winter of Tahoe snowpack, animated, from five angles.

**Sparklines.** We store 200 records per sensor per pull and display exactly one. A 24-hour trace under each card turns a snapshot into motion.

**A real map.** Every one of the 43 inventory rows carries lat/lng. Colour by freshness, size by record count, and the network's geography — the Glenbrook cluster, the lonely west-shore stations, the north-end campus — becomes legible at a glance.

**USGS as a second source.** Blackwood Creek (gauge `10336660`) has continuous turbidity and discharge back to **1961**. Glenbrook Creek (`10336730`) is active too. TEON gives us fifteen-minute resolution; USGS gives us sixty-five years of context and covers Blackwood while TEON's own station there is offline.

**The calibration log.** `/api/calibration/events?site_name=…` exists and we've never called it. It probably explains the dead pH probes and the Sunnyside turbidity offset in one request.

### The actual science

**Wildfire in the water.** Smoke deposition drops nutrients on the lake, nutrients feed algae, algae eat clarity. Overlay NASA FIRMS active-fire detections and NOAA HRRR-Smoke plume forecasts on the chlorophyll and turbidity traces and you can watch a fire's fingerprint arrive in Tahoe. Needs a few weeks of continuous history to have anything to detect against — that clock started the moment the cron went live.

**Clarity nowcast.** Predict Secchi depth from live chlorophyll, turbidity and wave stirring, validated against UC Davis TERC's published annual clarity record. If it calibrates over a season, it's a real contribution: the 1865 instrument, estimated continuously, from the inside.

### Housekeeping

- `tests/` doesn't exist yet, so `pixi run -e dev test` errors on an empty path
- `/api/aquatic-metadata/` redirect-loops our client — likely holds deployment depths and QC context
- Dendrometer units are inferred from magnitude (~1,000–3,000 → µm), not documented
- MiniDot, HOBO, Stream Chemistry and Precipitation Gauge slugs are unconfirmed — no live sensors to probe against. Re-run `pixi run probe` when they wake up
- `inspect_schemas.py` should move out of the repo root

---

## Running it

[pixi](https://pixi.sh) handles the environment — conda-forge underneath, one lockfile per platform, ready for GDAL when the geospatial layer lands.

```bash
# Windows
iwr -useb https://pixi.sh/install.ps1 | iex
```

```bash
# macOS / Linux
curl -fsSL https://pixi.sh/install.sh | bash
```

Then:

```bash
pixi install
pixi run pipeline     # pull every live sensor, then rebuild the dashboard data
pixi run serve        # http://localhost:8000
```

| Task | What it does |
|---|---|
| `pixi run ingest-all` | every live, visible sensor across all six types |
| `pixi run ingest` | lake sondes only — faster, for water-quality iteration |
| `pixi run probe` | resolve endpoint slugs and print a report, writing nothing |
| `pixi run transform` | rebuild parquet + `latest.json` |
| `pixi run pipeline` | ingest-all → transform |
| `pixi run serve` | dashboard on :8000 |

Dev environment adds ruff, pytest and JupyterLab: `pixi run -e dev lab`.

---

## How it's wired

```
        TEON REST API  (AWS App Runner, undocumented)
               │
               ▼
    src/secchi/sources/teon.py     slug resolution, pagination, visibility flags
               │
               ▼
    src/secchi/ingest.py           hourly snapshots, append-only
               │
               ▼
        data/raw/**.json           the durable record — never overwritten
               │
               ▼
    src/secchi/transform.py        dedupe, numeric/asset split, scale, clip
               │
               ├──► data/processed/*.parquet     for notebooks and modelling
               └──► web/assets/latest.json       for the page
                            │
                            ▼
                   GitHub Pages  →  brooksgroves.com/secchi
```

`latest.json` is **generated at deploy time**, not committed — it's a derived artifact, and committing it meant every local run raced the hourly cron for the same file. Two workflows: `fetch.yml` archives raw snapshots on the hour, `pages.yml` rebuilds and publishes.

Full endpoint map, record schemas, unit notes and open questions: **[`docs/data-dictionary.md`](docs/data-dictionary.md)**.

---

## Data & attribution

All sensor data belong to the **Tahoe Environmental Observatory Network**, a project of the Tahoe Institute for Global Sustainability at the University of Nevada, Reno, with the U.S. Forest Service, the Tahoe Science Advisory Council, USGS, NOAA, TRPA and the NV Energy Foundation.

Data are provisional. Per TEON:

> Data may be subject to inaccuracies due to instrument performance, maintenance cycles, or environmental conditions at measurement locations.

`secchi` honours TEON's own `/site-visibility/disabled` flags — a site the observatory marks non-public is neither ingested nor displayed. If you use anything derived from this repo, please cite both `secchi` and TEON.

---

## Part of

🌲 **Environmental Intelligence Lab** · [Brooks Labs](https://github.com/bdgroves)

MIT licensed. Built by [Brooks Groves](https://brooksgroves.com), GISP® — who got his undergraduate degree at the university that built the network this reads from.

---

> *A plate on a rope, and a hundred and sixty-one years of wanting to know how deep you can see.*
