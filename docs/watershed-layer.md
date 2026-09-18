# The watershed characteristics layer

**Found, and read.** The sniff turned up three endpoints, and the answer was
better than any of the four cases I'd predicted — not ArcGIS, not vector
tiles, just a static GeoJSON plus two plain JSON APIs.

| Endpoint | Contents |
|---|---|
| `tahoeenvironmentalobservatorynetwork.org/data/TahoeWatersheds.json` | Catchment polygons, static file on their own domain |
| `rftazkiysf…/api/watersheds/variables` | 168 variable definitions — key → label + units |
| `rftazkiysf…/api/watersheds/attributes` | The data: one record per catchment, ~168 fields each |

No key, no auth, no tiles. The `variables` endpoint is a proper data
dictionary, which is more than TEON provides for its own sensors.

---

## The headline: the rain shadow is real and it is 3×

This was the whole point of the transect, and the catchment data settles it
without needing a single storm to arrive.

Annual precipitation (`PrecipAvg`, mm) across 39 catchments:

```
  Watson Creek        1459   NW shore, wettest
  Burton Creek        1283
  Carnelian Bay       1267
  Cedar Flats         1246
  ...
  Glenbrook Creek      689   <-- the transect's east anchor
  ...
  Bijou East           579
  Cave Rock            555
  Deadman Point        482   E shore, driest
```

**3.03× from wettest to driest, across about twenty kilometres of lake.**

That reframes the transect result from earlier in this project. Homewood
and Glenbrook 5 read essentially identical soil moisture (3.5 % each) and
air temperature — and the honest read was "no rain in weeks, nothing to
detect". Which is right. But the *climatology* is not identical at all:
these two stations sit in catchments receiving very different annual
precipitation. When the first atmospheric river lands, they should diverge,
and now there's a number to predict the divergence against rather than
just a hypothesis.

---

## Variables worth wiring in first

Of 168, these do specific work for this project:

**Forcing** — the gap TEON's dormant precipitation gauge left
- `PrecipAvg` / `PrecipStDe` — annual precipitation, mm
- `CWDAvg` — climatic water deficit
- `AETAvg`, `PETavg` — actual and potential evapotranspiration
- `TempAvg` — mean annual temperature
- `Runoff`

**Explaining Glenbrook 2's 42 % soil moisture** — the outlier we could
only flag, not account for
- `TWID8Avg` — Topographic Wetness Index
- `DWaterAvg` — depth to water table (cm)
- `Meadow` — percent meadow
- `ValleyBott` — percent valley bottoms
- `TotalFlood` / `ActiveFloo` — floodplain extent
- `KsatAvg` — saturated hydraulic conductivity

Glenbrook Creek's catchment reads `Meadow` 5.8 %, `DWaterAvg` 178 cm (among
the shallowest water tables in the basin) and `ValleyBott` 19.4 %. That is
the beginning of a real explanation rather than "it's wet and its
neighbours aren't".

**The wildfire feature** — burn history by individual year
- `TotalFire`, and `Fire2001` … `Fire2021`
- Only three catchments show any burn at all: Tahoe Vista (11.3 % in 2003),
  Edgewood Creek (15.4 %, almost all 2002), Burke Creek (0.2 % in 2001).
  So the per-year columns are sparse — enough to test a mechanism on
  Edgewood, not enough for a basin-wide analysis.

**Fixing the bounding box** — `USGS_BBOX` is a rectangle standing in for a
watershed, and the first discover run caught fourteen stations draining to
the Carson River or the Truckee below the outlet. With polygons, station
membership becomes point-in-polygon. One catchment is even labelled
*"Tahoe State Park (drains into Truckee River not Tahoe)"* — they've
already marked the exception for us.

---

## Data-quality problems to handle before trusting any of it

Five, found by reading the values rather than assuming they were clean.

**1. Soil pH is unusable in some catchments.** `pHAvg` reads **1.79** at
Cave Rock, 2.61 at Glenbrook Creek, 2.74 at Mill Creek. A soil pH of 1.8
is essentially battery acid and does not occur in a Sierra granite
catchment. The accompanying `pHStDev` values are enormous (2.7–3.1), which
is the signature of nodata cells being averaged in as zeros. **Do not
display soil pH.** Same caution for `CECAvg`/`ECECAvg`, which show 0.0 in
several catchments.

**2. One Topographic Wetness Index value is wild.** Marlette Creek reads
`TWID8Avg` **833.9** against a 5–55 range everywhere else. That catchment
contains Marlette Lake (`OpenWaterA` 0.12), and TWI diverges over standing
water. Needs clipping or exclusion, and it happens to sit in one of the
variables I most want to use.

**3. Evapotranspiration units don't match the label.** `AETAvg` and
`PETavg` are ~1.3–2.0 with units given as mm. Annual actual ET in this
climate is several hundred mm. These are presumably daily means or a
different unit entirely. `CWDAvg` (~4.7–6.3 mm) has the same problem.
**Treat the ET and CWD units as unverified** — the relative pattern between
catchments is probably still meaningful, the absolute values are not.

**4. Field order varies between records.** `Fire2007`, `BedrockAvg`,
`BDStDev`, `DBedAvg`, `DWaterAvg` and the `PFG`/`Shrub`/`Tree` block appear
*after* the main body in each record rather than in schema order, and
`Fire2007` is missing from the early field list entirely. Parse by key,
never by position.

**5. Label typos in their own dictionary.** "Perimter in meters", "coarse
fragements", "Percent percent shrub", doubled spaces in "Percent  first
order streams" and "Litter  standard deviation". Cosmetic, but a reminder
this is a research product rather than a polished API. Several unit strings
are visibly wrong too: `Area_Mi` is given as `mi3`, `Area_M` as `m3`
(both should be squared), and `CECStDev`/`ECECAvg`/`ECECStDev` carry
incrementing exponents `100g-2`, `100g-3`, `100g-4` that look like a
copy-paste artifact.

---

## Integration plan

1. **Cache the polygons once.** `TahoeWatersheds.json` is a static file and
   catchment boundaries don't change — fetch to `data/reference/`, commit,
   never put it on the cron.
2. **Cache the attributes and the dictionary** the same way, with a
   refresh task rather than an hourly pull.
3. **Keep a curated subset** for display. 168 columns is a lot of payload
   for a web map; the lists above are a defensible starting set, minus the
   variables flagged unusable.
4. **Choropleth layer on the Leaflet map** with an attribute selector and
   opacity control, matching what TEON offers. Precipitation is the obvious
   default given it's the story.
5. **Point-in-polygon join** so every station and gauge carries its own
   catchment's attributes. This is the step with scientific value — it's
   what turns "Glenbrook 2 is an outlier" into "Glenbrook 2 is a
   valley-bottom meadow site with a shallow water table". Everything before
   it is plumbing.
6. **Replace `USGS_BBOX`** with polygon membership, and drop
   `USGS_OUT_OF_BASIN` once the geometry decides it.

Step 5 is the one worth doing properly. Steps 1–3 are an afternoon.
