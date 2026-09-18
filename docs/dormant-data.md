# The dormant fleet

`pixi run probe` previously only resolved slugs for sensor types with a
*live* sensor. That hid four entire sensor types whose instruments stopped
reporting but whose history is still served by the API.

## What was invisible

| Sensor type | Sites | Records | Last reporting |
|---|---|---|---|
| Precipitation Gauge | 1 (Blackwood 2) | 593,507 | 2026-08-14 |
| MiniDot | 6 nearshore lake | 265,340 | 2026-06-10 |
| HOBO | 6 nearshore lake | 214,378 | 2026-06-10 |
| Stream Chemistry | 1 (Blackwood 2) | 124,620 | 2026-06-18 |
| **Total** | | **1,197,845** | |

For scale, a full live ingest yields about 1,600 unique records per run.
This is a different order of magnitude, and it is history rather than
current state — which is precisely what a project about long-term change
needs.

## Why it matters for this project

**The MiniDot and HOBO fleets are twelve nearshore instruments at six
sites** — Camp Richardson, Lake Forest, Lakeside, Incline, Tahoe Keys and
Tallac. MiniDots log dissolved oxygen and temperature; HOBOs log
temperature. Six nearshore sites of paired DO and temperature, roughly
40,000–47,000 observations each, is a real spatial picture of the lake
margin that the three deep-water EXO sondes cannot give.

**Blackwood 2 stream chemistry (124,620 records)** covers the west-shore
creek whose TEON station is currently dark. It pairs directly with the USGS
gauge on the same creek.

**Blackwood 2 precipitation (593,507 records)** is the single largest
dataset in the network, and precipitation is the forcing variable the whole
"same storm, two watersheds" question depends on. Without it we can see
watershed *response* but not the storm driving it.

The seasonal pattern is worth noting: MiniDot and HOBO both stop around
2026-06-10, and the two manual EXO sondes stop 2026-07-09. That clustering
looks like a summer haul-out for maintenance rather than equipment failure.
If so, this data is not a dead archive — those instruments are likely back
in the water and their records will extend when the next download happens.

## Two new modes

    pixi run probe            # every sensor type, live and dormant
    pixi run probe-live       # live types only (the old behaviour)
    pixi run ingest-history   # every sensor in the inventory, dormant included

The probe report now includes per-type site counts, record totals and
freshness, and calls out how many records sit in types with nothing live —
so this gap can't quietly reopen.

**Caution on `ingest-history`.** It pulls every sensor rather than the live
subset, and `DEFAULT_INGEST_PAGE_SIZE` caps each at 200 records per run, so
one pass gets recent history for everything rather than full archives.
Pulling 1.2 million records properly needs a deliberate backfill with
pagination and a raised cap — worth doing as a one-off, not on the hourly
cron.
