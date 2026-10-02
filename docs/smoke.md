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

**A missing file is not a clear day.** In 2025, August has files only for
the 1st and the 25th to 31st, a dozen October days are missing, and
November has only its last six (that autumn's federal shutdown). Those days
are stored with `hms_analysed = 0` and no density, so they're excluded from
any comparison rather than counted as clear.

The first full run reported no missing KML days at all, so each response is
now checked: anything whose root element isn't `<kml>` (a server error page
served with HTTP 200, say) also counts as not analysed, and `hms_polygons`
shows whether a file is suspiciously empty.

"Over the lake" means the lake's centre or a point off any shore falls
inside a smoke polygon. Density comes from the polygon's folder ("Smoke
(Light)" and so on) or its description; older files used numbers (5, 16,
21/27), newer ones words, and both are read. A polygon with neither stops
the run with an error rather than being guessed.

## Next

Once the record is in: which days had smoke over the lake, and how heavy;
then, comparing smoky and clear days *within the same month* (smoke season
is also the season chlorophyll and temperature change on their own), the
sondes' chlorophyll, turbidity, oxygen and temperature, and a healthy
station's solar charging peak as a rough measure of sunlight.
