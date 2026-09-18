# Data dictionary

## TEON API

Base URL (as of 2026-09-17): `https://rftazkiysf.us-west-2.awsapprunner.com/api`

An AWS App Runner container; assume it may move. If it does, update `TEON_API_BASE` in `src/secchi/config.py`.

### Endpoints

| Path | Purpose | Status |
|---|---|---|
| `/sensors/locations` | Full inventory by category → sensor type, with coordinates, first/last update, row counts. | in use |
| `/sensors/{slug}?site={site}&page={n}&page_size={s}` | Paginated time series for one (sensor type, site) pair. | in use |
| `/site-visibility/disabled` | Sites the frontend hides. Returns `{"disabled": ["4hcamp|lake|EXO"]}`. | in use |
| `/calibration/events?site_name={site}` | Per-site calibration log. Schema is `{events, rows, count}`. **Checked 2026-09-17 for Glenbrook and Sunnyside: both return `count: 0`.** Scaffolding TEON has not populated, so it does not explain the dead pH probes or the Sunnyside turbidity offset. | empty |
| `/aquatic-metadata/` | Aquatic sensor metadata. | redirect-loops for our client; unresolved |
| `/stats/visits`, `/auth/status` | Site analytics and auth state. | not relevant |

### Pagination envelope

```json
{ "data": [...], "count": 50, "total": 46223, "page": 1, "page_size": 50, "total_pages": 925 }
```

Requested `page_size` below ~50 appears clamped up; treat 50 as the effective minimum.

### Sensor type → URL slug

TEON truncates the display label to its **leading concept** and kebab-cases it, rather than transliterating the full name. This is why `air-temperature-relative-humidity` 404s but `air-temperature` works.

| Display name (inventory key) | URL slug | Confirmed |
|---|---|---|
| `EXO` | `exo-sensor` | ✓ 2026-09-17 |
| `Air Temperature & Relative Humidity` | `air-temperature` | ✓ 2026-09-17 |
| `Soil Environmental Conditions` | `soil-moisture` | ✓ 2026-09-17 |
| `Tree stress and growth` | `tree-stress` | ✓ 2026-09-17 |
| `Field Camera` | `field-camera` | ✓ 2026-09-17 |
| `Stream Level` | `stream-level` | ✓ 2026-09-17 |
| `Stream Chemistry` | `stream-chemistry`? | unconfirmed — no live sensor to probe |
| `Precipitation Gauge` | `precipitation`? | unconfirmed — no live sensor to probe |
| `Minidot` | `mini-dot`? | unconfirmed — no live sensor to probe |
| `Hobo` | `hobo`? | unconfirmed — no live sensor to probe |

`pixi run python -m secchi.ingest --mode probe` re-resolves all slugs and prints a report. Worth re-running after TEON ships backend changes, or when a dormant sensor type comes back online.

---

## Shared loggers

**Important structural finding.** At every terrestrial station, the `air-temperature`, `soil-moisture` and `tree-stress` endpoints return *three projections of one Campbell Scientific logger table*, not three independent sensors. At UNR Tahoe Campus:

```
air-temperature  uuid 02035d8b-…  TIMESTAMP 2026-09-17T08:15:00  BattV_Avg 13.44  total 70411
soil-moisture    uuid 02035d8b-…  TIMESTAMP 2026-09-17T08:15:00  BattV_Avg 13.44  total 70411
```

Identical record uuid, identical diagnostics, identical row count. Consequences:

- The inventory's "43 sensors" is closer to **~10 physical logger stations**, each exposing several sensor packages. Identical `data_count` values across soil/air/tree at one site are the tell.
- Deduping on `(uuid, site, variable)` in the transform collapses the shared `BattV_Avg` / `PTemp_C_Avg` channels to one row, which is correct. `site` is in the key as defensive hardening only: the shared-logger case is always within one site, so including site never blocks the collapse, but it makes a uuid collision across two sites harmless instead of silently discarding one site's observations.
- The `sensor_type` recorded against a shared diagnostic channel is whichever snapshot the transform reached first. Cosmetic only — the value is identical either way.

---

## Timestamps

TEON returns naive ISO 8601 with no offset (`"2026-09-17T08:15:00"`). These are **Pacific local** (the loggers' wall clock), set via `TEON_TIMEZONE` in config.

Evidence, since this is inferred rather than documented:

- UNR Tahoe Campus reported `last_update` of `08:15` while observed freshness at ~16:15 PDT was 8 hours. That works only if `08:15` is local; as UTC it would read 15 hours.
- Diurnal shape agrees. Logger panel temperature bottoms out around 05:00–06:00 in these timestamps (pre-dawn local, sunrise ~06:45 mid-September), and air temperature climbs through 08:00–14:00 with falling RH.

`transform.py` localizes every timestamp before writing `latest.json`, so the JSON carries an explicit `-07:00` offset and browsers in any timezone agree on the instant. Without that the browser parses naive strings as the *viewer's* local time.

**If TEON confirms otherwise, change `TEON_TIMEZONE` — everything downstream derives from it.**

---

## Record schemas

Every record carries `uuid`, `site`, `latitude`, `longitude`, `TIMESTAMP`. Fields below are the measurements.

### EXO sonde (lake) — 15-minute cadence

| Field | Units | Notes |
|---|---|---|
| `Temp` | °C | Water temperature. |
| `Chl_a` | µg/L | Chlorophyll-a — the primary clarity driver. Reads slightly negative near the detection floor; clipped to 0 for display. |
| `Do_mgL` | mg/L | Dissolved oxygen. |
| `Do_percent` | % sat | DO saturation. |
| `Turbidity` | FNU | **Sunnyside reads as low as −1.98 raw.** That's too large for noise — likely a calibration offset. Clipped for display; worth checking `/calibration/events`. |
| `phycocyanin` | µg/L | Cyanobacteria proxy. |
| `specific_conductivity` | µS/cm | |
| `Salinity` | ppt | Very low at Tahoe (~0.04). |
| `TDS` | mg/L | Total dissolved solids. |
| `depth` | m | Sonde deployment depth. |
| `pH` | — | **Unusable fleetwide** — returns `0` at Glenbrook, `null` at Sunnyside. Hidden from the dashboard. |
| `TSS` | — | Always null. Lab-only field. |
| `BattV_Min` | V | Solar cycle, 12.4–14.5 V. Diagnostic, hidden. |
| `kor_site_name` | — | Always null. Likely an unpopulated FK to a KorEXO lookup. |

### Air temperature & humidity (terrestrial)

| Field | Units | Notes |
|---|---|---|
| `Air_Temp` | °C | |
| `RH` | % | Relative humidity. |
| `BattV_Avg`, `PTemp_C_Avg` | V, °C | Logger diagnostics, shared across this station's other endpoints. Hidden. |

### Soil environmental conditions (terrestrial)

Two depths instrumented at every station observed so far. Channels `_3` through `_5` exist in the schema but return null everywhere — either unpopulated or reserved for deeper installs.

| Field | Units | Notes |
|---|---|---|
| `Soil_VWC`, `Soil_VWC_2` | % | Volumetric water content. Raw values are fractions (`0.057`); scaled ×100 for display. |
| `Soil_T`, `Soil_T_2` | °C | Soil temperature. |
| `Soil_EC`, `Soil_EC_2` | dS/m | Bulk electrical conductivity. |
| `Soil_*_3` … `Soil_*_5` | — | Null in all observed data. Hidden. |

### Tree stress and growth (terrestrial)

Band dendrometers, eight trees per station, at Glenbrook 2, 4, and 5.

| Field | Units | Notes |
|---|---|---|
| `Tree_1_diameter_change` … `Tree_8_diameter_change` | µm | **Units inferred from magnitude** (~1,000–3,000), not documented. Stem diameter both grows seasonally and swells/shrinks diurnally with tree water content, so this doubles as a drought-stress signal. |

### Stream level

| Field | Units | Notes |
|---|---|---|
| `Uncalibrated_water_depth` | m | Field is literally named "Uncalibrated" upstream. Glenbrook 2 reads ~0.001 m — a dry channel, or a pending datum correction. **Treat as relative, not absolute.** |

### Field camera

| Field | Type | Notes |
|---|---|---|
| `image` | string | An `s3://teon-loggernet-data-storage/…` object reference, e.g. `s3://teon-loggernet-data-storage/Glenbrook 2 - Terrestrial/Snow photos/Glenbrook2Photo524.jpg`. **Not resolvable over HTTP**, so a browser can't render it. Handled as an asset reference (`ASSET_FIELDS`) rather than a numeric variable. |

The bucket path confirms the backend stack: Campbell LoggerNet writing to S3. The "Snow photos" folder suggests these are aimed at snowpack monitoring.

Frame counts are reported two ways, because they differ: `upstream_total` is what TEON says exists (from the pagination envelope's `total`), while `held` is what we have locally, capped by `DEFAULT_INGEST_PAGE_SIZE` per run. As of 2026-09-17 there are 3,378 frames upstream across five stations, oldest dating to November 2025.

---

## Sites

### Lake EXO sondes

| Site | Shore | Lat | Lng | Status |
|---|---|---|---|---|
| Sunnyside | west | 39.134643 | −120.151085 | live |
| Glenbrook | east | 39.088329 | −119.941133 | live |
| 4H Camp | south | 38.974209 | −119.951608 | **hidden by TEON** |
| Blackwood 3 | west | 39.10763 | −120.15777 | manual, offline since 2026-07-09 |
| Meeks | west | 39.03773 | −120.12117 | manual, offline since 2026-07-09 |

### Terrestrial stations

| Site | Shore | Lat | Lng | Status |
|---|---|---|---|---|
| Homewood | west | 39.0752101 | −120.1842931 | live |
| Blackwood 2 | west | 39.111167 | −120.186889 | offline since 2026-06-18 |
| UNR Tahoe Campus | north | 39.243064 | −119.940336 | live |
| Glenbrook 1 | east | 39.0881 | −119.9391 | live |
| Glenbrook 2 | east | 39.08588889 | −119.9220278 | live (+ stream level, camera) |
| Glenbrook 4 | east | 39.09344 | −119.9015 | live |
| Glenbrook 5 | east | 39.07463889 | −119.89125 | live |

### The transect pair

`TRANSECT_PAIR = ("Homewood", "Glenbrook 5")`

Homewood (39.07521 N, west shore) and Glenbrook 5 (39.07464 N, east shore) sit **64 m apart in latitude** — effectively the same line across the map, on opposite sides of the lake. Both are upland hillslope stations.

Latitude offset from Homewood, for every east-shore candidate:

| Station | Δ latitude | North–south offset |
|---|---|---|
| **Glenbrook 5** | 0.00057° | **64 m** |
| Glenbrook 2 | 0.01068° | 1,189 m |
| Glenbrook 1 | 0.01289° | 1,435 m |
| Glenbrook 4 | 0.01823° | 2,029 m |

Two earlier candidates were rejected:

- **Blackwood 2**, the original plan, is ~4 km further north and offline since 2026-06-18.
- **Glenbrook 2** is 1,189 m off Homewood's latitude *and* is a riparian microsite. It reads 42 % volumetric water content while every upland station around it reads 3.5–11.5 %, and it is the one station that also carries a stream gauge. Pairing it with Homewood measured "streambank vs. hillslope", not "wet side vs. rain shadow".

### Transect alignment

`TRANSECT_ALIGN_TOLERANCE_MINUTES = 20`

The transect compares both sites **at the same instant**, found by intersecting the two stations' timestamp sets and taking the most recent shared value. Loggers report on a 15-minute grid so exact matches are normal; the tolerance covers clock drift.

This matters because the first version of the feature compared each site's *latest* reading, and those can be hours apart — Homewood reporting at 10:15 against Glenbrook 5 at 05:45. Air temperature and relative humidity swing enormously over a diurnal cycle, so that version displayed a time-of-day artifact as a geographic signal. Any east-west difference it showed was mostly just the sun.

Only meteorology and soil state are compared (`Air_Temp`, `RH`, `Soil_VWC`, `Soil_T`). Stream depth and dendrometers exist at some stations and not others, so including them would make the two columns structurally different rather than comparable.

---

## Open questions

1. **Field camera image URLs.** Is there an HTTPS form of those `s3://` refs, or a proxy endpoint? A DevTools sniff on a field-camera detail page that renders an actual image would answer it. This unlocks a snowpack/smoke time-lapse.
2. **`/aquatic-metadata/` redirect loop.** Likely holds sonde deployment depths and QC context. Needs a look at what the frontend sends.
3. **Sunnyside turbidity offset.** −1.98 FNU is a calibration problem, not noise. `/calibration/events` is empty, so this needs another route — USGS turbidity at Blackwood (parameter `63680`) would give an independent cross-check from a separate instrument. See [`usgs-plan.md`](usgs-plan.md).
4. **Fleetwide pH failure.** Null or zero everywhere. The calibration log is empty, so there is no documented explanation available through the API.
5. **Dendrometer units.** µm is inferred from magnitude alone.
6. **Stream level datum.** "Uncalibrated" in the field name; is a correction published anywhere?
7. **Dormant sensor slugs.** Minidot, HOBO, Stream Chemistry, Precipitation Gauge are unconfirmed — no live sensors to probe against. Re-run `--mode probe` when they return.
8. **Mixed timestamp conventions.** Loggers appear to be on Pacific wall-clock; a Glenbrook 4 camera frame is timestamped six hours *after* the snapshot containing it, which only resolves if cameras are on UTC. Separate devices with independently configured clocks would explain it. `TEON_TIMEZONE` currently applies one zone to everything, so camera freshness may be off by the offset.

---

## Provenance

All source data are provisional and provided by the Tahoe Environmental Observatory Network (https://tahoeenvironmentalobservatorynetwork.org/). Per TEON's disclaimer:

> Data may be subject to inaccuracies due to instrument performance, maintenance cycles, or environmental conditions at measurement locations. Users assume full responsibility for the interpretation and use of these data.

`secchi` additionally honours TEON's `/site-visibility/disabled` flags: a site the observatory has marked non-public is neither ingested nor displayed.
