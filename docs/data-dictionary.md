# Data dictionary

## TEON API

Base URL (as of 2026-09-17): `https://rftazkiysf.us-west-2.awsapprunner.com/api`

The hostname is an AWS App Runner container; assume it may change. If it does, update `TEON_API_BASE` in `src/secchi/config.py`.

### Endpoints in use

| Path | Purpose |
|---|---|
| `/sensors/locations` | Full inventory: every sensor by category → sensor type, with coordinates, first/last update, data count. |
| `/sensors/{slug}?site={site}&page={n}&page_size={s}` | Paginated time series for one (sensor type, site) pair. |
| `/aquatic-metadata/` | Aquatic sensor metadata (not yet consumed — endpoint currently redirect-loops for the ingest client; needs investigation). |
| `/calibration/events?site_name={site}` | Calibration event log per site — relevant to the `pH = 0` fleetwide issue. |
| `/site-visibility/disabled` | Sites the frontend hides. |

### Pagination envelope

Every list endpoint returns:

```json
{ "data": [...], "count": 50, "total": 46223, "page": 1, "page_size": 50, "total_pages": 925 }
```

`page_size` requests below ~50 appear to be clamped up; treat 50 as the effective minimum.

### Sensor type → URL slug

| Payload key | URL slug | Confirmed? |
|---|---|---|
| `ExoSensor` | `exo-sensor` | ✓ |
| `ExoSensorManual` | `exo-sensor` | ✓ (same endpoint) |
| `MiniDotSensor` | `mini-dot-sensor` | ? |
| `HoboSensor` | `hobo-sensor` | ? |
| `SoilEnvironmentalConditions` | `soil-environmental-conditions` | ? |
| `AirTemperatureRelativeHumidity` | `air-temperature-relative-humidity` | ? |
| `TreeStressAndGrowth` | `tree-stress-and-growth` | ? |
| `PrecipitationGauge` | `precipitation-gauge` | ? |
| `FieldCamera` | `field-camera` | ? |
| `StreamChemistry` | `stream-chemistry` | ? |
| `StreamLevel` | `stream-level` | ? |

Unconfirmed slugs will 404 in `TeonClient.fetch_sensor` and log a warning; the ingest continues on to the next sensor.

## EXO sonde record

15-minute cadence. Every record has these fields:

| Field | Units | Notes |
|---|---|---|
| `uuid` | — | Stable per observation. Used to dedupe in the transform layer. |
| `site` | — | Human-readable site name. |
| `longitude`, `latitude` | ° | Sensor location. |
| `TIMESTAMP` | ISO 8601 (naive) | Treated as UTC. Timezone is uncertain — verify against reality if the offset matters. |
| `BattV_Min` | V | Battery voltage. Oscillates 12.9–14.5 V on solar. Hidden from dashboard. |
| `depth` | m | Sonde deployment depth. |
| `Temp` | °C | Water temperature. |
| `pH` | — | **Broken/uncalibrated fleetwide — reports 0.0 in every record.** Hidden from dashboard. |
| `specific_conductivity` | µS/cm | |
| `Salinity` | ppt | Very low at Tahoe (~0.04). |
| `TDS` | mg/L | Total dissolved solids. |
| `Turbidity` | FNU | Can dip below zero at very clear water; clip to 0 for display. |
| `Do_mgL` | mg/L | Dissolved oxygen. |
| `Do_percent` | % sat | Dissolved oxygen % saturation. |
| `Chl_a` | µg/L | Chlorophyll-a — the primary clarity-driver signal. |
| `phycocyanin` | µg/L | Cyanobacteria (blue-green algae) proxy. |
| `kor_site_name` | — | Always null in observed data. Likely FK to a KorEXO lookup. |
| `TSS` | — | Always null. Lab-only field, not populated by the sonde. |

## Sites currently ingested

Three telemetered EXO sondes, one per shore. Confirmed live on 2026-09-17.

| Site | Shore | Lat | Lng |
|---|---|---|---|
| Sunnyside | west | 39.134643 | -120.151085 |
| Glenbrook | east | 39.088329 | -119.941133 |
| 4H Camp | south | 38.974209 | -119.951608 |

Two additional EXO sondes are **offline pending retrieval** — Blackwood 3 (west, 39.108, -120.158) and Meeks (west, 39.038, -120.121), both `ExoSensorManual`, last update 2026-07-09.

## Provenance

All source data are provisional and provided by the Tahoe Environmental Observatory Network
(https://tahoeenvironmentalobservatorynetwork.org/). Per TEON's own disclaimer:

> Data may be subject to inaccuracies due to instrument performance, maintenance cycles, or environmental conditions at measurement locations. Users assume full responsibility for the interpretation and use of these data.
