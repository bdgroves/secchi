# Readings no lake can produce

Twice, a lake sonde's channels were filed under the wrong names, and both
times it was found by hand, months later:

| Sonde | Window | Channels | Tell |
|---|---|---|---|
| Sunnyside | 2026-04-30 11:30 → 06-25 08:00 | all | "water temperature" of 85–102 |
| 4H Camp | 2026-07-17 14:15 → 07-24 13:15 | optical | oxygen concentration of 86 mg/L |

Inside those windows many values still look plausible — a turbidity
"spike", a chlorophyll reading — so nothing downstream would notice. But a
scramble almost always drops *some* channel's value somewhere physically
impossible, and oxygen and temperature are the reliable places to look.

## The check

`scan_readings` in `src/secchi/sources/watch.py` runs with every watch (every
six hours) and scans the **whole** lake record with DuckDB against
`IMPOSSIBLE`:

| Instrument | Variable | Allowed |
|---|---|---|
| EXO | `Temp` | −2 to 35 °C |
| EXO | `Do_mgL` | 0 to 20 mg/L |
| EXO | `Do_percent` | 50 to 150 % |
| EXO | `pH` | 0 to 14 (exact 0 is a known dead channel, not flagged) |
| MiniDOT | `Temperature`, `Dissolved Oxygen`, `Dissolved Oxygen Saturation` | as above |
| HOBO | `temperature` | −2 to 35 °C |

Each **episode** — one site, one variable, one month — becomes a notable
change in the watcher's issue, **once**. The keys already raised travel in
`watch_baseline.json` as `quality_reported`, so a bad week doesn't reopen an
issue every six hours, and a local `pixi run watch` still changes nothing.

It scans the whole record rather than recent days because hand-collected
sondes upload months at once, so a bad stretch can arrive with old
timestamps.

**The first run reports all 28 known episodes in one issue.** After that,
only new ones.

## Why 50 % saturation

Surface water at Tahoe sits around 70–130 %. Below 50 % is either a fault or
a sonde out of place — Blackwood 3's April 30 service visit reads 45–50 % for
six and a half hours. Those are real readings of a disturbed instrument,
correctly caught: they shouldn't be used either.

## What it doesn't do

It doesn't rewrite anything. The windows are reported, and excluded by the
oxygen check; anyone analysing another lake variable should exclude them
too (see HANDOFF.md).
