# Three naming conventions in one API

TEON's backend aggregates data from at least three different instrument
vendors, and it keeps each vendor's field names verbatim. That is a
defensible choice — it preserves provenance — but it means there is no
single schema, and code that assumes one breaks silently.

## The timestamp column

| Sensor type | Timestamp field |
|---|---|
| EXO, Campbell loggers, cameras, precipitation | `TIMESTAMP` |
| Hobo | `timestamp` |
| **Minidot** | **`Pacific Standard Time`** |

That last one is not a typo. A PME MiniDOT's own software writes the
**timezone** as the header of its time column, and whoever built TEON's
loader kept the header as-is. The field *name* is a timezone; the field
*value* is the timestamp.

It cost 1,490,116 rows their timestamps on 2026-09-22, because
`to_frames` read `rec.get("TIMESTAMP")` — one hardcoded key.

`TEON_TIMESTAMP_FIELDS` is now an ordered list, and the transform logs a
warning naming any sensor type that resolves to something unexpected.

## The measurement columns

The same split, and worse:

| Sensor type | Style | Example |
|---|---|---|
| Campbell loggers | `Snake_Case_Abbrev` | `Air_Temp`, `BattV_Avg`, `PTemp_C_Avg` |
| EXO | `Mixed_case` | `Do_mgL`, `Chl_a`, `Turbidity` |
| Hobo | `lowercase` | `temperature`, `conductivity` |
| **Minidot** | **`Title Case With Spaces`** | `Dissolved Oxygen Saturation` |

Four conventions. `SENSOR_VARIABLES` now carries entries for
`MiniDotSensor` and `HoboSensor` so their measurements get labels and
units instead of being stored and never displayed.

## A question this raised, deliberately left open

MiniDOT's time column says **PST** specifically, not "Pacific Time". If
those records carry a fixed −08:00 offset year-round, they do **not**
shift for daylight saving — and parsing them as `America/Los_Angeles`,
which does shift, puts every summer reading an hour out.

This is unconfirmed. Settling it needs records either side of a DST
boundary, and the fleet ran through March 2026, so the backfill should
contain one. `MINIDOT_TIMESTAMP_IS_FIXED_PST` is declared as `None` in
config with the reasoning attached, so the question stays visible instead
of being silently decided by the default.

An hour is not nothing for a diurnal DO signal, which is the main thing a
MiniDOT measures.

## And one more to check after the backfill

MiniDOT reports `Dissolved Oxygen Saturation`. TEON's EXO `Do_percent`
turned out to be referenced to a **sea-level** atmosphere, which at
1,898 m makes an 81 % reading really about 102 %.

A MiniDOT computes saturation the same way — from temperature and a
configured barometric pressure — so the same failure mode applies. The
field is marked `"saturation reference unverified"` and
`pixi run oxygen-check` will settle it once these rows have timestamps
and can be paired with concentration.

## The general lesson

**Run `pixi run record-shape` before backfilling a sensor type you
haven't pulled before.** It fetches one record per type and reports its
field names. It costs ten requests and would have saved a 40-minute fetch
and 24 MB of unusable rows.

This is the seventh time this project has been bitten by assuming a
schema instead of asking for one.
