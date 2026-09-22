# How each sensor's data actually reaches us

Three answers, and the map now draws them differently because they mean
different things.

| Collection | What it means | Map |
|---|---|---|
| **telemetered** | radio or cellular, continuous | green / amber / grey by age |
| **by hand** | self-logging, retrieved and downloaded | hollow ring, ember if unpulled |
| **offline** | every channel at the station is dark | grey with an ember edge |

## Hand-collected is wider than TEON labels it

TEON puts `Manual` in the id of exactly two sensors — the Blackwood 3 and
Meeks EXO sondes. But two whole fleets are self-logging **by instrument
class**:

- **PME MiniDOT** — a battery-powered dissolved-oxygen and temperature
  logger with no radio. You retrieve it and download over USB. That is
  the entire product category.
- **Onset HOBO** — the same. A HOBO is a logger you go and collect.

Neither is offered with telemetry in an underwater deployment, so both
are hand-collected whether or not the identifier says so.

### The dates corroborate it

| Fleet | Sites | Last record |
|---|---|---|
| MiniDot | 6 | 2026-06-10 |
| HOBO | 6 | 2026-06-10 |
| EXO (manual) | 2 | 2026-07-09 |

Twelve instruments across six shared nearshore sites, stopping on **the
same day**. Twelve simultaneous failures is not plausible; one boat trip
is. The manual EXO sondes stop four weeks later — a second trip.

### Confidence

The instrument classes are named verbatim by the API, so "MiniDot means a
PME MiniDOT" is solid. That such a logger has no telemetry is general
product knowledge rather than something TEON documents, so this is a
**strong inference, not an established fact**.

It's recorded as an inference in `MANUAL_COLLECTION_TYPES` with the
reasoning attached. The competing explanation — twelve radios failing on
one day and none since — is considerably worse.

## Blackwood 2 is offline, not hand-collected

| Channel | Records | Last |
|---|---|---|
| Stream Chemistry | 124,620 | ~Jun |
| Stream Level | 124,620 | ~Jun |
| Air Temperature & RH | 124,620 | ~Jun |
| Soil Conditions | 124,620 | ~Jun |
| Precipitation Gauge | 593,507 | 2026-08-14 |

The first four report **identical** counts — one Campbell logger under
four names, the shared-logger pattern found at every terrestrial station.
The precipitation gauge is a separate device and stopped **five weeks
later**.

A logger failing and a rain gauge failing at different times is a station
going down, not a collection schedule. Labelling it "hand-collected"
would invent a maintenance cycle that isn't there.

So: a location gets `offline` when it carries at least
`OFFLINE_STATION_MIN_TYPES` sensor types and **none** of them is live. One
quiet channel is a channel; every channel quiet is the station.

## What this changes in practice

**Twelve more instruments are now correctly framed.** The MiniDot and
HOBO fleets stop being "dormant" — which implies broken — and become
hand-collected, which is what they are. They also gain coverage cards
showing what period we hold and what's outstanding.

**Blackwood 2 reads as a station outage**, which is actionable in a
different way: it doesn't need a backfill, it needs someone to notice the
station is down. It's the only west-shore terrestrial station, and its
precipitation gauge is the forcing variable the transect wants most.

**The backfill stages already match.** `nearshore` covers MiniDot and
HOBO — now recognisably a hand-collected backfill rather than a dormant
one. `blackwood` is the offline station's history, worth having even
though the station itself needs a field visit rather than a download.
