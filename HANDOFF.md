# Handoff: picking up secchi

*Rewritten 2026-10-02, at the end of a long working session; updated the
same evening with the Beyond TEON additions and a battery correction. Read this first
on a new computer or in a new Claude chat. `README.md` is the public story;
this is the working state. `CLAUDE.md` holds the coding rules and is loaded
automatically by Claude Code.*

---

## 0. The state of things, in ten lines

1. **It runs itself.** Hourly CI fetches TEON, USGS, SNOTEL and NOAA smoke data, rebuilds the store and commits; the page redeploys hourly; a watcher opens GitHub issues on anything notable.
2. **The data is complete and verified**: about 12.0 million TEON observations, June 2024 to now, plus USGS gauges, SNOTEL precipitation and snowpack, and a daily smoke record.
3. **`pixi run status`** is the one screen to check. Today its to-do list is two items, both TEON's: Glenbrook 1 and Glenbrook 5 aren't charging.
4. **TEON has been told.** An email went on 2026-10-02 to Carina Seitz and Sudeep Chandra, with the full note linked. No reply yet; follow up around 2026-10-09.
5. **Social posts are drafted and held** until TEON replies.
6. **The rainfall transect is answered, as a range**: west/east precipitation 2.16× at the best-matched gauge, 1.20× at the other; soil wetting 2.32×.
7. **Snowmelt is measured**: of 8 no-rain wetting events, 5 coincide with a shrinking snowpack and 3 remain unexplained.
8. **Wildfire smoke left no detectable mark** on the lake in 2025–26, once each month's trend is removed (114 smoke days since June 2024; no lake data for summer 2024).
9. **Beyond TEON grew** (2026-10-02): TERC's Secchi record since 1967, the UC Berkeley Snow Lab (SNOTEL 428 daily, snowfall since 1879) and NWS airport weather at South Lake Tahoe and Truckee. All context, collapsed, beneath TEON's pins on the map. `docs/beyond-teon.md`.
10. **69 tests pass.** Run them before every commit. **Next**: wait for TEON; watch the batteries; then reliable hourly downloads, the tree sensors, or Glenbrook 2's wetness (section 7).

---

## 1. What this is

**secchi** is a public dashboard and data archive for Lake Tahoe built on the
[Tahoe Environmental Observatory Network](https://tahoeenvironmentalobservatorynetwork.org/)
(TEON, University of Nevada, Reno), with USGS stream gauges, and, as context
from outside TEON, NRCS SNOTEL precipitation and NOAA's satellite smoke
analysis. Live at **https://brooksgroves.com/secchi/**. Repo
`git@github.com:bdgroves/secchi.git`; local `C:\data\01_Projects\secchi`.

TEON's API had no documentation; everything about it was worked out by
probing. The project's most useful work so far has been finding problems in
TEON's published data, with evidence, and reporting them back. Built by Brooks
Groves (UNR B.S. Biology 1994), who read about TEON in the alumni newsletter.

---

## 2. Setting up on a new computer

You need **git** (with an SSH key on GitHub, or the HTTPS URL) and
**[pixi](https://pixi.sh)**. Everything else comes from pixi.

```powershell
git clone git@github.com:bdgroves/secchi.git
cd secchi
pixi install
pixi run setup-git            # once per clone: the parquet merge driver
pixi run -e dev test          # 69 tests, all should pass
pixi run transform            # builds web/assets, which aren't committed
pixi run status               # one screen: is anything wrong?
pixi run serve                # http://localhost:8000
```

**`setup-git` matters.** It registers a merge driver that merges store files as
sets of readings; without it `git pull` stops on a conflict whenever CI and
your machine both touched the current day. Check it with
`git config --get merge.parquet-union.driver`.

**USGS key**, only for fetching USGS data locally (CI has it as a secret):
`setx USGS_API_KEY "your-key"`, then open a new window. A lost key can't be
read back from GitHub; get a new one free at https://api.waterdata.usgs.gov/signup/
and update it locally and in the repo's Actions secrets.

**The clone is about 1.1 GB**, because the data store is committed. A clone is
the complete dataset.

**Windows notes.** SQL goes at the `secchi>` prompt after `pixi run query`, not
straight into PowerShell. `Set-Content` in Windows PowerShell 5 writes ANSI;
edit text files in an editor instead. For long outputs,
`pixi run smoke 2>&1 | Select-Object -Last 5` shows just the summary.

**Working with Claude.** In a claude.ai chat, Claude can't touch your disk:
changes arrive as zip bundles you unpack and copy in, and the worst bugs here
came from assembling those bundles from stale copies. **Claude Code**, run in
the repo on your machine, edits the files directly and reads `CLAUDE.md`
automatically. Prefer it for the next stretch.

---

## 3. How it runs without you

| Workflow | When | What |
|---|---|---|
| `fetch.yml` | hourly at :41 | ingest TEON and USGS; reset `data/processed/` to the remote's copy; transform; ingest SNOTEL (incl. the Snow Lab), HMS smoke, TERC Secchi (only on a new revision), the Snow Lab climatology (13 UTC) and airport weather (once a day); prune; commit |
| `pages.yml` | hourly at :25 | ingest, transform, deploy the page; commits nothing |
| `watch.yml` | every 6 hours | diff TEON's inventory against a baseline, scan for impossible readings, open GitHub issues |

- **SNOTEL, smoke and the Beyond-TEON steps run after the reset**, because
  they write straight into `data/processed/` (or `data/reference/`) with no
  raw buffer; before it, the reset would wipe them. Each failure is
  non-fatal and shows as a warning on the run. Step "conclusions" on those
  steps always read success (continue-on-error); the warnings are the
  signal. One SNOTEL step failed transiently on 2026-10-03 02:22 UTC and
  ran clean twice minutes later.
- **GitHub runs scheduled jobs late and drops some**, even off the top of the
  hour: snapshots land every few hours. No data is lost (each run reaches back
  days), but the page can be hours behind. `status` shows the snapshot's age.
- **`pixi run watch` locally is read-only**; only CI advances the baseline.

---

## 4. The data

```
data/processed/
    observations/source=teon/year=YYYY/month=MM/part.parquet     TEON
    usgs_observations/source=usgs/...                            USGS gauges
    assets/source=teon/...                                       camera frames
    snotel_observations/source=snotel/...                        PRCP, PREC, TAVG, WTEQ
    smoke_observations/source=hms/...                            hms_analysed, smoke_density, hms_polygons
    asos_observations/source=asos/...                            TMAX, TMIN, PRCP, SNOW, SNWD (airports)
data/raw/          7-day rolling buffer of raw API snapshots (committed, pruned)
data/reference/    watershed polygons, watch_baseline.json, snotel_stations.json,
                   terc_secchi.csv + terc_package.json, cssl_snow_climatology.csv
web/assets/        built by `transform`, not committed
```

- **Months before October 2026 are one file; from October 2026, one file per
  day** (`part.d01.parquet` …). Rows are written in a fixed order and unchanged
  files are never rewritten, so a quiet run commits nothing and a normal one
  adds a few hundred KB (it was ~5.5 MB). Every reader globs `part*.parquet`.
- **Query tables**: `obs`, `usgs`, `snotel`, `smoke`, `asos`, `assets`,
  `stations`, `catchments`, plus `terc` and `cssl_climo` (reference CSVs). `obs` columns: `uuid, source, site, sensor_type, timestamp,
  lat, lng, variable, value`, plus `year` and `month`.
- **Timestamps are naive Pacific local.** `Soil_VWC` is a fraction (0.034 = 3.4 %).
- **Four naming conventions**: EXO `Temp`/`Do_mgL`/`Chl_a`, MiniDOT
  `Temperature`/`Dissolved Oxygen`, HOBO `temperature`, Campbell loggers
  `Air_Temp`/`Soil_VWC`/`BattV_Avg`.
- **Forest-station endpoints share record IDs but return different columns.**
  Never fetch one and treat it as standing in for the rest.
- **TEON's hidden-site list is honoured**, and ingest fails closed if it can't
  be read. It's empty now.
- **Reading parquet directly** (not through `pixi run query`): DuckDB reads the
  `month` folder as text, so `CAST(month AS INTEGER)` before comparing.

---

## 5. Commands

| Task | What |
|---|---|
| `status` | **start here**: live, dark, failing or waiting, and a to-do list |
| `pipeline` / `transform` | ingest and rebuild / rebuild only |
| `query` | DuckDB SQL over everything; `.examples`, `.save file.csv` |
| `backfill --stage manual\|nearshore\|live\|blackwood [--site "Name"]` | full history; skips complete sensors |
| `station-health` | logger batteries: charging, not charging, power failure, swaps |
| `transect` / `transect-rain` | west vs east soil; the same against SNOTEL precipitation and snowpack |
| `snotel [--since YYYY-MM-DD]` | daily SNOTEL data; `--force` re-looks-up the stations by name |
| `smoke [--since …]` / `smoke-lake` | NOAA HMS smoke over the lake; smoky vs clear days |
| `terc [--force]` / `terc-discover` | TERC's Secchi record via DataONE; what the package holds |
| `cssl` | the Snow Lab's snowfall per water year since 1879 |
| `asos [--since …]` | daily weather at the South Lake Tahoe and Truckee airports |
| `trees` | the dendrometers: daily stem shrinkage per station, channel checks |
| `oxygen-check` | which atmosphere each instrument references |
| `glenbrook` | why Glenbrook 2 is wet (probably a saturated canyon floor) |
| `record-shape` | field names per sensor type; run before any new backfill |
| `drop-undated` | remove undated rows; refuses if any has no dated copy |
| `store-status`, `watch`, `setup-git` | store sizes; upstream changes (read-only); merge driver |

---

## 6. What we know, and how sure

**Solid**

- **Oxygen saturation**: all five EXO sondes reference sea level (each fits to
  0.004 mg/L); the MiniDOTs reference lake pressure. At 1,898 m, EXO reads
  ~78–85 % where the water is ~99–107 %.
- **Two channel scrambles**: Sunnyside 2026-04-30 to 06-25 (every channel) and
  4H Camp 2026-07-17 to 07-24 (optical channels). Probably recoverable upstream.
- **Glenbrook 1 isn't charging, since July 2025; Glenbrook 5 isn't, since June
  2026.** Test: no daily peak above 13 V in 14 days (charging stations peak
  14.0–14.4 V). Both live on battery swaps; a power failure's gap never
  backfills. Glenbrook 5's June 2026 outage was one. Glenbrook 4's charging was
  repaired in September 2025 and is the model.
- **Outages on working batteries come back**: Blackwood 2 uploaded every
  reading of a 99-day outage. Power failures don't.
- **pH works only on the hand-collected sondes**; the soil pH layer averages
  empty cells as zero in 38 of 60 catchments (recoverable).
- **TEON's Blackwood 2 rain gauge** caught 57 % of Ward Creek #3's precipitation
  on rain days and 24 % on snow days; it's missing 136 days.

**Answered as a range**

- **The transect** (Homewood west, Glenbrook 5 east): soil wetting **2.32×**;
  precipitation **2.16×** with Ward Creek #3 (7 km from Homewood, closest to
  its catchment's long-term 1,463 mm) but **1.20×** with Rubicon #2 (10 km).
  West-shore precipitation varies sharply over short distances. In 10 real
  storms, the wetter shore by rain was the wetter by soil 7 times.
- **Snowmelt**: 5 of 8 no-rain wetting events coincide with a measured
  snowpack loss (up to 53 mm in days); 3 unexplained, likely melt at the soil
  stations' lower elevation, which the gauges can't see.

**A careful null**

- **Wildfire smoke**: 114 smoke days over the lake since June 2024, mostly
  light. The lake sondes have no summer-2024 data. In 2025–26, with each
  month's trend removed, 3 of 60 tests below p = 0.05 (chance level); no
  cooling of forest daytime highs. A raw within-month comparison showed a
  striking Glenbrook algae "effect" (p = 0.001) that was a late-summer trend.
  Worth watching: oxygen ~0.06 mg/L lower a week after smoke at two sondes.

**Open**

- **Glenbrook 2 is wet** (46 % vs 11–16 % nearby): probably a saturated
  riparian site at the floor of the Glenbrook Creek canyon (section 7);
  needs a site photo or TEON's notes to close.

---

## 7. Where we left off

**Out in the world, as of 2026-10-02**

- **TEON**: email sent 2026-10-02 to Carina Seitz (TEON's contact address) and
  Sudeep Chandra (TEON lead), from Brooks's personal Gmail. It leads with the
  two batteries and links `docs/teon-note-2026-09-28.md`, which has since
  gained an addendum on the Blackwood 2 gauge. **If no reply by ~2026-10-09**,
  a short follow-up to both; Katie Senft (research vessel) for the field side.
- **Then**, Kylie Papson (Tahoe Institute communications) and the UNR alumni
  association; then social posts (drafts in the old chat: celebrate the open
  data, don't list faults).
- **Batteries**: no *repair* seen yet. **Correction (2026-10-02 evening):**
  this note originally said no swap had been seen either, which was wrong.
  **Glenbrook 5's battery was swapped on 2026-09-09** (overnight low 10.90 V,
  next reading 12.52 V); it hasn't charged since, its daily peak sliding
  ~0.02 V/day to ~12.07 V by 10-01, which is why `station-health` shows a
  rising 30-day trend. `status` tests charging, not replacement, so it
  didn't say so; a swap looks like an overnight jump of half a volt or more
  in the daily voltages. Glenbrook 1: no swap, ~0.01 V/day down, 11.35 V on
  10-02. Glenbrook 5's overnight lows have wandered since 09-29 (11.55 to
  11.84 V) while its peaks keep sliding: likely the first cold nights.
  **`status` and `station-health` now report swaps** ("replaced") and
  power restorations; a swap at any station shows for 14 days. Glenbrook 5
  has lived on swaps: nine restorations since December 2024.
- **Stations**: Blackwood 2 quiet since 2026-09-25 on a healthy battery.
  Blackwood 3 and Meeks (hand-collected EXO) last read 2026-07-09, due a
  visit. Lakeside's HOBO silent since 2025-08-08. A boat crew serviced all six
  nearshore sites on 2026-09-28.

**Done the evening of 2026-10-02**

- **Battery swaps reported** (above).
- **The trees** (`pixi run trees`, `docs/trees.md`, page section "The trees,
  working for water"): stems peak ~7 am and bottom out mid-afternoon at
  every station; daily shrinkage ~0 in winter, 30-40 µm in July-August,
  more on hot days and (Blackwood 2) dry-soil days. 33 of 36 channels pass
  the checks. Growth deliberately not shown.
- **Glenbrook 2: probably a saturated canyon floor** (`docs/glenbrook-result.md`,
  `data/reference/station_ground.json`).
- **TERC's annual average**: mean of monthly means at the index station
  reproduces 2022 and 2024; the page uses it.
- **TEON vs Secchi**: 28 same-day pairs, Mar 2025-Jun 2026. Nearshore
  turbidity and chlorophyll don't track offshore clarity (|r| mostly <0.3;
  Glenbrook turbidity -0.46 is marginal; temperature -0.40 is the season).
  Different water, too little overlap. Not on the page.
- **Hourly trigger written, not switched on**: `ops/hourly-trigger/`.

**Next, roughly in order**

1. **Answer TEON** when they reply; adjust anything they ask about the page.
   Follow up ~2026-10-09 if not.
2. **Switch on the hourly trigger** (Brooks: a fine-grained token, then three
   commands in `ops/hourly-trigger/README.md`). `status` shows snapshots per
   24 h; GitHub alone gives ~4.
3. **Confirm Glenbrook 2** with a site photo or TEON's site notes (worth
   asking in the TEON thread), and ask what species and trunk size each
   dendrometer band is on: that's what the east/west tree comparison needs.
4. **Smoke, as the seasons accumulate**: rerun `smoke-lake` after each summer;
   the oxygen lead needs more episodes.
5. **Housekeeping**: `pixi lock` once locally (silences a CI warning); close
   old watcher issues; delete the `probe/extras` branch on GitHub (this
   workspace can't); PurpleAir needs an API key if wanted.
6. **TEON vs Secchi** again after summer 2027, when the overlap has two
   summers.

---

## 8. How to work on this without breaking it

Most bugs here were self-inflicted and **reported success while doing nothing**.
The README's mistakes table lists 45; these rules came out of them.

- **Run `pixi run -e dev test` before every commit.**
- **Never rebuild a file from an older copy of it.** Edit the current file.
  When replacing text, confirm the target exists exactly once first.
- **Check the thing itself, not a proxy**: count rows, query the store,
  `git check-attr` rather than reading `.gitattributes`.
- **A check that couldn't run must say so**, never report as passing.
- **A missing file isn't a zero, and an error page isn't an empty file.**
- **Quarantine the two scrambled windows** before analysing any lake variable.
- **Within-month comparisons need the month's trend removed** (the smoke trap).
  **One gauge isn't a shore**: compare across gauges and report a range.
- **Detect soil events on daily means**; soil moisture has a daily cycle.
- **Store**: day files from October 2026, fixed row order, never rewrite
  unchanged files; backfills append then compact; writes are atomic.
- **Anything writing straight into `data/processed/` runs after CI's reset.**
- **The page**: each section renders in its own `try`; calendar days go through
  `fmtDay`, never `new Date("YYYY-MM-DD")`; catchment shading uses one ramp
  (pale low, deep high) over the 5th–95th percentile; non-TEON data lives under
  "Beyond TEON", and its map markers go in the `context` pane (beneath
  TEON's) and stay out of the initial `fitBounds`. Test with jsdom, the
  real Leaflet inlined, in `TZ=America/Los_Angeles`; jsdom needs
  `SVGSVGElement.prototype.createSVGRect` stubbed or Leaflet finds no
  renderer for the transect line, and inline Leaflet with a *function*
  replacement (its code contains `$` sequences).
- **Honour TEON's visibility flag, and fail closed.**

---

## 9. Where things are written down

| Doc | About |
|---|---|
| `docs/teon-note-2026-09-28.md` | what was sent to TEON, with the 2026-10-02 addendum |
| `docs/dissolved-oxygen.md` | the altitude correction; EXO vs MiniDOT |
| `docs/station-outages.md` | outages and batteries; what backfills and what doesn't |
| `docs/snotel.md` | SNOTEL, the rainfall transect and snowmelt |
| `docs/smoke.md` | the HMS smoke record and the smoke-vs-lake null |
| `docs/beyond-teon.md` | TERC Secchi, the Snow Lab, the airports: routes, what failed, caveats |
| `docs/trees.md` | the dendrometers: the daily cycle, shrinkage, why growth isn't shown |
| `ops/hourly-trigger/README.md` | switching on the Cloudflare cron that runs fetch and pages on time |
| `docs/transect-result.md`, `transect-method.md` | the soil transect and its corrections |
| `docs/glenbrook-result.md` | Glenbrook 2: three failed methods, then the ground (probably explained) |
| `docs/storage.md`, `parquet-conflicts.md` | the store layout, growth fixes, merge driver |
| `docs/querying.md` | the DuckDB shell and its pitfalls |
| `docs/silent-failures.md` | the general pattern behind most bugs |

---

## 10. Starting a new Claude chat

Paste this as the first message, adjusting the last line:

> I'm continuing work on **secchi**, a Lake Tahoe dashboard and data archive
> built on UNR's TEON sensor network: repo `git@github.com:bdgroves/secchi.git`,
> live at https://brooksgroves.com/secchi/, local `C:\data\01_Projects\secchi`
> on Windows with pixi. Please read `HANDOFF.md` and `CLAUDE.md` in the repo
> first (clone it, or I'll paste them). Key rules: run the tests before every
> commit, never rebuild a file from an older copy, and check results against
> the real data rather than trusting a log line. Today I'd like to …
