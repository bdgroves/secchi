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
    # Time series lives at /sensors/{slug} — see SENSOR_TYPE_SLUGS.
}

# Sensor-type identifiers in the /sensors/locations payload → URL slugs on
# the /sensors/{slug} time-series endpoint. Only ``exo-sensor`` is confirmed
# from network inspection; the rest are best-guess kebab-cased inferences
# that the ingest module will probe and disable on 404.
SENSOR_TYPE_SLUGS: dict[str, str] = {
    "ExoSensor":                     "exo-sensor",        # confirmed
    "ExoSensorManual":               "exo-sensor",        # same endpoint
    "MiniDotSensor":                 "mini-dot-sensor",
    "HoboSensor":                    "hobo-sensor",
    "SoilEnvironmentalConditions":   "soil-environmental-conditions",
    "AirTemperatureRelativeHumidity":"air-temperature-relative-humidity",
    "TreeStressAndGrowth":           "tree-stress-and-growth",
    "PrecipitationGauge":            "precipitation-gauge",
    "FieldCamera":                   "field-camera",
    "StreamChemistry":               "stream-chemistry",
    "StreamLevel":                   "stream-level",
}

# EXO sonde variables and how to render them. `null_if_zero` handles the
# fleetwide broken-pH-probe case (every record returns pH = 0.0).
EXO_VARIABLES: dict[str, dict] = {
    "Temp":                  {"label": "Water temp",  "units": "°C"},
    "Do_mgL":                {"label": "DO",          "units": "mg/L"},
    "Do_percent":            {"label": "DO sat",      "units": "%"},
    "Chl_a":                 {"label": "Chlorophyll", "units": "µg/L"},
    "phycocyanin":           {"label": "Phycocyanin", "units": "µg/L"},
    "Turbidity":             {"label": "Turbidity",   "units": "FNU", "clip_low": 0.0},
    "specific_conductivity": {"label": "Conductivity","units": "µS/cm"},
    "Salinity":              {"label": "Salinity",    "units": "ppt"},
    "TDS":                   {"label": "TDS",         "units": "mg/L"},
    "depth":                 {"label": "Depth",       "units": "m"},
    "pH":                    {"label": "pH",          "units": "",   "hide": True,
                              "note": "Broken/uncalibrated fleetwide — reports 0.0"},
    "BattV_Min":             {"label": "Battery",     "units": "V",  "hide": True},
}

# The three EXO sites currently reporting on autonomous telemetry. Chosen
# for cardinal spread — one hero per side of the lake. Blackwood 3 and
# Meeks are manual sondes; both stopped Jul 9 pending physical retrieval.
LIVE_EXO_SITES: tuple[str, ...] = ("Sunnyside", "Glenbrook", "4H Camp")

SITE_METADATA: dict[str, dict] = {
    "Sunnyside": {"shore": "west",  "lat": 39.134643, "lng": -120.151085},
    "Glenbrook": {"shore": "east",  "lat": 39.088329, "lng": -119.941133},
    "4H Camp":   {"shore": "south", "lat": 38.974209, "lng": -119.951608},
    "Blackwood 3": {"shore": "west","lat": 39.10763333, "lng": -120.1577667,
                    "note": "Manual EXO — offline since 2026-07-09"},
    "Meeks":     {"shore": "west",  "lat": 39.03773333, "lng": -120.1211667,
                  "note": "Manual EXO — offline since 2026-07-09"},
}

# A sensor is considered "live" if its last_update is within this window.
LIVE_WINDOW_HOURS = 24

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

# How many records to pull per sensor per cron tick. At 15-min cadence,
# 200 records covers ~50 hours of history — comfortably more than the
# hourly ingest interval, so short outages backfill on the next run.
DEFAULT_INGEST_PAGE_SIZE = 200
