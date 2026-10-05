"""The morning report: what counts as a change, and that it renders.

Built on hand-made facts, so no store or snapshot is needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from secchi import daily

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)   # 7 am Pacific


def quiet_facts(**over) -> dict:
    f = {
        "now": NOW.isoformat(), "hours": 24, "unavailable": [],
        "labels": {"tallac_lake": "Tallac"},
        "sondes": ["Glenbrook"], "forest": ["Homewood", "Blackwood 2"],
        "snapshot_at": "2026-10-05T12:41:00+00:00", "snapshots_24h": 6,
        "last_seen": {"Homewood": "2026-10-04T22:15:00-07:00",
                      "Glenbrook": "2026-10-05T03:00:00-07:00",
                      "Blackwood 2": "2026-09-25T12:00:00-07:00"},
        "news": [],
        "lake": {"Glenbrook": {
            "t_end": "2026-10-05 03:00:00",
            "Temp": {"mean24": 16.2, "mean_prev": 16.1, "max24": 16.4, "median30": 17.0, "last": 16.2},
            "Turbidity": {"mean24": 0.2, "mean_prev": 0.2, "max24": 0.4, "median30": 0.3, "last": 0.2},
            "Chl_a": {"mean24": 0.1}, "Do_percent": {"mean24": 83.0}}},
        "forest_stats": {"Homewood": {
            "t_end": "2026-10-04 22:15:00",
            "Air_Temp": {"min": 6.0, "max": 22.0, "mean": 13.0, "min_prev": 5.0},
            "Soil_VWC": {"min": 0.03, "max": 0.04, "mean": 0.034, "min_prev": 0.03},
            "BattV_Avg": {"min": 12.6, "max": 13.4, "mean": 12.9, "min_prev": 12.6}}},
        "batteries": {"Blackwood 2": {"dark": True, "final_min": 12.86},
                      "Glenbrook 1": {"not_charging": True, "current_floor": 11.33,
                                      "peak_14d": 11.47}},
        "level": {"gage_ft": 6.87, "gage_week_ago_ft": 6.92},
        "gauges": {"10337000": {"readings": {"00065:00011:datum": {"value": 6226.87}}},
                   "10337500": {"readings": {"00060:00011": {"value": 74.5}}}},
        "airports": {"stations": [{"name": "South Lake Tahoe airport", "last_day": "2026-10-04",
                                   "tmax_c": 26.1, "tmin_c": 2.8, "last7_mm": 0.0}]},
        "snowlab": {"daily": {"newest_day": "2026-10-03", "swe_mm": 0.0, "water_year_mm": 0.0}},
        "rain": {"stations": [{"name": "Ward Creek #3", "last7_mm": 5.1}]},
    }
    f.update(over)
    return f


def test_a_quiet_day_says_so():
    assert daily.changes(quiet_facts()) == []
    text = daily.render(quiet_facts())
    assert "Nothing new" in text
    assert "Monday October 5, 2026" in text


def test_ongoing_lists_dark_and_not_charging():
    og = " ".join(daily.ongoing(quiet_facts()))
    assert "Blackwood 2 quiet since Fri Sep 25" in og
    assert "12.86 V" in og
    assert "Glenbrook 1 isn't charging" in og


def test_news_items_are_changes_with_days_filled():
    f = quiet_facts(news=[{"at": "2026-10-05T09:00:00+00:00", "source": "TEON",
                           "text": "Hand-collected data up to {day:2026-09-28}."}])
    (c,) = daily.changes(f)
    assert c == "Hand-collected data up to Mon Sep 28."


def test_battery_crossing_the_floor_is_a_change():
    f = quiet_facts()
    f["forest_stats"]["Homewood"]["BattV_Avg"].update(min=11.45, min_prev=11.55)
    assert any("dropped below 11.5 V" in c for c in daily.changes(f))


def test_frost_lake_swing_and_turbidity():
    f = quiet_facts()
    f["forest_stats"]["Homewood"]["Air_Temp"]["min"] = -1.0
    f["lake"]["Glenbrook"]["Temp"].update(mean24=14.9, mean_prev=16.2)
    f["lake"]["Glenbrook"]["Turbidity"].update(max24=3.0, median30=0.3)
    c = " ".join(daily.changes(f))
    assert "Frost at Homewood" in c
    assert "Glenbrook cooled 2.3 °F" in c
    assert "Turbidity at Glenbrook reached 3.0 FNU" in c


def test_negative_turbidity_offset_is_not_a_jump():
    # 4H Camp reads about -5.6 raw; a max of -5.0 is not news.
    f = quiet_facts()
    f["lake"]["Glenbrook"]["Turbidity"].update(max24=-5.0, median30=-5.6)
    assert daily.changes(f) == []


def test_a_stalled_pipeline_is_flagged():
    f = quiet_facts(snapshot_at="2026-10-04T20:00:00+00:00",
                    last_seen={"Homewood": "2026-10-04T08:00:00-07:00"})
    c = " ".join(daily.changes(f))
    assert "hasn't updated in 18 hours" in c
    assert "No station has sent anything new" in c


def test_numbers_render():
    text = daily.render(quiet_facts())
    assert "61.2 °F (16.2 °C)" in text            # 24 h mean water temp
    assert "6,226.87 ft (-0.6 in this week)" in text
    assert "74 cfs" in text
    assert "43 °F - 72 °F" in text                 # Homewood air
    assert "3.4%" in text                          # soil, stored as a fraction
    assert "no precipitation yet this water year" in text
    assert 'Ward Creek #3 0.20"' in text


def test_a_failed_section_is_reported_not_hidden():
    text = daily.render(quiet_facts(unavailable=["batteries"]))
    assert "Couldn't check:** batteries" in text
