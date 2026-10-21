"""The tables that move, and how each one is fingerprinted on both sides.

One entry per SQL Server table. Each says where the rows come from in the
store (a view from ``secchi.query.connect``), what feeds it and what reads
it (for the inventory), and three expressions written twice, once in
DuckDB and once in T-SQL:

``key``    a text key that is unique per row. Its MD5 is summed, so a row
           that is missing, duplicated or attached to the wrong site or
           variable changes the total.
``ts``     the row's time, as a naive datetime (USGS: UTC). It picks the
           month the row is counted in, and its min and max are compared.
``value``  the main number. Min and max must match exactly; the sum (each
           value rounded to 4 places) must match too.

The two versions of each expression must produce the same text for the
same row. ``tests/test_cloud.py`` runs the DuckDB side; the CI workflow
(.github/workflows/cloud.yml) runs both against SQL Server 2022.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Table:
    name: str                 # secchi.<name> in SQL Server
    view: str                 # the DuckDB view or table it comes from
    about: str
    fed_by: str
    read_by: str
    key_duck: str
    key_tsql: str
    ts_duck: str | None
    ts_tsql: str | None
    value_duck: str | None
    value_tsql: str | None
    from_tsql: str            # FROM clause on the SQL Server side
    export_sql: str           # DuckDB SELECT, columns in the table's order
    monthly: bool = True
    utf8: bool = False        # text may hold non-ASCII; stored with a UTF-8 collation


def _wide(view: str, extra: str = "") -> str:
    return f"""SELECT uuid, site, sensor_type, timestamp AS ts, lat, lng,
               variable, value{extra} FROM {view} ORDER BY timestamp, uuid, variable"""


TABLES: list[Table] = [
    Table(
        name="obs", view="obs",
        about="Every TEON reading: one row per record per variable. Sites, sensor "
              "types and variables are moved into lookup tables.",
        fed_by="fetch.yml hourly (ingest-all), backfill",
        read_by="transform (the page), status, daily, every analysis, query, export",
        key_duck="concat(uuid, '|', variable)",
        key_tsql="CONCAT(LOWER(CONVERT(char(36), o.record_uuid)), '|', v.variable)",
        ts_duck="timestamp", ts_tsql="o.ts",
        value_duck="value", value_tsql="o.value",
        from_tsql="secchi.obs AS o JOIN secchi.variable AS v ON v.variable_id = o.variable_id",
        export_sql="""SELECT o.uuid, s.site_id, t.sensor_type_id, o.timestamp AS ts,
                             v.variable_id, o.value
                      FROM obs AS o
                      JOIN dim_site AS s USING (site)
                      JOIN dim_sensor_type AS t USING (sensor_type)
                      JOIN dim_variable AS v USING (variable)
                      ORDER BY o.timestamp, o.uuid, o.variable""",
    ),
    Table(
        name="usgs", view="usgs",
        about="USGS stream gauges, instantaneous values. Times are stored as "
              "datetimeoffset in UTC.",
        fed_by="fetch.yml hourly (usgs)",
        read_by="transform (the page), glenbrook, status, query, export",
        key_duck="concat(uuid, '|', variable, '|', statistic_id)",
        key_tsql="CONCAT(uuid, '|', variable, '|', statistic_id)",
        ts_duck="timezone('UTC', timestamp)",
        ts_tsql="CONVERT(datetime2(6), SWITCHOFFSET(ts, '+00:00'))",
        value_duck="value", value_tsql="value",
        from_tsql="secchi.usgs",
        export_sql="""SELECT uuid, site, site_number, sensor_type,
                             strftime(timezone('UTC', timestamp), '%Y-%m-%d %H:%M:%S') || ' +00:00' AS ts,
                             lat, lng, variable, statistic_id, statistic, value,
                             unit_of_measure, approval_status, qualifier
                      FROM usgs ORDER BY timestamp, uuid, variable, statistic_id""",
    ),
    Table(
        name="snotel", view="snotel",
        about="NRCS SNOTEL daily precipitation, temperature and snow water "
              "(Beyond TEON context).",
        fed_by="fetch.yml hourly (snotel), after the store reset",
        read_by="transect-rain, Beyond TEON panel, query, export",
        key_duck="concat(uuid, '|', variable)", key_tsql="CONCAT(uuid, '|', variable)",
        ts_duck="timestamp", ts_tsql="ts", value_duck="value", value_tsql="value",
        from_tsql="secchi.snotel", export_sql=_wide("snotel", ", unit"),
    ),
    Table(
        name="asos", view="asos",
        about="NWS airport daily weather, South Lake Tahoe and Truckee (Beyond TEON).",
        fed_by="fetch.yml once a day (asos)",
        read_by="Beyond TEON panel, query, export",
        key_duck="concat(uuid, '|', variable)", key_tsql="CONCAT(uuid, '|', variable)",
        ts_duck="timestamp", ts_tsql="ts", value_duck="value", value_tsql="value",
        from_tsql="secchi.asos", export_sql=_wide("asos", ", unit"),
    ),
    Table(
        name="smoke", view="smoke",
        about="NOAA HMS satellite smoke analysis over the lake, daily.",
        fed_by="fetch.yml hourly (smoke)",
        read_by="smoke-lake, the page's smoke strip, query, export",
        key_duck="uuid", key_tsql="uuid",
        ts_duck="timestamp", ts_tsql="ts", value_duck="value", value_tsql="value",
        from_tsql="secchi.smoke", export_sql=_wide("smoke"),
    ),
    Table(
        name="assets", view="assets",
        about="Field-camera frame references (s3 paths), not the images.",
        fed_by="fetch.yml hourly (ingest-all)",
        read_by="the page's camera panel",
        key_duck="uuid", key_tsql="uuid",
        ts_duck="timestamp", ts_tsql="ts",
        value_duck="CAST(total_available AS DOUBLE)", value_tsql="CAST(total_available AS float)",
        from_tsql="secchi.assets",
        export_sql="""SELECT uuid, site, sensor_type,
                             strftime(timestamp, '%Y-%m-%d %H:%M:%S.%f') AS ts, lat, lng, kind, ref,
                             total_available FROM assets ORDER BY timestamp, uuid""",
    ),
    Table(
        name="terc", view="terc",
        about="UC Davis TERC Secchi depths since 1967 (reference CSV).",
        fed_by="fetch.yml, only when TERC publishes a new revision (terc)",
        read_by="the clarity panel, query",
        # The note is part of the key so its text is checked byte for byte:
        # one note holds non-ASCII (a mangled apostrophe, as published).
        key_duck="concat(station, '|', strftime(date_time_local, '%Y-%m-%d %H:%M:%S'), '|', lake_condition)",
        key_tsql="CONCAT(station, '|', CONVERT(char(19), date_time_local, 120), '|', lake_condition)",
        ts_duck="date_time_local", ts_tsql="date_time_local",
        value_duck="secchi_m", value_tsql="secchi_m",
        from_tsql="secchi.terc", monthly=False, utf8=True,
        export_sql="""SELECT station, date_time_local, secchi_m, disappear_m, reappear_m,
                             viewing_condition, lake_condition
                      FROM terc ORDER BY date_time_local, station""",
    ),
    Table(
        name="cssl_climo", view="cssl_climo",
        about="UC Berkeley Snow Lab snowfall and peak depth per water year, since 1879.",
        fed_by="fetch.yml daily at 13 UTC (cssl)",
        read_by="Beyond TEON panel, query",
        key_duck="CAST(water_year AS VARCHAR)", key_tsql="CAST(water_year AS varchar(10))",
        ts_duck=None, ts_tsql=None, value_duck="snowfall_cm", value_tsql="snowfall_cm",
        from_tsql="secchi.cssl_climo", monthly=False,
        export_sql="""SELECT water_year, snowfall_cm, max_depth_cm, snowfall_hs_cm
                      FROM cssl_climo ORDER BY water_year""",
    ),
    Table(
        name="watershed", view="watershed",
        about="The 60 TEON catchment polygons, stored as geometry (SRID 4326).",
        fed_by="reference (fetched once)",
        read_by="catchment-join, the map's catchment layer, query (stations)",
        key_duck="CAST(watershed_id AS VARCHAR)", key_tsql="CAST(watershed_id AS varchar(10))",
        ts_duck=None, ts_tsql=None, value_duck="area_km", value_tsql="area_km",
        from_tsql="secchi.watershed", monthly=False,
        export_sql="SELECT watershed_id, name, area_km, wkt FROM watershed ORDER BY watershed_id",
    ),
]

# Lookup tables for obs. Built in DuckDB, loaded first, never fingerprinted
# on their own: every obs key passes through them, so a wrong mapping shows
# up as an obs mismatch.
DIMENSIONS = {
    "site": """SELECT row_number() OVER (ORDER BY site) AS site_id, site,
                      any_value(lat) AS lat, any_value(lng) AS lng
               FROM obs GROUP BY site""",
    "sensor_type": """SELECT row_number() OVER (ORDER BY sensor_type) AS sensor_type_id,
                             sensor_type FROM (SELECT DISTINCT sensor_type FROM obs)""",
    "variable": """SELECT row_number() OVER (ORDER BY variable) AS variable_id,
                          variable FROM (SELECT DISTINCT variable FROM obs)""",
}

# Files in data/reference/ and what happens to each. Not everything moves:
# some files are the app's own bookkeeping, and moving them would only
# create a second copy to keep in step.
REFERENCE_PLAN = {
    "terc_secchi.csv": "move (table terc)",
    "cssl_snow_climatology.csv": "move (table cssl_climo)",
    "tahoe_watersheds.geojson": "move (table watershed, geometry)",
    "watershed_attributes.json": "later: 168 attributes per catchment, wide; move when a report needs them",
    "watershed_variables.json": "later: the dictionary for the attributes above",
    "snotel_stations.json": "later: station metadata; SNOTEL rows already carry name and location",
    "station_ground.json": "later: hand-recorded site notes",
    "terc_package.json": "stay: TERC revision bookkeeping for the fetcher",
    "watch_baseline.json": "stay: the watcher's state, owned by watch.yml",
    "news_log.json": "stay: the page's What's new feed",
}


def fingerprint_duck(t: Table) -> str:
    """One row per month (or one row, 'all') of fingerprints, in DuckDB."""
    ts = t.ts_duck or "CAST(NULL AS TIMESTAMP)"
    v = t.value_duck or "CAST(NULL AS DOUBLE)"
    part = "strftime(ts, '%Y-%m')" if t.monthly else "'all'"
    return f"""
SELECT '{t.name}' AS tbl, {part} AS part,
       count(*) AS n_rows,
       count(DISTINCT k) AS n_keys,
       sum(('0x' || substr(md5(k), 1, 8))::BIGINT) AS key_hash,
       strftime(min(ts), '%Y-%m-%d %H:%M:%S.%f') AS ts_min,
       strftime(max(ts), '%Y-%m-%d %H:%M:%S.%f') AS ts_max,
       min(v) AS v_min, max(v) AS v_max,
       CAST(sum(CASE WHEN abs(v) < 1e12 THEN CAST(v AS DECIMAL(38, 4)) END) AS VARCHAR) AS v_sum,
       count(*) - count(v) AS v_null
FROM (SELECT {t.key_duck} AS k, {ts} AS ts, {v} AS v FROM {t.view})
GROUP BY ALL ORDER BY part"""


def fingerprint_tsql(t: Table) -> str:
    """The same fingerprint, computed by SQL Server from the loaded table.

    MD5's first four bytes cast to bigint are unsigned, as in DuckDB.
    Floats print with style 3 (17 digits), which round-trips exactly.
    """
    ts = f"CAST({t.ts_tsql} AS datetime2(6))" if t.ts_tsql else "CAST(NULL AS datetime2(6))"
    v = t.value_tsql or "CAST(NULL AS float)"
    part = "CONVERT(char(7), ts, 120)" if t.monthly else "'all'"
    # A UTF-8 collation makes HASHBYTES see the same bytes DuckDB's md5 does.
    collate = "COLLATE Latin1_General_100_CI_AS_SC_UTF8" if t.utf8 else ""
    return f"""
SELECT '{t.name}' AS tbl, {part} AS part,
       COUNT_BIG(*) AS n_rows,
       COUNT_BIG(DISTINCT k) AS n_keys,
       SUM(CAST(SUBSTRING(HASHBYTES('MD5', k), 1, 4) AS bigint)) AS key_hash,
       CONVERT(varchar(26), MIN(ts), 121) AS ts_min,
       CONVERT(varchar(26), MAX(ts), 121) AS ts_max,
       CONVERT(varchar(30), MIN(v), 3) AS v_min,
       CONVERT(varchar(30), MAX(v), 3) AS v_max,
       CAST(SUM(CASE WHEN ABS(v) < 1e12 THEN CAST(v AS decimal(38, 4)) END) AS varchar(60)) AS v_sum,
       COUNT_BIG(*) - COUNT_BIG(v) AS v_null
FROM (SELECT CAST({t.key_tsql} AS varchar(2000)) {collate} AS k, {ts} AS ts, {v} AS v
      FROM {t.from_tsql}) AS x
{"GROUP BY " + part if t.monthly else ""}"""
