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


def check_saturation_basis() -> int:
    """Decide whether the published saturation percentage is altitude-corrected.

    The test: for each reading that has temperature, concentration *and*
    percentage, compute what the concentration would be under each
    hypothesis and see which matches the reported value.

    Both hypotheses are checked against real data rather than one being
    assumed — the same reason the probe commands exist.
    """
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas is required for this check")
        return 1

    path = PROCESSED_DIR / "observations.parquet"
    if not path.exists():
        log.error("no %s — run `pixi run transform` first", path.name)
        return 1

    df = pd.read_parquet(path)
    need = {"site", "timestamp", "variable", "value"}
    if not need.issubset(df.columns):
        log.error("%s is missing %s", path.name, need - set(df.columns))
        return 1

    wanted = ("Temp", "Do_mgL", "Do_percent")
    sub = df[df["variable"].isin(wanted)]
    if sub.empty:
        log.error("no oxygen or temperature observations stored yet")
        return 1

    wide = (sub.pivot_table(index=["site", "timestamp"], columns="variable",
                            values="value", aggfunc="last")
              .reset_index())
    for col in wanted:
        if col not in wide.columns:
            log.error("no %s observations stored — cannot run the check", col)
            return 1
    wide = wide.dropna(subset=list(wanted))
    if wide.empty:
        log.error("no reading has temperature, concentration and percentage "
                  "on the same timestamp")
        return 1

    ratio = pressure_ratio()
    print(f"\n  lake surface        {LAKE_SURFACE_M:,.0f} m")
    print(f"  barometric pressure {ratio*100:.1f}% of sea level "
          f"({760*ratio:.0f} of 760 mmHg)\n")
    print(f"  {len(wide):,} readings carry temperature, concentration and "
          f"percentage\n")

    rows = []
    for _, r in wide.iterrows():
        temp = float(r["Temp"])
        mgl = float(r["Do_mgL"])
        pct = float(r["Do_percent"])
        if not (0 < temp < 40) or mgl <= 0 or pct <= 0:
            continue
        sea = do_saturation_sealevel(temp)
        loc = sea * ratio
        rows.append({
            "site": r["site"],
            "temp": temp,
            "reported_mgl": mgl,
            "reported_pct": pct,
            # What the concentration would be under each hypothesis.
            "implied_sea": pct / 100 * sea,
            "implied_local": pct / 100 * loc,
            # What the percentage would be under each hypothesis.
            "pct_if_sea": mgl / sea * 100,
            "pct_if_local": mgl / loc * 100,
        })

    if not rows:
        log.error("no usable readings after filtering")
        return 1

    n = len(rows)
    err_sea = sum(abs(r["implied_sea"] - r["reported_mgl"]) for r in rows) / n
    err_local = sum(abs(r["implied_local"] - r["reported_mgl"]) for r in rows) / n
    spread = sum(abs(r["implied_sea"] - r["implied_local"]) for r in rows) / n

    print("  Mean absolute error between reported concentration and the")
    print("  concentration implied by the reported percentage:\n")
    print(f"    percentage referenced to SEA LEVEL      {err_sea:6.3f} mg/L")
    print(f"    percentage referenced to LAKE ALTITUDE  {err_local:6.3f} mg/L")
    print(f"\n    the two hypotheses differ by            {spread:6.3f} mg/L\n")

    if spread < DISCRIMINATION_THRESHOLD_MGL:
        print("  INCONCLUSIVE — the hypotheses are too close together at these")
        print("  temperatures for the data to separate them.\n")
        return 0

    if err_sea < err_local:
        print("  CONCLUSION: the published percentage is referenced to")
        print("  SEA-LEVEL pressure and is NOT altitude-corrected.\n")
        sample = rows[0]
        print(f"  Which means a reported {sample['reported_pct']:.1f}% at "
              f"{sample['temp']:.1f} °C is really")
        print(f"  {sample['pct_if_local']:.0f}% of what this water can hold at "
              f"{LAKE_SURFACE_M:,.0f} m.\n")
        if sample["pct_if_local"] > 100:
            print("  That flips the reading: not oxygen-poor, but slightly")
            print("  SUPERSATURATED — which is what photosynthesis does in a")
            print("  sunlit lake, and the opposite of what the raw percentage")
            print("  suggests.\n")
    else:
        print("  CONCLUSION: the published percentage IS altitude-corrected.")
        print("  It can be read at face value.\n")

    print(f"  {'site':14}{'temp':>7}{'mg/L':>8}{'reported %':>12}"
          f"{'% at altitude':>15}")
    print("  " + "-" * 56)
    seen = set()
    for r in rows[::-1]:
        if r["site"] in seen:
            continue
        seen.add(r["site"])
        print(f"  {str(r['site'])[:13]:14}{r['temp']:>6.1f}C"
              f"{r['reported_mgl']:>8.2f}{r['reported_pct']:>11.1f}%"
              f"{r['pct_if_local']:>14.0f}%")
    print()
    print("  Caveat: pressure_ratio() uses the standard atmosphere. Real")
    print("  barometric pressure varies with weather by a few percent, which")
    print("  is smaller than the altitude effect but not nothing. Treat the")
    print("  altitude-referenced percentage as approximate.\n")
    return 0
