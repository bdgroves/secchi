"""The transect against measured precipitation on both shores.

The soil-only transect found the west shore's soil took about 2.3 times
the wetting of the east's over a year, close to the catchments' long-term
rainfall ratio of 2.12. But that compared soil against a climatology. This
compares it against the precipitation that actually fell, measured by
SNOTEL at both ends:

    west   Ward Creek #3   (Rubicon #2 as a fallback)
    east   Marlette Lake

Three questions, in order of how much they can be trusted:

1. Over the shared record, what was the real west/east precipitation
   ratio — all precipitation, and rain only (days averaging above 0 °C)?
2. Storm by storm, did the shore that got more rain wet up more?
3. Which sizeable storms did a soil sensor not register — and were they
   snow, which wets soil only later, as it melts?

Caveats that matter more than usual:

* **Marlette Lake sits ~1,000 ft above Ward Creek #3.** Precipitation
  usually rises with elevation, so its totals may overstate the east
  shore's lowland precipitation. The ratio here is a lower bound on the
  true west/east contrast at the soil stations' elevations, not a
  measurement of it.
* **SNOTEL precipitation is gauge catch** — snow and rain together, and
  wind undercatch in snow is a known issue.
* **One soil sensor per shore.** A soil probe responds to rain on a few
  square metres; a gauge a few miles away measures somewhere else.

TEON's own gauge at Blackwood 2 is shown as a cross-check on the west
side, on the days it was running.
"""

from __future__ import annotations

import logging

import pandas as pd

from secchi.config import PROCESSED_DIR

log = logging.getLogger("secchi.analysis.transect_rain")

WEST_GAUGES = ("Ward Creek #3", "Rubicon #2")
EAST_GAUGES = ("Marlette Lake",)
EVENT_WINDOW = (-1, 1)          # days around a wetting onset
BIG_STORM_MM = 10.0             # a storm this wet should register in the soil
RAIN_EVENT_MM = 5.0             # a wetting with less than this at both gauges wasn't a storm
STORM_DAY_MM = 1.0              # consecutive days at or above this are one storm
SNOW_BELOW_C = 0.0              # daily mean air temperature at or below = snow
TEON_GAUGE = ("Blackwood 2", "Accu_NRT")


def _daily(snotel: pd.DataFrame, site: str, variable: str) -> pd.Series:
    sub = snotel[(snotel["site"] == site) & (snotel["variable"] == variable)]
    if sub.empty:
        return pd.Series(dtype=float)
    return (sub.assign(day=pd.to_datetime(sub["timestamp"]).dt.normalize())
               .groupby("day")["value"].mean().sort_index())


def _first_available(snotel: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for n in names:
        if not _daily(snotel, n, "PRCP").empty:
            return n
    return None


def _window_sum(series: pd.Series, day: pd.Timestamp) -> float | None:
    lo, hi = day + pd.Timedelta(days=EVENT_WINDOW[0]), day + pd.Timedelta(days=EVENT_WINDOW[1])
    part = series.loc[lo:hi]
    if len(part) < (EVENT_WINDOW[1] - EVENT_WINDOW[0] + 1):
        return None                              # a missing day: don't guess
    return float(part.sum())


def analyse(df: pd.DataFrame | None = None, snotel: pd.DataFrame | None = None) -> dict | None:
    from secchi.analysis.transect import analyse as transect_analyse
    from secchi.store import read_partitions

    if df is None:
        df = read_partitions(PROCESSED_DIR / "observations")
    if snotel is None:
        snotel = read_partitions(PROCESSED_DIR / "snotel_observations")
    if snotel is None or snotel.empty:
        log.error("no SNOTEL data stored; run `pixi run snotel --since 2024-06-01` first")
        return None

    tr = transect_analyse(df)
    if tr is None:
        return None

    west_g, east_g = _first_available(snotel, WEST_GAUGES), _first_available(snotel, EAST_GAUGES)
    if not west_g or not east_g:
        log.error("SNOTEL precipitation missing for one side (west %s, east %s)", west_g, east_g)
        return None
    pw, pe = _daily(snotel, west_g, "PRCP"), _daily(snotel, east_g, "PRCP")
    tw, te = _daily(snotel, west_g, "TAVG"), _daily(snotel, east_g, "TAVG")

    # The shared record: the transect's overlap, on days both gauges reported.
    ev = tr.west_events + tr.east_events
    start = min(e.start for e in ev).normalize() if ev else pw.index.min()
    end = max(e.start for e in ev).normalize() if ev else pw.index.max()
    days = pw.index.intersection(pe.index)
    days = days[(days >= start - pd.Timedelta(days=30)) & (days <= end + pd.Timedelta(days=30))]

    def totals(mask_w=None, mask_e=None):
        w = pw.reindex(days); e = pe.reindex(days)
        if mask_w is not None:
            w = w[mask_w.reindex(days).fillna(False).to_numpy()]
            e = e[mask_e.reindex(days).fillna(False).to_numpy()]
        return float(w.sum()), float(e.sum())

    all_w, all_e = totals()
    rain_w, rain_e = (totals(tw > SNOW_BELOW_C, te > SNOW_BELOW_C)
                      if not tw.empty and not te.empty else (None, None))
    soil_w = sum(e.magnitude for e in tr.west_events)
    soil_e = sum(e.magnitude for e in tr.east_events)

    events = []
    for w, e in tr.matched:
        rw = _window_sum(pw, w.start.normalize())
        re_ = _window_sum(pe, e.start.normalize())
        # A wetting with no precipitation at either gauge wasn't a storm.
        # On 2026-10-02, 7 of the 18 shared events were like this, all in
        # winter and spring — most likely snowmelt, which wets both shores
        # in the same warm spell.
        kind = None
        if rw is not None and re_ is not None:
            kind = "rain" if max(rw, re_) >= RAIN_EVENT_MM else "melt?"
        events.append({"day": w.start.normalize(), "rain_w": rw, "rain_e": re_,
                       "soil_w": w.magnitude, "soil_e": e.magnitude, "kind": kind})

    # Storms a soil sensor didn't register. Consecutive wet days are one
    # storm: counted day by day, the 2025-12-20 storm the soil DID register
    # showed up as two "missed" days on the 22nd and 24th.
    def storms(series, temps):
        out, run = [], []
        for day, mm in series.loc[start:end].items():
            if mm >= STORM_DAY_MM:
                run.append((day, float(mm)))
                continue
            if run:
                out.append(run); run = []
        if run:
            out.append(run)
        result = []
        for run in out:
            total = sum(mm for _, mm in run)
            if total < BIG_STORM_MM:
                continue
            snow_mm = sum(mm for d, mm in run
                          if not temps.empty and temps.get(d) is not None and temps.get(d) <= SNOW_BELOW_C)
            result.append({"start": run[0][0], "end": run[-1][0], "days": len(run),
                           "mm": total, "snow_share": snow_mm / total})
        return result

    def missed(series, temps, wet_events):
        onsets = [e.start.normalize() for e in wet_events]
        return [st for st in storms(series, temps)
                if not any(st["start"] - pd.Timedelta(days=1) <= o <= st["end"] + pd.Timedelta(days=1)
                           for o in onsets)]

    def snow_share(series, temps):
        part = series.reindex(days).fillna(0)
        t = temps.reindex(days)
        total = part.sum()
        return float(part[(t <= SNOW_BELOW_C).fillna(False).to_numpy()].sum() / total) if total else None

    teon = df[(df["site"] == TEON_GAUGE[0]) & (df["variable"] == TEON_GAUGE[1])]
    teon_daily = (teon.assign(day=pd.to_datetime(teon["timestamp"]).dt.normalize())
                      .groupby("day").agg(mm=("value", "sum"), minutes=("value", "size"))
                  if not teon.empty else pd.DataFrame(columns=["mm", "minutes"]))

    return {
        "west_gauge": west_g, "east_gauge": east_g, "west_soil": tr.west, "east_soil": tr.east,
        "days": len(days), "start": days.min() if len(days) else None, "end": days.max() if len(days) else None,
        "all": (all_w, all_e), "rain": (rain_w, rain_e), "soil": (soil_w, soil_e),
        "events": events,
        "missed_west": missed(pw, tw, tr.west_events),
        "missed_east": missed(pe, te, tr.east_events),
        "storms_west": len(storms(pw, tw)), "storms_east": len(storms(pe, te)),
        "snow_share": (snow_share(pw, tw), snow_share(pe, te)),
        "snotel_west_temp": tw,
        "teon_daily": teon_daily, "snotel_west": pw,
    }


def _ratio(a, b):
    return None if a is None or b in (None, 0) else a / b


def report() -> int:
    r = analyse()
    if r is None:
        return 1
    print(f"\n  {r['west_soil']} (west) vs {r['east_soil']} (east), against SNOTEL")
    print(f"  precipitation at {r['west_gauge']} (west) and {r['east_gauge']} (east)")
    if r["start"] is not None:
        print(f"  {r['days']} days both gauges reported, "
              f"{r['start']:%Y-%m-%d} to {r['end']:%Y-%m-%d}\n")

    (aw, ae), (rw, re_), (sw, se) = r["all"], r["rain"], r["soil"]
    print(f"  {'':28}{'west':>10}{'east':>10}{'ratio':>8}")
    print(f"  {'all precipitation (mm)':28}{aw:>10,.0f}{ae:>10,.0f}{_ratio(aw, ae) or 0:>8.2f}")
    if rw is not None:
        print(f"  {'rain only, above 0 C (mm)':28}{rw:>10,.0f}{re_:>10,.0f}{_ratio(rw, re_) or 0:>8.2f}")
        sw_, se_ = r["snow_share"]
        if sw_ is not None and se_ is not None:
            print(f"  {'share that fell as snow':28}{100*sw_:>9.0f}%{100*se_:>9.0f}%")
    print(f"  {'soil wetting (VWC points)':28}{sw*100:>10.1f}{se*100:>10.1f}{_ratio(sw, se) or 0:>8.2f}")
    print("\n  The gauges differ in elevation (Marlette Lake ~1,000 ft higher), so")
    print("  the precipitation ratio is a lower bound on the contrast at the")
    print("  soil stations, not a measurement of it. The higher gauge is also")
    print("  colder, so more of its precipitation falls as snow: the rain-only")
    print("  ratio mostly measures that, not the shores.\n")

    ev = [e for e in r["events"] if e["kind"] is not None]
    if ev:
        rain = [e for e in ev if e["kind"] == "rain"]
        print(f"  shared wetting events: {len(ev)} — {len(rain)} with precipitation, "
              f"{len(ev) - len(rain)} with none at either gauge (probably snowmelt)\n")
        print(f"  {'onset':12}{'rain W':>8}{'rain E':>8}{'soil W':>8}{'soil E':>8}   wetter by rain / by soil")
        agree = 0
        for e in ev:
            by_soil = "W" if e["soil_w"] > e["soil_e"] else "E" if e["soil_e"] > e["soil_w"] else "="
            if e["kind"] == "rain":
                by_rain = "W" if e["rain_w"] > e["rain_e"] else "E" if e["rain_e"] > e["rain_w"] else "="
                agree += by_rain == by_soil
                tag = f"{by_rain} / {by_soil}"
            else:
                tag = "melt?"
            print(f"  {e['day']:%Y-%m-%d}  {e['rain_w']:>7.0f} {e['rain_e']:>7.0f} "
                  f"{e['soil_w']*100:>7.1f} {e['soil_e']*100:>7.1f}   {tag}")
        if rain:
            print(f"\n  in the {len(rain)} storms with precipitation, the wetter shore by rain was "
                  f"also the wetter by soil in {agree}")
            tot_rw, tot_re = sum(e["rain_w"] for e in rain), sum(e["rain_e"] for e in rain)
            tot_sw, tot_se = sum(e["soil_w"] for e in rain), sum(e["soil_e"] for e in rain)
            print(f"  over those storms: rain ratio {_ratio(tot_rw, tot_re) or 0:.2f}, "
                  f"soil ratio {_ratio(tot_sw, tot_se) or 0:.2f}")

    for side, key, n_key in (("west", "missed_west", "storms_west"), ("east", "missed_east", "storms_east")):
        m = r[key]
        mostly_snow = sum(x["snow_share"] >= 0.5 for x in m)
        print(f"\n  {side}: {len(m)} of {r[n_key]} storms of {BIG_STORM_MM:.0f}+ mm the soil didn't "
              f"register; {mostly_snow} fell mostly as snow, which wets soil only as it melts")
        for x in m[:8]:
            print(f"    {x['start']:%Y-%m-%d}  {x['days']} day(s)  {x['mm']:5.0f} mm  "
                  f"{100*x['snow_share']:3.0f}% snow")

    td = r["teon_daily"]
    if len(td):
        full = td[td["minutes"] >= 1380]                 # >= 23 h of the day
        both = (full.join(r["snotel_west"].rename("snotel"), how="inner")
                    .join(r["snotel_west_temp"].rename("t"), how="left"))
        if len(both):
            print(f"\n  cross-check, TEON's Blackwood 2 gauge vs {r['west_gauge']}, on "
                  f"{len(both)} days it reported in full:")
            for label, mask in (("rain days (above 0 C)", both["t"] > SNOW_BELOW_C),
                                ("snow days (0 C or below)", both["t"] <= SNOW_BELOW_C)):
                part = both[mask.fillna(False)]
                if len(part) and part["snotel"].sum():
                    print(f"    {label:26}{len(part):4} days  {part['mm'].sum():6,.0f} vs "
                          f"{part['snotel'].sum():6,.0f} mm  ({100*part['mm'].sum()/part['snotel'].sum():.0f}%)")
    print()
    return 0
