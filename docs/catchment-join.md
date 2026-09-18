# The catchment join

The step that turns an observation into an explanation.

We can currently say: *Glenbrook 2 reads 42 % soil moisture while every
upland station around it reads 3.5 %.* True, and useless on its own. With
catchment attributes attached we can say *why*.

## How it works

`pixi run reference` caches three files into `data/reference/`:

| File | Source |
|---|---|
| `tahoe_watersheds.geojson` | Catchment polygons — static file on TEON's domain |
| `watershed_attributes.json` | ~168 variables per catchment |
| `watershed_variables.json` | The variable dictionary (labels, units) |

Static datasets, fetched once, never on the cron. Each is validated as
JSON *before* being written, so a cached file is never a half-written
response or an HTML error page.

Then `pixi run catchment-join` reads the station coordinates already in
`web/assets/latest.json`, assigns each to its containing catchment, and
prints the attributes that would attach.

## Why the geometry is hand-rolled

Point-in-polygon is implemented directly in
`src/secchi/sources/reference.py` rather than via shapely or geopandas.
Those are the right tools for real geospatial work and belong in the `geo`
pixi feature — but a ray-casting test against a few dozen polygons needs
no dependency, and keeping the default environment light matters when it
has to solve on three platforms and install on every CI run.

Three details that are easy to get wrong and are tested:

- **Vertex crossings.** The `(y1 > lat) != (y2 > lat)` guard stops a ray
  passing exactly through a vertex being counted twice.
- **Holes.** GeoJSON polygons can have interior rings. A point inside the
  outer ring *and* inside a hole is outside the polygon.
- **Nesting.** If a point falls in more than one catchment, the smallest
  by bounding-box area wins — the more specific catchment is the intended
  one.

Bounding boxes are checked before the ring test, since most candidates are
ruled out that way and the ring test is the expensive part.

Verified against synthetic geometry covering all three cases plus
multipolygons, and against the real Glenbrook Creek attribute record.

## What it should show for Glenbrook 2

From the live attributes endpoint, the Glenbrook Creek catchment:

| Variable | Value | Meaning |
|---|---|---|
| `PrecipAvg` | 689 mm | annual precipitation |
| `TWID8Avg` | 10.9 | Topographic Wetness Index |
| `DWaterAvg` | **178 cm** | depth to water table — among the shallowest in the basin |
| `Meadow` | 5.8 % | percent meadow |
| `ValleyBott` | 19.4 % | percent valley bottoms |

That's the beginning of a real account of the 42 % reading: a
valley-bottom site with meadow cover and a shallow water table, rather
than an anomaly to be flagged.

**A caveat on resolution.** Glenbrook 2 and Glenbrook 5 both sit in the
Glenbrook Creek catchment, so the join gives them *identical* attributes —
yet they read 42 % and 3.5 % soil moisture respectively. Catchment
averages explain between-catchment variation; they cannot explain
within-catchment variation. For that you would need the underlying rasters
sampled at the station point, not the catchment mean. Worth being clear
about: this join is a real step forward and it is not the whole answer.

## What it also fixes

`USGS_BBOX` is a rectangle standing in for a watershed, and the first
discover run caught fourteen active stations draining to the Carson River
or the Truckee below the outlet. With polygons cached, station membership
becomes point-in-polygon and `USGS_OUT_OF_BASIN` can be retired.

TEON even labelled one catchment *"Tahoe State Park (drains into Truckee
River not Tahoe)"* — they marked the exception for us.

## Variables deliberately excluded

`WATERSHED_UNUSABLE_VARIABLES` in config records six, with reasons. The
worst:

- **`pHAvg`** reads **1.79** at Cave Rock and 2.61 at Glenbrook Creek. Soil
  pH of 1.8 does not occur in a Sierra granite catchment; the huge
  companion standard deviations indicate nodata cells averaged in as
  zeros.
- **`AETAvg` / `PETavg`** are ~1.3–2.0 with units given as mm. Annual
  actual evapotranspiration here is several hundred mm, so either the unit
  is wrong or these are daily means. The relative pattern between
  catchments may hold; the absolute values do not.

And `Marlette Creek` is excluded from TWI analysis specifically: it reads
`TWID8Avg` 833.9 against a 5–55 range everywhere else, because the
catchment contains Marlette Lake and the wetness index diverges over
standing water.
