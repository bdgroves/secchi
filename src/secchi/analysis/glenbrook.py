"""Why is Glenbrook 2 wet?

Glenbrook 2 reads about **42 % volumetric water content** while every
other terrestrial station in the network sits between 3.4 % and 11.5 %
— including Glenbrook 4 and Glenbrook 5, on the same hillslope, in the
same catchment, a few hundred metres away.

The catchment join can't explain it. All three Glenbrook stations fall
in the Glenbrook Creek catchment and therefore carry *identical*
attributes: 689 mm precipitation, TWI 10.9, 178 cm to the water table.
Catchment means describe variation between catchments, not within one.

Yesterday the answer was "this needs the source rasters sampled at each
station point", which is real GIS work.

The backfill made a cheaper test possible. **Glenbrook 2 is the only
terrestrial station that also carries a stream level sensor**, and we
now hold twenty months of both. So:

    does its soil moisture track its own stream?

If it does, the site is hydrologically connected to the channel — bank
storage or shallow groundwater — and that explains 42 % against 3.5 %
on the same hillslope without appealing to soil texture at all. If it
doesn't, the wetness is something else and the raster work is
justified.

## What this can and can't establish

Correlation between two signals that both respond to rainfall is not
evidence of a connection between them — a storm raises the creek and
wets the soil independently. So the test needs to be sharper than a
raw correlation:

1. **Compare against a control.** Glenbrook 4 and 5 sit on the same
   hillslope in the same catchment with no stream. If Glenbrook 2's
   soil tracks the creek *more closely than theirs does*, the
   difference isn't shared weather.
2. **Look at recession, not peaks.** Both respond to rain. What
   separates them is what happens afterwards: a hillslope soil dries
   on its own schedule, while a bank-storage soil follows the stream
   down.
3. **Check the baseline.** A connected site should stay wet during dry
   spells when the creek still has water. That is the 42 % itself.
"""

from __future__ import annotations

import logging

import pandas as pd

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.analysis.glenbrook")

WET_SITE = "Glenbrook 2"
CONTROL_SITES = ("Glenbrook 4", "Glenbrook 5")
SOIL_VARIABLE = "Soil_VWC"
# The field is literally named "Uncalibrated_water_depth" upstream, and
# Glenbrook 2 reads ~0.001 m — either a dry channel or a pending datum
# correction. That doesn't matter here: this test uses the SHAPE of the
# signal, not its absolute value, and a correlation is invariant to
# whatever constant offset the calibration would apply.
STREAM_VARIABLE = "Uncalibrated_water_depth"
VWC_DISPLAY_SCALE = 100.0

# Days with no wetting anywhere, used for the recession comparison. A
# storm correlates everything; the interesting behaviour is between them.
DRY_SPELL_MIN_DAYS = 5


def _daily(df: pd.DataFrame, site: str, variable: str) -> pd.Series:
    """Daily mean for one site and variable.

    Daily rather than hourly for the same reason the transect needs it:
    soil moisture has a strong diurnal cycle, and an hourly correlation
    would largely measure two sensors both following the sun.
    """
    sub = df[(df["site"] == site) & (df["variable"] == variable)
             & df["timestamp"].notna() & df["value"].notna()]
    if sub.empty:
        return pd.Series(dtype=float)
    return (sub.set_index("timestamp")["value"]
               .sort_index().resample("1D").mean().dropna())


def _align(*series: pd.Series) -> pd.DataFrame:
    """Join series on their common days."""
    frame = pd.concat(series, axis=1).dropna()
    return frame


def _recession_days(stream: pd.Series, min_run: int = 3) -> pd.DatetimeIndex:
    """Days within a run of at least ``min_run`` consecutive declines.

    Single down-days are noise. A run of three means the storm has
    passed and the catchment is draining, which is the regime where a
    connected soil and a hillslope soil behave differently.
    """
    falling = stream.diff() < 0
    runs: list[pd.Timestamp] = []
    current: list[pd.Timestamp] = []
    for day, is_falling in falling.items():
        if is_falling:
            current.append(day)
        else:
            if len(current) >= min_run:
                runs.extend(current)
            current = []
    if len(current) >= min_run:
        runs.extend(current)
    return pd.DatetimeIndex(runs)


def _recession_tau(series: pd.Series,
                   days: pd.DatetimeIndex,
                   min_run: int = 5) -> float | None:
    """Recession time constant in days, from a log-linear fit.

    CORRELATION IS THE WRONG TOOL HERE, which took two attempts to see.
    During a recession everything is declining smoothly, and any two
    smooth declines correlate highly regardless of mechanism — a
    directly stream-driven soil scored 0.967 against hillslope controls
    at 0.843 and 0.869, a margin too small to conclude anything from.

    What actually separates them is the RATE. A soil connected to the
    channel drains on the channel's schedule; a hillslope soil drains on
    its own. So fit an exponential to each recession run and take the
    median time constant.

    Fitted above each run's own floor, so the constant describes the
    decaying part rather than the baseline it decays toward.
    """
    import numpy as np

    sub = series.loc[series.index.intersection(days)].sort_index()
    if sub.empty:
        return None

    # Split into consecutive runs.
    runs: list[pd.Series] = []
    current: list = []
    prev = None
    for day, value in sub.items():
        if prev is not None and (day - prev).days > 1:
            if len(current) >= min_run:
                runs.append(sub.loc[current[0]:current[-1]])
            current = []
        current.append(day)
        prev = day
    if len(current) >= min_run:
        runs.append(sub.loc[current[0]:current[-1]])

    taus: list[float] = []
    for run in runs:
        floor = float(run.min())
        above = run - floor
        # Drop the tail where it's at the floor; log needs positives.
        above = above[above > above.max() * 0.02]
        if len(above) < min_run:
            continue
        t = (above.index - above.index[0]).days.to_numpy(dtype=float)
        y = np.log(above.to_numpy(dtype=float))
        if not np.isfinite(y).all() or np.ptp(t) == 0:
            continue
        slope = np.polyfit(t, y, 1)[0]
        if slope < 0:
            taus.append(-1.0 / slope)

    if not taus:
        return None
    return float(pd.Series(taus).median())


def _recession_run_lengths(series: pd.Series,
                           days: pd.DatetimeIndex,
                           min_run: int = 5) -> list[int]:
    """Lengths of the runs the tau fit actually used.

    Needed because tau is only meaningful if the runs are long enough to
    contain it. On the real record every series returned 2.9-3.2 days,
    a stream and three soils at different moisture levels landing within
    0.3 days of each other. That uniformity is the method measuring its
    own window:

        tau  ~  run_length / log_range

    and with 6-day runs spanning ~2 e-folds, everything returns ~3 days
    regardless of how fast it actually drains. The synthetic test had
    30-day recessions, so the fit had room to separate them; real Tahoe
    storms arrive closer together than that.
    """
    sub = series.loc[series.index.intersection(days)].sort_index()
    if sub.empty:
        return []
    lengths: list[int] = []
    current = 0
    prev = None
    for day in sub.index:
        if prev is not None and (day - prev).days > 1:
            if current >= min_run:
                lengths.append(current)
            current = 0
        current += 1
        prev = day
    if current >= min_run:
        lengths.append(current)
    return lengths


# A tau this close to the run length is pinned by the window rather than
# by the data. Ratio of median run length to tau; below this, the
# estimate can't be trusted to discriminate.
TAU_VALIDITY_RATIO = 3.0


def analyse(df: pd.DataFrame | None = None) -> dict | None:
    """Test whether Glenbrook 2's soil moisture follows its stream."""
    if df is None:
        from secchi.store import read_partitions
        df = read_partitions(PROCESSED_DIR / "observations")
    if df is None or df.empty:
        log.error("no observations stored")
        return None

    soil = _daily(df, WET_SITE, SOIL_VARIABLE)
    stream = _daily(df, WET_SITE, STREAM_VARIABLE)
    if soil.empty:
        log.error("no soil moisture for %s", WET_SITE)
        return None
    if stream.empty:
        log.error("no stream level for %s — that sensor is what makes "
                  "this test possible", WET_SITE)
        return None

    soil.name, stream.name = "soil", "stream"
    result: dict = {
        "site": WET_SITE,
        "soil_mean": float(soil.mean()) * VWC_DISPLAY_SCALE,
        "soil_min": float(soil.min()) * VWC_DISPLAY_SCALE,
        "soil_max": float(soil.max()) * VWC_DISPLAY_SCALE,
        "days": int(len(soil)),
    }

    paired = _align(soil, stream)
    result["paired_days"] = int(len(paired))
    if len(paired) >= 30:
        result["r_soil_stream"] = float(paired["soil"].corr(paired["stream"]))
        # Day-over-day change correlates the dynamics rather than the
        # levels. Two signals can share a seasonal shape without either
        # driving the other; their daily deltas are a harder test.
        deltas = paired.diff().dropna()
        if len(deltas) >= 30:
            result["r_delta"] = float(deltas["soil"].corr(deltas["stream"]))

    # RECESSION is the discriminating test. Everything correlates during
    # a storm — rain raises the creek and wets every soil at once, which
    # is why the raw correlations cluster. What separates a connected
    # site is what happens AFTERWARDS: a hillslope soil drains on its own
    # schedule, while a bank-storage soil follows the stream down.
    #
    # So: take the days when the stream is receding, and compare how fast
    # each soil declines relative to it.
    recession = _recession_days(stream)
    result["recession_days"] = int(len(recession))
    if len(recession) >= 20:
        result["tau_soil"] = _recession_tau(soil, recession)
        result["tau_stream"] = _recession_tau(stream, recession)
        runs = _recession_run_lengths(stream, recession)
        if runs:
            result["median_run_days"] = float(pd.Series(runs).median())
            result["n_runs"] = len(runs)
            taus = [t for t in (result["tau_soil"], result["tau_stream"]) if t]
            if taus:
                # Can the runs resolve the differences we'd be claiming?
                result["tau_trustworthy"] = (
                    result["median_run_days"] / max(taus) >= TAU_VALIDITY_RATIO)

    # Controls: same hillslope, same catchment, no stream of their own.
    controls: dict = {}
    for site in CONTROL_SITES:
        c_soil = _daily(df, site, SOIL_VARIABLE)
        if c_soil.empty:
            continue
        c_soil.name = "soil"
        joined = _align(c_soil, stream)
        entry = {
            "soil_mean": float(c_soil.mean()) * VWC_DISPLAY_SCALE,
            "paired_days": int(len(joined)),
        }
        if len(joined) >= 30:
            entry["r_soil_stream"] = float(joined["soil"].corr(joined["stream"]))
            d = joined.diff().dropna()
            if len(d) >= 30:
                entry["r_delta"] = float(d["soil"].corr(d["stream"]))
        if len(recession) >= 20:
            entry["tau_soil"] = _recession_tau(c_soil, recession)
        controls[site] = entry
    result["controls"] = controls

    return result


def report() -> int:
    """Print the Glenbrook 2 comparison."""
    r = analyse()
    if r is None:
        return 1

    print(f"\n  {r['site']}: why is it wet?\n")
    print(f"    soil moisture   mean {r['soil_mean']:.1f}%   "
          f"range {r['soil_min']:.1f}–{r['soil_max']:.1f}%")
    print(f"    record          {r['days']:,} days\n")

    ctl = r.get("controls") or {}
    if ctl:
        print("    same hillslope, same catchment, no stream of their own:\n")
        for site, c in sorted(ctl.items()):
            print(f"      {site:16} mean {c['soil_mean']:.1f}%")
        print()

    rs = r.get("r_soil_stream")
    if rs is None:
        print("    Not enough paired days to correlate soil against stream.\n")
        return 0

    print(f"    correlation with its OWN stream level, daily means:\n")
    print(f"      {'site':18}{'r (level)':>11}{'r (day-to-day change)':>24}")
    print("      " + "-" * 53)
    rd = r.get("r_delta")
    print(f"      {r['site']:18}{rs:>11.3f}"
          f"{(f'{rd:.3f}' if rd is not None else '—'):>24}")
    for site, c in sorted(ctl.items()):
        c_rs = c.get("r_soil_stream")
        c_rd = c.get("r_delta")
        print(f"      {site:18}"
              f"{(f'{c_rs:.3f}' if c_rs is not None else '—'):>11}"
              f"{(f'{c_rd:.3f}' if c_rd is not None else '—'):>24}")
    print()

    tau_soil = r.get("tau_soil")
    tau_stream = r.get("tau_stream")
    tau_ctl = {s_: c.get("tau_soil") for s_, c in ctl.items()
               if c.get("tau_soil") is not None}

    trustworthy = r.get("tau_trustworthy")
    if tau_soil is not None and tau_stream is not None and trustworthy is False:
        print(f"    RECESSION RATE — NOT USABLE ON THIS RECORD\n")
        print(f"      {r.get('n_runs', 0)} recession runs, median "
              f"{r.get('median_run_days', 0):.0f} days")
        print(f"      fitted time constants all ~{tau_stream:.1f} d\n")
        print("      A run has to be several times longer than the time")
        print("      constant for the fit to see it. These are about the")
        print("      same length, so every series returns roughly the same")
        print("      number whatever its real drainage rate — the method is")
        print("      measuring its own window.\n")
        print("      Synthetic data with 30-day recessions separated a")
        print("      connected site (1.10x) from hillslope controls (1.59x,")
        print("      1.61x) cleanly. Real storms here arrive closer together.\n")
    elif tau_soil is not None and tau_stream is not None:
        print(f"    RECESSION RATE ({r.get('recession_days', 0)} days) — how")
        print("    fast each drains once the storm has passed:\n")
        print(f"      {'':18}{'time constant':>16}{'vs stream':>12}")
        print("      " + "-" * 46)
        print(f"      {'the creek':18}{tau_stream:>13.1f} d{'—':>12}")
        print(f"      {r['site']:18}{tau_soil:>13.1f} d"
              f"{tau_soil / tau_stream:>12.2f}")
        for site, v in sorted(tau_ctl.items()):
            print(f"      {site:18}{v:>13.1f} d{v / tau_stream:>12.2f}")
        print()

    if trustworthy is False:
        # Fall back to what the data CAN say.
        rd = r.get("r_delta")
        ctl_rd = {s_: c.get("r_delta") for s_, c in ctl.items()
                  if c.get("r_delta") is not None}
        if rd is not None and ctl_rd:
            print("    What the record does support, day-to-day:\n")
            print(f"      {r['site']:18}r = {rd:.3f}")
            for site, v in sorted(ctl_rd.items()):
                print(f"      {site:18}r = {v:.3f}")
            print()
            if rd < min(ctl_rd.values()):
                print("      Glenbrook 2 tracks the creek LESS closely than the")
                print("      hillslope controls do. Weak evidence against a")
                print("      stream connection, not for one.\n")
        print("    VERDICT: unexplained. The cheap test doesn't work on this")
        print("    record, and what signal there is points away from a stream")
        print("    connection. Sampling the source rasters at each station")
        print("    point — soil depth, texture, aspect — is the honest next")
        print("    step, and it is real GIS work rather than another query.\n")
    elif tau_soil is not None and tau_stream is not None and tau_ctl:
        # A connected soil drains at roughly the stream's rate; a
        # hillslope soil drains faster. Compare how close each is.
        wet_ratio = tau_soil / tau_stream
        ctl_ratios = [v / tau_stream for v in tau_ctl.values()]
        best_control = min(ctl_ratios, key=lambda x: abs(x - 1.0))
        margin = abs(best_control - 1.0) - abs(wet_ratio - 1.0)
        print(f"    Glenbrook 2 drains at {wet_ratio:.2f}x the creek's rate; "
              f"the closest\n    control is at {best_control:.2f}x.\n")
        if margin > 0.25:
            print("    As the creek drains, Glenbrook 2's soil drains WITH")
            print("    it, while the hillslope stations dry on their own")
            print("    schedule. That is what a hydrological connection")
            print("    looks like — bank storage or shallow groundwater,")
            print("    not a difference in soil texture.")
        elif margin < -0.1:
            print("    A control drains closer to the creek's rate than")
            print("    Glenbrook 2 does, which argues against a stream")
            print("    connection. The wetness is something else.")
        else:
            print("    All three drain at similar rates relative to the")
            print("    creek. This test does not separate a stream")
            print("    connection from shared weather, and the raster work")
            print("    would be the next step.")
    print()
    print("    The absolute time constants are approximate — daily")
    print("    resampling and the baseline estimate both bias them, and on")
    print("    synthetic data with known values the fit recovered the")
    print("    ordering but not the magnitudes. The RATIO is what the")
    print("    conclusion rests on.\n")
    print("    Caveat: this is observational. A shared drainage rate is")
    print("    consistent with a hydrological connection and does not")
    print("    prove one — two sites can drain alike for unrelated")
    print("    reasons. The controls are what make it more than a")
    print("    correlation, and the rate is what makes it more than")
    print("    'everything responds to rain'.\n")
    return 0
