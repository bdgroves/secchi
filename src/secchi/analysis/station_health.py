"""Station health, from the loggers' own battery voltage.

Two of TEON's seven terrestrial stations have gone dark: Blackwood 2 in
June, Glenbrook 2 on 2026-09-22. Both took every channel with them —
air, soil, tree, stream level, camera — which rules out individual
sensor faults and points at something shared: power, telemetry, or the
logger.

From outside we can't see which. But Campbell loggers report
``BattV_Avg``, their own supply voltage, in every record. We hold twenty
months of it for all seven stations, which makes three questions
answerable rather than speculative:

1. Was a station's battery declining before it died?
2. How long was the warning?
3. **Is any station showing that decline right now?**

The third is the useful one. A remote station's battery doesn't fail
suddenly — it sags under load, recovers less each morning, and finally
can't carry the logger through the night. That's visible weeks ahead if
anyone is looking.

## What voltage means on these loggers

A Campbell datalogger runs on a 12 V sealed lead-acid battery on solar
charge. Rough guide, and deliberately rough because the thresholds
depend on battery chemistry, temperature and what else is on the rail:

===========  ==========================================================
 > 12.5 V     healthy, charging
 12.0-12.5    normal working range
 11.5-12.0    worth watching
 < 11.5       many loggers refuse to write; sensors may brown out first
===========  ==========================================================

The absolute numbers matter less than the **trend** and the **daily
minimum**. A battery that reaches 13 V each afternoon and 11.6 V each
dawn is in more trouble than one sitting flat at 12.2 V, because the
overnight floor is what actually stops the logger.
"""

from __future__ import annotations

import logging

import pandas as pd

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.analysis.station_health")

BATTERY_VARIABLE = "BattV_Avg"

# Below this the logger is at risk overnight. Advisory, not a spec —
# see the module docstring.
LOW_VOLTAGE = 11.5
WATCH_VOLTAGE = 12.0

# A trend only matters relative to how much headroom is left. A
# battery losing 0.04 V/week at 12.9 V has eight months before it
# reaches the floor; the same slope at 11.6 V has weeks.
#
# The first version triggered on slope alone and called Blackwood 2 —
# which died at 12.84 V, entirely healthy — "a power failure with a
# visible run-up". It read a trend without looking at the level.
#
# So: flag when the projected time to reach LOW_VOLTAGE is shorter
# than this.
WEEKS_OF_HEADROOM = 12

# Days over which to measure the trend. Long enough to see through
# weather, short enough to catch a decline while it's still a warning.
TREND_DAYS = 30

# A station this stale is treated as down rather than declining.
DARK_AFTER_HOURS = 36


def _now_like(index: pd.DatetimeIndex) -> pd.Timestamp:
    """Current time, matched to whether ``index`` carries a timezone.

    The stored timestamps come back from parquet tz-NAIVE, while my
    synthetic test built them tz-aware — so comparing against
    ``Timestamp.now(tz="UTC")`` worked in testing and raised
    ``Cannot subtract tz-naive and tz-aware`` on the real record.

    Rather than assume either way, match whatever the data has.
    """
    tz = getattr(index, "tz", None)
    return pd.Timestamp.now(tz=tz) if tz is not None else pd.Timestamp.now()


def _battery(df: pd.DataFrame, site: str) -> pd.DataFrame:
    """Daily mean, minimum and maximum supply voltage for one station."""
    sub = df[(df["site"] == site) & (df["variable"] == BATTERY_VARIABLE)
             & df["timestamp"].notna() & df["value"].notna()]
    if sub.empty:
        return pd.DataFrame()
    daily = sub.set_index("timestamp")["value"].sort_index().resample("1D")
    out = pd.DataFrame({"mean": daily.mean(), "min": daily.min(),
                        "max": daily.max()}).dropna()
    return out


def _slope_per_week(series: pd.Series) -> float | None:
    """Least-squares trend in volts per week."""
    if len(series) < 7:
        return None
    import numpy as np
    days = (series.index - series.index[0]).days.to_numpy(dtype=float)
    if np.ptp(days) == 0:
        return None
    return float(np.polyfit(days, series.to_numpy(dtype=float), 1)[0] * 7.0)


def analyse(df: pd.DataFrame | None = None) -> dict | None:
    """Battery state and trend for every station reporting voltage."""
    if df is None:
        from secchi.store import read_partitions
        df = read_partitions(PROCESSED_DIR / "observations")
    if df is None or df.empty:
        log.error("no observations stored")
        return None

    sites = sorted(df[df["variable"] == BATTERY_VARIABLE]["site"].unique())
    if not sites:
        log.error("no %s readings stored — this analysis needs the "
                  "terrestrial loggers' supply voltage", BATTERY_VARIABLE)
        return None

    out: dict = {"stations": {}}

    for site in sites:
        batt = _battery(df, site)
        if batt.empty:
            continue

        last_day = batt.index.max()
        hours_stale = (_now_like(batt.index) - last_day).total_seconds() / 3600
        recent = batt.loc[batt.index >= last_day - pd.Timedelta(days=TREND_DAYS)]

        entry = {
            "last_reading": last_day.isoformat(),
            "hours_stale": round(hours_stale, 1),
            "dark": hours_stale > DARK_AFTER_HOURS,
            "days_of_record": int(len(batt)),
            "final_mean": round(float(batt["mean"].iloc[-1]), 2),
            "final_min": round(float(batt["min"].iloc[-1]), 2),
            # The overnight floor is what stops a logger, so track the
            # minimum separately from the mean.
            "recent_floor": round(float(recent["min"].min()), 2),
            "trend_v_per_week": None,
            "weeks_to_floor": None,
            # The floor over the LAST WEEK, not the last 30 days. A
            # station that dipped a month ago and recovered is not at
            # risk now, and the 30-day minimum reads history as a
            # forecast.
            "current_floor": round(
                float(batt["min"].tail(7).min()), 2),
        }
        slope = _slope_per_week(recent["mean"])
        if slope is not None:
            entry["trend_v_per_week"] = round(slope, 3)
            if slope < -0.001:
                headroom = entry["current_floor"] - LOW_VOLTAGE
                entry["weeks_to_floor"] = round(headroom / abs(slope), 1)
        out["stations"][site] = entry

    return out


def report() -> int:
    """Print station battery health, dark stations first."""
    r = analyse()
    if r is None:
        return 1

    stations = r["stations"]
    if not stations:
        print("\n  No battery readings stored.\n")
        return 0

    dark = {s: v for s, v in stations.items() if v["dark"]}
    live = {s: v for s, v in stations.items() if not v["dark"]}

    print(f"\n  Supply voltage at {len(stations)} terrestrial station(s)\n")

    if dark:
        print("  DARK — no recent reading:\n")
        print(f"    {'station':20}{'last seen':>12}{'final mean':>12}"
              f"{'final min':>11}{'30d trend':>12}")
        print("    " + "-" * 67)
        for site, v in sorted(dark.items(), key=lambda kv: -kv[1]["hours_stale"]):
            days = v["hours_stale"] / 24
            trend = (f"{v['trend_v_per_week']:+.3f} V/wk"
                     if v["trend_v_per_week"] is not None else "—")
            print(f"    {site:20}{days:>9.0f} d{v['final_mean']:>12.2f}"
                  f"{v['final_min']:>11.2f}{trend:>12}")
        print()
        # Did the battery warn us?
        for site, v in sorted(dark.items()):
            t = v["trend_v_per_week"]
            # Order matters: a station that died BELOW the low-voltage
            # threshold is a power failure whatever its recent slope.
            # The first version tested the slope first and left a
            # station that expired at 10.15 V with no verdict at all.
            # Level first, then trend. A healthy final voltage rules
            # out power whatever the slope was doing.
            if v["final_min"] >= WATCH_VOLTAGE:
                print(f"    {site}: battery was HEALTHY at the last reading "
                      f"({v['final_min']:.2f} V,")
                print(f"    floor {v['recent_floor']:.2f} V). NOT a power "
                      f"failure.")
                print("    More likely telemetry, the logger, or physical "
                      "damage — and if")
                print("    it is telemetry the logger kept recording and the "
                      "gap will")
                print("    backfill when it reconnects.")
                print()
                continue
            if v["final_min"] < LOW_VOLTAGE:
                print(f"    {site}: died at {v['final_min']:.2f} V, below the "
                      f"{LOW_VOLTAGE} V floor a logger")
                trend_note = (f" after falling {abs(t):.3f} V/week"
                              if t is not None and t < 0 else "")
                print(f"    needs to keep writing{trend_note}.")
                print("    That is a POWER FAILURE, and the data for "
                      "that period is probably")
                print("    gone rather than buffered.")
                print()
                continue
            if t is None:
                continue
            if t < -0.02:
                print(f"    {site}: voltage was falling {abs(t):.3f} V/week "
                      f"before it stopped,")
                print(f"    reaching a floor of {v['recent_floor']:.2f} V. "
                      f"That is a power failure with a\n    visible run-up.\n")
            elif v["final_min"] >= WATCH_VOLTAGE:
                print(f"    {site}: battery was healthy "
                      f"({v['final_min']:.2f} V minimum, trend "
                      f"{t:+.3f} V/week)")
                print("    right up to the last reading. NOT a power failure —")
                print("    more likely telemetry, the logger, or physical\n"
                      "    damage. If it's telemetry the gap will backfill\n"
                      "    when it reconnects.\n")

    if live:
        print("  REPORTING:\n")
        print(f"    {'station':20}{'last seen':>12}{'mean':>9}{'min':>9}"
              f"{'7d floor':>12}{'30d trend':>13}")
        print("    " + "-" * 75)
        for site, v in sorted(live.items(),
                              key=lambda kv: kv[1]["current_floor"]):
            trend = (f"{v['trend_v_per_week']:+.3f} V/wk"
                     if v["trend_v_per_week"] is not None else "—")
            # Flag on where it IS and where it is GOING, together.
            flag = ""
            wtf = v.get("weeks_to_floor")
            if v["current_floor"] < LOW_VOLTAGE:
                flag = "  AT RISK"
            elif wtf is not None and wtf < WEEKS_OF_HEADROOM:
                flag = f"  {wtf:.0f} wk to floor"
            elif v["current_floor"] < WATCH_VOLTAGE:
                flag = "  watch"
            print(f"    {site:20}{v['hours_stale']:>9.0f} h"
                  f"{v['final_mean']:>9.2f}{v['final_min']:>9.2f}"
                  f"{v['current_floor']:>12.2f}{trend:>13}{flag}")
        print()

        at_risk = [s for s, v in live.items()
                   if v["current_floor"] < WATCH_VOLTAGE
                   or (v.get("weeks_to_floor") is not None
                       and v["weeks_to_floor"] < WEEKS_OF_HEADROOM)]
        if at_risk:
            print(f"    {len(at_risk)} station(s) worth watching: "
                  f"{', '.join(sorted(at_risk))}\n")
        else:
            print("    No station is showing a declining supply or a low\n"
                  "    overnight floor. Nothing here predicts another\n"
                  "    outage.\n")

    print("  Thresholds are advisory. A 12 V sealed lead-acid logger supply")
    print(f"  is comfortable above {WATCH_VOLTAGE} V and at risk below "
          f"{LOW_VOLTAGE} V, but the")
    print("  exact figures depend on battery chemistry, temperature and what")
    print("  else shares the rail. The TREND and the overnight MINIMUM carry")
    print("  more signal than the absolute number — a battery that recovers")
    print("  to 13 V each afternoon but hits 11.6 V at dawn is in more")
    print("  trouble than one sitting flat at 12.2 V.\n")
    return 0
