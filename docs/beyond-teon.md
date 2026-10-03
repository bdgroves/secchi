# Beyond TEON: the Secchi record, the Snow Lab, the airports

*Added 2026-10-02.* Three context sources beside SNOTEL and the smoke
record. None of them is TEON and none feeds a TEON number. On the page
each is a collapsed panel under **Beyond TEON**; on the map each sits in
a pane beneath TEON's pins (`pane: "context"`, z-index 590 against the
marker pane's 600) and is left out of the initial framing, so the map
still opens on the network.

| Source | Command | Where it lands | Refreshes |
|---|---|---|---|
| TERC Secchi depth, edi.1340 | `terc` | `data/reference/terc_secchi.csv`, `terc_package.json` | only when TERC publishes a new revision |
| Snow Lab snowfall, 1879 onward | `cssl` | `data/reference/cssl_snow_climatology.csv` | CI asks once a day (13 UTC) |
| Snow Lab daily (SNOTEL 428, "Css Lab") | `snotel` | `snotel_observations`, side `snowlab` | hourly with the other SNOTEL stations |
| Airports, South Lake Tahoe and Truckee | `asos` | `asos_observations` | once a day, complete Pacific days only |

Query tables: `terc`, `cssl_climo`, `asos` (and `snotel` for the lab's
daily values, `site = 'Css Lab'`).

None of these hosts can be reached from a Claude workspace, so every
route below was established by a throwaway workflow on a branch
(`probe/extras`), which ran on a GitHub runner and committed what came
back. The test fixtures in `tests/fixtures/` are trimmed copies of those
real responses.

## TERC's Secchi record

**What it is.** UC Davis TERC's Secchi disk readings: the index station
(LTP, 165 m of water off the west shore) since 1967-07-28 and the
mid-lake station (MLTP, 450 m) since 1980-04-29. Columns: local time,
`Secchi` (m, the mean of the disappear and reappear depths),
`Secchi_Disappear`, `Secchi_Reappear`, `Viewing_Condition` (the
observer's 0-7 score), `Lake_Condition` (free text, kept). Licence CC BY
4.0; creator in the metadata Shohei Watanabe, UC Davis TERC. Revision 17
(published 2026-08-17) runs to 2026-06-29: 1,647 index-station and 578
mid-lake readings.

**How it's reached.** EDI's own API (PASTA) answers **403 to datacenter
addresses**, GitHub's runners included, whatever the User-Agent, and its
portal is behind a Cloudflare challenge. EDI is also a DataONE member
node, so DataONE's search index lists every file of every revision, and
`cn.dataone.org/cn/v2/resolve/<pid>` redirects to EDI's DataONE endpoint
(`gmn.edirepository.org`), which serves the bytes. `sources/terc.py`
asks the index for the newest revision (two small queries a run),
downloads only if it's newer than the one held, and rewrites the CSV only
if its content changed. The index can list a revision's metadata before
its data files, so identifiers for newer revisions are built from each
file's entity hash (stable across revisions) and tried first.

**Yearly means: a caveat, stated on the page.** A plain average of the
index station's readings doesn't reproduce TERC's published annual
averages (config `TERC_ANNUAL_MEANS_FT`):

| Year | TERC published | plain mean, LTP | monthly means, LTP | both stations, monthly |
|---|---|---|---|---|
| 2023 | 68.2 ft | 69.6 | 67.4 | 68.0 |
| 2024 | 62.3 ft | 61.0 | 62.1 | 61.7 |
| 2025 | 69.2 ft | 68.1 | 71.3 | 70.2 |

(revision 15 of the data). No obvious method matches all three within a
foot.

**Update, later the same evening.** Two more anchors from TERC's own
reports: 2022 is 71.7 ft (21.9 m; State of the Lake 2023, clarity chapter)
and 2024 is 19.0 m from **27** accepted readings (2024 Clarity Report). The
reports don't state the formula. Against revision 17, the **mean of
monthly means at the index station** gives 2022 21.90 m (exact) and 2024
18.94 m (-0.06 m); a plain mean misses every year by 0.1-0.4 m, and
filtering on viewing condition doesn't help. The two years it misses line
up with gaps in the published record: it has 25 readings for 2024 where
TERC counted 27, and none for May 2025. So the page now averages months
first (`sources.terc.yearly_means_m`), says so, and still says to quote
TERC's figures.

**TEON against Secchi.** 28 days with an index-station reading and a TEON
nearshore sonde reading, March 2025 to June 2026. Correlations of Secchi
depth with same-day turbidity and chlorophyll are mostly within ±0.3, at a
sample size where ±0.4 is the bar for anything; Glenbrook's turbidity
(-0.46, n = 25) has the expected sign but only just clears it, and
temperature (-0.40) is the season. Nearshore sondes and the deep-water
Secchi stations see different water. Worth repeating with two summers of
overlap.

## The Central Sierra Snow Laboratory

UC Berkeley's lab at Donner Summit, 2,100 m, about 30 km northwest of
Homewood: outside the basin, on the wet side of the crest. Context for the
season; **never a transect shore** (`SNOTEL_STATIONS["Css Lab"] =
"snowlab"`, and the rain panel lists `west`/`east` only). If NRCS ever
renames the station, the lookup warns and skips it rather than stopping
the shores.

- **Daily values** come from NRCS SNOTEL site 428, listed as "Css Lab".
  Adding it also added `SNWD` (snow depth, stored in cm) for every SNOTEL
  station; `snotel.ingest` now backfills from 2024-06-01 any station or
  element it holds nothing for, so the history arrived with the first run
  (853 days each, no gaps).
- **The snowfall record since 1879.** The lab's data page is a Next.js
  app; its "Download Data" button reads table `CSSL_SnowClimo` from a
  Supabase project using the public browser key in the page's script.
  `sources/cssl.py` takes that key from the page at run time (it will
  rotate), refuses any key whose role isn't `anon`, and reads the one
  147-row table. "NA" (water year 2020) stays missing.
- **Not read:** the lab's minute-level instrument tables (`S2S1_Met_data`,
  `3000mm_Geo_Hz&Prcp`, `S2S2_Table1`). They are raw logger output only
  months old — one temperature probe reads -80 °C — and SNOTEL 428
  carries the quality-controlled daily equivalents. The daily 1971-2025
  record on Dryad (doi:10.6078/D1941T, CC0) isn't fetched either: Dryad's
  API now answers 401 to anonymous file downloads and its web route sits
  behind a bot check.

Worth knowing: if the lab would rather the climatology be read some other
way, cssl@berkeley.edu is the address on its site.

## The airports

The National Weather Service's daily climate record at South Lake Tahoe
(TVL, 1,907 m, in the basin, about 5 km from the south shore, since 1968)
and Truckee (TRK, 1,798 m, Martis Valley, just outside the basin, since
2000; also reports snowfall and snow depth), read from NOAA's Applied
Climate Information System (`data.rcc-acis.org/StnData`). Values arrive
as strings: a number, `M` (missing, stored as nothing), `T` (trace,
stored as a small positive amount, never zero) or `S` (accumulated into a
later day, skipped).

**The first route failed, visibly only after a fix.** The first version
read the Iowa Environmental Mesonet. It answered small requests but
refused the two-year backfill with "server over capacity" on every try,
all evening. The step exited 0 with a log warning, so the run page showed
nothing: exactly the quiet failure `docs/silent-failures.md` describes. It
now posts a `::warning` annotation on the run when the service is busy;
then the source moved to ACIS, which returned the same two years per
station in under a second.

**Pacific days.** CI runs in UTC; after 4 or 5 pm Pacific, UTC's
"yesterday" is the lake's today, still in progress. `asos.ingest` works
in America/Los_Angeles and fetches only days before today.

## Not added

**PurpleAir** ground smoke sensors would check NOAA's satellite smoke
polygons against what was breathed, but its API needs an account and a
key (develop.purpleair.com), so it waits until that's wanted.
