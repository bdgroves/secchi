# The trees: what the dendrometers show

*2026-10-02. `pixi run trees`; code in `src/secchi/analysis/trees.py`; on
the page under "The trees, working for water".*

## The data

`TreeStressAndGrowth` sensors: band dendrometers, `Tree_1_diameter_change`
… `Tree_8_diameter_change`, every 15 minutes, integers.

| Station | Bands | Since |
|---|---|---|
| Blackwood 2 | 8 | 2024-06 (dark since 2026-09-25) |
| Glenbrook 2 | 8 | 2025-08 |
| Glenbrook 4 | 8 | 2025-05 (flat until July 2025) |
| Glenbrook 5 | 8 | 2025-06 |
| Homewood | 4 | 2026-07-27 |

**Units are undocumented.** µm is inferred from magnitude, and the daily
cycle supports it: 10-40 units a day is what conifer stems do in
micrometres. Values in the thousands are the sensor's position along its
travel, not the trunk's size.

## What it shows

Every station's average day (last 14 days, 2026-10-02) is largest at 7-8
am and smallest at 3-5 pm: stems refill overnight and shrink as the tree
transpires. The drop from the morning peak (03-10 h) to the afternoon low
(12-19 h), the **maximum daily shrinkage** (MDS), is how hard the tree
worked for water that day.

| Station | Jul-Aug | Dec-Feb |
|---|---|---|
| Blackwood 2 | 32 | 10 |
| Glenbrook 2 | 37 | 10 |
| Glenbrook 4 | 28 | 11 |
| Glenbrook 5 | 26 | 5 |
| Homewood | 9 (Aug 2026 only) | — |

June-September, MDS correlates with the day's high air temperature at
every station (+0.24 to +0.62) and inversely with soil moisture at
Blackwood 2 (-0.42), more weakly at Glenbrook 2 (-0.32) and 4 (-0.23).

Homewood vs Glenbrook 5, the transect pair: ~10 vs ~26-30 µm in the same
weeks. Fits the wet west shore, but species and trunk size per band aren't
published and could explain it alone. Stated as such on the page.

## Channel checks (last 30 days)

A channel is left out if its median daily range is under 3 (barely moves),
it has more than 20 jumps of 50+ between readings (resets or glitches), or
its cycle is out of step (largest outside 03-10 h or smallest outside
11-19 h). On 2026-10-02 that left out Blackwood 2 tree 2 (range 2),
Glenbrook 2 tree 3 (118 jumps) and tree 7 (27 jumps): 33 of 36 used.
Tree-days are dropped when the channel isn't engaged (daily median under
1: Glenbrook 4's bands read fractions of a unit around zero for May-July
2025) or didn't change at all. A station-day needs at least half its
usable trees and at least three (or all it has): one March 2026 week at
Blackwood 2 was two channels, one reading 204-264 µm on near-freezing
days, and drew a 109 µm spike on the first version of the chart. So each
station's series starts when enough bands were engaged: Blackwood 2
August 2025, Glenbrook 4 and 5 July 2025.

## Not done: growth

The morning size creeps up over a season. With jumps over 50 removed,
April-October 2026 per-tree estimates ran from -0.1 to +6.5 mm, and
Glenbrook 4's 2025 ones to -3.4 mm. That spread is the method: spring
rehydration after snowmelt swells stems without wood, and band resets and
re-tensioning aren't published. Needs TEON's maintenance log.
