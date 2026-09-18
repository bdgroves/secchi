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
| `Chl_a` | µg/L | Chlorophyll-a — the primary clarity driver. **Sunnyside also reads persistently negative here** (around −0.12), which means both of its optical channels are offset, not just turbidity. Clipped to 0 for display. |
| `Do_mgL` | mg/L | Dissolved oxygen. |
| `Do_percent` | % sat | DO saturation. |
| `Turbidity` | FNU | **Sunnyside has a hard zero-offset fault.** Across 48 consecutive records it read −2.109 FNU with a standard deviation of 0.042 (range −2.17 to −1.93). A spread that tight around a strongly negative mean is a mis-set zero point, not noise: add +2.11 and the site reads ~0.0 FNU, which is correct for clear Tahoe water. Independently corroborated — the USGS gauge on Blackwood Creek reads +0.3 FNU on the same measure with the same units from a separate instrument. Clipped to 0 for display and flagged on the card. `/calibration/events` is empty, so there is no documented explanation via the API. |
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

## Manual vs. telemetered sondes

Two of the five lake EXO sondes carry `Manual` in their inventory `id`
(`ExoSensorManual_Blackwood 3_…`, `ExoSensorManual_Meeks_…`). These are
**self-logging instruments whose data arrives only when someone dives and
downloads them**, as opposed to the three sondes on live telemetry. This
is now documented rather than inferred:

**1. TEON states the retrieval cycle outright.** From their ArcGIS
StoryMap: *Emily Carlson and Katie Senft ride and snorkel to their sensors
every month, come rain, shine, and even snow in the winter months.* The UNR
launch coverage adds the detail — collect the sondes, bring them to the
lab, clean off accumulated algae, download and back up the data, then
recalibrate against third-party certified standards.

**2. Record completeness has the signature of internal logging.** A
self-logging instrument has no radio link to drop packets, so its record
should be near-perfect; a telemetered one loses transmissions.

| Sonde | Telemetry | Records | Expected at 15-min | Complete |
|---|---|---|---|---|
| Blackwood 3 | manual | 15,451 | 15,642 | **98.8 %** |
| Meeks | manual | 15,441 | 15,632 | **98.8 %** |
| 4H Camp | live | 52,443 | 54,344 | 96.5 % |
| Glenbrook | live | 46,271 | 47,811 | 96.8 % |
| Sunnyside | live | 38,562 | 52,422 | 73.6 % |

**3. Both manual units are short by exactly 191 records.** 15,642 − 15,451
and 15,632 − 15,441 both equal 191, which is 47.8 hours. An identical
two-day gap on two separate instruments is not random packet loss; it is
one shared service event when both were out of the water at once.

**4. The record ends abruptly rather than degrading.** Both stop on
2026-07-09 within 30 minutes of each other, having been deployed on
2026-01-27 within three hours of each other. Coordinated deployment,
coordinated retrieval.

**The nearshore network is bigger than the API shows.** The StoryMap says
ten stations measure "oxygen, temperature, conductivity, turbidity,
chlorophyll, and pH" around the lake. The API exposes five EXO sites. The
gap is almost certainly the dormant MiniDot and HOBO fleet — twelve
instruments across six shared nearshore sites (Camp Richardson, Lake
Forest, Lakeside, Incline, Tahoe Keys, Tallac). Five EXO plus six
MiniDot/HOBO locations is eleven, which matches "ten stations" closely.
That reframes the dormant fleet: it isn't peripheral, it's half of what the
nearshore programme was built to do. See [`dormant-data.md`](dormant-data.md).

**pH is meant to work.** It appears in the StoryMap's stated parameter
list, so the fleetwide pH failure is a fault, not an unequipped channel.

**A fourth monitoring domain is missing entirely.** The StoryMap describes
four: nearshore water quality, terrestrial, stream, and *aquatic* — upland
lakes, ponds and wet meadows, with the note that east-shore sites are most
climate-vulnerable because so few exist there. `/sensors/locations` exposes
only `lake`, `stream` and `terrestrial`. Ponds and meadows are not in the
API at all.

**Why manual at these sites.** Not documented. Telemetry needs power, a
radio or cellular path and a shore receiver; Meeks Bay and Blackwood are
less developed stretches of the west shore. A self-logging sonde is the
cheaper deployment where infrastructure is thin.

**What remains genuinely uncertain.** Whether 2026-07-09 means *removed
from the lake* or *last download, still logging*. If the latter, roughly
two months of 15-minute data is sitting in those sondes' memory waiting for
the next dive — not lost, just not yet uploaded. The API cannot distinguish
these cases; only TEON can say.

**The connection to data quality.** Biofouling is the reason sondes need
retrieval at all — algae grows over optical windows. That is very likely
related to the calibration problems catalogued below, since optical
channels drift as their windows foul and pH probes drift fastest of all.

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
