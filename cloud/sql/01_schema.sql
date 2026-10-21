-- ============================================================================
-- 01_schema.sql: the secchi tables in SQL Server 2022 or Azure SQL Database.
-- Drops and recreates everything: a load is always a full, fresh copy, so a
-- rerun can never leave half of an old load behind.
-- Run by `pixi run cloud-load` before the bulk copy. Needs QUOTED_IDENTIFIER
-- (sqlcmd -I).
-- ============================================================================
SET NOCOUNT ON;
IF SCHEMA_ID('secchi') IS NULL EXEC('CREATE SCHEMA secchi');
GO
DROP VIEW  IF EXISTS secchi.site_watershed;
DROP TABLE IF EXISTS secchi.obs, secchi.site, secchi.sensor_type, secchi.variable,
                     secchi.usgs, secchi.snotel, secchi.asos, secchi.smoke,
                     secchi.assets, secchi.terc, secchi.cssl_climo,
                     secchi.watershed_stage, secchi.watershed;
GO

-- Lookups for obs. 12 million rows repeat 18 site names and 55 variable
-- names; storing a small number instead is most of the saving.
-- The names are case-sensitive (BIN2): TEON has "Temperature" (MiniDOT)
-- and "temperature" (HOBO), and "Specific_Conductivity" and
-- "specific_conductivity". SQL Server's default collation would call each
-- pair the same name and refuse the second.
CREATE TABLE secchi.site (
    site_id   smallint     NOT NULL PRIMARY KEY,
    site      varchar(60)  COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
    lat       float        NOT NULL,
    lng       float        NOT NULL
    -- location geography is added by 02_after_load.sql: bcp needs the
    -- table's columns to match the file's, one for one
);
CREATE TABLE secchi.sensor_type (
    sensor_type_id smallint    NOT NULL PRIMARY KEY,
    sensor_type    varchar(60) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE
);
CREATE TABLE secchi.variable (
    variable_id smallint    NOT NULL PRIMARY KEY,
    variable    varchar(60) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE
);

-- The big one. Loaded as a heap, then given a clustered columnstore index
-- (02_after_load.sql), which compresses it and suits month-by-month scans.
CREATE TABLE secchi.obs (
    record_uuid    uniqueidentifier NOT NULL,
    site_id        smallint         NOT NULL,
    sensor_type_id smallint         NOT NULL,
    ts             datetime2(0)     NOT NULL,   -- naive logger time, see docs/data-dictionary.md
    variable_id    smallint         NOT NULL,
    value          float            NULL
);

-- The rest move as they are in the store: same columns, same names.
-- Fixing their shape is a separate, later change.
CREATE TABLE secchi.usgs (
    uuid            varchar(64)    NOT NULL,
    site            varchar(120)   NULL,
    site_number     varchar(20)    NULL,
    sensor_type     varchar(40)    NULL,
    ts              datetimeoffset(0) NOT NULL,
    lat             float          NULL,
    lng             float          NULL,
    variable        varchar(20)    NOT NULL,
    statistic_id    varchar(20)    NULL,
    statistic       varchar(40)    NULL,
    value           float          NULL,
    unit_of_measure varchar(20)    NULL,
    approval_status varchar(40)    NULL,
    qualifier       int            NULL
);
CREATE TABLE secchi.snotel (
    uuid varchar(80) NOT NULL, site varchar(120) NULL, sensor_type varchar(40) NULL,
    ts datetime2(0) NOT NULL, lat float NULL, lng float NULL,
    variable varchar(20) NOT NULL, value float NULL, unit varchar(20) NULL
);
CREATE TABLE secchi.asos (
    uuid varchar(80) NOT NULL, site varchar(120) NULL, sensor_type varchar(40) NULL,
    ts datetime2(0) NOT NULL, lat float NULL, lng float NULL,
    variable varchar(20) NOT NULL, value float NULL, unit varchar(20) NULL
);
CREATE TABLE secchi.smoke (
    uuid varchar(80) NOT NULL, site varchar(120) NULL, sensor_type varchar(40) NULL,
    ts datetime2(0) NOT NULL, lat float NULL, lng float NULL,
    variable varchar(40) NOT NULL, value float NULL
);
CREATE TABLE secchi.assets (
    uuid varchar(64) NOT NULL, site varchar(120) NULL, sensor_type varchar(40) NULL,
    ts datetime2(6) NOT NULL, lat float NULL, lng float NULL,
    kind varchar(20) NULL, ref varchar(400) NULL, total_available bigint NULL
);
CREATE TABLE secchi.terc (
    station varchar(10) COLLATE Latin1_General_100_CI_AS_SC_UTF8 NOT NULL,  -- same collation as the note,
    date_time_local datetime2(0) NOT NULL,                                  -- so the key can join them
    secchi_m float NULL, disappear_m float NULL, reappear_m float NULL,
    viewing_condition int NULL,
    lake_condition varchar(1000) COLLATE Latin1_General_100_CI_AS_SC_UTF8 NULL  -- one note has non-ASCII
);
CREATE TABLE secchi.cssl_climo (
    water_year int NOT NULL PRIMARY KEY, snowfall_cm float NULL,
    max_depth_cm int NULL, snowfall_hs_cm float NULL
);
-- Polygons arrive as WKT text and become geometry after the load.
CREATE TABLE secchi.watershed_stage (
    watershed_id int NOT NULL, name varchar(120) NULL, area_km float NULL, wkt varchar(max) NOT NULL
);
CREATE TABLE secchi.watershed (
    watershed_id int NOT NULL PRIMARY KEY, name varchar(120) NULL, area_km float NULL,
    shape geometry NOT NULL
);
GO
