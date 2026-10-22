# A Power BI dashboard on the Azure copy

*Written 2026-10-21.* Power BI Desktop (free, Windows) reads secchi's Azure
SQL Database directly. It doesn't read the 12 million raw readings. It reads
six small summary tables that the sync rebuilds after every run (the "gold"
layer), so a refresh imports a few thousand rows in seconds.

## The tables

Built by `secchi.cloud.quality.dash_sql()`, run after every load and sync:

| Table | One row per | Columns |
|---|---|---|
| `secchi.dash_lake_day` | sonde, day, variable | `site`, `day`, `variable`, `variable_label`, `unit`, `mean_value`, `min_value`, `max_value`, `readings`. Water temperature, turbidity, chlorophyll-a, dissolved oxygen, conductivity and phycocyanin, from `obs_clean`, so failed readings are already out |
| `secchi.dash_gauge_day` | USGS gauge, day (Pacific standard) | `site_number`, `site`, `kind` (inflow, lake level, outflow), `day`, `flow_cfs`, `gage_height_ft`, `water_temp_c`, `turbidity_fnu` |
| `secchi.dash_snow_day` | SNOTEL site, day | `site`, `day`, `snow_depth_cm`, `snow_water_mm`, `precip_mm`, `air_temp_c` |
| `secchi.dash_quality_month` | TEON site, month | `month`, `site`, `sensor_type`, `readings`, `flagged` (any flag), `unusable` (a flag that removes it from `obs_clean`) |
| `secchi.dash_flags_month` | TEON site, month, flag | `month`, `site`, `sensor_type`, `flag`, `unusable`, `readings` (a reading with two flags counts under both) |
| `secchi.dash_station` | station | `network`, `kind`, `station`, `site_number`, `lat`, `lng` |
| `secchi.run_log` | sync run | when, what reloaded, months matched, result, free compute left |

CI checks the tables on every change: their sizes, and that
`dash_quality_month` counts every TEON reading exactly once.

## Connect (once)

1. Install **Power BI Desktop** from the Microsoft Store (free).
2. **Home → Get data → Azure → Azure SQL database.**
3. Server: `secchi-brooks.database.windows.net`. Database: `secchi`.
   **Data connectivity mode: Import.**
4. Sign in on the **Database** tab with `secchi_reader` and its password.
   It's read-only, the same login QGIS uses.
5. In the Navigator, tick the seven tables above and click **Load**.

**Import, not DirectQuery.** Import copies the summary rows into the
report file once, and the database can go back to sleep. DirectQuery would
query Azure on every click and keep it awake, which uses the free compute.
Each **Refresh** wakes the database once (about an hour of free compute,
like any connection). Refresh when you want new data, not constantly.

**Model view.** Power BI may guess relationships between tables that share
a column name, like `site`. These tables are meant to be used separately,
so delete any it created (Model view, right-click the line, Delete).

## Measures (DAX)

Add with **Modeling → New measure**:

```dax
Latest lake temp (°C) =
VAR d = CALCULATE ( MAX ( dash_lake_day[day] ), dash_lake_day[variable] = "Temp" )
RETURN CALCULATE ( AVERAGE ( dash_lake_day[mean_value] ),
                   dash_lake_day[variable] = "Temp", dash_lake_day[day] = d )

Inflow today (cfs) =
VAR d = MAX ( dash_gauge_day[day] )
RETURN CALCULATE ( SUM ( dash_gauge_day[flow_cfs] ),
                   dash_gauge_day[kind] = "Inflow (creek or river)", dash_gauge_day[day] = d )

Usable readings % =
1 - DIVIDE ( SUM ( dash_quality_month[unusable] ), SUM ( dash_quality_month[readings] ) )

Last sync = MAX ( run_log[run_utc] )
```

"Inflow today" adds up the latest day, which may be part of a day; for a
complete day, filter to yesterday.

## The pages

**1. The lake**

- Cards: `Latest lake temp (°C)`, `Inflow today (cfs)`, `Usable readings %`,
  `Last sync`.
- Line chart: `dash_lake_day` `day` on the X axis, `mean_value` as the Y,
  `site` as the legend. A slicer on `variable_label` switches between
  temperature, turbidity, chlorophyll and the rest.
- Line chart: `dash_gauge_day` `day`, `flow_cfs`, legend `site`, filtered to
  `kind` = inflow.
- Map (Azure Maps visual): `dash_station` `lat` and `lng`, legend `network`.

**2. Data quality**

- Stacked column chart: `dash_flags_month` `month`, `readings`, legend
  `flag`. Turn on the `unusable` filter to see only the readings that are
  left out.
- Matrix: rows `site`, columns `month`, values `Usable readings %`, with
  conditional formatting (pale to deep, one colour ramp).

**3. Snow** (the new water year started October 1)

- Line chart: `dash_snow_day` `day`, `snow_water_mm`, legend `site`.
- Line chart below it: `dash_gauge_day` inflows over the same dates, to
  watch snow turn into flow in spring.

Save the report as `C:\Users\brook\GIS\secchi_dashboard.pbix`. Keep it out
of the repo; the report file holds a copy of the data.
