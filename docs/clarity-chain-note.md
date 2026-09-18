# Addendum: concentration vs. load

`docs/clarity-chain.md` describes `70369` as the regulated clarity
pollutant. That's right, but the 2026-09-18 probe of Upper Truckee turned
up a companion parameter that is arguably a better match to the
regulation:

| Code | Name | Statistic | Units | Meaning |
|---|---|---|---|---|
| `70369` | Susp sed particles, est | 00011 instantaneous | count/L | **Concentration** — how many fine particles are in the water right now |
| `70372` | Susp sed particle load, est | 00006 sum | count/day | **Load** — how many fine particles the creek delivered today |

The Lake Tahoe TMDL sets its targets as **loads**: particles per year,
allocated across four source categories (urban upland, forest upland,
stream channel erosion, atmospheric deposition), with a required 65 %
reduction basin-wide. So `70372` is the quantity the regulation is written
in, and `70369` is the concentration that produces it once multiplied by
discharge.

Having both, plus discharge (`00060`) at the same gauge, means the
relationship between them is checkable rather than assumed — load should
be approximately concentration × discharge × a unit conversion, and any
systematic departure would say something about how USGS's regression
surrogates are constructed.

Both remain **surrogates computed by regression**, per USGS's own
definitions, not direct particle counts. That caveat applies to the load
as much as the concentration.

## Why this nearly went missing

`70372` is published under statistic `00006` (Sum), because a daily load
is a total. Our client had been pinning `statistic_id=00011` to stop daily
aggregates colliding with instantaneous readings — a fix that was correct
for its purpose and would have silently dropped this series. Now handled
via `USGS_EXTRA_STATISTICS`.

The general lesson for this codebase: **a filter added to prevent bad data
can also prevent good data.** Both times we tightened something on the
USGS side — the statistic pin, and the bounding box — it excluded
something we wanted. The probe modes exist to surface that.
