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

# Below this average daily swing, the series is treated as not
# moving. A healthy solar-charged 12 V supply swings tenths of a volt
# between afternoon charge and pre-dawn sag.
FLATLINE_SWING = 0.02

# A battery with nothing charging it looks flat within each day — no
# afternoon rise — but falls week after week under the logger's load.
# Glenbrook 1 did exactly this: 36 straight weekly declines from 12.74 V
# to 11.40 V, with a daily swing too small to see. An earlier version
# read the flat days as a stuck channel and REMOVED it from the at-risk
# list, when it was the station most at risk.
#
# So "flat" alone decides nothing. Flat within a day AND unchanged week
# to week is a stuck channel. Flat within a day AND falling week after
# week is a battery that isn't being charged.
NO_CHARGE_SWING = 0.10        # V: below this daily swing, nothing is charging it
NOT_CHARGING_WEEKS = 8        # complete weeks examined
NOT_CHARGING_MIN_DROP = 0.15  # V: total fall across those weeks

# The sturdier test: a charging battery reaches a charging voltage on any
# sunny day. Over 2026-09-14..30 every charging station's highest daily
# peak was 13.97-14.38 V, while Glenbrook 1 never passed 11.53 and Glenbrook
# 5 never passed 12.39. The "flat day" rule above missed Glenbrook 5,
# whose voltage still wobbles ~0.2 V a day as it drains. One sunny day
# above CHARGE_PEAK_V in the window proves the panel works.
CHARGE_PEAK_V = 13.0
CHARGE_PEAK_DAYS = 14
STUCK_MAX_CHANGE = 0.02       # V: a stuck channel changes less than this

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
    raw = sub.set_index("timestamp")["value"].sort_index()
    daily = raw.resample("1D")
    out = pd.DataFrame({"mean": daily.mean(), "min": daily.min(),
                        "max": daily.max()}).dropna()
    # The RAW last timestamp. Staleness computed from the daily index is
    # quantized to midnight, so it can only take values 24 h apart: the
    # first real run showed three stations at exactly 34 h, which was
    # "last reported yesterday", not three simultaneous outages.
    out.attrs["last_raw"] = raw.index.max()
    # Distinct values in the last week. A frozen channel or placeholder
    # repeats the SAME number; a real battery, however steady, varies in
    # the third decimal. "Barely moves" can't tell them apart — Glenbrook
    # 4, healthy and steady, was flagged stuck by a swing threshold.
    last_week = raw.loc[raw.index >= raw.index.max() - pd.Timedelta(days=7)]
    out.attrs["distinct_7d"] = int(last_week.round(4).nunique())
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

        last_raw = batt.attrs.get("last_raw", batt.index.max())
        hours_stale = (_now_like(batt.index) - last_raw).total_seconds() / 3600
        last_day = batt.index.max()
        recent = batt.loc[batt.index >= last_day - pd.Timedelta(days=TREND_DAYS)]

        entry = {
            "last_reading": last_raw.isoformat(),
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
            # Daily swing over the last week. A solar-charged battery
            # charges through the afternoon and sags overnight, every
            # day. Glenbrook 1 reported mean, min and floor all exactly
            # 11.45 V — a series that doesn't move at all. Real
            # batteries don't flatline; stuck channels and placeholder
            # values do.
            "daily_swing": round(float(
                (batt["max"] - batt["min"]).tail(7).mean()), 3),
        }
        # Week-on-week behaviour, from complete weeks only — the current
        # week is usually partial, and a partial week of a falling series
        # would look like the bottom of the trend.
        weekly = batt["mean"].resample("W").mean().dropna().iloc[:-1]
        window = weekly.tail(NOT_CHARGING_WEEKS)
        changes = window.diff().dropna()
        total_change = (float(window.iloc[-1] - window.iloc[0])
                        if len(window) >= 2 else 0.0)
        run = 0
        for step in reversed(weekly.diff().dropna().tolist()):
            if step < 0:
                run += 1
            else:
                break
        entry["weeks_falling"] = run
        entry["weekly_change_v"] = round(total_change, 3)
        entry["weekly_rate_v"] = (round(float(changes.tail(4).mean()), 3)
                                  if len(changes) else None)
        recent_peaks = batt["max"].tail(CHARGE_PEAK_DAYS)
        entry["peak_14d"] = round(float(recent_peaks.max()), 2) if len(recent_peaks) else None
        never_charged = bool(len(recent_peaks) >= CHARGE_PEAK_DAYS // 2
                             and recent_peaks.max() < CHARGE_PEAK_V)
        flat_and_falling = bool(
            entry["daily_swing"] < NO_CHARGE_SWING
            and len(changes) >= NOT_CHARGING_WEEKS - 1
            and int((changes < 0).sum()) >= len(changes) - 1
            and -total_change >= NOT_CHARGING_MIN_DROP)
        entry["not_charging"] = never_charged or flat_and_falling
        # Stuck means frozen: one repeated value all week, and not a
        # battery that is visibly running down.
        entry["distinct_7d"] = batt.attrs.get("distinct_7d")
        entry["flatlined"] = bool(
            entry["distinct_7d"] is not None
            and entry["distinct_7d"] <= 1
            and not entry["not_charging"])
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
            if v.get("not_charging"):
                flag = "  NOT CHARGING"
            elif v.get("flatlined"):
                flag = "  STUCK?"
            elif v["current_floor"] < LOW_VOLTAGE:
                flag = "  AT RISK"
            elif wtf is not None and wtf < WEEKS_OF_HEADROOM:
                flag = f"  {wtf:.0f} wk to floor"
            elif v["current_floor"] < WATCH_VOLTAGE:
                flag = "  watch"
            print(f"    {site:20}{v['hours_stale']:>9.1f} h"
                  f"{v['final_mean']:>9.2f}{v['final_min']:>9.2f}"
                  f"{v['current_floor']:>12.2f}{trend:>13}{flag}")
        print()

        for site, v in sorted(live.items()):
            if not v.get("not_charging"):
                continue
            rate = v.get("weekly_rate_v")
            peak = v.get("peak_14d")
            print(f"    {site}: not being charged. Its highest daily peak in the")
            print(f"    last {CHARGE_PEAK_DAYS} days was {peak:.2f} V; a charging battery reaches")
            print("    about 14 V on any sunny day.")
            if v.get("weeks_falling", 0) >= 3:
                print(f"    It has fallen every week for {v['weeks_falling']} weeks"
                      + (f", {abs(rate):.3f} V/week lately." if rate else "."))
            print("    A failed solar panel or charge controller. This ends in a")
            print("    power failure, and power-failure gaps don't backfill.\n")

        stuck = sorted(s for s, v in live.items() if v.get("flatlined"))
        if stuck:
            print(f"    {', '.join(stuck)}: battery reading hasn't moved in")
            print("    weeks — no daily cycle and no weekly change. A real")
            print("    solar-charged battery always swings. This is most")
            print("    likely a stuck channel or a placeholder value, NOT a")
            print("    flat battery, and it isn't counted as at risk.\n")

        # Parenthesised on purpose. Without them, `and` binds tighter
        # than `or`, so the stuck-exclusion only applied to the first
        # condition — and Glenbrook 1, flagged STUCK and explicitly "not
        # counted as at risk", was counted anyway through the second.
        # Fully parenthesised: `and` binds tighter than `or`, and a
        # missing pair here once counted a station the text said wasn't.
        at_risk = [s for s, v in live.items()
                   if v.get("not_charging")
                   or (not v.get("flatlined")
                       and (v["current_floor"] < WATCH_VOLTAGE
                            or (v.get("weeks_to_floor") is not None
                                and v["weeks_to_floor"] < WEEKS_OF_HEADROOM)))]
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
