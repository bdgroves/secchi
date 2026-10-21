# secchi inventory

*Generated 2026-10-21 by `pixi run cloud-inventory`. Regenerate rather than edit.*

Step one of moving secchi into SQL Server and Azure SQL Database: everything
that exists, how big it is, what feeds it, what reads it, and whether it
moves. The month-by-month fingerprints written alongside
(`cloud/out/fingerprints.json`) are what the database copy is checked
against. See [docs/cloud.md](../cloud.md).

## Tables that move

| SQL table | Rows | Months | Time span | Store files | On disk | Fed by | Read by |
|---|---:|---:|---|---:|---:|---|---|
| `secchi.obs` | 11,944,385 | 29 | 2024-06-13 to 2026-10-20 | 48 | 139.9 MB | fetch.yml hourly (ingest-all), backfill | transform (the page), status, daily, every analysis, query, export |
| `secchi.usgs` | 123,549 | 2 | 2026-09-16 to 2026-10-21 | 22 | 5.3 MB | fetch.yml hourly (usgs) | transform (the page), glenbrook, status, query, export |
| `secchi.snotel` | 17,393 | 29 | 2024-06-01 to 2026-10-19 | 47 | 0.5 MB | fetch.yml hourly (snotel), after the store reset | transect-rain, Beyond TEON panel, query, export |
| `secchi.asos` | 6,848 | 29 | 2024-06-01 to 2026-10-19 | 47 | 0.4 MB | fetch.yml once a day (asos) | Beyond TEON panel, query, export |
| `secchi.smoke` | 2,616 | 29 | 2024-06-01 to 2026-10-20 | 48 | 0.3 MB | fetch.yml hourly (smoke) | smoke-lake, the page's smoke strip, query, export |
| `secchi.assets` | 2,724 | 10 | 2026-01-24 to 2026-10-20 | 29 | 0.3 MB | fetch.yml hourly (ingest-all) | the page's camera panel |
| `secchi.terc` | 2,225 | n/a | 1967-07-28 to 2026-06-29 | 1 file | reference | fetch.yml, only when TERC publishes a new revision (terc) | the clarity panel, query |
| `secchi.cssl_climo` | 147 | n/a | n/a | 1 file | reference | fetch.yml daily at 13 UTC (cssl) | Beyond TEON panel, query |
| `secchi.watershed` | 60 | n/a | n/a | 1 file | reference | reference (fetched once) | catchment-join, the map's catchment layer, query (stations) |

**12,099,947 rows in 9 tables.**

## What each table is

**`secchi.obs`**: Every TEON reading: one row per record per variable. Sites, sensor types and variables are moved into lookup tables.

Columns in the store: `uuid` varchar, `source` varchar, `site` varchar, `sensor_type` varchar, `timestamp` timestamp, `lat` double, `lng` double, `variable` varchar, `value` double.

**`secchi.usgs`**: USGS stream gauges, instantaneous values. Times are stored as datetimeoffset in UTC.

Columns in the store: `uuid` varchar, `source` varchar, `site` varchar, `site_number` varchar, `sensor_type` varchar, `timestamp` timestamp with time zone, `lat` double, `lng` double, `variable` varchar, `statistic_id` varchar, `statistic` varchar, `value` double, `unit_of_measure` varchar, `approval_status` varchar, `qualifier` integer.

**`secchi.snotel`**: NRCS SNOTEL daily precipitation, temperature and snow water (Beyond TEON context).

Columns in the store: `uuid` varchar, `source` varchar, `site` varchar, `sensor_type` varchar, `timestamp` timestamp, `lat` double, `lng` double, `variable` varchar, `value` double, `unit` varchar.

**`secchi.asos`**: NWS airport daily weather, South Lake Tahoe and Truckee (Beyond TEON).

Columns in the store: `uuid` varchar, `source` varchar, `site` varchar, `sensor_type` varchar, `timestamp` timestamp, `lat` double, `lng` double, `variable` varchar, `value` double, `unit` varchar.

**`secchi.smoke`**: NOAA HMS satellite smoke analysis over the lake, daily.

Columns in the store: `source` varchar, `site` varchar, `sensor_type` varchar, `timestamp` timestamp, `lat` double, `lng` double, `uuid` varchar, `variable` varchar, `value` double.

**`secchi.assets`**: Field-camera frame references (s3 paths), not the images.

Columns in the store: `uuid` varchar, `source` varchar, `site` varchar, `sensor_type` varchar, `timestamp` timestamp, `lat` double, `lng` double, `kind` varchar, `ref` varchar, `total_available` bigint.

**`secchi.terc`**: UC Davis TERC Secchi depths since 1967 (reference CSV).

Columns in the store: `station` varchar, `date_time_local` timestamp, `secchi_m` double, `disappear_m` double, `reappear_m` double, `viewing_condition` bigint, `lake_condition` varchar.

**`secchi.cssl_climo`**: UC Berkeley Snow Lab snowfall and peak depth per water year, since 1879.

Columns in the store: `water_year` bigint, `snowfall_cm` double, `max_depth_cm` bigint, `snowfall_hs_cm` double.

**`secchi.watershed`**: The 60 TEON catchment polygons, stored as geometry (SRID 4326).

Columns in the store: `watershed_id` bigint, `name` varchar, `area_km` double, `wkt` varchar.

## Lookup tables

`secchi.obs` stores numbers instead of names: 18 sites, 9 sensor types and 55 variables go into `secchi.site`, `secchi.sensor_type` and `secchi.variable`. `secchi.site` also gets a geography point for each station.

## Reference files

| File | Size | Plan |
|---|---:|---|
| `.gitkeep` | 0.0 KB | not yet decided |
| `cssl_snow_climatology.csv` | 3.2 KB | move (table cssl_climo) |
| `news_log.json` | 27.9 KB | stay: the page's What's new feed |
| `snotel_stations.json` | 0.6 KB | later: station metadata; SNOTEL rows already carry name and location |
| `station_ground.json` | 4.2 KB | later: hand-recorded site notes |
| `tahoe_watersheds.geojson` | 7,678.3 KB | move (table watershed, geometry) |
| `terc_package.json` | 0.2 KB | stay: TERC revision bookkeeping for the fetcher |
| `terc_secchi.csv` | 278.6 KB | move (table terc) |
| `watch_baseline.json` | 9.3 KB | stay: the watcher's state, owned by watch.yml |
| `watershed_attributes.json` | 270.4 KB | later: 168 attributes per catchment, wide; move when a report needs them |
| `watershed_variables.json` | 14.3 KB | later: the dictionary for the attributes above |

Not everything moves. Files that hold the app's own bookkeeping stay
with the app; moving them would only make a second copy to keep in step.

## Months, one line each

| Table | Month | Rows | Distinct keys | First | Last |
|---|---|---:|---:|---|---|
| obs | 2024-06 | 4,850 | 4,850 | 2024-06-13 16:15:00 | 2024-06-26 11:45:00 |
| obs | 2024-07 | 38,511 | 38,511 | 2024-07-23 12:48:00 | 2024-07-31 23:59:00 |
| obs | 2024-08 | 464,579 | 464,579 | 2024-08-01 00:00:00 | 2024-08-31 23:59:00 |
| obs | 2024-09 | 397,729 | 397,729 | 2024-09-01 00:00:00 | 2024-09-30 23:45:00 |
| obs | 2024-10 | 80,559 | 80,559 | 2024-10-01 00:00:00 | 2024-10-31 23:45:00 |
| obs | 2024-11 | 131,540 | 131,540 | 2024-11-01 00:00:00 | 2024-11-30 23:45:00 |
| obs | 2024-12 | 155,146 | 155,146 | 2024-12-01 00:00:00 | 2024-12-31 23:45:00 |
| obs | 2025-01 | 164,002 | 164,002 | 2025-01-01 00:00:00 | 2025-01-31 23:55:00 |
| obs | 2025-02 | 221,652 | 221,652 | 2025-02-01 00:00:00 | 2025-02-28 23:59:00 |
| obs | 2025-03 | 354,258 | 354,258 | 2025-03-01 00:00:00 | 2025-03-31 23:59:00 |
| obs | 2025-04 | 372,294 | 372,294 | 2025-04-01 00:00:00 | 2025-04-30 23:55:00 |
| obs | 2025-05 | 299,103 | 299,103 | 2025-05-01 00:00:00 | 2025-05-31 23:59:00 |
| obs | 2025-06 | 451,985 | 451,985 | 2025-06-01 00:00:00 | 2025-06-30 23:59:00 |
| obs | 2025-07 | 443,971 | 443,971 | 2025-07-01 00:00:00 | 2025-07-31 23:59:00 |
| obs | 2025-08 | 481,519 | 481,519 | 2025-08-01 00:00:00 | 2025-08-31 23:59:00 |
| obs | 2025-09 | 433,703 | 433,703 | 2025-09-01 00:00:00 | 2025-09-30 23:59:00 |
| obs | 2025-10 | 507,645 | 507,645 | 2025-10-01 00:00:00 | 2025-10-31 23:59:00 |
| obs | 2025-11 | 429,134 | 429,134 | 2025-11-01 00:00:00 | 2025-11-30 23:47:00 |
| obs | 2025-12 | 564,895 | 564,895 | 2025-12-01 00:00:00 | 2025-12-31 23:47:00 |
| obs | 2026-01 | 530,464 | 530,464 | 2026-01-01 00:00:00 | 2026-01-31 23:59:00 |
| obs | 2026-02 | 625,716 | 625,716 | 2026-02-01 00:00:00 | 2026-02-28 23:47:00 |
| obs | 2026-03 | 674,282 | 674,282 | 2026-03-01 00:00:00 | 2026-03-31 23:59:00 |
| obs | 2026-04 | 677,410 | 677,410 | 2026-04-01 00:00:00 | 2026-04-30 23:59:00 |
| obs | 2026-05 | 706,721 | 706,721 | 2026-05-01 00:00:00 | 2026-05-31 23:59:00 |
| obs | 2026-06 | 660,095 | 660,095 | 2026-06-01 00:00:00 | 2026-06-30 23:59:00 |
| obs | 2026-07 | 678,363 | 678,363 | 2026-07-01 00:00:00 | 2026-07-31 23:59:00 |
| obs | 2026-08 | 642,489 | 642,489 | 2026-08-01 00:00:00 | 2026-08-31 23:47:00 |
| obs | 2026-09 | 533,163 | 533,163 | 2026-09-01 00:00:00 | 2026-09-30 23:45:00 |
| obs | 2026-10 | 218,607 | 218,607 | 2026-10-01 00:00:00 | 2026-10-20 14:00:00 |
| usgs | 2026-09 | 55,580 | 55,580 | 2026-09-16 00:30:00 | 2026-09-30 23:45:00 |
| usgs | 2026-10 | 67,969 | 67,969 | 2026-10-01 00:00:00 | 2026-10-21 00:00:00 |
| snotel | 2024-06 | 600 | 600 | 2024-06-01 00:00:00 | 2024-06-30 00:00:00 |
| snotel | 2024-07 | 620 | 620 | 2024-07-01 00:00:00 | 2024-07-31 00:00:00 |
| snotel | 2024-08 | 620 | 620 | 2024-08-01 00:00:00 | 2024-08-31 00:00:00 |
| snotel | 2024-09 | 600 | 600 | 2024-09-01 00:00:00 | 2024-09-30 00:00:00 |
| snotel | 2024-10 | 620 | 620 | 2024-10-01 00:00:00 | 2024-10-31 00:00:00 |
| snotel | 2024-11 | 600 | 600 | 2024-11-01 00:00:00 | 2024-11-30 00:00:00 |
| snotel | 2024-12 | 620 | 620 | 2024-12-01 00:00:00 | 2024-12-31 00:00:00 |
| snotel | 2025-01 | 620 | 620 | 2025-01-01 00:00:00 | 2025-01-31 00:00:00 |
| snotel | 2025-02 | 560 | 560 | 2025-02-01 00:00:00 | 2025-02-28 00:00:00 |
| snotel | 2025-03 | 620 | 620 | 2025-03-01 00:00:00 | 2025-03-31 00:00:00 |
| snotel | 2025-04 | 596 | 596 | 2025-04-01 00:00:00 | 2025-04-30 00:00:00 |
| snotel | 2025-05 | 612 | 612 | 2025-05-01 00:00:00 | 2025-05-31 00:00:00 |
| snotel | 2025-06 | 591 | 591 | 2025-06-01 00:00:00 | 2025-06-30 00:00:00 |
| snotel | 2025-07 | 620 | 620 | 2025-07-01 00:00:00 | 2025-07-31 00:00:00 |
| snotel | 2025-08 | 620 | 620 | 2025-08-01 00:00:00 | 2025-08-31 00:00:00 |
| snotel | 2025-09 | 598 | 598 | 2025-09-01 00:00:00 | 2025-09-30 00:00:00 |
| snotel | 2025-10 | 620 | 620 | 2025-10-01 00:00:00 | 2025-10-31 00:00:00 |
| snotel | 2025-11 | 600 | 600 | 2025-11-01 00:00:00 | 2025-11-30 00:00:00 |
| snotel | 2025-12 | 620 | 620 | 2025-12-01 00:00:00 | 2025-12-31 00:00:00 |
| snotel | 2026-01 | 620 | 620 | 2026-01-01 00:00:00 | 2026-01-31 00:00:00 |
| snotel | 2026-02 | 560 | 560 | 2026-02-01 00:00:00 | 2026-02-28 00:00:00 |
| snotel | 2026-03 | 619 | 619 | 2026-03-01 00:00:00 | 2026-03-31 00:00:00 |
| snotel | 2026-04 | 599 | 599 | 2026-04-01 00:00:00 | 2026-04-30 00:00:00 |
| snotel | 2026-05 | 620 | 620 | 2026-05-01 00:00:00 | 2026-05-31 00:00:00 |
| snotel | 2026-06 | 600 | 600 | 2026-06-01 00:00:00 | 2026-06-30 00:00:00 |
| snotel | 2026-07 | 620 | 620 | 2026-07-01 00:00:00 | 2026-07-31 00:00:00 |
| snotel | 2026-08 | 620 | 620 | 2026-08-01 00:00:00 | 2026-08-31 00:00:00 |
| snotel | 2026-09 | 598 | 598 | 2026-09-01 00:00:00 | 2026-09-30 00:00:00 |
| snotel | 2026-10 | 380 | 380 | 2026-10-01 00:00:00 | 2026-10-19 00:00:00 |
| asos | 2024-06 | 238 | 238 | 2024-06-01 00:00:00 | 2024-06-30 00:00:00 |
| asos | 2024-07 | 248 | 248 | 2024-07-01 00:00:00 | 2024-07-31 00:00:00 |
| asos | 2024-08 | 248 | 248 | 2024-08-01 00:00:00 | 2024-08-31 00:00:00 |
| asos | 2024-09 | 235 | 235 | 2024-09-01 00:00:00 | 2024-09-30 00:00:00 |
| asos | 2024-10 | 243 | 243 | 2024-10-01 00:00:00 | 2024-10-31 00:00:00 |
| asos | 2024-11 | 235 | 235 | 2024-11-01 00:00:00 | 2024-11-30 00:00:00 |
| asos | 2024-12 | 243 | 243 | 2024-12-01 00:00:00 | 2024-12-31 00:00:00 |
| asos | 2025-01 | 248 | 248 | 2025-01-01 00:00:00 | 2025-01-31 00:00:00 |
| asos | 2025-02 | 224 | 224 | 2025-02-01 00:00:00 | 2025-02-28 00:00:00 |
| asos | 2025-03 | 248 | 248 | 2025-03-01 00:00:00 | 2025-03-31 00:00:00 |
| asos | 2025-04 | 240 | 240 | 2025-04-01 00:00:00 | 2025-04-30 00:00:00 |
| asos | 2025-05 | 243 | 243 | 2025-05-01 00:00:00 | 2025-05-31 00:00:00 |
| asos | 2025-06 | 235 | 235 | 2025-06-01 00:00:00 | 2025-06-30 00:00:00 |
| asos | 2025-07 | 248 | 248 | 2025-07-01 00:00:00 | 2025-07-31 00:00:00 |
| asos | 2025-08 | 248 | 248 | 2025-08-01 00:00:00 | 2025-08-31 00:00:00 |
| asos | 2025-09 | 234 | 234 | 2025-09-01 00:00:00 | 2025-09-30 00:00:00 |
| asos | 2025-10 | 242 | 242 | 2025-10-01 00:00:00 | 2025-10-31 00:00:00 |
| asos | 2025-11 | 240 | 240 | 2025-11-01 00:00:00 | 2025-11-30 00:00:00 |
| asos | 2025-12 | 237 | 237 | 2025-12-01 00:00:00 | 2025-12-31 00:00:00 |
| asos | 2026-01 | 238 | 238 | 2026-01-01 00:00:00 | 2026-01-31 00:00:00 |
| asos | 2026-02 | 214 | 214 | 2026-02-01 00:00:00 | 2026-02-28 00:00:00 |
| asos | 2026-03 | 243 | 243 | 2026-03-01 00:00:00 | 2026-03-31 00:00:00 |
| asos | 2026-04 | 239 | 239 | 2026-04-01 00:00:00 | 2026-04-30 00:00:00 |
| asos | 2026-05 | 235 | 235 | 2026-05-01 00:00:00 | 2026-05-31 00:00:00 |
| asos | 2026-06 | 235 | 235 | 2026-06-01 00:00:00 | 2026-06-30 00:00:00 |
| asos | 2026-07 | 240 | 240 | 2026-07-01 00:00:00 | 2026-07-31 00:00:00 |
| asos | 2026-08 | 245 | 245 | 2026-08-01 00:00:00 | 2026-08-31 00:00:00 |
| asos | 2026-09 | 230 | 230 | 2026-09-01 00:00:00 | 2026-09-30 00:00:00 |
| asos | 2026-10 | 152 | 152 | 2026-10-01 00:00:00 | 2026-10-19 00:00:00 |
| smoke | 2024-06 | 90 | 90 | 2024-06-01 00:00:00 | 2024-06-30 00:00:00 |
| smoke | 2024-07 | 93 | 93 | 2024-07-01 00:00:00 | 2024-07-31 00:00:00 |
| smoke | 2024-08 | 93 | 93 | 2024-08-01 00:00:00 | 2024-08-31 00:00:00 |
| smoke | 2024-09 | 90 | 90 | 2024-09-01 00:00:00 | 2024-09-30 00:00:00 |
| smoke | 2024-10 | 93 | 93 | 2024-10-01 00:00:00 | 2024-10-31 00:00:00 |
| smoke | 2024-11 | 90 | 90 | 2024-11-01 00:00:00 | 2024-11-30 00:00:00 |
| smoke | 2024-12 | 93 | 93 | 2024-12-01 00:00:00 | 2024-12-31 00:00:00 |
| smoke | 2025-01 | 93 | 93 | 2025-01-01 00:00:00 | 2025-01-31 00:00:00 |
| smoke | 2025-02 | 84 | 84 | 2025-02-01 00:00:00 | 2025-02-28 00:00:00 |
| smoke | 2025-03 | 93 | 93 | 2025-03-01 00:00:00 | 2025-03-31 00:00:00 |
| smoke | 2025-04 | 90 | 90 | 2025-04-01 00:00:00 | 2025-04-30 00:00:00 |
| smoke | 2025-05 | 93 | 93 | 2025-05-01 00:00:00 | 2025-05-31 00:00:00 |
| smoke | 2025-06 | 90 | 90 | 2025-06-01 00:00:00 | 2025-06-30 00:00:00 |
| smoke | 2025-07 | 93 | 93 | 2025-07-01 00:00:00 | 2025-07-31 00:00:00 |
| smoke | 2025-08 | 93 | 93 | 2025-08-01 00:00:00 | 2025-08-31 00:00:00 |
| smoke | 2025-09 | 90 | 90 | 2025-09-01 00:00:00 | 2025-09-30 00:00:00 |
| smoke | 2025-10 | 93 | 93 | 2025-10-01 00:00:00 | 2025-10-31 00:00:00 |
| smoke | 2025-11 | 90 | 90 | 2025-11-01 00:00:00 | 2025-11-30 00:00:00 |
| smoke | 2025-12 | 93 | 93 | 2025-12-01 00:00:00 | 2025-12-31 00:00:00 |
| smoke | 2026-01 | 93 | 93 | 2026-01-01 00:00:00 | 2026-01-31 00:00:00 |
| smoke | 2026-02 | 84 | 84 | 2026-02-01 00:00:00 | 2026-02-28 00:00:00 |
| smoke | 2026-03 | 93 | 93 | 2026-03-01 00:00:00 | 2026-03-31 00:00:00 |
| smoke | 2026-04 | 90 | 90 | 2026-04-01 00:00:00 | 2026-04-30 00:00:00 |
| smoke | 2026-05 | 93 | 93 | 2026-05-01 00:00:00 | 2026-05-31 00:00:00 |
| smoke | 2026-06 | 90 | 90 | 2026-06-01 00:00:00 | 2026-06-30 00:00:00 |
| smoke | 2026-07 | 93 | 93 | 2026-07-01 00:00:00 | 2026-07-31 00:00:00 |
| smoke | 2026-08 | 93 | 93 | 2026-08-01 00:00:00 | 2026-08-31 00:00:00 |
| smoke | 2026-09 | 90 | 90 | 2026-09-01 00:00:00 | 2026-09-30 00:00:00 |
| smoke | 2026-10 | 60 | 60 | 2026-10-01 00:00:00 | 2026-10-20 00:00:00 |
| assets | 2026-01 | 10 | 10 | 2026-01-24 19:30:26 | 2026-01-29 19:30:28 |
| assets | 2026-02 | 77 | 77 | 2026-02-01 22:30:28 | 2026-02-27 19:30:49 |
| assets | 2026-03 | 222 | 222 | 2026-03-02 23:30:37 | 2026-03-31 22:30:35 |
| assets | 2026-04 | 224 | 224 | 2026-04-01 00:30:20 | 2026-04-30 22:31:20 |
| assets | 2026-05 | 275 | 275 | 2026-05-01 00:30:26 | 2026-05-31 22:31:02 |
| assets | 2026-06 | 226 | 226 | 2026-06-01 00:30:32 | 2026-06-30 22:31:09 |
| assets | 2026-07 | 334 | 334 | 2026-07-01 00:30:39 | 2026-07-31 23:49:36 |
| assets | 2026-08 | 577 | 577 | 2026-08-03 19:36:59 | 2026-08-31 22:33:25 |
| assets | 2026-09 | 523 | 523 | 2026-09-01 00:30:45 | 2026-09-30 22:34:05 |
| assets | 2026-10 | 256 | 256 | 2026-10-01 00:31:59 | 2026-10-20 22:31:40 |
| terc | all | 2,225 | 2,225 | 1967-07-28 00:00:00 | 2026-06-29 13:15:00 |
| cssl_climo | all | 147 | 147 |  |  |
| watershed | all | 60 | 60 |  |  |
