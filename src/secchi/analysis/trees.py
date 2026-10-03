"""The trees, working for water: what TEON's band dendrometers show.

    pixi run trees

Five forest stations carry eight band dendrometers each (Blackwood 2,
Glenbrook 2, 4 and 5 since 2024-25; Homewood since 2026-07-27), reporting
``Tree_N_diameter_change`` every 15 minutes. TEON doesn't document the
units; micrometres are inferred, and this analysis supports that: the
daily cycle below is 10-40 units, which is what conifer stems do in µm.

**A stem breathes.** It refills with water overnight and shrinks through
the day as the tree transpires faster than its roots can resupply. In
TEON's data, found 2026-10-02 in 33 of 36 channels (Homewood has four
bands, the others eight): largest around 7 am, smallest around 3-5 pm. The daily drop from the morning peak to the
afternoon low, the *maximum daily shrinkage*, is how hard the tree worked
for water that day:

* near zero in winter (dormant, or frozen), climbing through spring to a
  July-August peak of 30-40 µm, falling again in autumn;
* larger on hotter days at every station (correlation with the day's
  high +0.24 to +0.62, June-September);
* larger when the soil is drier at Blackwood 2 (-0.42), more weakly at
  Glenbrook 2 and 4.

**Not shown, on purpose: growth.** The morning size creeps up over a
season, but separating wood from spring rehydration and from band resets
needs maintenance records TEON doesn't publish. Per-tree estimates for
April-October 2026 ranged from -0.1 to +6.5 mm, which says the method,
not the trees. See docs/trees.md.

**Comparing stations needs care.** Homewood's trees shrink about a third
as much as Glenbrook 5's in the same weeks, which fits the wet-west /
dry-east transect, but which species and what size each band is on isn't
published either, and that alone could do it.
"""

from __future__ import annotations

import logging

import pandas as pd

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.analysis.trees")

SENSOR_TYPE = "TreeStressAndGrowth"
VAR_PREFIX = "Tree_"
# Morning peak and afternoon low windows, local (Pacific) clock hours.
AM_HOURS = (3, 10)
PM_HOURS = (12, 19)
# A tree-day needs most of its readings: 96 a day at 15 minutes.
MIN_READINGS = 80
# Shrinkage outside this range is a reset or a glitch, not a tree.
MDS_RANGE = (-20.0, 400.0)
# Channel checks over the last QC_DAYS days.
QC_DAYS = 30
STILL_RANGE = 3.0           # median daily range below this: not responding
RESET_STEP = 50.0           # a step this big between readings is a reset/glitch
NOISY_RESETS = 20           # more than this many in QC_DAYS: too noisy to use
PROFILE_DAYS = 14


def tree_rows(df: pd.DataFrame) -> pd.DataFrame:
    """The dendrometer channels only, with tree number and local day/hour."""
    t = df[(df["variable"].astype(str).str.startswith(VAR_PREFIX))
           & df["timestamp"].notna() & df["value"].notna()].copy()
    if "sensor_type" in t.columns:
        t = t[t["sensor_type"].astype(str) == SENSOR_TYPE]
    t["timestamp"] = pd.to_datetime(t["timestamp"])
    t["tree"] = t["variable"].str.extract(r"Tree_(\d+)_")[0].astype(int)
    t["day"] = t["timestamp"].dt.normalize()
    t["hour"] = t["timestamp"].dt.hour
    return t[["site", "tree", "timestamp", "day", "hour", "value"]]


def channel_qc(t: pd.DataFrame, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per (site, tree): usable, or why not, over the last QC_DAYS."""
    out = []
    for (site, tree), g in t.groupby(["site", "tree"]):
        last = g["timestamp"].max()
        w = g[g["timestamp"] > (end or last) - pd.Timedelta(days=QC_DAYS)].sort_values("timestamp")
        if w.empty:
            out.append({"site": site, "tree": tree, "ok": False, "why": "no recent data"})
            continue
        s = w.set_index("timestamp")["value"]
        daily_range = float((s.resample("1D").max() - s.resample("1D").min()).median())
        resets = int((s.diff().abs() > RESET_STEP).sum())
        prof = w.groupby("hour")["value"].median()
        peak, trough = int(prof.idxmax()), int(prof.idxmin())
        why = None
        if daily_range < STILL_RANGE:
            why = f"barely moves (median daily range {daily_range:.0f})"
        elif resets > NOISY_RESETS:
            why = f"{resets} jumps of more than {RESET_STEP:.0f} in {QC_DAYS} days"
        elif not (AM_HOURS[0] <= peak <= AM_HOURS[1] and PM_HOURS[0] - 1 <= trough <= PM_HOURS[1]):
            why = f"cycle out of step (largest at {peak}:00, smallest at {trough}:00)"
        out.append({"site": site, "tree": tree, "ok": why is None, "why": why,
                    "daily_range": round(daily_range, 1), "resets": resets,
                    "peak_hour": peak, "trough_hour": trough})
    return pd.DataFrame(out)


def daily_shrinkage(t: pd.DataFrame) -> pd.DataFrame:
    """Maximum daily shrinkage per tree-day: morning peak minus afternoon low."""
    am = t[t["hour"].between(*AM_HOURS)].groupby(["site", "tree", "day"])["value"].max()
    pm = t[t["hour"].between(*PM_HOURS)].groupby(["site", "tree", "day"])["value"].min()
    grp = t.groupby(["site", "tree", "day"])["value"]
    n, rng = grp.count(), grp.max() - grp.min()
    d = pd.DataFrame({"mds": am - pm, "n": n, "range": rng}).dropna().reset_index()
    # A channel that didn't change at all that day wasn't engaged (Glenbrook
    # 4 read flat for its first two months, May-July 2025). A frozen winter
    # stem still moves a little; exactly flat is the sensor, not the tree.
    d = d[(d["n"] >= MIN_READINGS) & (d["range"] > 0) & d["mds"].between(*MDS_RANGE)]
    return d[["site", "tree", "day", "mds"]]


def station_daily(mds: pd.DataFrame, qc: pd.DataFrame) -> pd.DataFrame:
    """Median shrinkage across a station's usable trees, per day.

    Usable means passing today's channel checks. A channel that has gone
    bad recently may have been fine earlier, but one rule for the whole
    record is simpler to state, and the page says which were left out.
    """
    good = qc[qc["ok"]][["site", "tree"]]
    m = mds.merge(good, on=["site", "tree"])
    g = m.groupby(["site", "day"])
    return pd.DataFrame({"mds": g["mds"].median(), "trees": g["tree"].nunique()}).reset_index()


def hourly_profile(t: pd.DataFrame, qc: pd.DataFrame, days: int = PROFILE_DAYS) -> dict[str, list[float]]:
    """The average day, per station: stem size by hour relative to its own daily mean (µm)."""
    good = qc[qc["ok"]][["site", "tree"]]
    w = t.merge(good, on=["site", "tree"])
    out = {}
    for site, g in w.groupby("site"):
        g = g[g["timestamp"] > g["timestamp"].max() - pd.Timedelta(days=days)]
        if g.empty:
            continue
        # Remove each tree-day's mean so trees of different size line up.
        g = g.assign(rel=g["value"] - g.groupby(["tree", "day"])["value"].transform("mean"))
        prof = g.groupby("hour")["rel"].median().reindex(range(24))
        if prof.notna().sum() >= 20:
            out[site] = [None if pd.isna(v) else round(float(v), 1) for v in prof]
    return out


def analyse(df: pd.DataFrame | None = None) -> dict | None:
    if df is None:
        from secchi.store import read_partitions
        df = read_partitions(PROCESSED_DIR / "observations")
    if df is None or df.empty:
        return None
    t = tree_rows(df)
    if t.empty:
        log.error("no dendrometer readings stored")
        return None
    qc = channel_qc(t)
    mds = daily_shrinkage(t)
    st = station_daily(mds, qc)
    return {"qc": qc, "mds": mds, "station": st, "profile": hourly_profile(t, qc),
            "first_day": t["day"].min(), "last_day": t["day"].max()}


def summary(r: dict) -> dict:
    """JSON-ready payload for the page."""
    st, qc = r["station"], r["qc"]
    stations = []
    for site, g in st.groupby("site"):
        g = g.sort_values("day")
        last = g["day"].max()
        wk = g[g["day"] > last - pd.Timedelta(days=7)]
        summer = g[g["day"].dt.month.isin([7, 8])]
        q = qc[qc["site"] == site]
        stations.append({
            "site": site,
            "trees_ok": int(q["ok"].sum()), "trees": int(len(q)),
            "left_out": [f"tree {int(x.tree)}: {x.why}" for x in q[~q["ok"]].itertuples()],
            "first_day": g["day"].min().date().isoformat(),
            "last_day": last.date().isoformat(),
            "last7_um": None if wk.empty else round(float(wk["mds"].median()), 1),
            "summer_um": None if summer.empty else round(float(summer["mds"].median()), 1),
            "winter_um": (None if g[g["day"].dt.month.isin([12, 1, 2])].empty else
                          round(float(g[g["day"].dt.month.isin([12, 1, 2])]["mds"].median()), 1)),
        })
    # Weekly medians for the chart: smooth enough to read, honest about gaps.
    series = {}
    for site, g in st.groupby("site"):
        w = g.set_index("day")["mds"].resample("W").median().dropna()
        series[site] = {"weeks": [d.date().isoformat() for d in w.index],
                        "um": [round(float(v), 1) for v in w]}
    return {"stations": stations, "weekly": series, "profile": r["profile"],
            "profile_days": PROFILE_DAYS,
            "newest_day": r["last_day"].date().isoformat()}


def report() -> int:
    r = analyse()
    if r is None:
        return 1
    s = summary(r)
    print("\n  Stem shrinkage: morning peak minus afternoon low, median of a station's")
    print("  usable trees (um; units inferred). Bigger = the trees worked harder for water.\n")
    print(f"    {'station':14}{'trees':>8}{'last 7 d':>10}{'Jul-Aug':>10}{'Dec-Feb':>10}   since")
    print("    " + "-" * 64)
    f = lambda v: "-" if v is None else f"{v:.0f}"  # noqa: E731
    for x in sorted(s["stations"], key=lambda x: x["site"]):
        print(f"    {x['site']:14}{x['trees_ok']:>4}/{x['trees']:<3}{f(x['last7_um']):>10}"
              f"{f(x['summer_um']):>10}{f(x['winter_um']):>10}   {x['first_day']}")
    print()
    for x in s["stations"]:
        for why in x["left_out"]:
            print(f"    left out, {x['site']} {why}")
    print(f"\n  The average day over the last {PROFILE_DAYS} days (um from the daily mean):")
    for site, prof in sorted(s["profile"].items()):
        vals = [v for v in prof if v is not None]
        hi = prof.index(max(vals)); lo = prof.index(min(vals))
        print(f"    {site:14} largest at {hi:2d}:00 (+{max(vals):.0f}), smallest at {lo:2d}:00 ({min(vals):.0f})")
    print()
    return 0
