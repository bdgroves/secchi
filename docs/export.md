# Downloadable data: `pixi run export`

People who want the data usually want one site and one date range as a CSV.
A few want all of it. This builds both, as one flat directory that maps
one-to-one onto GitHub Release assets.

```powershell
pixi run export                       # preview bundle in exports/ (gitignored)
pixi run export --only lake_exo       # one dataset, to look at
pixi run export --verify exports\secchi-data-2026-10-07
pixi run export --release --teon-permission "who at TEON, when, how"
```

`--offline-visibility` reads the watcher's baseline instead of asking TEON,
and only while the baseline is under 48 hours old.

## What comes out

Per dataset, `<name>.parquet` (everything) and `<name>_<year>.csv.gz`.
Datasets: `lake_exo`, `lake_nearshore`, `forest` (TEON), then `usgs`,
`snotel`, `smoke`, `asos`. Plus `terc_secchi.csv`, `README.md`,
`SOURCES.md`, `CITATION.cff`, `manifest.json` and `SHA256SUMS`.

The three TEON datasets partition the whole store: `forest` is "everything
that isn't a lake sensor", so a sensor type nobody planned for lands there
and does not vanish. The export checks that they add up to the visible
store and stops if they don't.

First full run (2026-10-07): 12,092,376 TEON rows, about 555 MB, ~4 minutes,
one process, 4 GB of memory. `forest.parquet` is the largest file at 167 MB.

## Rules

- **Raw values are never changed.** Suspect readings get a `quality_flag`.
  Nothing is corrected, remapped or dropped.
- **TEON's visibility flag is honoured and fails closed.** If the list
  can't be read the export refuses. After writing, it searches the output
  files for any hidden site.
- **A release needs a recorded permission.** TEON's 2026-10-06 reply
  covers sharing the project. Publishing its data as files is a separate
  question; `--release` refuses without `--teon-permission`. Without it the
  bundle is a *preview* and says so in the manifest, the README and
  SOURCES.md.
- **Every bundle carries a plain-English disclaimer**: provisional, not an
  official product, flags are not quality control, no warranty. It is in the
  README, SOURCES.md, a standalone `DISCLAIMER.md` and the manifest, and is
  defined once in `export.DISCLAIMER`. CSV files cannot carry it without
  breaking parsers, so it rides in the release notes and beside them. It is
  careful wording, not legal advice; have the Tahoe Institute or UNR look
  at it if the project grows.
- **A source whose terms aren't confirmed is left out and says so.** Today
  that is the Snow Lab's snowfall record. TERC's record is CC BY 4.0 and is
  included.
- **Every count is checked on the written file**, not on the query: Parquet
  rows, CSV rows, per-year totals, and the three TEON datasets against the
  store. All writes go to a temp name first.

## The quality flags

Inferred here from the data. They are not TEON's quality control, and
the README says so. Defined once, in `export.QUALITY_FLAGS`, which the
README is generated from; a test fails if the SQL can write a flag that
isn't defined.

| flag | rows (2026-10-07) |
|---|---:|
| `exo_sat_sea_level` | 170,694 |
| `negative_turbidity` | 122,377 |
| `channel_scramble` | 54,150 |
| `ph_zero` | 46,698 |
| `impossible_range` | 11,481 |
| `duplicate_reading` | 579,460 |
| `conflicting_duplicate` | 380 |

`channel_scramble` is per reading, and its trigger is the signature of the
swap itself: oxygen *saturation* (~85) in the concentration field
(`Do_mgL > 20`), or saturation in the water-temperature field
(`Temp > 35`). It reproduces the documented windows exactly: Sunnyside
5,145 timestamps, 2026-04-30 11:30 to 06-25 08:00; 4H Camp 565.

**A first version used "saturation below 50 %" as a second trigger, and
was wrong.** It marked 4H Camp's readings from October 2025, a service
visit at Blackwood 3, and isolated zeros at Glenbrook as scrambles.
`test_low_oxygen_at_a_service_visit_is_impossible_but_not_a_scramble` holds
that line. It was found by reading the flagged rows by site and date
before trusting the counts: the totals looked reasonable.

## What building this found

Three things about the store and the published claims. None was known
before.

1. **About 290,000 forest readings are stored twice.** Same site, variable
   and timestamp, different record ids, and nearly always the same value.
   The store deduplicates on record ids, so it cannot see them. They
   cluster in **November 2025 to February 2026** (with a smaller burst in
   April 2026) and are almost all at the forest stations; Glenbrook 4 has
   162,516 duplicated keys alone. That is 2.4 % of the store, and it would
   double-count in anyone's monthly mean. The export flags them and
   deletes nothing; whether to delete is a decision about the committed
   store. The cause is not known. Either TEON re-issued records under new
   ids in that period, or something here wrote them; `git log` on those
   months and a look at the raw buffers would say which.
2. **Negative turbidity is every EXO sonde, not two.** The README and the
   TEON note say Sunnyside and 4H Camp. In the store, all five read below
   zero for most of their record: Blackwood 3 98.9 % of readings (median
   -1.38 FNU), 4H Camp 76.5 %, Glenbrook 74.4 %, Sunnyside 62.6 %, Meeks
   43.4 %. What is distinctive at Sunnyside and 4H Camp is that the offset
   steps at service visits. The README line "a second sonde reading below
   zero after Sunnyside's -2.1" understates it.
3. **4H Camp's scramble is a little different from the write-up.** The
   readings run 2026-07-17 14:15 to 07-24 13:15, about seven days, not six,
   and 07-22 14:00 to 07-23 14:00 in the middle is normal (median
   Do_mgL 7.7). There is also one stray reading on 2026-06-23 15:30
   (86.4 mg/L). The week is not one block, which is why the flag is per
   reading and not a window.

## What this does not do yet

- **The panel and the custom download are on the `downloads-panel`
  branch** until merged. See "The custom download" below.
- **GitHub release assets can't be read by the page's own code.** Checked
  2026-10-07: neither github.com's 302 nor release-assets.githubusercontent.com
  sends `Access-Control-Allow-Origin`. Plain links to them work; a script
  on brooksgroves.com fetching them is refused. That is why the custom
  download's files live on the site.
- **No Zenodo deposit**, so no DOI. Worth doing once the first release is
  real, so people can cite a fixed version.
- **No corrected oxygen column.** `exo_sat_sea_level` marks the problem; a
  derived lake-pressure saturation column would be a second set of
  numbers to defend, and is left out until TEON has responded.
- **No schedule.** `.github/workflows/export.yml` is manual only. Add a
  `schedule:` after the permission is recorded.

## The custom download

What most people want: one dataset, some stations, some variables, a
period (last 7 days, 30 days, 3 or 12 months, everything, or custom dates),
as a CSV. The page's "Custom download" form does it in the browser, with no
server.

- **`pixi run webdata`** (`src/secchi/webdata.py`) writes `web/data/`
  (gitignored): one gzipped CSV per dataset, site and month, the export's
  rows and columns including `quality_flag`, plus `catalog.json` (every
  file's path, rows and size; every site's variables with labels and
  stored units). The pages deploy runs it after `transform`. 2026-10-07:
  577 files, 340 MB, about 90 s. GitHub Pages allows 1 GB per site and
  100 MB per file; the largest file is 12 MB.
- **It reuses the export's code**: datasets, quality flags, disclaimer and
  the hidden-site rule. If TEON's hidden-site list can't be read, the TEON
  datasets are left out of the catalog, not served unfiltered. Every file
  is read back: rows per file must match, and no hidden site may appear.
- **The page** fetches only the months the period touches, keeps the
  matching lines exactly as written, and hands over a .zip: the CSV plus
  README.txt with the disclaimer, what was selected, the variables and
  units, the flag definitions and the source to cite. The download button
  stays disabled until the visitor ticks "I understand these are
  provisional data...". "Last" periods count back from the newest reading,
  not from now. Over 150 MB to fetch, it points to the whole-dataset files.
- **Tests**: `tests/test_custom_download.py` builds the files from the
  export tests' store, runs the page's own JavaScript in Node against
  them, opens its zip and compares the rows with the store. Checked by
  breaking the date filter and the hidden-site filter and watching tests
  fail. Also driven by hand in Chromium against the real 2026-10-07 data:
  Sunnyside and Glenbrook chlorophyll, last 7 days, 1,337 rows, equal to a
  query on the store; USGS lake level, Oct 1-3, 66 rows, equal.
- **Permission**: Brooks recorded on 2026-10-07 that TEON has given
  permission to work with and share its data with provisional-data
  language (`webdata.REDISTRIBUTION`). The name and date of the TEON
  contact belong there and in the export's `--teon-permission`.

## Testing

`tests/test_export.py` builds a small store with one of every case:
scrambled, optical-only, normal-in-the-same-week, service-visit low oxygen,
duplicates, a hidden site and a sensor type nobody planned for. Two checks
earn their place: the hidden-site search on the *written* files, and
`test_raw_values_are_exactly_as_stored`, which compares every output value
with the input. Both were verified by breaking the code (restoring the old
trigger, then removing the visibility filter) and watching tests fail.
