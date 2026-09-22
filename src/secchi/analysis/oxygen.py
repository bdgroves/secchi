"""Dissolved oxygen: concentration, saturation, and Tahoe's altitude.

Oxygen is the one lake measurement published two ways, and the two are
not interchangeable:

- **Concentration** (``Do_mgL``, mg/L) — how much oxygen is dissolved.
- **Percent saturation** (``Do_percent``, %) — how much relative to what
  water at that temperature and pressure *can* hold.

Both matter. Concentration is what a fish experiences. Saturation tells
you which way the biology is pushing: above 100 % means photosynthesis is
outrunning respiration, below means the reverse.

**Why Tahoe makes this interesting.** Saturation depends on barometric
pressure, and the lake surface sits at roughly 1,898 m — about **79.5 %**
of sea-level pressure. Water that would be 81 % saturated at sea level is
slightly *super*saturated at that altitude. The correction is large enough
to invert the ecological reading, so it's worth knowing whether the
published percentage accounts for it.

A YSI EXO computes saturation from temperature, salinity and barometric
pressure. If the sonde has no barometer, or was configured at a default
sea-level pressure, the percentage will be referenced to the wrong
atmosphere.

This module doesn't assume either way. It reads the stored record and
checks which hypothesis the numbers actually fit.
"""

from __future__ import annotations

import logging
import math

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.analysis.oxygen")

# Lake surface elevation in metres, derived from the USGS datum-corrected
# reading (6,220.00 ft gage datum + gage height, converted).
LAKE_SURFACE_M = 1898.0

# How far apart the two hypotheses must be before the test can distinguish
# them, in mg/L. Below this the answer is "inconclusive" rather than a
# coin flip.
DISCRIMINATION_THRESHOLD_MGL = 0.3


def do_saturation_sealevel(temp_c: float) -> float:
    """Oxygen saturation in fresh water at 1 atm, mg/L.

    Weiss (1970), the standard formulation, valid 0–40 °C. Salinity is
    ignored: Tahoe runs about 0.04 ppt, which changes saturation by well
    under a tenth of a percent.
    """
    t = temp_c + 273.15
    ln_c = (
        -139.34411
        + 1.575701e5 / t
        - 6.642308e7 / t**2
        + 1.243800e10 / t**3
        - 8.621949e11 / t**4
    )
    return math.exp(ln_c)


def pressure_ratio(elevation_m: float = LAKE_SURFACE_M) -> float:
    """Barometric pressure as a fraction of sea level.

    International Standard Atmosphere. Real pressure varies with weather
    by a few percent, which is smaller than the altitude effect here but
    not negligible — a reason the conclusion below is expressed as a
    comparison rather than a correction factor to apply blindly.
    """
    return (1 - 2.25577e-5 * elevation_m) ** 5.25588


def do_saturation_local(temp_c: float,
                        elevation_m: float = LAKE_SURFACE_M) -> float:
    """Oxygen saturation at altitude, mg/L."""
    return do_saturation_sealevel(temp_c) * pressure_ratio(elevation_m)


# Which fields carry concentration, temperature and saturation, per
# instrument family. Three vendors, three naming conventions.
FIELD_SETS: dict[str, dict[str, str]] = {
    "ExoSensor":     {"temp": "Temp",        "mgl": "Do_mgL",
                      "pct": "Do_percent"},
    "MiniDotSensor": {"temp": "Temperature", "mgl": "Dissolved Oxygen",
                      "pct": "Dissolved Oxygen Saturation"},
}


def _verdict(rows: list[dict]) -> dict:
    """Which reference the reported percentage fits, for one group."""
    n = len(rows)
    err_sea = sum(abs(r["implied_sea"] - r["reported_mgl"]) for r in rows) / n
    err_local = sum(abs(r["implied_local"] - r["reported_mgl"]) for r in rows) / n
    spread = sum(abs(r["implied_sea"] - r["implied_local"]) for r in rows) / n

    if spread < DISCRIMINATION_THRESHOLD_MGL:
        basis = "inconclusive"
    elif err_sea < err_local:
        basis = "sea level"
    else:
        basis = "lake pressure"
    return {"n": n, "err_sea": err_sea, "err_local": err_local,
            "spread": spread, "basis": basis}


def check_saturation_basis() -> int:
    """Decide, per instrument family, what the saturation is referenced to.

    Reports per FAMILY rather than pooling. An earlier version gave one
    verdict for the whole network, which was right when we assumed every
    instrument behaved the same — and the data says otherwise. MiniDOT is
    altitude-corrected; the EXO sondes are not. Pooling them averages a
    correct fleet with an incorrect one and describes neither.
    """
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas is required for this check")
        return 1

    from secchi.store import read_partitions

    # The store is partitioned now; the monolithic parquet this
    # originally read was migrated away.
    df = read_partitions(PROCESSED_DIR / "observations")
    if df.empty:
        log.error("no observations stored — run `pixi run transform` first")
        return 1

    need = {"site", "timestamp", "variable", "value", "sensor_type"}
    missing = need - set(df.columns)
    if missing:
        log.error("stored observations are missing %s", ", ".join(sorted(missing)))
        return 1

    ratio = pressure_ratio()
    print(f"\n  lake surface        {LAKE_SURFACE_M:,.0f} m")
    print(f"  barometric pressure {ratio*100:.1f}% of sea level "
          f"({760*ratio:.0f} of 760 mmHg)\n")

    families: dict[str, list[dict]] = {}
    per_site: dict[tuple, list[dict]] = {}

    for family, fields in FIELD_SETS.items():
        sub = df[(df["sensor_type"] == family)
                 & df["variable"].isin(fields.values())]
        if sub.empty:
            continue
        wide = (sub.pivot_table(index=["site", "timestamp"], columns="variable",
                                values="value", aggfunc="last")
                  .reset_index())
        cols = [fields["temp"], fields["mgl"], fields["pct"]]
        if not all(c in wide.columns for c in cols):
            log.info("%s: no reading carries all three of %s",
                     family, ", ".join(cols))
            continue
        wide = wide.dropna(subset=cols)

        for _, r in wide.iterrows():
            temp, mgl, pct = (float(r[fields["temp"]]), float(r[fields["mgl"]]),
                              float(r[fields["pct"]]))
            if not (0 < temp < 40) or mgl <= 0 or pct <= 0:
                continue
            sea = do_saturation_sealevel(temp)
            row = {
                "site": r["site"], "temp": temp,
                "reported_mgl": mgl, "reported_pct": pct,
                "implied_sea": pct / 100 * sea,
                "implied_local": pct / 100 * sea * ratio,
                "pct_if_local": mgl / (sea * ratio) * 100,
            }
            families.setdefault(family, []).append(row)
            per_site.setdefault((family, r["site"]), []).append(row)

    if not families:
        log.error("no reading carries temperature, concentration and "
                  "percentage on the same timestamp")
        return 1

    print("  Mean absolute error between the reported concentration and the")
    print("  concentration implied by the reported percentage:\n")
    print(f"  {'instrument':16}{'readings':>10}{'if sea level':>14}"
          f"{'if lake':>10}   referenced to")
    print("  " + "-" * 68)
    verdicts = {}
    for family, rows in sorted(families.items()):
        v = _verdict(rows)
        verdicts[family] = v
        print(f"  {family:16}{v['n']:>10,}{v['err_sea']:>13.3f}"
              f"{v['err_local']:>10.3f}   {v['basis'].upper()}")
    print()

    bases = {f: v["basis"] for f, v in verdicts.items()}
    if len(set(bases.values())) > 1:
        print("  THE FLEETS DISAGREE.\n")
        for family, basis in sorted(bases.items()):
            print(f"    {family:16} referenced to {basis}")
        print()
        print("  Both instrument families compute saturation from temperature")
        print("  and a configured barometric pressure, so this is not a")
        print("  difference in method. One is configured for the lake's")
        print("  altitude and the other is not.")
        print()
        print("  It also rules out a deliberate sea-level convention adopted")
        print("  for cross-site comparability, which would have been applied")
        print("  to both.\n")

    print(f"  {'instrument':16}{'site':18}{'n':>8}{'temp':>7}"
          f"{'mg/L':>8}{'published':>11}{'at lake':>10}")
    print("  " + "-" * 78)
    for (family, site), rows in sorted(per_site.items()):
        n = len(rows)
        last = rows[-1]
        print(f"  {family:16}{str(site)[:17]:18}{n:>8,}{last['temp']:>6.1f}C"
              f"{last['reported_mgl']:>8.2f}{last['reported_pct']:>10.1f}%"
              f"{last['pct_if_local']:>9.0f}%")
    print()
    print("  'at lake' is computed from the instrument's own concentration")
    print("  and temperature at lake pressure. Where it matches 'published',")
    print("  that instrument is already altitude-corrected.\n")
    print("  Caveat: pressure_ratio() uses the standard atmosphere, while an")
    print("  instrument uses whatever pressure it was configured with, and")
    print("  real barometric pressure moves a few percent with weather. A")
    print("  residual under a point is agreement, not a discrepancy.\n")
    return 0
