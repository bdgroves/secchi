# SNOTEL: rain on both shores

The transect compares how Homewood's soil (west) and Glenbrook 5's (east)
respond to storms. Checking that against the rain itself needs a gauge on
each shore, and TEON has one — at Blackwood 2, on the west, missing 136
days including most of November 2025 and January 2026.

So secchi also reads NRCS **SNOTEL**:

| Station | Side | Elevation |
|---|---|---|
| Ward Creek #3 | west, primary | 6,764 ft |
| Rubicon #2 | west, fallback | 7,618 ft |
| Marlette Lake | east | 7,883 ft |

    pixi run snotel --since 2024-06-01     # first run: the whole record
    pixi run snotel                        # afterwards: the last 30 days
    pixi run transect-rain

Station IDs are looked up **by name** from SNOTEL's own list on first run
and saved to `data/reference/snotel_stations.json`. `--force` looks them
up again.

Data comes from the NRCS Air and Water Database REST API
(`https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/`, endpoints
`stations` and `data`): daily `PRCP` (precipitation increment), `PREC`
(water-year accumulation) and `TAVG` (mean air temperature), converted to
millimetres and °C using the units SNOTEL states. Daily values run
midnight to midnight Pacific Standard Time.

## What `transect-rain` reports

1. West and east totals over the shared record — all precipitation, and
   rain only (days averaging above 0 °C) — next to the soil wetting ratio.
2. Storm by storm: rain at each gauge over the day before to the day after
   each wetting onset, and whether the wetter shore by rain was also the
   wetter by soil.
3. Days of 10 mm or more the soil didn't register, flagged as snow where
   it was below freezing — snow wets soil only as it melts.
4. TEON's Blackwood 2 gauge against Ward Creek #3 on its complete days.

## Caveats

* **Marlette Lake is ~1,000 ft higher than Ward Creek #3**, and
  precipitation generally rises with elevation, so the precipitation ratio
  understates the west/east contrast at the soil stations.
* Gauge catch includes snow, and wind undercatch in snow is a known bias.
* One soil sensor and one gauge per shore, miles apart.

## If the first run fails

The response layout was taken from the documentation and independent
clients; it couldn't be tested from where this was written. The parser
stops with a sample of what arrived rather than storing nothing — paste
that error, and the fix is usually one field name.
