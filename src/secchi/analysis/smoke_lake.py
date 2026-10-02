"""Do smoky days leave a mark on the lake?

Smoke can reach a lake two ways: haze cutting the light algae and the water
warm on, and ash and nutrients falling in, which can feed algae over the
following days. This compares days with NOAA HMS smoke over the lake against
clear days, for TEON's lake sondes and, as a check that the smoke reaches the
ground at all, the forest stations' daytime highs.

How, and why each step:

* **Within the same month.** Smoke season is also the season chlorophyll and
  water temperature change on their own, so a smoky July day is compared
  with clear days of that July, never with June or September.
* **Against chance, honestly.** Smoke days come in runs (56 episodes for 114
  days), so the days aren't independent and an ordinary t-test would be far
  too confident. Instead the month's smoke calendar is rotated by a random
  number of days, many times, keeping each episode's shape, and the real
  difference is compared with those rotated ones.
* **With a lag.** Light acts the same day; nutrients over days. Each
  measure is tested on the smoky day itself and up to a week after.
* **With each month's trend removed.** The first run found Glenbrook's
  chlorophyll and blue-green pigment higher on and after smoky days (p as
  low as 0.001), robust to air temperature — and entirely gone once each
  month's straight-line trend was taken out (phycocyanin +0.33 to +0.02,
  p 0.86). Late-summer chlorophyll climbs through the month, from growth
  or from sensor fouling, and late-summer smoke tends to arrive late in the
  month. Rotating the smoke calendar doesn't protect against that; removing
  the trend does. Both figures are reported, so the trap stays visible.

Limits, stated plainly:

* The lake sondes have no summer-2024 data, which was the smokiest summer
  (43 smoke days). The lake comparison rests on summers 2025 and 2026.
* HMS sees smoke from above, at any altitude. The air-temperature check
  asks whether it's near the ground on the days in question.
* Many measures and lags are tested, so a few "significant" results are
  expected by chance alone. The report says how many.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import warnings

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.analysis.smoke_lake")

SEASON = (6, 9)                 # June through September
LAGS = (0, 1, 3, 7)
PERMUTATIONS = 4000
MIN_CLEAR_DAYS = 3

EXO_SITES = ("Glenbrook", "4H Camp", "Sunnyside")
EXO_VARS = {"Chl_a": "chlorophyll", "phycocyanin": "phycocyanin (blue-green algae)",
            "Turbidity": "turbidity", "Temp": "water temperature", "Do_mgL": "dissolved oxygen"}
AIR_VAR = "Air_Temp"

# Physically possible ranges; readings outside are dropped before averaging.
PLAUSIBLE = {"Chl_a": (0, 100), "phycocyanin": (-5, 100), "Turbidity": (-5, 500),
             "Temp": (-2, 35), "Do_mgL": (0, 20), "Air_Temp": (-40, 45)}

# Windows when a sonde's channels were in the wrong columns. Found earlier in
# this project; the scrambled values look plausible, so ranges alone won't
# catch them.
KNOWN_BAD = [
    ("4H Camp", "2026-07-17 14:15", "2026-07-24 13:15"),
    ("Sunnyside", "2026-04-30 11:30", "2026-06-25 08:00"),
]


def _daily(df: pd.DataFrame, site: str, variable: str, how: str) -> pd.Series:
    sub = df[(df["site"] == site) & (df["variable"] == variable)][["timestamp", "value"]]
    if sub.empty:
        return pd.Series(dtype=float)
    ts = pd.to_datetime(sub["timestamp"])
    lo, hi = PLAUSIBLE.get(variable, (-np.inf, np.inf))
    keep = sub["value"].between(lo, hi)
    for s, a, b in KNOWN_BAD:
        if s == site:
            keep &= ~ts.between(pd.Timestamp(a), pd.Timestamp(b))
    sub = sub[keep]
    g = sub.groupby(pd.to_datetime(sub["timestamp"]).dt.normalize())["value"]
    return getattr(g, how)().sort_index()


def detrend(series: pd.Series) -> pd.Series:
    """Remove each month's straight-line trend (see the module docstring)."""
    out = []
    for _, b in series.groupby([series.index.year, series.index.month]):
        if len(b) > 5:
            x = (b.index - b.index[0]).days.to_numpy(float)
            k = np.polyfit(x, b.to_numpy(float), 1)
            out.append(b - np.polyval(k, x))
    return pd.concat(out).sort_index() if out else series.iloc[0:0]


def _blocks(series: pd.Series, smoke: pd.Series, lag: int):
    """Per (year, month): smoke labels and the measure `lag` days later."""
    y = series.copy()
    y.index = y.index - pd.Timedelta(days=lag)          # y at day d is the value at d+lag
    out = []
    for (yr, mo), lab in smoke.groupby([smoke.index.year, smoke.index.month]):
        if not SEASON[0] <= mo <= SEASON[1]:
            continue
        vals = y.reindex(lab.index).to_numpy(dtype=float)
        labs = lab.to_numpy(dtype=float)                 # 0 clear, >0 smoky, nan not analysed
        if np.nansum(labs > 0) == 0:
            continue
        out.append((labs, vals))
    return out


def _block_stats(labs: np.ndarray, vals: np.ndarray):
    """(difference smoky-minus-clear, weight) for every rotation of the labels."""
    n = len(labs)
    diffs, weights = np.full(n, np.nan), np.zeros(n)
    ok = ~np.isnan(vals)
    for k in range(n):
        lab = np.roll(labs, k)
        smoky = (lab > 0) & ok
        clear = (lab == 0) & ok
        if smoky.sum() and clear.sum() >= MIN_CLEAR_DAYS:
            diffs[k] = vals[smoky].mean() - vals[clear].mean()
            weights[k] = smoky.sum()
    return diffs, weights


def test(series: pd.Series, smoke: pd.Series, lag: int, rng) -> dict | None:
    blocks = [_block_stats(l, v) for l, v in _blocks(series, smoke, lag)]
    blocks = [(d, w) for d, w in blocks if w[0] > 0]
    if not blocks:
        return None
    w0 = np.array([w[0] for _, w in blocks])
    observed = float(np.sum([d[0] * w[0] for d, w in blocks]) / w0.sum())
    # Null: an independent random rotation in each month.
    draws = np.empty(PERMUTATIONS)
    picks = [rng.integers(1, len(d), PERMUTATIONS) if len(d) > 1 else np.zeros(PERMUTATIONS, int)
             for d, _ in blocks]
    for i in range(PERMUTATIONS):
        num = den = 0.0
        for (d, w), p in zip(blocks, picks):
            k = p[i]
            if w[k] > 0 and not np.isnan(d[k]):
                num += d[k] * w[k]; den += w[k]
        draws[i] = num / den if den else np.nan
    draws = draws[~np.isnan(draws)]
    p = (np.sum(np.abs(draws) >= abs(observed)) + 1) / (len(draws) + 1)
    # Scale: the typical day-to-day spread within those months.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        spread = np.nanmean([np.nanstd(v) for l, v in _blocks(series, smoke, lag)])
    return {"effect": observed, "p": float(p), "smoke_days": int(w0.sum()),
            "months": len(blocks), "spread": float(spread) if spread else None}


def analyse(df: pd.DataFrame | None = None, smoke_df: pd.DataFrame | None = None,
            seed: int = 2026) -> dict | None:
    from secchi.store import read_partitions
    if smoke_df is None:
        smoke_df = read_partitions(PROCESSED_DIR / "smoke_observations")
    if smoke_df is None or smoke_df.empty:
        log.error("no smoke record; run `pixi run smoke --since 2024-06-01` first")
        return None
    if df is None:
        df = read_partitions(PROCESSED_DIR / "observations")

    sm = smoke_df[smoke_df["variable"] == "smoke_density"]
    smoke = (sm.assign(day=pd.to_datetime(sm["timestamp"]).dt.normalize())
               .groupby("day")["value"].max().sort_index())
    smoke = smoke.reindex(pd.date_range(smoke.index.min(), smoke.index.max(), freq="D"))

    rng = np.random.default_rng(seed)
    rows = []
    def both(s, lag):
        """The test on the trend-removed series, with the raw result alongside."""
        d = test(detrend(s), smoke, lag, rng)
        raw = test(s, smoke, lag, rng)
        if d and raw:
            d["raw_effect"], d["raw_p"] = raw["effect"], raw["p"]
        return d

    for site in sorted(df.loc[df["variable"] == AIR_VAR, "site"].unique()):
        s = _daily(df, site, AIR_VAR, "max")
        r = both(s, 0)
        if r:
            rows.append({"group": "air", "site": site, "measure": "daytime high, air",
                         "variable": AIR_VAR, "lag": 0, **r})
    for site in EXO_SITES:
        for var, label in EXO_VARS.items():
            s = _daily(df, site, var, "mean")
            if s.empty:
                continue
            for lag in LAGS:
                r = both(s, lag)
                if r:
                    rows.append({"group": "lake", "site": site, "measure": label,
                                 "variable": var, "lag": lag, **r})
    res = pd.DataFrame(rows)
    usable = smoke[(smoke.index.month >= SEASON[0]) & (smoke.index.month <= SEASON[1])]
    return {"results": res, "smoke_days": int((smoke > 0).sum()),
            "season_smoke_days": int((usable > 0).sum())}


def report() -> int:
    r = analyse()
    if r is None:
        return 1
    res = r["results"]
    if res.empty:
        print("\n  no overlap between smoke days and instrument records\n")
        return 0
    print(f"\n  Smoke over the lake (NOAA HMS): {r['smoke_days']} days, "
          f"{r['season_smoke_days']} of them June to September.")
    print("  Smoky days against clear days of the same month, each month's trend")
    print(f"  removed; p from rotating each month's smoke calendar {PERMUTATIONS:,} times.")
    print("  The raw figure (trend left in) is shown for comparison.\n")

    air = res[res["group"] == "air"]
    if len(air):
        print("  Does the smoke reach the ground? Daytime high air temperature:")
        for row in air.itertuples():
            print(f"    {row.site:18} {row.effect:+5.2f} C on smoky days   p={row.p:.3f}   "
                  f"(raw {row.raw_effect:+.2f}, p={row.raw_p:.3f}; {row.smoke_days} smoky days)")
        print()

    lake = res[res["group"] == "lake"]
    if len(lake):
        print(f"  {'sonde':10}{'measure':32}{'lag':>4}{'smoky-clear':>13}{'p':>8}   {'raw (trend left in)':>20}")
        for row in lake.itertuples():
            flag = " *" if row.p < 0.05 else "  "
            print(f"  {row.site:10}{row.measure:32}{row.lag:>4}{row.effect:>13.3f}{row.p:>8.3f}{flag}"
                  f"  {row.raw_effect:>+8.3f} (p {row.raw_p:.3f})")
        n, hits = len(lake), int((lake["p"] < 0.05).sum())
        raw_hits = int((lake["raw_p"] < 0.05).sum())
        print(f"\n  {hits} of {n} lake tests below p = 0.05 with trends removed (about {n * 0.05:.0f} "
              f"expected by chance); {raw_hits} without.")
    print("\n  The lake sondes have no summer-2024 data, the smokiest summer; the lake")
    print("  comparison rests on summers 2025 and 2026.\n")
    return 0
