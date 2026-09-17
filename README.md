# ⚪⚫ secchi

## A modern Secchi disk for Lake Tahoe

For over 150 years, limnologists have measured lake clarity by lowering a black-and-white disk — the **Secchi disk** — into the water and recording the depth at which it disappears. It is the oldest instrument in freshwater science and, for Lake Tahoe, still the defining measure of ecosystem health.

`secchi` builds on that tradition with data from the [Tahoe Environmental Observatory Network (TEON)](https://tahoeenvironmentalobservatorynetwork.org/), a live sensor network launched in 2026 by the Tahoe Institute for Global Sustainability at the University of Nevada, Reno. Where the original Secchi disk gave a single number at a single time, TEON's ~35 sensors across the basin let us watch clarity emerge from its drivers — temperature, chlorophyll, dissolved oxygen, wave height, wildfire smoke — in real time.

---

## What this is

An environmental-intelligence project sitting at the intersection of limnology, watershed hydrology, and wildfire science. Three headline features drive the design:

**Same storm, two watersheds.** TEON's sentinel sites — Blackwood Creek on the wet west side and Glenbrook Creek in the east-side rain shadow — are a deliberately contrasting pair. When a single atmospheric river or smoke plume hits the basin, both experience the same forcing but respond in wildly different ways. `secchi` shows those divergent responses side by side.

**Wildfire in the water.** Smoke deposition delivers nutrients to the lake; nutrients feed algae; algae erode clarity. Overlaying NASA FIRMS active-fire data and HRRR-Smoke plume forecasts on TEON's chlorophyll, DO, and temperature signals lets us watch a fire's fingerprint arrive in the lake.

**Clarity nowcast.** A live estimate of Secchi depth predicted from real-time sensor readings — chlorophyll, turbidity proxies, wave stirring — with the historical Secchi record as ground truth.

---

## Status

Early scaffolding. Data ingestion pipeline is stubbed pending confirmation of TEON's public data endpoint; dashboard is placeholder.

---

## Structure

```
secchi/
├── src/secchi/          # Python package: ingestion, transforms, models
├── data/
│   ├── raw/             # Raw JSON pulls from TEON, timestamped
│   └── processed/       # Cleaned parquet + latest.json for the web layer
├── web/                 # Static dashboard (GitHub Pages target)
├── notebooks/           # Exploration and modeling notebooks
├── docs/                # Design notes, data dictionary, methodology
└── .github/workflows/   # Scheduled ingestion + deploy
```

---

## Running locally

```bash
# Windows PowerShell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Pull the latest snapshot
python -m secchi.ingest

# Serve the dashboard
python -m http.server 8000 --directory web
```

---

## Data & attribution

Sensor data are provided by the **Tahoe Environmental Observatory Network** and are provisional. Per TEON: *"Data may be subject to inaccuracies due to instrument performance, maintenance cycles, or environmental conditions at measurement locations. Users assume full responsibility for the interpretation and use of these data."*

If you use anything derived from this repo, please cite both `secchi` and TEON.

---

## Part of

🌲 Environmental Intelligence Lab · [Brooks Labs](https://github.com/bdgroves)

---

*"The lake is clearest where you look longest."*
