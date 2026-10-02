# Asking questions of the record

    pixi run query

opens a SQL shell over the whole store. Or ask one thing and exit:

    pixi run query "SELECT site, count(*) FROM obs GROUP BY site"

DuckDB reads the partitioned parquet **in place** — nothing is imported
or converted, and the store stays exactly as the pipeline writes it. A
filtered aggregate over 7.5 million rows answers in well under a second,
because DuckDB opens only the monthly partitions and columns a query
touches. Loading the same data into pandas costs ~650 MB before you've
asked anything.

## Tables

| Table | What it is |
|---|---|
| `obs` | Every TEON observation. One row per reading per variable: `uuid, source, site, sensor_type, timestamp, lat, lng, variable, value`, plus `year` and `month` |
| `usgs` | The USGS gauge observations, including the `approval` flag |
| `snotel` | daily SNOTEL precipitation (`PRCP`, `PREC`, mm) and air temperature (`TAVG`, °C) at Ward Creek #3, Rubicon #2 and Marlette Lake |
| `assets` | Camera frame references |
| `stations` | One row per mapped location, with the catchment it sits in |
| `catchments` | The 60 catchments and their attributes |

`stations` uses the same point-in-polygon join as `pixi run
catchment-join`, so a site's catchment is the same everywhere.

## Shell commands

    .tables            list the tables
    .examples          example queries
    .run N             run example N
    .save FILE.csv     write the last result, in full, to CSV
    .quit

SQL can span lines; it runs when a line ends with `;`. Results over 40
rows are truncated on screen — `.save` writes all of them.

## From a notebook

    from secchi.query import connect
    con = connect()
    df = con.sql("SELECT * FROM obs WHERE site = 'Meeks'").df()

## Useful variable names

| Instrument | `sensor_type` | Some `variable` values |
|---|---|---|
| EXO sonde | `ExoSensor` | `Temp`, `Do_mgL`, `Do_percent`, `Chl_a`, `Turbidity` |
| MiniDOT | `MiniDotSensor` | `Temperature`, `Dissolved Oxygen`, `Dissolved Oxygen Saturation` |
| HOBO | `HoboSensor` | `temperature`, `conductivity` |
| Forest logger | `SoilEnvironmentalConditions` etc. | `Soil_VWC` (a fraction — ×100 for %), `Air_Temp`, `RH`, `BattV_Avg` |

Four naming conventions, because TEON keeps each vendor's field names
verbatim.

## Three things DuckDB does silently here

None raises an error on its own, which is why they're written down.

1. **The folder name overrides the file's `source` column.** Files hold
   `'TEON'`; they sit in folders named `source=teon`; you'll see
   `'teon'`. Harmless here.

2. **The partition folders are typed inconsistently.** `year=2026` reads
   as a number but `month=06` as text, because of the leading zero — so
   `month = 9` works by coincidence while `month >= 8` raises a type
   error. The shell declares both as integers.

3. **A column present in only some partitions is dropped** unless the
   read uses `union_by_name`. USGS `approval` is the case here. The shell
   always sets it.

If you write your own `read_parquet` over this store, set
`hive_partitioning`, `union_by_name` and `hive_types` too.

## Why DuckDB isn't the storage

A `.duckdb` file is a single binary, and git stores a complete new copy
of it on every write — the exact churn the monthly partitioning exists
to avoid. So the store stays parquet, and DuckDB is only ever the
reading layer.

## Two investigation queries worth keeping

Holes in one station's record (change the site):

```sql
SELECT prev_reading, next_reading, next_reading - prev_reading AS gap
FROM (
  SELECT lag(timestamp) OVER (ORDER BY timestamp) AS prev_reading,
         timestamp AS next_reading
  FROM (SELECT DISTINCT timestamp FROM obs
        WHERE site = 'Glenbrook 5' AND variable = 'Soil_VWC')
)
WHERE next_reading - prev_reading > INTERVAL 2 HOUR
ORDER BY prev_reading;
```

Every variable during a bad stretch against the rest of the month — how the
two channel scrambles were decoded (change the site, month and condition):

```sql
WITH bad AS (
  SELECT DISTINCT timestamp FROM obs
  WHERE site = '4H Camp' AND variable = 'Do_percent' AND value < 50
)
SELECT o.variable,
       round(avg(o.value) FILTER (WHERE b.timestamp IS NOT NULL), 2) AS during_bad,
       round(avg(o.value) FILTER (WHERE b.timestamp IS NULL), 2) AS normal
FROM obs o LEFT JOIN bad b USING (timestamp)
WHERE o.site = '4H Camp' AND o.year = 2026 AND o.month = 7
GROUP BY o.variable ORDER BY o.variable;
```

