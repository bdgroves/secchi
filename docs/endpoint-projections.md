# Shared rows are not shared columns

## What was found

The first query run in the new SQL shell listed every variable stored at
Homewood and which sensor type it was filed under:

| Filed under | Variables | Rows | From |
|---|---|---|---|
| Air temperature | `Air_Temp`, `RH`, `BattV_Avg`, `PTemp_C_Avg` | 728 | 2026-09-15 |
| Soil | `Soil_VWC`, `Soil_T`, `Soil_EC` (two depths each), `BattV_Avg`, `PTemp_C_Avg` | ~33,000 | 2025-09-11 |

`Air_Temp` and `RH` appear **only** under air, and only for 7.5 days.
Soil has a year.

## Why

At every forest station, TEON exposes one Campbell logger table through
several endpoints. They share record ids, row counts, battery voltage and
logger temperature — which is what led to the finding that 43 listed
sensors are really 13 devices.

That finding is still true. What was wrong was the conclusion drawn from
it for fetching. Each endpoint is a **projection** of the table, returning
different measurement columns:

| Endpoint | Columns beyond the shared ones |
|---|---|
| `/sensors/soil-moisture` | `Soil_VWC`, `Soil_T`, `Soil_EC`, second-depth variants |
| `/sensors/air-temperature` | `Air_Temp`, `RH` |
| `/sensors/tree-stress` | the eight band dendrometers |
| `/sensors/stream-level` | `Uncalibrated_water_depth` |

The backfill grouped endpoints with identical record counts, fetched one,
and marked it as standing in for the rest. It fetched soil. So the history
of everything else was never requested:

- **Air temperature and humidity** at all seven forest stations
- **Tree stress** at Glenbrook 2, 4 and 5
- **Stream level** at Blackwood 2, whose count matched its logger

Only the hourly job's recent rows existed for those, which is why the
store-wide summary showed air temperature and tree stress starting
2026-09-13.

## What still worked

Everything that read soil moisture or battery voltage: the transect,
Glenbrook 2, and station health. Those variables came through the soil
endpoint, so their history is intact. The transect results stand.

## The fix

**No more collapsing.** Every endpoint is fetched.

**Skip what's complete instead.** Before fetching, the backfill counts
the records already held per site and sensor type (with DuckDB, which
reads three columns rather than loading the store) and skips any sensor
within 100 records of its upstream count. So rerunning `--stage live`
fetches the missing air, tree and stream-level history and skips soil and
the lake sondes. `--force` fetches everything regardless.

The shared columns deduplicate correctly: the store keys on record id,
site and variable, so a `BattV_Avg` reading arriving from both soil and
air is kept once. Verified: after recovering air history, battery rows
equal the record count exactly.

## The general point

Two identifiers matching tells you two things describe the same event. It
doesn't tell you they describe it the same way. The original note called
these endpoints "projections of one logger table" — the right word — and
the collapse then treated them as interchangeable anyway.
