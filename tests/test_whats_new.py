"""What's new (secchi.whats_new) and the watcher's news log (sources.watch).

Events here are shaped like the real ones the watcher logged in the week
of 2026-09-26 (replayed from the stored inventory snapshots).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

import secchi.sources.watch as W
import secchi.whats_new as N

NOW = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=14)


def ev(at, kind, detail, **kw):
    e = {"at": at, "kind": kind, "detail": detail}
    parts = detail.split("/")
    if len(parts) == 3:
        e["category"], e["sensor_type"], e["site"] = parts
    return {**e, **kw}


def test_one_boat_trip_is_one_line():
    events = [ev("2026-10-01T16:42:00+00:00", "dormant sensor received an upload", "lake/Minidot/Camp Richardson",
                 last_update="2026-09-28T15:31:00", added=9894),
              ev("2026-10-01T22:00:00+00:00", "dormant sensor received an upload", "lake/Hobo/tallac_lake",
                 last_update="2026-09-28T15:15:00", added=9882),
              ev("2026-10-01T22:00:00+00:00", "dormant sensor received an upload", "lake/Minidot/incline_lake",
                 last_update="2026-09-28T13:46:00", added=9896)]
    items = N.from_log(events, set(), SINCE)
    assert len(items) == 1
    it = items[0]
    assert it["kind"] == "manual_upload" and it["day"] == "2026-10-01"
    assert "Camp Richardson" in it["text"] and "Incline" in it["text"] and "Tallac" in it["text"]
    assert "{day:2026-09-28}" in it["text"]
    assert "9,900 readings" in it["detail"]


def test_a_site_hidden_and_shown_within_a_day_is_not_news():
    events = [ev("2026-09-26T14:05:00+00:00", "site hidden by TEON", "4hcamp"),
              ev("2026-09-26T18:18:00+00:00", "site un-hidden by TEON", "4hcamp")]
    assert N.from_log(events, set(), SINCE) == []


def test_a_station_back_is_one_line_not_four_sensors():
    t = "2026-09-29T21:03:00+00:00"
    events = [ev(t, "sensor resumed", f"terrestrial/{s}/Glenbrook 2") for s in
              ("Air Temperature & Relative Humidity", "Soil Environmental Conditions", "Tree stress and growth")]
    events.append(ev(t, "dormant sensor received an upload", "stream/Stream Level/Glenbrook 2", added=88))
    items = N.from_log(events, {"Glenbrook 2"}, SINCE)
    texts = [i["text"] for i in items]
    assert "Glenbrook 2 is reporting again." in texts
    assert len([x for x in texts if "Glenbrook 2" in x]) <= 2      # terrestrial + stream at most


def test_a_quiet_station_that_came_back_is_not_listed_as_quiet():
    events = [ev("2026-09-28T10:33:00+00:00", "sensor went quiet", "terrestrial/Soil Environmental Conditions/Glenbrook 5",
                 last_update="2026-09-27T05:45:00")]
    assert N.from_log(events, {"Glenbrook 5"}, SINCE) == []
    assert N.from_log(events, set(), SINCE)[0]["kind"] == "quiet"


def test_new_sensors_group_by_type():
    events = [ev("2026-09-30T19:37:00+00:00", "new sensor", "terrestrial/Tree stress and growth/Homewood"),
              ev("2026-09-30T23:12:00+00:00", "new sensor", "terrestrial/Tree stress and growth/Blackwood 2")]
    items = N.from_log(events, set(), SINCE)
    assert [i["text"] for i in items] == ["New tree sensors at Homewood and Blackwood 2."]
    assert items[0]["link"] == "#trees"


def test_old_events_fall_out_of_the_window():
    events = [ev("2026-09-01T00:00:00+00:00", "sensor resumed", "terrestrial/Field Camera/Glenbrook 4")]
    assert N.from_log(events, {"Glenbrook 4"}, SINCE) == []


def test_news_log_appends_once_and_trims(tmp_path):
    path = tmp_path / "news.json"
    e = [{"at": "2026-10-01T22:00:00+00:00", "kind": "sensor resumed", "detail": "x/y/z"}]
    old = [{"at": "2026-01-01T00:00:00+00:00", "kind": "sensor resumed", "detail": "a/b/c"}]
    path.write_text(json.dumps(old))
    assert W.append_news(e, path, now=NOW) == 1
    assert W.append_news(e, path, now=NOW) == 0               # idempotent
    kept = json.loads(path.read_text())
    assert [k["detail"] for k in kept] == ["x/y/z"]          # January aged out


def test_news_events_keep_where_and_how_many():
    state = {"captured_at": "2026-10-01T22:00:00+00:00",
             "sensors": {"lake/Hobo/tallac_lake": {"last_update": "2026-09-28T15:15:00"}}}
    changes = [{"severity": "notable", "kind": "dormant sensor received an upload",
                "detail": "lake/Hobo/tallac_lake", "note": "record count 46,847 -> 56,729 (+9,882), newest x"},
               {"severity": "info", "kind": "state change", "detail": "lake/Hobo/tallac_lake"}]
    out = W.news_events(changes, state)
    assert len(out) == 1 and out[0]["added"] == 9882 and out[0]["site"] == "tallac_lake"


def test_snow_sensor_noise_on_a_warm_dry_day_is_not_first_snow(monkeypatch, tmp_path):
    # The real 2026-10-02 reading: 2.5 cm of "snow" at 15 C, no precipitation, no SWE.
    days = pd.date_range("2026-09-30", "2026-10-02")
    rows = []
    for d, snwd, tavg, prcp, wteq in zip(days, (0, 0, 2.5), (15.0, 15.1, 15.2), (0, 0, 0), (0, 0, 0)):
        for var, v in (("SNWD", snwd), ("TAVG", tavg), ("PRCP", prcp), ("WTEQ", wteq)):
            rows.append({"site": "Css Lab", "variable": var, "timestamp": d, "value": v})
    frame = pd.DataFrame(rows)
    monkeypatch.setattr("secchi.store.read_partitions", lambda *a, **k: frame)
    monkeypatch.setattr(N, "PROCESSED_DIR", tmp_path)
    (tmp_path / "snotel_observations").mkdir()
    assert N.from_first_snow(SINCE) == []
    # Same depth on a cold day with precipitation: that's snow.
    cold = frame.copy()
    cold.loc[(cold.variable == "TAVG") & (cold.timestamp == "2026-10-02"), "value"] = -1.0
    cold.loc[(cold.variable == "PRCP") & (cold.timestamp == "2026-10-02"), "value"] = 6.0
    monkeypatch.setattr("secchi.store.read_partitions", lambda *a, **k: cold)
    items = N.from_first_snow(SINCE)
    assert len(items) == 1 and items[0]["source"] == "Beyond TEON"
