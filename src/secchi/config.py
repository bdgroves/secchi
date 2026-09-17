"""Configuration constants for secchi.

TEON's public data endpoints are not yet formally documented at the time of
writing. The URL below is a placeholder — before wiring up production ingest:

    1. Open https://tahoeenvironmentalobservatorynetwork.org/teon-network
       in a browser and open dev tools (Network tab).
    2. Watch the JSON/XHR requests the sensor-network page fires as it loads
       and updates. Those are the endpoints powering the frontend.
    3. Update `TEON_API_BASE` and the station map below to match.

Alternatively, email Katie Senft (research vessel operator) or Sudeep Chandra
(Global Water Center director) at UNR and ask about programmatic access.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# TEON data endpoints (TODO: confirm)
# ---------------------------------------------------------------------------

TEON_API_BASE = "https://tahoeenvironmentalobservatorynetwork.org/api"  # placeholder
"""Root of the TEON public data API. Update once confirmed."""

# Placeholder station identifiers. TEON currently operates ~35 sensors in air,
# land, and water across the basin, anchored on two sentinel watersheds:
# Blackwood Creek (wet, west side) and Glenbrook Creek (dry, east-side rain
# shadow). Populate the full list once the API is characterized.
STATIONS: dict[str, dict[str, str]] = {
    "blackwood": {
        "name": "Blackwood Creek",
        "watershed": "Blackwood",
        "side": "west",
    },
    "glenbrook": {
        "name": "Glenbrook Creek",
        "watershed": "Glenbrook",
        "side": "east",
    },
}

# Sensor variables to pull. Names align with TEON's documented in-lake suite;
# terrestrial variables (soil moisture, air temp, PM2.5 from smoke) will be
# added once the terrestrial endpoint is confirmed.
VARIABLES: tuple[str, ...] = (
    "water_temperature",
    "dissolved_oxygen",
    "chlorophyll",
    "conductivity",
    "wave_height",
)

# ---------------------------------------------------------------------------
# Local paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
WEB_DIR = REPO_ROOT / "web"

# HTTP client settings
HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = "secchi/0.1 (+https://github.com/bdgroves/secchi)"
