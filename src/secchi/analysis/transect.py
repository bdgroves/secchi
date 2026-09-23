"""The transect: does the rain shadow reach the soil?

Homewood and Glenbrook 5 sit **64 metres apart in latitude** on opposite
shores of Lake Tahoe. Both are upland hillslope stations. One faces the
Pacific; one sits in the lee of the Carson Range.

Their catchments receive **2.12× different annual precipitation** —
1,463 mm at Madden Creek against 689 mm at Glenbrook Creek — and
basin-wide the gradient reaches 3.03×. That's the climatology. This
module asks whether it shows up in the soil, event by event.

## Method, and why it isn't the obvious one

The obvious design needs a precipitation record: find storms, compare
responses. We can't. Blackwood 2 holds the network's only rain gauge, it
has been dark since August, and the USGS store only covers September
because the hourly cron fetches a six-hour window and the gauges were
never backfilled.

So the events are detected in the **soil moisture itself**. Soil wets
fast and dries slowly, and that asymmetry is the signal: a sharp rise in
volumetric water content is a wetting event, no external forcing record
required.

Then:

1. Detect wetting events independently at each station.
2. Match them in time — a storm reaching both shores does so within
   hours.
3. Where both responded, compare magnitude.
4. Count the events only one station saw.

Step 4 is the interesting one. If the west shore logs wetting the east
never sees, the rain shadow isn't only changing how much rain falls —
it's changing whether a storm registers at all.

## What this cannot say

Without a rain gauge we cannot attribute an event to a named storm, only
observe that both stations responded at the same time. Nor can we rule
out a non-precipitation cause for a rise — snowmelt, irrigation, a
sensor being disturbed. The synchrony across two stations 20 km apart is
what makes precipitation the parsimonious explanation, not proof.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from secchi.config import PROCESSED_DIR, TRANSECT_PAIR

log = logging.getLogger("secchi.analysis.transect")

# A rise of this much volumetric water content counts as wetting.
#
# UNITS MATTER HERE. Soil_VWC is STORED as a fraction and displayed
# multiplied by 100 — a card reading "3.4 %" is 0.034 in the parquet. A
# threshold of 0.3 in stored units would need a thirty-point rise and
# nothing would ever trigger.
#
# 0.003 stored = 0.3 percentage points displayed. These stations sit
# between about 3 % and 12 %, with instrument noise in the hundredths of
# a point, so that's well clear of noise and well below a real storm.
WETTING_THRESHOLD_VWC = 0.003

# Display multiplier, so the report speaks in the same units as the
# dashboard rather than in fractions.
VWC_DISPLAY_SCALE = 100.0

# The window a rise must occur within to count as one event. Six hours
# is long enough to capture a storm's wetting front through the profile
# and short enough not to merge two days of separate rain.
EVENT_WINDOW = pd.Timedelta(hours=6)

# After an event, ignore further rises for this long.
#
# Was 18 hours, which was WRONG: soil moisture has a strong daily cycle,
# and a peak recurring every 24 h falls outside an 18-hour block, so each
# day registered as a fresh event. The first real run produced runs of
# six and eight consecutive days, all at the same time of afternoon, with
# small magnitudes — a diurnal signal, not rain.
#
# 30 hours guarantees one daily cycle cannot produce two events.
EVENT_COOLDOWN = pd.Timedelta(hours=30)

# DETECTION RUNS ON DAILY MEANS, not hourly samples.
#
# Two earlier attempts failed on the same signal. Soil moisture has a
# strong daily cycle; the first run produced runs of six and eight
# consecutive "events" at the same time each afternoon. Lengthening the
# cooldown to 30 h helped and wasn't enough. Adding a persistence test —
# is it still elevated 24 h later? — failed too, and instructively: a
# PERIODIC signal sampled exactly one period later looks perfectly
# persistent.
#
# A daily mean cancels a daily cycle by construction. That is the same
# fix the sparklines needed on day one, when a 48-hour slope reported air
# temperature "rising 4.85" because it was measuring where in the cycle
# the window happened to start.
#
# A storm raises the daily mean. A diurnal swing cannot.
DAILY_RISE_WINDOW_DAYS = 3
DAILY_COOLDOWN_DAYS = 3

# Two events are "the same storm" if their DETECTION DAYS are within
# this many days of each other.
#
# Matched on hourly onsets in a 12-hour window originally, which was
# incoherent once detection moved to daily means: a storm starting late
# on day N raises the west station's daily mean on day N and the east
# station's on day N+1, so both onsets land near the start of their own
# day — exactly 24 hours apart, outside a 12-hour window. One storm
# became two one-sided events, one per shore, thirteen times in a single
# run.
#
# Matching must use the same resolution as detection. One day of
# tolerance covers a storm straddling midnight plus a few hours of
# west-to-east travel.
SYNCHRONY_DAYS = 1

SOIL_VARIABLE = "Soil_VWC"


@dataclass
class WettingEvent:
    station: str
    start: pd.Timestamp
    peak: pd.Timestamp
    before: float
    after: float

    @property
    def magnitude(self) -> float:
        return self.after - self.before


@dataclass
class TransectResult:
    west: str
    east: str
    span_days: float = 0.0
    west_events: list = field(default_factory=list)
    east_events: list = field(default_factory=list)
    matched: list = field(default_factory=list)
    west_only: list = field(default_factory=list)
    east_only: list = field(default_factory=list)


def _series(df: pd.DataFrame, site: str, variable: str) -> pd.Series:
    """One station's series for one variable, hourly, sorted."""
    sub = df[(df["site"] == site) & (df["variable"] == variable)
             & df["timestamp"].notna() & df["value"].notna()]
    if sub.empty:
        return pd.Series(dtype=float)
    s = (sub.set_index("timestamp")["value"]
            .sort_index()
            .resample("1h").mean()
            .dropna())
    return s


def detect_wetting(series: pd.Series, station: str) -> list[WettingEvent]:
    """Wetting events, detected on DAILY MEANS and timed on hourly data.

    Detection uses daily means because soil moisture has a strong diurnal
    cycle and a daily mean cancels it by construction. Onset is then
    refined against the hourly series within the detected day, so the
    west-to-east lag stays measurable at hourly resolution.
    """
    events: list[WettingEvent] = []
    if series.empty:
        return events

    daily = series.resample("1D").mean().dropna()
    if len(daily) < DAILY_RISE_WINDOW_DAYS + 1:
        return events

    trough = daily.rolling(f"{DAILY_RISE_WINDOW_DAYS}D").min()
    rise = daily - trough

    blocked_until: pd.Timestamp | None = None
    for day, amount in rise.items():
        if amount < WETTING_THRESHOLD_VWC:
            continue
        if blocked_until is not None and day < blocked_until:
            continue

        before = float(trough.loc[day])

        # Refine onset within the day: the first hourly sample that has
        # climbed a quarter of the way up. Gives the lag its resolution
        # back without letting the diurnal cycle drive detection.
        hourly = series.loc[day:day + pd.Timedelta(days=1)]
        if hourly.empty:
            continue
        after = float(hourly.max())
        target = before + (after - before) * 0.25
        crossed = hourly[hourly >= target]
        onset = crossed.index[0] if len(crossed) else hourly.index[0]

        events.append(WettingEvent(
            station=station, start=onset, peak=hourly.idxmax(),
            before=before, after=after,
        ))
        blocked_until = day + pd.Timedelta(days=DAILY_COOLDOWN_DAYS)

    return events


def match_events(west: list[WettingEvent],
                 east: list[WettingEvent]) -> tuple[list, list, list]:
    """Pair events across stations by onset time.

    Greedy nearest-match. Two stations, a handful of events per winter —
    an optimal assignment would be the same answer with more code.
    """
    matched: list[tuple[WettingEvent, WettingEvent]] = []
    used_east: set[int] = set()
    tolerance = pd.Timedelta(days=SYNCHRONY_DAYS)

    for w in west:
        best_i, best_gap = None, None
        for i, e in enumerate(east):
            if i in used_east:
                continue
            # Compare the DAYS the events were detected on, not the
            # refined hourly onsets — that's the resolution detection
            # actually works at. The hourly onsets are kept for the lag.
            gap = abs(e.start.normalize() - w.start.normalize())
            if gap <= tolerance and (best_gap is None or gap < best_gap):
                best_i, best_gap = i, gap
        if best_i is not None:
            used_east.add(best_i)
            matched.append((w, east[best_i]))

    west_matched = {id(w) for w, _ in matched}
    west_only = [w for w in west if id(w) not in west_matched]
    east_only = [e for i, e in enumerate(east) if i not in used_east]
    return matched, west_only, east_only


def analyse(df: pd.DataFrame | None = None) -> TransectResult | None:
    """Run the comparison over the stored record."""
    if df is None:
        from secchi.store import read_partitions
        df = read_partitions(PROCESSED_DIR / "observations")
    if df is None or df.empty:
        log.error("no observations stored")
        return None

    west, east = TRANSECT_PAIR
    w_series = _series(df, west, SOIL_VARIABLE)
    e_series = _series(df, east, SOIL_VARIABLE)
    if w_series.empty or e_series.empty:
        log.error("no soil moisture for %s and/or %s", west, east)
        return None

    # Only the overlapping period — comparing a station's wet winter
    # against the other's dry summer would be meaningless.
    start = max(w_series.index.min(), e_series.index.min())
    end = min(w_series.index.max(), e_series.index.max())
    w_series = w_series.loc[start:end]
    e_series = e_series.loc[start:end]

    result = TransectResult(west=west, east=east)
    result.span_days = round((end - start).total_seconds() / 86400, 1)
    result.west_events = detect_wetting(w_series, west)
    result.east_events = detect_wetting(e_series, east)
    result.matched, result.west_only, result.east_only = match_events(
        result.west_events, result.east_events)
    return result


def report() -> int:
    """Print the transect comparison."""
    result = analyse()
    if result is None:
        return 1

    w, e = result.west, result.east
    print(f"\n  {w} (west)  vs  {e} (east)")
    print(f"  64 m apart in latitude · catchments at 1,463 and 689 mm/yr "
          f"(2.12x)")
    print(f"  {result.span_days:,.0f} days of overlapping record\n")

    print(f"  wetting events (soil moisture rise > "
          f"{WETTING_THRESHOLD_VWC * VWC_DISPLAY_SCALE:.1f} points in "
          f"{int(EVENT_WINDOW.total_seconds()//3600)} h)\n")
    print(f"    {w:20}{len(result.west_events):>4}")
    print(f"    {e:20}{len(result.east_events):>4}")
    print(f"    {'both responded':20}{len(result.matched):>4}")
    print(f"    {'west only':20}{len(result.west_only):>4}")
    print(f"    {'east only':20}{len(result.east_only):>4}\n")

    if result.matched:
        ratios = []
        print("  where BOTH responded:\n")
        print(f"    {'onset (west)':22}{'west rise':>11}{'east rise':>11}"
              f"{'ratio':>8}{'lag':>8}")
        print("    " + "-" * 60)
        for wv, ev in result.matched:
            ratio = wv.magnitude / ev.magnitude if ev.magnitude else float("nan")
            ratios.append(ratio)
            lag = (ev.start - wv.start).total_seconds() / 3600
            print(f"    {wv.start.strftime('%Y-%m-%d %H:%M'):22}"
                  f"{wv.magnitude * VWC_DISPLAY_SCALE:>10.2f}"
                  f"{ev.magnitude * VWC_DISPLAY_SCALE:>11.2f}"
                  f"{ratio:>8.2f}{lag:>7.1f}h")
        finite = [r for r in ratios if r == r and r > 0]
        if finite:
            import math
            # NOT the arithmetic mean. Ratios are multiplicative: 14.7 and
            # its inverse 0.068 describe equal and opposite asymmetries and
            # should cancel, but they average to 7.4. The first real run
            # produced a headline of 3.31 from three outliers while five
            # of fifteen events actually favoured the EAST.
            geo = math.exp(sum(math.log(r) for r in finite) / len(finite))
            total = (sum(w.magnitude for w, _ in result.matched)
                     / sum(e.magnitude for _, e in result.matched))
            favoured_east = sum(1 for r in finite if r < 1)

            print(f"\n    geometric mean ratio   {geo:.2f}")
            print(f"    ratio of totals        {total:.2f}")
            print(f"    events favouring east  {favoured_east} of {len(finite)}")
            print(f"    catchment precip ratio 2.12")

        # Frequency, which turns out to carry the signal better than
        # magnitude does.
        w_n, e_n = len(result.west_events), len(result.east_events)
        if e_n:
            print(f"\n    event-count ratio      {w_n / e_n:.2f}"
                  f"   ({w_n} vs {e_n})")
        if result.east_only:
            print(f"    one-sided ratio        "
                  f"{len(result.west_only) / len(result.east_only):.2f}"
                  f"   ({len(result.west_only)} vs {len(result.east_only)})")

        # TOTAL WETTING: every event at each station, summed. This is the
        # single best summary, and it is measured directly rather than by
        # multiplying a frequency ratio by a magnitude ratio — two derived
        # numbers with their own errors.
        #
        # It is what the catchment precipitation ratio should be compared
        # against: the gradient can reach the soil through how OFTEN it
        # rains, how HARD it rains, or both, and only the total captures
        # the combination.
        w_total = sum(ev.magnitude for ev in result.west_events)
        e_total = sum(ev.magnitude for ev in result.east_events)
        if e_total:
            print(f"\n    TOTAL WETTING RATIO    {w_total / e_total:.2f}"
                  f"   ({w_total * VWC_DISPLAY_SCALE:.0f} vs "
                  f"{e_total * VWC_DISPLAY_SCALE:.0f} points, all events)")
            print(f"    catchment precip ratio 2.12")

        # DIRECTION is the robust part: which shore wetted first. That is
        # a sign, and survives the detector's coarse timing.
        #
        # The lag in HOURS is not. Detection runs on daily means, and a
        # storm that began the previous evening is stamped at 00:00 of
        # the detection day, so many onsets sit at midnight and many lags
        # come out at exactly 24 h. Earlier versions reported a mean lag
        # of +3.7 h and then +11.7 h and called it the sturdiest finding;
        # the first was truncated by a 12-hour matching window, the second
        # inflated by midnight stamps. Neither was a travel time.
        #
        # So the hour figure is reported only for pairs where BOTH onsets
        # were resolved to a real hour, with its spread and count, and a
        # warning when that's too thin to read as anything.
        lags = [(e.start - w.start).total_seconds() / 3600
                for w, e in result.matched]
        if lags:
            west_first = sum(1 for l in lags if l > 0)
            east_first = sum(1 for l in lags if l < 0)
            same = len(lags) - west_first - east_first
            print(f"\n    reached the west shore first   {west_first} of {len(lags)}"
                  f"   (east first {east_first}, same hour {same})")

            def _midnight(ts):
                return ts.hour == 0 and ts.minute == 0
            resolved = sorted(
                (e.start - w.start).total_seconds() / 3600
                for w, e in result.matched
                if not _midnight(w.start) and not _midnight(e.start))
            if resolved:
                mid = resolved[len(resolved) // 2] if len(resolved) % 2 else \
                    (resolved[len(resolved) // 2 - 1] + resolved[len(resolved) // 2]) / 2
                spread = resolved[-1] - resolved[0]
                print(f"    lag, both onsets resolved      median {mid:+.1f} h, "
                      f"range {resolved[0]:+.0f} to {resolved[-1]:+.0f} h, "
                      f"n = {len(resolved)}")
                if len(resolved) < 10 or spread > 12:
                    print("      Too few or too spread to read as a storm travel")
                    print("      time. Detection runs on daily means, so most")
                    print("      onsets can only be placed to the day, not the hour.")
            else:
                print("    lag in hours: no pair has both onsets resolved to a")
                print("      real hour, so only the direction is meaningful.")
        print()

    if result.west_only or result.east_only:
        print("  events only ONE station saw:\n")
        for ev in sorted(result.west_only + result.east_only,
                         key=lambda x: x.start):
            side = "west" if ev.station == w else "east"
            print(f"    {ev.start.strftime('%Y-%m-%d %H:%M')}  {side:5} "
                  f"{ev.station:18} +{ev.magnitude * VWC_DISPLAY_SCALE:.2f}")
        print()
        if len(result.west_only) > len(result.east_only) * 2:
            print("    The west shore registers wetting the east does not see")
            print("    at all. The shadow changes not just how much rain")
            print("    falls but whether a storm registers.\n")

    print("  Caveats: events are detected from soil moisture itself, not")
    print("  from a rain gauge — Blackwood 2 holds the network's only")
    print("  precipitation record and it has been dark since August. So an")
    print("  event cannot be attributed to a named storm, and a rise could")
    print("  in principle be snowmelt or disturbance. Synchrony across two")
    print("  stations 20 km apart is what makes precipitation the")
    print("  parsimonious reading, not proof.\n")
    return 0
