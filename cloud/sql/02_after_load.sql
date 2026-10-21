-- ============================================================================
-- 02_after_load.sql: indexes and spatial columns, once the rows are in.
-- Building indexes after a bulk load is faster than loading into them.
-- ============================================================================
SET NOCOUNT ON;

-- Compress the 12 million readings. Typically several times smaller than
-- the heap, and scans read only the columns a query names.
CREATE CLUSTERED COLUMNSTORE INDEX cci_obs ON secchi.obs;

CREATE CLUSTERED INDEX cx_usgs   ON secchi.usgs (ts, uuid);
CREATE CLUSTERED INDEX cx_snotel ON secchi.snotel (ts, uuid);
CREATE CLUSTERED INDEX cx_asos   ON secchi.asos (ts, uuid);
CREATE CLUSTERED INDEX cx_smoke  ON secchi.smoke (ts, uuid);
CREATE CLUSTERED INDEX cx_assets ON secchi.assets (ts, uuid);
CREATE CLUSTERED INDEX cx_terc   ON secchi.terc (date_time_local, station);

-- Station points: geography, so distances come out in metres on the
-- ellipsoid.
ALTER TABLE secchi.site ADD location geography NULL;
GO
UPDATE secchi.site SET location = geography::Point(lat, lng, 4326);

-- Catchments: geometry in SRID 4326. Planar maths on degrees is fine for
-- "which polygon contains this point"; MakeValid repairs any ring that
-- isn't strictly valid instead of failing the load.
INSERT INTO secchi.watershed (watershed_id, name, area_km, shape)
SELECT watershed_id, name, area_km, geometry::STGeomFromText(wkt, 4326).MakeValid()
FROM secchi.watershed_stage;
DROP TABLE secchi.watershed_stage;
CREATE SPATIAL INDEX sx_watershed ON secchi.watershed (shape)
    WITH (BOUNDING_BOX = (-120.4, 38.8, -119.8, 39.4));
GO

-- Which catchment each TEON site sits in, worked out by SQL Server. The
-- store does the same join in Python (pixi run catchment-join); comparing
-- the two is a third check that the geometry survived the move. Where
-- catchments overlap, the smaller bounding box wins, as in the Python.
CREATE VIEW secchi.site_watershed AS
SELECT s.site_id, s.site, w.watershed_id, w.name AS watershed
FROM secchi.site AS s
OUTER APPLY (SELECT TOP (1) w.watershed_id, w.name
             FROM secchi.watershed AS w
             WHERE w.shape.STContains(geometry::Point(s.lng, s.lat, 4326)) = 1
             ORDER BY w.shape.STEnvelope().STArea(), w.watershed_id) AS w;
GO
