# The watershed characteristics layer

TEON's map has two layers we don't: `watershed` and `usgs`, reachable at

    https://tahoeenvironmentalobservatorynetwork.org/teon-network?view=map&layers=watershed,usgs

The watershed layer is stream catchment boundaries symbolised by any of
roughly **180 climate and landscape attributes**, with a transparency
slider and an animated stream-flow overlay.

## Why this is the most valuable thing left to get

The attribute list is not decorative. It contains, among others:

**Climate forcing** — annual precipitation, actual evapotranspiration,
potential ET, climatic water deficit, mean annual temperature, runoff,
each with a standard deviation.

**Fire history** — percent burned, and percent burned broken out by year
for 2001, 2002, 2003, 2006, 2007, 2014, 2016 and 2021.

**Terrain** — average slope, curvature, elevation range, relief ratio,
hypsometric integral, Topographic Wetness Index, Topographic Position
Index at two radii, Vector Ruggedness Measure, Heat Load Index, Wind
Exposure Index, Stream Power Index.

**Soil** — available water capacity, bulk density, cation exchange
capacity, depth to bedrock, depth to water table, saturated hydraulic
conductivity, percent sand/silt/clay, organic matter, pH.

**Land cover and change 1986–2020** — tree cover, shrub, bare ground,
annual and perennial forb/grass, litter, impervious surface, each with a
change value and a standard deviation.

**Stream network** — Strahler order lengths and percentages first through
sixth, drainage density, total stream length, modelled velocity, percent
active/inactive floodplain, percent meadows.

Three reasons this matters more than another sensor:

1. **It supplies the forcing variable we lack.** The transect asks whether
   two stations on opposite shores respond differently to the same storm.
   Right now we can measure response but not the storm — TEON's only
   precipitation gauge is dormant. Catchment annual precipitation and
   climatic water deficit give the climatological context directly.

2. **It makes "why" answerable.** We can currently say Glenbrook 2 reads
   42 % soil moisture while its neighbours read 3.5 %. With catchment
   attributes we could say *because* it sits in a valley bottom with a high
   Topographic Wetness Index, shallow depth to water table and a percent-
   meadow value its neighbours don't have. That's the difference between
   noticing an outlier and explaining it.

3. **The fire-year columns are the wildfire feature.** Per-catchment burn
   history by year, joined to stream chemistry and lake chlorophyll, is a
   real test of the smoke-and-ash mechanism TEON describes — smoke shading
   the lake surface, blocking UV, potentially triggering algal blooms, and
   ash delivering nutrients.

## What's needed

The endpoint. The attribute names look like a StreamCat-style derived
product, but whether it's served from TEON's own API, an ArcGIS
FeatureServer, or static vector tiles determines how we consume it.

**DevTools on that map URL**, Network tab, filter Fetch/XHR, then toggle
the watershed layer on. Look for whichever of these appears:

- `rftazkiysf.us-west-2.awsapprunner.com/api/...` — their own API, same
  pattern we already know how to read
- `services*.arcgis.com/.../FeatureServer/...` — an ArcGIS feature
  service, which supports GeoJSON export and attribute queries directly
- a `.geojson`, `.pbf` or `.mvt` request — static vector tiles
- `{z}/{x}/{y}` in the path — tiled, which needs different handling

Paste the URL and the shape of one response.

## How it would be consumed

Once the source is known, the plan is the same either way:

1. Fetch catchment polygons once and cache them — boundaries don't change,
   so this is not cron work. Store as GeoJSON under `data/reference/`.
2. Keep only the attributes we'd actually symbolise, since 180 columns is
   a lot of payload for a web map. Precipitation, climatic water deficit,
   percent burned by year, TWI, depth to water table, percent meadow,
   tree-cover change is a defensible starting set.
3. Add a choropleth layer to the Leaflet map with an attribute selector
   and a transparency control, matching what TEON offers.
4. Join catchment attributes to our station points by containment, so each
   forest logger and stream gauge carries its own catchment's
   characteristics. That's the join that makes the "why" question
   answerable.

Step 4 is the one with scientific value; the rest is plumbing.
