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

# The series we ingest. Everything else is a daily aggregate and would
# collide with the instantaneous value if mixed into one variable.
USGS_STATISTIC_INSTANTANEOUS = "00011"

# Gauges to ingest. Parameter lists below were CORRECTED against
# /time-series-metadata on 2026-09-17 (`pixi run usgs-probe`) — these are
# confirmed present with an instantaneous (00011) series that is currently
# reporting, not guesses.
USGS_GAUGES: dict[str, dict] = {
    "10336660": {
        "name": "Blackwood Creek nr Tahoe City, CA",
        "shore": "west",
        "note": "The long baseline TEON structurally cannot provide, and the "
                "only current west-shore stream data while TEON's own "
                "Blackwood 2 station is offline. Daily mean discharge runs "
                "back to 1960-10-01; instantaneous to 1987.",
        # Confirmed instantaneous and current (end 2026-09-17):
        #   00060 discharge      1987-10-02 ->
        #   00065 gage height    2007-10-01 ->
        #   00010 water temp     2015-01-20 ->
        #   63680 turbidity      2015-01-20 ->   (the EXO cross-check)
        #   00300 dissolved O2   2024-10-11 ->
        #   63160 stream level   2023-09-11 ->   (surveyed NAVD88 datum)
        "parameters": ("00060", "00065", "00010", "63680", "00300", "63160"),
    },
    "10336730": {
        "name": "Glenbrook Creek at Glenbrook, NV",
        "shore": "east",
        "note": "Pairs with TEON's Glenbrook 2 stream gauge. No turbidity "
                "series exists here — that measure is west-shore only.",
        # Confirmed instantaneous and current:
        #   00060 discharge      1987-11-17 ->
        #   00065 gage height    2007-10-01 ->
        #   00010 water temp     2022-09-30 ->
        #   00300 dissolved O2   2024-11-12 ->
        # Deliberately excluded: 00095 specific conductance, whose
        # instantaneous series ended 2024-11-07.
        "parameters": ("00060", "00065", "00010", "00300"),
    },
    "10337000": {
        "name": "Lake Tahoe a Tahoe City, CA",
        "shore": "west",
        "note": "Lake surface elevation. Gage height only — no water "
                "temperature series exists. The 32400 daily series runs "
                "back to 1957-10-01.",
        "parameters": ("00065",),
    },
    # 10336725 (Glenbrook Creek at Old Hwy 50) is deliberately absent.
    # The probe found only two series there, discharge and gage height,
    # both ending 2000-05-01 with no instantaneous data at all. It is a
    # historical station, not a live one.
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
    "63160": {"label": "Stream level", "units": "ft",
              "note": "Referenced to the NAVD88 vertical datum, unlike "
                      "TEON's Uncalibrated_water_depth. Absolute, not "
                      "relative."},
    "00095": {"label": "Conductance",  "units": "µS/cm"},
    # Historical only at Blackwood (1974-10-01 to 1992-09-29, daily mean),
    # but suspended sediment is the direct physical driver of Tahoe clarity
    # loss. Valuable for baseline work via the /daily endpoint.
    "80154": {"label": "Suspended sediment", "units": "mg/L",
              "note": "Blackwood 1974-1992, daily mean. Historical."},
    "80155": {"label": "Sediment discharge", "units": "tons/day",
              "note": "Blackwood 1974-1992, daily mean. Historical."},
}

# How much history to pull per run. ISO 8601 duration.
USGS_DEFAULT_PERIOD = "P2D"


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
