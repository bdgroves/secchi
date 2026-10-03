"""Battery swaps and restorations (analysis.station_health.battery_events).

Pinned to the real one: Glenbrook 5's battery was swapped on 2026-09-09
while its panel wasn't charging, and for three weeks nothing here said
so. The fixture is its BattV_Avg from 2026-08-28 to 2026-09-20, as stored.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from secchi.analysis.station_health import battery_events

FX = pathlib.Path(__file__).parent / "fixtures"


def _frame(site, ts, values):
    return pd.DataFrame({"site": site, "variable": "BattV_Avg",
                         "timestamp": pd.to_datetime(ts), "value": values})


def test_the_real_glenbrook_5_swap_is_found_and_dated():
    raw = pd.read_csv(FX / "glenbrook5_battery_2026-09.csv", parse_dates=["timestamp"])
    ev = battery_events(_frame("Glenbrook 5", raw["timestamp"], raw["value"]), "Glenbrook 5")
    assert len(ev) == 1
    e = ev[0]
    assert e["kind"] == "replaced" and e["day"] == "2026-09-09"
    assert e["floor_before"] < 11.0 < 12.0 < e["floor_after"]
    assert e["charging_before"] is False


def _days(n, start="2026-01-01"):
    return pd.date_range(start, periods=n * 96, freq="15min")


def test_a_charging_station_recovering_after_storms_is_not_a_swap():
    # Healthy panel: 14 V afternoons. A three-day storm drags the overnight
    # floor to 11.8 V; sun returns and it climbs back to 12.6 V.
    ts = _days(20)
    hour = ts.hour + ts.minute / 60
    sun = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None)
    floor = np.where((ts >= "2026-01-08") & (ts < "2026-01-11"), 11.8, 12.6)
    charge = np.where((ts >= "2026-01-08") & (ts < "2026-01-11"), 0.0, 1.6)
    ev = battery_events(_frame("X", ts, floor + charge * sun), "X")
    assert ev == []


def test_a_dead_logger_coming_back_is_power_restored():
    ts = _days(12)
    v = np.where(ts < "2026-01-06 10:00", 8.1, 12.7)
    ev = battery_events(_frame("X", ts, v), "X")
    assert [e["kind"] for e in ev] == ["restored"]
    assert ev[0]["day"] == "2026-01-06"
