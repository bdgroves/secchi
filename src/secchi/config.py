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
    # Unconfirmed — no live sensors of these types to probe.
    "Stream Chemistry": ("stream-chemistry", "stream-chem"),
    "Precipitation Gauge": ("precipitation", "precipitation-gauge"),
    "Minidot": ("mini-dot", "minidot", "mini-dot-sensor"),
    "Hobo": ("hobo", "hobo-sensor"),
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

# The east–west transect pair. Homewood (39.075 N, west shore) and
# Glenbrook 2 (39.086 N, east shore) sit within ~0.011° latitude of each
# other — about 1.2 km — on opposite sides of the lake. Same storms, same
# sun angle, opposite sides of the Sierra rain shadow. This is the cleanest
# available pairing for the "same storm, two watersheds" comparison, and a
# better match than the original Blackwood/Glenbrook idea (Blackwood 2 is
# 4 km further north and currently offline anyway).
TRANSECT_PAIR: tuple[str, str] = ("Homewood", "Glenbrook 2")

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

# ---------------------------------------------------------------------------
# Local paths
# ---------------------------------------------------------------------------

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
