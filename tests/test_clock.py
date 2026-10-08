"""The loggers' clocks: Pacific Standard Time all year, MiniDOT the exception.

Settled from the data on 2026-10-07 (docs/data-dictionary.md): on
2026-03-08 the forest loggers, EXO sondes and HOBOs logged readings at
02:xx, an hour a Pacific clock skips that day. These pin the rule down
everywhere a naive TEON time is turned into an instant.
"""

from __future__ import annotations

from datetime import datetime, timezone

from secchi.config import TEON_TIMEZONE, teon_zone
from secchi.daily import fmt_local
from secchi.sources import watch as W
from secchi.transform import _iso_local, _parse_iso


def test_the_zone_is_utc_minus_8_and_minidot_follows_daylight_saving():
    assert TEON_TIMEZONE == "Etc/GMT+8"                 # POSIX sign: this is UTC-8
    assert teon_zone() == teon_zone("ExoSensor") == "Etc/GMT+8"
    assert teon_zone("MiniDotSensor") == teon_zone("Minidot") == "America/Los_Angeles"


def test_summer_and_winter_readings_are_both_minus_eight():
    assert _iso_local("2026-07-01 12:00:00").endswith("-08:00")
    assert _iso_local("2026-01-15 12:00:00").endswith("-08:00")
    assert _iso_local("2026-07-01 12:00:00", "Minidot").endswith("-07:00")


def test_the_hour_daylight_saving_skips_is_a_real_logger_time():
    # Read as Pacific local this raised and fell back to UTC (8 h out).
    assert _iso_local("2026-03-08 02:30:00") == "2026-03-08T02:30:00-08:00"
    p = _parse_iso("2026-03-08T02:30:00")
    assert p.astimezone(timezone.utc).hour == 10


def test_the_watcher_ages_a_summer_reading_correctly():
    # 13:00 on the logger clock in October is 21:00 UTC (not 20:00).
    inv = {"locations": {"terrestrial": {"Air Temperature & Relative Humidity": [
        {"site": "Homewood", "last_update": "2026-10-05T13:00:00", "data_count": 1}]}}}
    live = W._snapshot_state(inv, set(), now=datetime(2026, 10, 6, 20, 30, tzinfo=timezone.utc))
    late = W._snapshot_state(inv, set(), now=datetime(2026, 10, 6, 21, 30, tzinfo=timezone.utc))
    assert next(iter(live["sensors"].values()))["state"] == "live"
    assert next(iter(late["sensors"].values()))["state"] == "quiet"


def test_the_daily_report_shows_logger_times_on_a_pacific_clock():
    # 13:00 PST in October is 2:00 pm PDT on a wall clock.
    assert fmt_local("2026-10-05 13:00:00") == "Mon 2:00 pm"
    assert fmt_local("2026-01-05 13:00:00") == "Mon 1:00 pm"
