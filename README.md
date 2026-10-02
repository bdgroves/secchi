# ⚪⚫ secchi

### A modern Secchi disk for Lake Tahoe

**[→ Live dashboard](https://brooksgroves.com/secchi/)** · two agencies, 12.0 million observations, twenty-seven months, updating hourly and watching itself

---

## Cold open

In 1865 the Papal Navy asked an astronomer a simple question: *how clear is the water?*

Angelo Secchi lowered a white plate over the side of a yacht in the Mediterranean and wrote down the depth at which it disappeared.

That's the instrument. A plate on a rope.

One hundred and sixty-one years later it is still the world standard for lake transparency, and it is still how Lake Tahoe's clarity gets reported every year.

On **September 15, 2026**, the University of Nevada, Reno switched on the [Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/). I saw it in the alumni newsletter and went to look at the API.

There was no API documentation yet — normal for a network this new — so everything here started by probing it.

---

## What this found

TEON had been live for days when this started, and all of it was found because the network publishes its data openly to anyone. These are the growing pains of a new observatory seen from outside, and every one has been, or is being, passed back to the TEON team.

### 1. Half of TEON's oxygen saturation is referenced to the wrong atmosphere

Lake Tahoe's surface sits at **1,898 m**, where air pressure is about **79.5 %** of sea level. Saturation depends on pressure, so a percentage referenced to sea level is wrong by roughly twenty points — enough to invert the reading.

Tested across **427,517 paired readings**, eleven instruments, twenty months:

| | readings | fit to sea level | fit to lake pressure |
|---|---|---|---|
| **EXO sondes** | 162,201 | **0.004 mg/L** | 1.847 mg/L |
| **MiniDOT** | 265,316 | 2.097 mg/L | **0.142 mg/L** |

0.004 mg/L is the precision of the Weiss (1970) formula itself.

**The MiniDOTs are altitude-corrected. The EXO sondes are not.** Every one of the eleven sites agrees with its instrument family, and **each of the five EXO sondes fits sea level to 0.004 mg/L on its own** — EXO reading 78–85 % where the water is really at 99–107 %, MiniDOT agreeing with the corrected figure to within half a point.

The fifth sonde, 4H Camp, arrived when TEON made the site public on 2026-09-26, and at first it looked like an exception: the fleet's fit jumped from 0.004 to 0.242 mg/L. It wasn't configuration. For a week in July 2026 its optical channels were filed under the wrong names — saturation in the concentration field, chlorophyll in the saturation field — and the check was averaging those into every EXO verdict. Impossible readings are now set aside and counted, and each site is fitted on its own: **5,765 set aside across four sondes**, most of them Sunnyside's eight-week scramble (below), which an earlier version dropped without counting.

I expected both fleets to share the error, and being wrong made it stronger: one fleet right and one wrong, in the same network, rules out a deliberate sea-level convention. The MiniDOTs are the control, and they prove the correction is achievable in TEON's own pipeline.

Reported upstream. `pixi run oxygen-check`.

### 2. Forty-three sensors are thirteen devices

At every forest station, the soil, air-temperature, tree-stress *and* stream-level endpoints return **projections of one Campbell logger table** — identical record UUIDs, identical battery voltage, identical row counts.

A full pull grabs 4,600 records; deduplicating collapses it to **1,600**. When this was first counted, the live network was 2 lake sondes, 6 forest loggers and 5 cameras. On 2026-09-28 it's 3 lake sondes (4H Camp went public), 4 of 7 forest loggers and 3 of 5 cameras; `pixi run status` gives the current picture.

**But shared rows are not shared columns**, and I learned that the expensive way. Each endpoint is a *projection* of the logger table: soil returns soil moisture and temperature, air returns air temperature and humidity, tree returns the dendrometers. They share only record IDs and a couple of housekeeping fields. A backfill that fetched one endpoint and treated it as standing in for the rest silently discarded every forest station's air temperature, humidity and tree-stress history. The first query run in the new SQL shell showed air temperature at Homewood covering 7.5 days while soil covered a year.

---

## Same storm, two watersheds

Homewood and Glenbrook 5 sit **64 metres apart in latitude** on opposite shores. Their catchments receive **2.12×** different annual precipitation — basin-wide the gradient reaches **3.03×**.

`pixi run transect` detects wetting events in the soil moisture itself, matches them across stations, and compares response.

The overlap is about a year because Homewood is the youngest forest station, reporting only since September 2025.

**The result: total soil wetting runs at about the rainfall ratio.** Summed over every wetting event in the overlapping year, Homewood's soil took 146 points of wetting against Glenbrook 5's 63 — **2.32×**, against a catchment rainfall ratio of 2.12×. It's the one statistic built to be compared with rainfall, and it doesn't depend on how storms are paired between stations. It leans on a few big west-shore storms Glenbrook 5 never felt (+22.2 on 2025-10-02, +16.1 on 2025-12-17), so it's encouraging rather than settled.

**Storms mostly reach the west shore first: 15 of 18 shared events.** How long they take to cross can't be measured here. Detection runs on daily means, which it has to in order to reject the soil's daily cycle, so most storm onsets can only be placed to the day. I twice reported a lag in hours — +3.7 h, then +11.7 h — and called it the sturdiest finding. The first was biased short by a 12-hour matching window; the second was inflated by storms stamped at midnight. Only 6 of 18 pairs resolve to a real hour, spread from 4 to 37 hours.

**Getting here took five wrong answers, and every correction moved toward the null:** 3.31× from averaging ratios, 2.82× from counting the daily cycle as storms, 1.29× from matching at the wrong resolution, and the two lag figures. The number that survived is the one defined, before its value was known, to be compared against rainfall.

**Checked against the rain that actually fell (2026-10-02).** TEON's only rain gauge couldn't settle it: it's on the west shore, it's missing 136 days — mostly the wet season — and on snow days it catches about a quarter of what a SNOTEL gauge nearby records. So secchi now also reads three NRCS **SNOTEL** stations: Ward Creek #3 and Rubicon #2 on the west, Marlette Lake on the east (`pixi run transect-rain`, `docs/snotel.md`).

| September 2025 to June 2026 | West | East | Ratio |
|---|---|---|---|
| **Precipitation, measured** | 1,643 mm | 762 mm | **2.16** |
| **Soil wetting** | 146 points | 63 points | **2.32** |
| Catchments' long-term average | | | 2.12 |

**The rain shadow reaches the soil in about the same proportion it falls,** and this was an ordinary year for it. Marlette Lake sits about 1,000 ft above Ward Creek #3, and higher gauges usually catch more, so the true contrast at the soil stations is probably a little larger than 2.16 — closer still to the soil's 2.32.

Two things the rain revealed. **Eight of the 18 shared wetting events had no precipitation at either gauge** — all between October and April, almost certainly snowmelt, warming both shores at once. And in the **10 real storms, the shore that got more rain also wetted more in 7.** Of the storms the soil didn't register, most fell as snow, which wets soil only later, as it melts.

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

Fourteen instruments across eight sites are self-logging — read by boat and snorkel, roughly monthly. TEON labels only two; the rest are MiniDOTs and HOBOs, which have no telemetry as a product category.

You can watch a field crew's day in the data. On **Monday 2026-09-28** someone serviced all six nearshore sites in three hours — Lake Forest at 1:00 pm, Incline at 1:45, then after the long crossing Tallac at 3:15, Camp Richardson 3:30, Tahoe Keys 3:45 and Lakeside 4:02 — each handing over about 9,900 readings, HOBO first and MiniDOT a minute later on the same mooring. Lakeside's HOBO is the exception: it hasn't reported since 2025-08-08. The two hand-collected EXO sondes, Blackwood 3 and Meeks, were last read on 2026-07-09.

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

**pH works on the hand-collected sondes and fails on the telemetered ones.** Blackwood 3 and Meeks read a steady 7.8–7.9. Glenbrook and 4H Camp report `0` for most of their record, Sunnyside has stopped reporting it, and 4H Camp once reported a pH of 89.4. pH probes need regular calibration; the hand-collected sondes are visited about monthly.

**Sunnyside reads negative turbidity** — −2.109 FNU, σ 0.042, across 48 readings. A mis-set zero point. USGS on Blackwood Creek reads +0.3 FNU from a different instrument and agency.

**A soil pH of 1.79** at Cave Rock in TEON's watershed layer — and it isn't alone. **22 of 60 catchments average below 4.5**, and the layer's own standard deviations show why: a catchment whose cells are partly zero and partly real soil has a predictable mean *and* spread, and they match. Cave Rock's 1.79 with a spread of 2.75 is exactly a 70/30 mix of zeros and pH 6.0. Solving for each catchment, **38 of 60 contain empty cells averaged as zero**, and once removed every one comes out at pH 5.6–6.4, in line with the 22 clean catchments. The layer is right about the soil and wrong about the arithmetic, and it's recoverable.

**A Topographic Wetness Index of 833.9** against a 5–55 range. Marlette Creek, whose catchment contains a lake.

**A camera filed a frame from the future** — six hours ahead of the snapshot containing it.

**Instruments go quiet without the logger noticing.** At Glenbrook 5 the air temperature and humidity probe was offline from early June to mid-August 2025 while its soil sensors logged straight through — temperature and humidity always drop out together, the signature of one probe. The same station nearly vanished for June 2026, with 31 readings all month, and that month never came back upstream; its battery had collapsed to 10.9 V, so that one was a power failure.

**Outages that came back.** In late September three stations went dark on working batteries, and all three returned. Blackwood 2, silent since June 18, uploaded its whole outage at once: 9,519 new records to September 25, which at one reading every 15 minutes is every reading in those 99 days — then went quiet again from September 25, on a healthy battery. Glenbrook 5's three-day gap filled completely too, and Glenbrook 2 is back, though its September 22–28 gap hasn't (yet). A logger that loses only its link keeps recording; one that loses power records nothing.

**An outage's cause is written in the battery.** Glenbrook 4 lost about 68 days to outages in 2024–25 that never came back. Before each winter outage its battery had collapsed to 6.8–8.1 V: power failures, with the logger shut down and nothing recorded to recover. This September it dropped out for a day on a healthy battery, and every reading came back when it reconnected — the logger had kept recording while it couldn't transmit. `pixi run station-health` tells the two apart for every station.

**Two batteries that nobody is charging.** A charging battery reaches about 14 V on any sunny day; over two weeks in late September, every charging station's highest peak was 14.0–14.4 V, Glenbrook 5's was 12.4 and Glenbrook 1's was 11.5. Glenbrook 5 has had no charging since June 2026, surviving on battery swaps in May and September, and is falling again. Glenbrook 1's daily charging swing disappeared in **July 2025**. Since then its supply has only fallen — through summer, when a panel charges hardest — apart from one jump in January 2026 that looks like a battery swap, and it's at 11.40 V now: fifteen months without charging, kept alive by swaps. Glenbrook 4 shows what a repaired station looks like: its daily peaks jumped from about 13 V to 14.2–14.6 V in September 2025, the level a working charge controller reaches, and its next winter held above 12 V where the one before had collapsed to 7. It was reported here as a *stuck channel* first, because its readings barely move within a day; that label took the most at-risk station off the at-risk list. It's heading for a power failure, the kind whose gap never comes back, and it's preventable.

**Channels filed under the wrong names.** For about six days in July 2026, ending 2026-07-24, 4H Camp's optical sensors reported into each other's columns: its oxygen *saturation* (normally ~85 %) arrived in the concentration field as "85.8 mg/L", its *chlorophyll* (~0.67) arrived as "0.65 % saturation", and its *concentration* (~7.8 mg/L) arrived as turbidity, a spike that never happened. Temperature, conductivity and depth were untouched, so the scramble is confined to the optical sensors — consistent with sensors moved between ports at a service visit while the parser kept the old order. That's inference, but it means the readings are probably intact and could be remapped. I first wrote that the concentration "read normally"; it read 85 mg/L, which no lake holds.

**A whole sonde scrambled for eight weeks.** From 2026-04-30 11:30 to 06-25 08:00, every one of Sunnyside's channels was filed under another's name — 5,145 readings with a "water temperature" of 85–102, which is its oxygen saturation; its real temperature arrived as saturation, and depth, salinity and turbidity were jumbled too. April 30 is also the day Blackwood 3, a few miles down the west shore, shows a service visit, and the scramble ends with three impossible readings on the morning of June 25: two visits, one that scrambled the sonde and one that fixed it. None of it was flagged anywhere.

**Service visits aren't flagged.** Blackwood 3's April 30 visit left six and a half hours of warm, low-oxygen, disturbed readings with a freshly charged battery: a sonde being handled. They're real readings, and they shouldn't be used.

The watcher now scans the whole lake record every six hours for readings no lake can produce — oxygen above 20 mg/L, saturation outside 50–150 %, water temperature outside −2 to 35 °C, pH outside 0–14 — and opens an issue for each new episode, once.

**4H Camp's turbidity reads −2.4 NTU** normally, a second sonde with a negative zero offset after Sunnyside's −2.1.

Cataloguing these *is* the work, and it's much easier from outside than operating the network. Reported, or in the next note to TEON.

---

## The record

```
data/processed/observations/
    source=teon/year=2025/month=01/part.parquet    frozen, one file per month
    ...
    source=teon/year=2026/month=09/part.parquet    frozen
    source=teon/year=2026/month=10/part.d01.parquet  one file per day from October 2026
    source=teon/year=2026/month=10/part.d02.parquet  ...only today's file changes
```

**12,023,843 observations, June 2024 to now** — twenty-seven months. Every reachable TEON record, across four backfill stages, plus USGS and 60 catchments. The network was built out progressively: Blackwood 2 first in June 2024, then UNR and the Glenbrook stations through late 2024, Glenbrook 2 in mid-2025, and Homewood last, in September 2025.

`pixi run query` opens a SQL shell over all of it, reading the parquet in place. A filtered aggregate over the whole store answers in under a tenth of a second.

Hive-partitioned because a parquet is rewritten whole on every update and git stores each version as a new blob. Historical months freeze. **That still wasn't enough:** the repository reached **1.1 GB in its first two weeks**, about 5.5 MB per run. Two causes, both fixed on 2026-10-01:

- **Unchanged files were rewritten.** Every run rewrote every month it touched even when it added nothing, and an unstable sort put tied rows in a different order each time, so identical data became a new file. Rows are now written in a fixed order, and a file whose contents haven't changed isn't written at all.
- **The current month was one 3 MB file.** From October 2026 it's one file per day, so a run that adds readings rewrites about 100 KB.

A run now adds about 0.4 MB to the repository, and nothing when there's no new data — roughly a fourteenth of before, which makes truly hourly runs affordable.

The total above is also smaller than it was: **740,660 undated rows** turned out to be duplicates of readings stored with proper dates, from a September backfill, and were inflating the count by 6 %. They're removed, `drop-undated` now refuses to delete any reading that has no dated copy, and `status` reports undated rows if they reappear.

Backfills **append then compact** rather than read-merge-write. The first design was quadratic: 1.78 million observations meant 89 flushes each rewriting everything accumulated — **80 million row writes, 45× amplification, ~2 GB to store 45 MB** — and it filled the disk mid-run.

All writes are **atomic**. That failed run left truncated partitions and took about **76,000 records** with it: 28,535 at the nearshore sites, noticed at once because those sites have coverage cards, and about 47,800 at the forest stations and lake sondes, noticed a day later only because a SQL query compared what we held against TEON's counts. All recovered. Writes now go to a temp file and replace on success; compaction refuses to touch a partition it can't fully read.

The hourly job and local runs both write the current month's partition, and git can't merge binary files. A custom merge driver treats each partition as a set of readings and merges three-way against the common ancestor, so `git pull` no longer stops on them. It's registered once per clone with `pixi run setup-git`.

---

## It watches itself

**fetch** hourly at :41, **pages** at :25, **watch** every six hours.

Not at :00. GitHub delays scheduled runs at busy times and drops some outright, and the top of the hour is the busiest: scheduled at minute 0, the "hourly" fetch actually landed every three to eight hours.

`pages` has its own schedule rather than triggering off fetch's commit, because GitHub deliberately does not create workflow runs from events triggered by the default `GITHUB_TOKEN`. That coupling looked right and never once fired.

`watch` opens a GitHub issue on anything notable — a sensor resuming, a new sensor type, a dormant sonde's record count jumping. Local runs are **read-only**: a local check that advanced the baseline would *consume* the change before CI could report it.

---

## The bugs were mostly mine

Forty errors shipped or nearly shipped. Every one produced **plausible-looking output** rather than a crash. The instructive ones:

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
| `.gitattributes` lines in the wrong order | The merge driver silently switched off; caught by asking git |
| A "stuck channel" label on a battery that wasn't charging | **The station most at risk, taken off the at-risk list** |
| One average across a whole instrument fleet | One sonde's bad week moved five sondes' fit sixty-fold |
| A guess from a ratio, written up as a finding | "Normal concentrations alongside" — they read 85 mg/L |
| A silent `continue` next to the counted one | **5,145 scrambled Sunnyside readings dropped unseen**; "3 set aside" |
| An hourly schedule at minute 0 | Snapshots every 3–8 hours, while the README said hourly |
| A freshness check timed from now, not from the snapshot | Two healthy stations reported dark by the new `status` |
| An unstable sort, and rewriting files that hadn't changed | **Identical data stored again on every run; 1.1 GB in two weeks** |
| Undated duplicates counted in the total | 740,660 phantom observations in the headline figure |
| A check that couldn't run, reported as passing | `status` said "all stations fine" with DuckDB missing |

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
  12,023,843   observations stored, Jun 2024 to now
     427,517   paired readings behind the oxygen finding
   1,490,116   rows that once landed with no timestamp, recovered
      76,361   records lost to a non-atomic write, all recovered
         375   days of overlapping transect history
          60   stream catchments, 164 attributes each
          43   sensors listed by the API
          40   of my own bugs caught before or shortly after shipping
          13   actual physical devices
          13   data-quality faults found upstream
        3.03×  rain-shadow gradient across the basin
    0.004 mg/L the EXO fit to a sea-level atmosphere
     0.00 km   reprojection error, against published centroids
           2   lake sondes with a working pH channel, both hand-collected
```

---

## Running it

```bash
git clone git@github.com:bdgroves/secchi.git
cd secchi
pixi install
pixi run setup-git    # once per clone: the parquet merge driver
pixi run -e dev test
pixi run transform    # builds the page's data, which isn't committed
pixi run serve        # http://localhost:8000
pixi run status       # is anything wrong, and is there anything to do?
```

Picking this up on a new machine, or in a new Claude session? Start with **[`HANDOFF.md`](HANDOFF.md)** — setup, current state, open questions and next steps.

| Task | What it does |
|---|---|
| `backfill --stage …` | full history for one fleet, straight to parquet |
| `transect` | do the two shores respond differently to the same storm |
| `glenbrook` | why is one station three to four times wetter than its neighbours |
| `station-health` | logger battery per station: power failure, or something else |
| `oxygen-check` | which atmosphere each instrument family references |
| `record-shape` | field names per sensor type — **run before any new backfill** |
| `status` | **one screen: stations, batteries, lake, data waiting, impossible readings, what to do** |
| `query` | SQL over the whole store, in place — see `docs/querying.md` |
| `catchment-join` | assign stations to catchments |
| `watch` | report upstream changes (read-only) |
| `store-status` | partitions, row counts, sizes |

Needs a free [USGS API key](https://api.waterdata.usgs.gov/signup/) in `USGS_API_KEY`.

---

## What the disk can't see yet

- **3,468 camera frames** in a bucket named *Snow photos*, back to November 2025. The bucket refuses anonymous reads. A winter of snowpack from five angles, one email away.
- **Why Glenbrook 2 is wet.** Needs rasters sampled at each station point.
- **Snowmelt in the transect.** Eight wetting events had no precipitation; matching them to SNOTEL's snow-water equivalent would confirm melt.
- **Ground truth for a clarity model.** TERC's Secchi record is in the [EDI repository](https://portal.edirepository.org/nis/mapbrowse?scope=edi&identifier=1340), versioned and DOI-bearing, back to 1968. Their 2025 report shows why any model must be **seasonal**: winter clarity is stable, summer is degrading, and 2025's summer average of 53.4 ft was the fifth poorest on record.

TERC — which *is* UC Davis, not a separate organisation — also began in 2025 assembling decades of clarity-driver data alongside Secchi depth. That's the same analysis this project's nowcast idea sketches, by the people with the instruments, the fifty-eight-year record and the funding.

So this isn't a novel scientific result. What it is: a fast, public, reproducible view over data otherwise scattered across two agencies and several undocumented endpoints, with its own problems stated on the face of it.

---

## Data & attribution

Sensor data from the **[Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/)** (Tahoe Institute for Global Sustainability, University of Nevada, Reno, with the U.S. Forest Service Pacific Southwest Research Station and the Tahoe Science Advisory Council) and the **U.S. Geological Survey**. TEON asks that its website be acknowledged in derived products; this is one, and it's acknowledged here and on every page.

All data is **provisional**, as TEON's own disclaimer says: raw or lightly processed, and subject to instrument performance, maintenance cycles and field conditions. Nothing here is an official TEON or USGS product.

`secchi` honours TEON's `/site-visibility/disabled` flags in both display and ingest.

Clarity context from UC Davis TERC and the Lake Tahoe TMDL. If you use anything derived from this repo, cite the upstream sources, not this one.

---

## Part of

🌲 **Environmental Intelligence Lab** · [Brooks Labs](https://github.com/bdgroves)

MIT licensed. Built by [Brooks Groves](https://brooksgroves.com), GISP® — University of Nevada, Reno, B.S. Biology 1994 — who read about TEON in the alumni newsletter and loves the lake.

---

> *A plate on a rope, and a hundred and sixty-one years of wanting to know how deep you can see.*
