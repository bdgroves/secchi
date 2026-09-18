"""Configuration for secchi.

The TEON backend is a REST API hosted at an AWS App Runner container. The
frontend at https://tahoeenvironmentalobservatorynetwork.org/ talks to it
via the endpoints defined below. Discovered by network inspection on
2026-09-17; endpoint may move — update ``TEON_API_BASE`` if it does.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# TEON backend
# ---------------------------------------------------------------------------

TEON_API_BASE = "https://rftazkiysf.us-west-2.awsapprunner.com/api"

TEON_ENDPOINTS = {
    "locations":         "/sensors/locations",
    "aquatic_metadata":  "/aquatic-metadata/",
    "site_visibility":   "/site-visibility/disabled",
    "calibration":       "/calibration/events",
}

# Sensor-type display names (the keys in /sensors/locations) → candidate URL
# slugs for /sensors/{slug}, in priority order.
#
# TEON's convention: the display label is truncated to its leading concept
# and kebab-cased, not transliterated in full.
#   "Air Temperature & Relative Humidity" → air-temperature
#   "Tree stress and growth"              → tree-stress
#   "Soil Environmental Conditions"       → soil-moisture
#
# All six live types confirmed by probe on 2026-09-17. The three dormant
# types (Minidot, Hobo, Stream Chemistry, Precipitation Gauge) are
# unconfirmed — they had no live sensors to probe against.
SENSOR_TYPE_SLUGS: dict[str, tuple[str, ...]] = {
    "EXO": ("exo-sensor",),                                       # ✓
    "Field Camera": ("field-camera",),                            # ✓
    "Stream Level": ("stream-level",),                            # ✓
    "Tree stress and growth": ("tree-stress",),                   # ✓
    "Air Temperature & Relative Humidity": ("air-temperature",),  # ✓
    "Soil Environmental Conditions": ("soil-moisture",),          # ✓
    # Dormant types, resolved by the 2026-09-18 all-types probe. These
    # have no live sensors but the API still serves their history.
    "Stream Chemistry": ("stream-chemistry",),                  # ✓
    "Precipitation Gauge": ("precipitation-gauge",),             # ✓
    "Hobo": ("hobo-sensor",),                                    # ✓
    "Minidot": ("minidot-sensor",),                              # ✓
    # All ten sensor types now resolve. The convention held exactly:
    # "{name}-sensor" with the display name NOT word-split.
}

# Display name → stable value stored in snapshots, so the parquet history
# doesn't churn if TEON relabels a display name.
SENSOR_TYPE_CANONICAL: dict[str, str] = {
    "EXO": "ExoSensor",
    "Field Camera": "FieldCamera",
    "Stream Level": "StreamLevel",
    "Tree stress and growth": "TreeStressAndGrowth",
    "Air Temperature & Relative Humidity": "AirTemperatureRelativeHumidity",
    "Soil Environmental Conditions": "SoilEnvironmentalConditions",
    "Stream Chemistry": "StreamChemistry",
    "Precipitation Gauge": "PrecipitationGauge",
    "Minidot": "MiniDotSensor",
    "Hobo": "HoboSensor",
}

# ---------------------------------------------------------------------------
# Record structure
# ---------------------------------------------------------------------------

# Present in every record: identity and geometry, not measurements.
RECORD_META_FIELDS = frozenset({
    "uuid", "site", "latitude", "longitude", "TIMESTAMP",
    "kor_site_name",
})

# Non-numeric measurement fields. These carry asset references rather than
# values and are routed to a separate frame (see transform.to_asset_frame),
# because forcing them into the numeric time series breaks the parquet write.
#
# CAMERA IMAGERY IS NOT PUBLICLY REACHABLE. Probed 2026-09-18
# (`pixi run camera-probe`): every standard S3 HTTPS form returns 403
# AccessDenied in us-west-2, and us-east-1 returns 301 PermanentRedirect —
# which incidentally confirms the bucket lives in us-west-2. Anonymous
# reads are blocked by bucket policy.
#
# Note AccessDenied is also what S3 returns for a missing key when
# s3:ListBucket is denied, so this does NOT prove the objects exist; it
# proves only that we cannot read them. A snowpack time-lapse needs either
# a TEON-side proxy endpoint (look for one in DevTools on a camera detail
# page that actually renders an image) or TEON granting public reads or
# presigned URLs.
ASSET_FIELDS = frozenset({"image"})

# Logger diagnostic channels, shared across every sensor type served by the
# same Campbell logger. Recorded but never surfaced on the dashboard.
DIAGNOSTIC_FIELDS = frozenset({"BattV_Avg", "BattV_Min", "PTemp_C_Avg"})

# ---------------------------------------------------------------------------
# Variable metadata, per canonical sensor type
# ---------------------------------------------------------------------------
# Keys: field name as it appears in the record.
#   label     — display name
#   units     — display units (after `scale`, if any)
#   scale     — multiply raw value for display (e.g. VWC 0.057 → 5.7 %)
#   clip_low  — floor the displayed value (optical channels read slightly
#               negative near their detection limit)
#   hide      — recorded but not surfaced on the dashboard
#   note      — data-quality caveat, carried into the snapshot
#
# NOTE ON SHARED LOGGERS: at Glenbrook 1/2/4/5, Homewood and UNR Tahoe
# Campus, the air-temperature, soil-moisture and tree-stress endpoints are
# three projections of ONE logger table — same record uuid, same
# BattV_Avg/PTemp_C_Avg, same total row count. Deduping on (uuid, variable)
# in the transform layer collapses the shared channels correctly.

SENSOR_VARIABLES: dict[str, dict[str, dict]] = {
    "ExoSensor": {
        "Temp":                  {"label": "Water temp",  "units": "°C"},
        "Chl_a":                 {"label": "Chlorophyll", "units": "µg/L", "clip_low": 0.0},
        "Do_percent":            {"label": "DO sat",      "units": "%"},
        "Do_mgL":                {"label": "DO",          "units": "mg/L", "clip_low": 0.0},
        "Turbidity":             {"label": "Turbidity",   "units": "FNU",  "clip_low": 0.0,
                                  "note": "Sunnyside reads as low as -1.98 FNU raw — likely a "
                                          "calibration offset, not noise. Clipped for display."},
        "phycocyanin":           {"label": "Phycocyanin", "units": "µg/L", "clip_low": 0.0},
        "specific_conductivity": {"label": "Conductivity","units": "µS/cm"},
        "Salinity":              {"label": "Salinity",    "units": "ppt"},
        "TDS":                   {"label": "TDS",         "units": "mg/L"},
        "depth":                 {"label": "Depth",       "units": "m"},
        "pH":                    {"label": "pH",          "units": "", "hide": True,
                                  "note": "Unusable fleetwide — returns 0 at Glenbrook, "
                                          "null at Sunnyside."},
        "TSS":                   {"label": "TSS",         "units": "mg/L", "hide": True,
                                  "note": "Always null — lab-only field, not sonde-measured."},
    },

    "AirTemperatureRelativeHumidity": {
        "Air_Temp": {"label": "Air temp",  "units": "°C"},
        "RH":       {"label": "Humidity",  "units": "%"},
    },

    "SoilEnvironmentalConditions": {
        # Two depths instrumented at every station observed so far; the
        # _3.._5 channels exist in the schema but return null everywhere.
        "Soil_VWC":   {"label": "Soil moisture",    "units": "%",    "scale": 100.0},
        "Soil_T":     {"label": "Soil temp",        "units": "°C"},
        "Soil_EC":    {"label": "Soil EC",          "units": "dS/m"},
        "Soil_VWC_2": {"label": "Soil moisture (2)","units": "%",    "scale": 100.0},
        "Soil_T_2":   {"label": "Soil temp (2)",    "units": "°C"},
        "Soil_EC_2":  {"label": "Soil EC (2)",      "units": "dS/m"},
        # Depths 3–5: present in schema, null in all observed data.
        "Soil_VWC_3": {"label": "Soil moisture (3)","units": "%", "scale": 100.0, "hide": True},
        "Soil_T_3":   {"label": "Soil temp (3)",    "units": "°C", "hide": True},
        "Soil_EC_3":  {"label": "Soil EC (3)",      "units": "dS/m", "hide": True},
        "Soil_VWC_4": {"label": "Soil moisture (4)","units": "%", "scale": 100.0, "hide": True},
        "Soil_T_4":   {"label": "Soil temp (4)",    "units": "°C", "hide": True},
        "Soil_EC_4":  {"label": "Soil EC (4)",      "units": "dS/m", "hide": True},
        "Soil_VWC_5": {"label": "Soil moisture (5)","units": "%", "scale": 100.0, "hide": True},
        "Soil_T_5":   {"label": "Soil temp (5)",    "units": "°C", "hide": True},
        "Soil_EC_5":  {"label": "Soil EC (5)",      "units": "dS/m", "hide": True},
    },

    "TreeStressAndGrowth": {
        # Band dendrometers — eight trees per station. Stem diameter both
        # grows seasonally and swells/shrinks diurnally with water content,
        # so this doubles as a drought-stress signal.
        f"Tree_{n}_diameter_change": {
            "label": f"Tree {n} stem Δ",
            "units": "µm",
            "note": "Units inferred from magnitude (~1,000–3,000); not documented by TEON.",
        }
        for n in range(1, 9)
    },

    "StreamLevel": {
        "Uncalibrated_water_depth": {
            "label": "Stream depth",
            "units": "m",
            "note": "Field is named 'Uncalibrated' upstream. Glenbrook 2 reads ~0.001 m — "
                    "either a dry channel or a pending datum correction. Treat as relative.",
        },
    },

    "FieldCamera": {
        # `image` is an asset reference, handled via ASSET_FIELDS rather than
        # as a numeric variable. No numeric channels on this sensor.
    },
}

# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

# Lake EXO sondes on autonomous telemetry.
LIVE_EXO_SITES: tuple[str, ...] = ("Sunnyside", "Glenbrook", "4H Camp")

# Terrestrial logger stations, in west→east display order.
TERRESTRIAL_STATIONS: tuple[str, ...] = (
    "Homewood",
    "Blackwood 2",
    "UNR Tahoe Campus",
    "Glenbrook 1",
    "Glenbrook 2",
    "Glenbrook 4",
    "Glenbrook 5",
)

SITE_METADATA: dict[str, dict] = {
    # Lake
    "Sunnyside":   {"shore": "west",  "lat": 39.134643,   "lng": -120.151085},
    "Glenbrook":   {"shore": "east",  "lat": 39.088329,   "lng": -119.941133},
    "4H Camp":     {"shore": "south", "lat": 38.974209,   "lng": -119.951608},
    "Blackwood 3": {"shore": "west",  "lat": 39.10763333, "lng": -120.1577667,
                    "note": "Manual EXO — offline since 2026-07-09"},
    "Meeks":       {"shore": "west",  "lat": 39.03773333, "lng": -120.1211667,
                    "note": "Manual EXO — offline since 2026-07-09"},
    # Terrestrial / stream
    "Homewood":         {"shore": "west", "lat": 39.0752101,  "lng": -120.1842931},
    "Blackwood 2":      {"shore": "west", "lat": 39.111167,   "lng": -120.186889,
                         "note": "Offline since 2026-06-18"},
    "UNR Tahoe Campus": {"shore": "north","lat": 39.243064,   "lng": -119.940336},
    "Glenbrook 1":      {"shore": "east", "lat": 39.0881,     "lng": -119.9391},
    "Glenbrook 2":      {"shore": "east", "lat": 39.08588889, "lng": -119.9220278},
    "Glenbrook 4":      {"shore": "east", "lat": 39.09344,    "lng": -119.9015},
    "Glenbrook 5":      {"shore": "east", "lat": 39.07463889, "lng": -119.89125},
}

# The east–west transect pair. Homewood (west shore, 39.07521 N) and
# Glenbrook 5 (east shore, 39.07464 N) sit just 64 m apart in latitude —
# effectively the same line of latitude, on opposite sides of the lake.
# Same storms, same sun angle, opposite sides of the Sierra rain shadow.
#
# Two earlier candidates were rejected:
#   * Blackwood 2, the original idea, is 4 km further north and has been
#     offline since 2026-06-18.
#   * Glenbrook 2 is 1,189 m off Homewood's latitude AND is a riparian
#     microsite — it reads 42 % soil moisture while every upland station
#     around it reads 3.5–11.5 %, and it's the one station that also
#     carries a stream gauge. Pairing it with Homewood measured
#     "streambank vs. hillslope", not "wet side vs. rain shadow".
#
# Both halves of the current pair are upland hillslope sites, which is
# what makes the comparison mean what it claims to mean.
TRANSECT_PAIR: tuple[str, str] = ("Homewood", "Glenbrook 5")

# Transect readings must come from the same moment to be comparable. Air
# temperature and humidity swing hard over a diurnal cycle, so pairing each
# site's *latest* reading — which is what the first version of this did —
# displayed a time-of-day artifact as a geographic signal. The transect
# builder instead finds the most recent timestamp both sites share.
# Loggers report on a 15-minute grid, so exact matches are the norm; this
# tolerance covers a site whose clock has drifted off the grid.
TRANSECT_ALIGN_TOLERANCE_MINUTES = 20

# A sensor is "live" if its last_update is within this window.
LIVE_WINDOW_HOURS = 24

# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

# TEON emits naive ISO 8601 with no offset ("2026-09-17T08:15:00"). The
# evidence says these are Pacific local (the loggers' wall clock), not UTC:
#
#   * UNR Tahoe Campus reported last_update 08:15 while the observed
#     freshness at ~16:15 PDT was 8 hours, which only works if 08:15 is
#     local. As UTC it would have been 15 hours.
#   * Diurnal shape agrees: logger panel temperature bottoms out around
#     05:00-06:00 in these timestamps, which is pre-dawn local, and air
#     temperature climbs through the 08:00-14:00 range with falling RH.
#
# This is an inference, not documentation. If TEON confirms otherwise,
# change this one constant — everything downstream derives from it.
TEON_TIMEZONE = "America/Los_Angeles"


# ===========================================================================
# USGS Water Data OGC APIs
# ===========================================================================
# The modernized USGS endpoints, which replace the legacy
# waterservices.usgs.gov/nwis/iv/ service USGS is retiring. Full endpoint
# mapping and rationale in docs/usgs-plan.md.

USGS_API_BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"

USGS_COLLECTIONS = {
    "latest_continuous":     "latest-continuous",
    "continuous":            "continuous",
    "daily":                 "daily",
    "monitoring_locations":  "monitoring-locations",
    "time_series_metadata":  "time-series-metadata",
    "parameter_codes":       "parameter-codes",
}

# Environment variable holding the API key. Without one USGS allows 50
# requests/hour per IP; with one, 1000. Free from
# https://api.waterdata.usgs.gov/signup/ — in CI this comes from a repo
# secret of the same name.
USGS_API_KEY_ENV = "USGS_API_KEY"

# Features per request. USGS caps `limit` at 10000; default is 10.
USGS_PAGE_LIMIT = 1000

# USGS statistic codes. A single parameter at a single gauge is served
# under several of these — Blackwood publishes water temperature as max,
# min, mean, median AND instantaneous. Continuous monitoring wants the
# instantaneous series; the aggregates belong to the /daily endpoint.
#
# Confirmed present in the 2026-09-17 probe output.
USGS_STATISTICS: dict[str, str] = {
    "00001": "max",
    "00002": "min",
    "00003": "mean",
    "00008": "median",
    "00011": "instantaneous",
    "32400": "observation at 2400",
}

# The series we ingest by default. Everything else is a daily aggregate
# and would collide with the instantaneous value if mixed into one
# variable — see the statistic_id handling in sources/usgs.py.
USGS_STATISTIC_INSTANTANEOUS = "00011"

# Parameters that only exist as a non-instantaneous statistic, and so are
# invisible to a request pinned to 00011.
#
# Found 2026-09-18: `70372` (fine sediment particle load) is published as
# statistic 00006 (Sum), because a load is a daily total rather than a
# spot reading. Pinning statistic_id=00011 meant the single most
# regulation-relevant series at the lake's largest tributary would never
# have been fetched — a silent omission, not an error.
#
# These statistics are added to the request alongside 00011. The
# transform layer already keys on (parameter, statistic), so they cannot
# collide with instantaneous values, and the card builder labels anything
# non-instantaneous.
USGS_EXTRA_STATISTICS: tuple[str, ...] = ("00006",)

# Bounding box for discovery: the Lake Tahoe basin, generously drawn.
# (minLon, minLat, maxLon, maxLat) — the order OGC API - Features expects.
# `pixi run usgs-discover` queries /monitoring-locations against this to
# find every USGS station in the basin, rather than us guessing site
# numbers. USGS runs seven major Tahoe drainages under LTIMP; hand-listing
# them is how you miss one.
# Tightened 2026-09-18 after the first discover run returned 1,277
# stations, many of them draining the wrong way: the east slope of the
# Carson Range (Kings Canyon, Ash Canyon, Franktown, Ophir, Davis, Winters,
# Steamboat) flows to the Carson River, and Donner Lake and the Truckee
# below Tahoe City are downstream of the lake, not tributary to it.
#
# A rectangle cannot express a watershed — this is an approximation that
# happens to fit Tahoe reasonably well. The real fix is the catchment
# polygon layer (see docs/watershed-layer.md); once we have it, station
# membership should be decided by point-in-polygon, not by bounding box.
USGS_BBOX = (-120.25, 38.90, -119.90, 39.28)

# Vertical datum offsets, in feet, for gauges whose published "gage height"
# is meaningless without one. Lake Tahoe's stage recorder sits on a datum
# 6,220.00 ft above the U.S. Bureau of Reclamation reference, so a 7.07 ft
# reading means the lake surface is at 6,227.07 ft. That is the number
# every agency actually quotes.
USGS_DATUMS: dict[str, dict] = {
    "10337000": {
        "parameter_code": "00065",
        "offset_ft": 6220.00,
        "label": "Lake elevation",
        "datum": "USBR datum (6,218.86 ft above NGVD 1929)",
        # Context lines the dashboard can show alongside the elevation.
        "reference_levels": {
            "natural rim": 6223.00,
            "legal maximum": 6229.10,
            "record high": 6231.26,   # 1907-07
            "record low": 6220.26,    # 1992-11-30
        },
    },
}

# Gauges to ingest. Parameter lists below were CORRECTED against
# /time-series-metadata on 2026-09-17 (`pixi run usgs-probe`) — these are
# confirmed present with an instantaneous (00011) series that is currently
# reporting, not guesses.
USGS_GAUGES: dict[str, dict] = {
    # ---- The lake itself, and the one channel out of it ----------------
    "10337000": {
        "name": "Lake Tahoe at Tahoe City",
        "role": "lake",
        "shore": "west",
        "note": "Lake surface elevation. Gage height only — no water "
                "temperature series. Add the 6,220.00 ft datum (see "
                "USGS_DATUMS) for true elevation. The 32400 daily series "
                "runs to 1957-10-01, when the recorder was installed.",
        "parameters": ("00065",),
    },
    "10337500": {
        "name": "Truckee River at Tahoe City",
        "role": "outlet",
        "shore": "west",
        "note": "THE OUTLET — 510 ft below the dam, the single channel "
                "every drop leaving Lake Tahoe passes through, completely "
                "regulated by that dam. Daily mean discharge runs back to "
                "1895-07-01: 131 years of outflow. Pairs with 10337000 "
                "upstream — lake level, and the rate it is being released.",
        # 63160 confirmed by probe. An earlier guess of 63158 was wrong;
        # this gauge uses the same NAVD88 code Blackwood does.
        "parameters": ("00060", "00065", "63160"),
    },

    # ---- Tributaries: the LTIMP network --------------------------------
    # USGS has monitored Tahoe's major drainages since the late 1980s for
    # discharge, sediment and water quality, with real-time turbidity
    # added recently. Turbidity (63680) is the direct clarity measure and
    # the independent cross-check on TEON's in-lake sondes, so gauges
    # carrying it are flagged below.
    "10336610": {
        "name": "Upper Truckee River at South Lake Tahoe",
        "role": "tributary", "shore": "south",
        "note": "The largest tributary to Lake Tahoe, draining the south "
                "end of the basin. Carries real-time turbidity.",
        # 70369 is fine sediment particles 0.5–16 µm — the pollutant the
        # Tahoe TMDL regulates. At the lake's largest tributary, this is
        # arguably the single most clarity-relevant series in the project.
        "parameters": ("00060", "00065", "00010", "63680", "70369", "70372"),
    },
    "10336780": {
        "name": "Trout Creek near Tahoe Valley",
        "role": "tributary", "shore": "south",
        "note": "South-shore drainage, paired with the Upper Truckee. "
                "Carries real-time turbidity.",
        "parameters": ("00060", "00065", "00010", "00300", "63680"),
    },
    "10336645": {
        "name": "General Creek near Meeks Bay",
        "role": "tributary", "shore": "west",
        "note": "West shore. Largely undeveloped catchment, which makes it "
                "a useful reference against disturbed watersheds. Carries "
                "real-time turbidity.",
        "parameters": ("00060", "00065", "00010", "00300", "63160", "63680"),
    },
    "10336660": {
        "name": "Blackwood Creek near Tahoe City",
        "role": "tributary", "shore": "west",
        "note": "Daily mean discharge to 1960-10-01; instantaneous to "
                "1987. Long baseline TEON cannot provide, and the only "
                "current west-shore stream data while TEON's own Blackwood "
                "station is offline.",
        "parameters": ("00060", "00065", "00010", "63680", "00300", "63160"),
    },
    "10336676": {
        "name": "Ward Creek at Highway 89",
        "role": "tributary", "shore": "west",
        "note": "West shore, immediately south of Blackwood. Carries "
                "real-time turbidity.",
        "parameters": ("00060", "00065", "00010", "63160", "63680"),
    },
    "10336698": {
        "name": "Third Creek near Crystal Bay",
        "role": "tributary", "shore": "north",
        "note": "North shore. No turbidity series here.",
        "parameters": ("00060", "00065", "00010", "00300", "63160"),
    },
    "10336700": {
        "name": "Incline Creek near Crystal Bay",
        "role": "tributary", "shore": "north",
        "note": "North shore, paired with Third Creek. No turbidity series.",
        "parameters": ("00060", "00065", "00010", "00300", "63160"),
    },
    "10336730": {
        "name": "Glenbrook Creek at Glenbrook",
        "role": "tributary", "shore": "east",
        "note": "East shore, rain shadow. Pairs with TEON's Glenbrook 2 "
                "stream gauge. No turbidity series exists here — that "
                "measure is west- and south-shore only.",
        "parameters": ("00060", "00065", "00010", "00300"),
    },
    # 10336725 (Glenbrook at Old Hwy 50) is deliberately absent — two
    # series, both ending 2000-05-01, no instantaneous data. Historical.
}

# Stations the bbox catches that are NOT in the Tahoe basin. Recorded so
# the exclusion is a documented decision rather than an oversight.
USGS_OUT_OF_BASIN: dict[str, str] = {
    "10311100": "Kings Canyon Ck — Carson Range east slope, Carson River basin",
    "10311200": "Ash Canyon Ck — Carson Range east slope, Carson River basin",
    "10348460": "Franktown Ck — Washoe Valley, Carson River basin",
    "10348505": "Franktown Ck at Old US 395 — Washoe Valley",
    "10348520": "Ophir Ck — Washoe Valley",
    "10348550": "Davis Ck — Washoe Valley",
    "10348570": "Winters Ck — Washoe Valley",
    "10348800": "Little Washoe Lake — Washoe Valley",
    "10348801": "Steamboat Ck — Truckee Meadows",
    "10338000": "Truckee R nr Truckee — downstream of the Tahoe outlet",
    "10338400": "Donner Lake — Donner Ck / Truckee basin, not Tahoe",
    "10338500": "Donner Ck at Donner Lake — Donner basin",
    "10338700": "Donner Ck at Hwy 89 — Donner basin",
    "10337810": "NF Washeshu Ck — Truckee basin below the outlet",
    "10336710": "Marlette Lake — diverted to Virginia City, not free-draining to Tahoe",
    "10336715": "Marlette Ck — below Marlette Lake's diversion",
    "103366092": "Upper Truckee at Hwy 50 abv Meyers — upper reach, "
                 "superseded by 10336610 downstream",
    "391004120083401": "Lake Tahoe outlet precip gage — reports only air "
                       "temperature (00020) in the last 30 days, no precipitation",
}

# Gauges with useful history but nothing current. Not ingested on the
# hourly cron; listed so the archival value isn't forgotten.
USGS_HISTORICAL_GAUGES: dict[str, dict] = {
    "10336725": {
        "name": "Glenbrook Creek at Old Hwy 50 nr Glenbrook, NV",
        "shore": "east",
        "note": "Discharge and gage height, 1992-01-01 to 2000-05-01. "
                "Daily values only; no instantaneous series.",
    },
}

# Parameter code -> display metadata. Codes are USGS-wide; the full list is
# at /collections/parameter-codes/items. Units here are ours for display;
# the API's own raw strings differ slightly (ft^3/s, _FNU, uS/cm, mg/l,
# degC) and arrive on each observation as `unit_of_measure`.
USGS_PARAMETERS: dict[str, dict] = {
    "00060": {"label": "Discharge",    "units": "ft³/s"},
    "00065": {"label": "Gage height",  "units": "ft"},
    "00010": {"label": "Water temp",   "units": "°C"},
    "63680": {"label": "Turbidity",    "units": "FNU",
              "note": "Same measure and units as the EXO sondes, from an "
                      "independent instrument — the cross-check on "
                      "Sunnyside's -1.98 FNU calibration offset. Blackwood "
                      "only; no turbidity series exists at Glenbrook."},
    "00300": {"label": "Dissolved O₂", "units": "mg/L",
              "note": "Directly comparable to the EXO sondes' Do_mgL."},

    "00095": {"label": "Conductance",  "units": "µS/cm"},
    # Fine sediment particle LOAD, as distinct from the concentration in
    # 70369 below. This is arguably the more regulation-relevant of the
    # two: the Tahoe TMDL sets its allocations as fine sediment particle
    # LOADS — particles per year by source category — not concentrations.
    #
    # Note the statistic: 00006 (Sum), not 00011 (instantaneous), because
    # a load is a daily total. See USGS_PARAMETER_STATISTICS for why that
    # needs special handling.
    "70372": {"label": "Fine sediment load", "units": "count/day",
              "note": "Daily load of fine sediment particles 0.5–16 µm. The "
                      "TMDL expresses its allocations as particle loads, so "
                      "this is the regulatory currency. Surrogate, computed "
                      "by regression. Daily sum (statistic 00006), not "
                      "instantaneous."},
    # THE REGULATED CLARITY POLLUTANT. Resolved 2026-09-18 via
    # `usgs-params --codes 70369`; USGS defines it as "Suspended sediment
    # particles between 0.50 to 16.00 microns, water, unfiltered, computed
    # by regression equation, counts per liter".
    #
    # That size class is not incidental. Lake Tahoe's TMDL identifies
    # inorganic fine sediment particles <16 µm as the dominant cause of
    # deep-water clarity loss — roughly two-thirds of the lake's
    # impairment — and Lake Tahoe Info states the responsible fraction as
    # 0.5–16 µm, matching this parameter exactly. The lake is Clean Water
    # Act 303(d)-listed as impaired for nitrogen, phosphorus and sediment
    # on that basis, and the TMDL requires a 65 % FSP reduction to restore
    # Secchi depth to 97.4 ft by 2076 (interim: 78 ft by 2031).
    #
    # Mechanism: fine sediment particles SCATTER light, algae ABSORB it.
    # Those are the two processes that set Secchi depth.
    #
    # CAVEAT, from the definition itself: "computed by regression
    # equation". This is a surrogate, almost certainly derived from
    # turbidity at the same gauge, not a laboratory particle count. It
    # inherits turbidity's error and its regression is site-specific.
    "70369": {"label": "Fine sediment", "units": "count/L",
              "note": "Fine sediment particles 0.5–16 µm — the fraction the "
                      "Lake Tahoe TMDL regulates as the dominant driver of "
                      "clarity loss (~2/3 of impairment). Surrogate value, "
                      "computed by regression rather than counted."},
    "63160": {"label": "Stream level", "units": "ft",
              "note": "Water-surface elevation above NAVD 1988 — already on "
                      "a national datum, so comparable between sites and "
                      "across years, unlike TEON's uncalibrated depth."},
    "80155": {"label": "Sediment discharge", "units": "tons/day"},
    # Historical only at Blackwood (1974-10-01 to 1992-09-29, daily mean),
    # but suspended sediment is the direct physical driver of Tahoe clarity
    # loss. Valuable for baseline work via the /daily endpoint.
    "80154": {"label": "Suspended sediment", "units": "mg/L",
              "note": "Blackwood 1974-1992, daily mean. Historical."},
    "80155": {"label": "Sediment discharge", "units": "tons/day",
              "note": "Blackwood 1974-1992, daily mean. Historical."},
}

# How much history to pull per run, as an ISO 8601 duration.
#
# Was P2D, which at an hourly cron meant re-fetching 47 of the 48 hours we
# already had — roughly 98 % of every commit was data already in the repo,
# about 180 MB/day. PT6H still gives six-fold overlap between consecutive
# runs, so five consecutive failures can pass without leaving a gap, at a
# quarter the volume.
#
# The sparkline trend window (SPARKLINE_WINDOW_HOURS) is satisfied from the
# accumulated parquet, not from a single pull, so shortening this does not
# shorten the charts.
USGS_DEFAULT_PERIOD = "PT6H"


# ===========================================================================
# Units and display
# ===========================================================================
# Readings are stored in whatever unit the source publishes — TEON is
# metric, USGS is imperial. Rather than convert at ingest (which would
# destroy the source value) we emit BOTH representations in latest.json and
# let the dashboard toggle. Each entry maps a stored unit to its
# counterpart in the other system.
#
#   factor/offset convert FROM the stored unit TO the target unit:
#       target = stored * factor + offset
UNIT_CONVERSIONS: dict[str, dict] = {
    # metric stored -> imperial
    "°C":     {"system": "metric",   "to": "°F",     "factor": 1.8,      "offset": 32.0},
    "m":      {"system": "metric",   "to": "ft",     "factor": 3.280839895},
    "mm":     {"system": "metric",   "to": "in",     "factor": 0.0393701},
    "m³/s":   {"system": "metric",   "to": "ft³/s",  "factor": 35.3146667},
    "km/h":   {"system": "metric",   "to": "mph",    "factor": 0.621371},
    # imperial stored -> metric
    "°F":     {"system": "imperial", "to": "°C",     "factor": 0.5555556, "offset": -17.7777778},
    "ft":     {"system": "imperial", "to": "m",      "factor": 0.3048},
    "in":     {"system": "imperial", "to": "mm",     "factor": 25.4},
    "ft³/s":  {"system": "imperial", "to": "m³/s",   "factor": 0.0283168},
    "mph":    {"system": "imperial", "to": "km/h",   "factor": 1.609344},
    # Dimensionless or system-neutral units: no counterpart. Listed
    # explicitly so a missing entry means "we forgot", not "no conversion".
    "%":      {"system": "both"},
    "µg/L":   {"system": "both"},
    "mg/L":   {"system": "both"},
    "FNU":    {"system": "both"},
    "µS/cm":  {"system": "both"},
    "dS/m":   {"system": "both"},
    "ppt":    {"system": "both"},
    "µm":     {"system": "both"},
    "V":      {"system": "both"},
    "":       {"system": "both"},
}

# Default system the dashboard opens in. US-facing project on a US lake.
DEFAULT_UNIT_SYSTEM = "imperial"

# ---------------------------------------------------------------------------
# Trend sparklines
# ---------------------------------------------------------------------------

# Window of history summarised behind each card reading.
SPARKLINE_WINDOW_HOURS = 48

# Points kept per sparkline. 48 hours at 15-minute cadence is 192 samples;
# ~60 is plenty for a 90px-wide inline SVG and keeps latest.json small.
SPARKLINE_POINTS = 60

# A trend is only reported when the window holds at least this many
# samples, so a sensor that just came online doesn't get a slope drawn
# through three points.
SPARKLINE_MIN_POINTS = 8

# Fraction of a variable's own observed range that the change across the
# window must exceed before we call it rising or falling rather than
# steady. Keeps instrument noise from reading as a trend.
TREND_SIGNIFICANCE = 0.15

# ---------------------------------------------------------------------------
# Local paths
# ---------------------------------------------------------------------------

# Raw snapshots are a working buffer, not the archive. The deduplicated
# parquet under data/processed is the durable record and accumulates across
# runs; raw exists so a transform bug can be found and reprocessed within a
# reasonable window. Without a retention limit the repo grows without
# bound — an hourly cron committing every fetch artifact forever.
RAW_RETENTION_DAYS = 7

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
WEB_DIR = REPO_ROOT / "web"

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = "secchi/0.1 (+https://github.com/bdgroves/secchi)"

# Records per sensor per run. At 15-min cadence, 200 covers ~50 hours —
# comfortably more than the hourly ingest interval, so brief outages
# backfill on the next run.
DEFAULT_INGEST_PAGE_SIZE = 200
