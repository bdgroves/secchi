# Wildfire smoke over the lake (NOAA HMS)

Smoke can reach a lake two ways: ash and nutrients falling into the water,
and haze cutting the light algae grow on. This chapter asks whether smoky
days leave a mark in TEON's lake sondes. It's the question behind the
University of Nevada, Reno's "lake smoke-day" research.

    pixi run smoke --since 2024-06-01     # first run: ~850 daily files, a few minutes
    pixi run smoke                        # afterwards: the last two weeks

Source: NOAA/NESDIS **Hazard Mapping System**. Analysts trace smoke on GOES
satellite imagery daily and publish polygons graded light, medium and heavy:
`https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Smoke_Polygons/KML/YYYY/MM/hms_smokeYYYYMMDD.kml`.
Questions about the product go to SSDFireTeam@noaa.gov.

Stored per day in `data/processed/smoke_observations` (`smoke` in the query
shell):

| variable | meaning |
|---|---|
| `hms_analysed` | 1 if NOAA published a file for the day, 0 if not |
| `smoke_density` | analysed days only: 0 none over the lake, 1 light, 2 medium, 3 heavy |
| `hms_polygons` | analysed days only: smoke polygons anywhere in the file |

## Two cautions

**Smoke seen from above.** HMS maps smoke at any altitude. A plume high
over the basin can leave the lake surface clear, so an HMS smoke day means
smoke overhead, not smoke in the water.

**A missing file is not a clear day.** NOAA's *shapefile* archive has holes
— in 2025, August has files only for the 1st and the 25th to 31st — so the
source was built to store a missing day as `hms_analysed = 0`, excluded from
any comparison rather than counted as clear. As it turned out, the *KML*
archive this reads is complete: all 853 days from June 2024 are present,
genuine KML, and every one has smoke polygons somewhere in North America.

The first full run reported no missing KML days at all, so each response is
now checked: anything whose root element isn't `<kml>` (a server error page
served with HTTP 200, say) also counts as not analysed, and `hms_polygons`
shows whether a file is suspiciously empty.

"Over the lake" means the lake's centre or a point off any shore falls
inside a smoke polygon. Density comes from the polygon's folder ("Smoke
(Light)" and so on) or its description; older files used numbers (5, 16,
21/27), newer ones words, and both are read. A polygon with neither stops
the run with an error rather than being guessed.

## The record

**114 days with smoke over the lake, June 2024 to October 2026**, in 56
episodes: 90 light, 22 medium, 2 heavy, all in fire season. Summer 2024 had
43, including 15 straight days in July; 2025 had 36 and 2026 had 29.

## Did it leave a mark? (`pixi run smoke-lake`)

Not one that can be measured yet.

* **The lake sondes have no summer-2024 data**, so the comparison rests on
  about 65 smoke days in 2025 and 2026, in roughly 20 episodes.
* **The forest stations' daytime highs weren't cooler on smoky days**: all
  seven read slightly warmer, none significantly. Mostly light smoke, high
  up, on hot fire-weather days.
* **In the lake, 3 of 60 tests fell below p = 0.05 once each month's trend
  was removed — about what chance alone gives.** The three are small:
  oxygen about 0.06 mg/L lower a week after smoke at Glenbrook and 4H Camp,
  and one turbidity result at Sunnyside whose sign flips from the raw test.
  The oxygen pair is worth watching as more smoke seasons accumulate.

**The near miss.** With the trend left in, Glenbrook's chlorophyll and
blue-green pigment looked clearly higher on and after smoky days — p as low
as 0.001, consistent at every lag, and not explained by air temperature.
Month by month it lived only in August and September, when chlorophyll
climbs through the month (growth, or a fouling sensor) and smoke tends to
arrive late. Removing each month's straight-line trend erased it:
phycocyanin +0.33 to +0.02 (p 0.86). The report prints both figures so the
trap stays visible, and `tests/test_smoke_lake.py` reproduces it.

Method: smoky days against clear days of the same month; p from rotating
each month's smoke calendar 4,000 times, which keeps each episode's shape
(smoke days come in runs, so an ordinary t-test would be over-confident);
lags of 0, 1, 3 and 7 days; two known channel-scramble windows excluded.
