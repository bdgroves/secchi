# Units

The dashboard shows every convertible measurement in either metric or
imperial, toggled top-right and remembered in `localStorage`. Worth
documenting because the two sources disagree — **TEON publishes metric,
USGS publishes imperial** — so something has to convert regardless.

## Where the conversion happens

**Card readings convert server-side.** `transform.py` emits each reading
with its source `value`/`units` plus an `alt` block in the other system.
The browser picks which to display. The source value is never overwritten,
and there's no duplicated conversion logic.

**Two kinds of number can't be pre-converted, and convert client-side:**

- **Trend deltas and ranges** on the sparklines. The trend is computed from
  the stored series, so its `change`, `min` and `max` arrive in the source
  unit.
- **Datum comparison lines** under lake elevation — the `+4.07 natural rim`
  figures — which are differences derived from reference levels in feet.
- **Catchment attributes** in the map choropleth, read from a GeoJSON that
  knows nothing about display preferences.

## The part that's easy to get wrong

**A difference does not convert like a value.**

```
value:   °F = °C × 1.8 + 32
delta:   ΔF = ΔC × 1.8          ← no offset
```

A 0.34 °C drop is a **0.61 °F** drop. Apply the value formula to a
difference and you get 32.61 °F — a plausible-looking number that is
completely wrong.

So `convert(value, unit, isDelta)` takes a third argument, and it matters
in exactly one place: temperature. Every other unit here (ft, m, mm, cm,
km², flow) converts by a pure factor, where value and delta are identical.

Both cases are covered:

| | stored | imperial | metric |
|---|---|---|---|
| water temp, value | 16.4 °C | 61.5 °F | 16.4 °C |
| water temp, delta | 0.34 °C | **0.61 °F** | 0.34 °C |
| lake elevation, value | 6,227.07 ft | 6,227.07 ft | 1,898.01 m |
| rim comparison, delta | 4.07 ft | 4.07 ft | **1.24 m** |

## Two bugs this fixed

**Trend deltas were printed in the source unit regardless of the toggle.**
In imperial mode a card read `61.5 °F` with `falling 0.34` beneath it — a
Celsius delta next to a Fahrenheit value. Wrong by a factor of 1.8, under
every reading on the page.

**Datum lines never converted at all.** Metric mode showed
`1,898.01 m` for lake elevation with `+4.07 natural rim` underneath —
a foot value, unlabelled, beside a metre one. Now `+1.24 m`.

Units are now printed on the deltas too, which they weren't before. That
alone would have made the first bug visible.

## A cosmetic case worth knowing about

A converted delta can round away. Blackwood's gage height falling 0.01 ft
is 0.003 m, which formats as `0` — and `↘ falling 0 m` reads as a
contradiction.

The direction is still real; the trend was classified from the underlying
data, not from the rounded display value. So when the delta rounds to
zero, the magnitude is dropped and only the direction shows. Better than
either printing `0` or inventing precision the reading doesn't have.

## What deliberately doesn't convert

| | Why |
|---|---|
| FNU, µg/L, mg/L, %, µS/cm | Dimensionless or system-neutral; marked as `"both"` in `UNIT_CONVERSIONS` |
| µm (dendrometers) | No imperial counterpart in practical use |
| Observation and frame counts | Dimensionless |
| Hours, minutes | Time is time |
| Topographic Wetness Index, slope in degrees | Indices and angles |
| `"64 metres apart in latitude"` in prose | Body text, not a reading. Inconsistent, low stakes. |
| Choropleth colour scaling | Normalised in stored units on purpose — the ramp only needs a 0–1 position, and doing it pre-conversion keeps the colours identical in both systems. Converting first would be harmless for pure factors but would shift temperature, because of the offset. |

The neutral list is explicit rather than implied: an entry missing from
`UNIT_CONVERSIONS` means *we forgot*, not *no conversion needed*.
