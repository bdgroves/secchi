"""SNOTEL parsing, station lookup, and the rainfall transect's plumbing.

The live service can't be reached from where this was written, so these
tests pin the documented response layout and, just as importantly, check
that anything else fails loudly instead of storing nothing.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import secchi.sources.snotel as S

STATIONS = {
    "Ward Creek #3": {"triplet": "848:CA:SNTL", "lat": 39.13, "lng": -120.22, "side": "west"},
    "Marlette Lake": {"triplet": "615:NV:SNTL", "lat": 39.16, "lng": -119.90, "side": "east"},
}


def payload(triplet, element, unit, values):
    return {"stationTriplet": triplet, "data": [{
        "stationElement": {"elementCode": element, "storedUnitCode": unit, "durationName": "DAILY"},
        "values": [{"date": d, "value": v} for d, v in values]}]}


def test_parse_converts_units_and_keys_each_value():
    data = [payload("848:CA:SNTL", "PRCP", "in", [("2025-11-13", 1.0), ("2025-11-14", 0.5)]),
            payload("615:NV:SNTL", "TAVG", "degF", [("2025-11-13", 32.0), ("2025-11-14", 50.0)])]
    df = S.parse(data, STATIONS)
    prcp = df[df.variable == "PRCP"].set_index("timestamp")["value"]
    assert prcp.loc["2025-11-13"] == pytest.approx(25.4)
    tavg = df[df.variable == "TAVG"].set_index("timestamp")["value"]
    assert tavg.loc["2025-11-13"] == pytest.approx(0.0)
    assert tavg.loc["2025-11-14"] == pytest.approx(10.0)
    assert df["uuid"].is_unique and set(df["site"]) == {"Ward Creek #3", "Marlette Lake"}


def test_missing_values_are_skipped_not_zeroed():
    df = S.parse([payload("848:CA:SNTL", "PRCP", "in", [("2025-11-13", None), ("2025-11-14", 0.2)])],
                 STATIONS)
    assert len(df) == 1


@pytest.mark.parametrize("bad", [
    {"not": "a list"},
    [{"stationTriplet": "848:CA:SNTL"}],
    [{"stationTriplet": "848:CA:SNTL", "data": [{"values": []}]}],
])
def test_an_unexpected_shape_fails_loudly(bad):
    with pytest.raises(S.SnotelShapeError):
        S.parse(bad, STATIONS)


def test_an_unexpected_unit_fails_loudly():
    with pytest.raises(S.SnotelShapeError):
        S.parse([payload("848:CA:SNTL", "PRCP", "furlongs", [("2025-11-13", 1.0)])], STATIONS)


class FakeClient:
    def __init__(self, stations):
        self.stations = stations

    def get(self, url, params=None):
        outer = self

        class R:
            def raise_for_status(self): pass
            def json(self): return outer.stations
        return R()


def test_stations_are_found_by_name_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATION_CACHE", tmp_path / "snotel_stations.json")
    listing = [{"stationTriplet": "848:CA:SNTL", "name": "Ward Creek #3", "latitude": 39.1, "longitude": -120.2},
               {"stationTriplet": "724:CA:SNTL", "name": "Rubicon #2", "latitude": 39.0, "longitude": -120.1},
               {"stationTriplet": "615:NV:SNTL", "name": "Marlette  Lake", "latitude": 39.2, "longitude": -119.9},
               {"stationTriplet": "1:CA:SNTL", "name": "Somewhere Else"}]
    out = S.resolve_stations(FakeClient(listing))
    assert out["Marlette Lake"]["triplet"] == "615:NV:SNTL"      # spacing tolerated
    assert json.loads((tmp_path / "snotel_stations.json").read_text())["Rubicon #2"]["side"] == "west"


def test_a_station_that_cant_be_found_says_what_was_nearby(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATION_CACHE", tmp_path / "snotel_stations.json")
    listing = [{"stationTriplet": "848:CA:SNTL", "name": "Ward Creek #3"},
               {"stationTriplet": "615:NV:SNTL", "name": "Marlette Lake"},
               {"stationTriplet": "725:CA:SNTL", "name": "Rubicon #3"}]
    with pytest.raises(S.SnotelShapeError, match="rubicon #3"):
        S.resolve_stations(FakeClient(listing))


def test_rainfall_transect_runs_end_to_end():
    """Synthetic soil and gauges: the west gets twice the rain and responds."""
    from secchi.analysis.transect import SOIL_VARIABLE, TRANSECT_PAIR
    from secchi.analysis.transect_rain import analyse

    west, east = TRANSECT_PAIR
    idx = pd.date_range("2025-10-01", "2026-04-30", freq="15min")
    storms = pd.to_datetime(["2025-11-01", "2025-12-01", "2026-01-15", "2026-03-01"])
    rows = []
    for site, size in ((west, 0.06), (east, 0.03)):
        v = np.full(len(idx), 0.08)
        for s in storms:
            age = (idx - s).total_seconds() / 86400
            v += np.where(age >= 0, size * np.exp(-np.clip(age, 0, None) / 4), 0)
        rows.append(pd.DataFrame({"uuid": [f"{site}{i}" for i in range(len(idx))], "site": site,
                                  "variable": SOIL_VARIABLE, "timestamp": idx, "value": v,
                                  "sensor_type": "SoilEnvironmentalConditions"}))
    soil = pd.concat(rows, ignore_index=True)

    days = pd.date_range("2025-09-01", "2026-05-31", freq="D")
    snow = []
    for site, mm in (("Ward Creek #3", 40.0), ("Marlette Lake", 20.0)):
        for d in days:
            wet = any(abs((d - s).days) <= 0 for s in storms)
            snow += [{"site": site, "variable": "PRCP", "timestamp": d, "value": mm if wet else 0.0,
                      "uuid": f"{site}P{d}"},
                     {"site": site, "variable": "TAVG", "timestamp": d, "value": 3.0, "uuid": f"{site}T{d}"}]
    r = analyse(df=soil, snotel=pd.DataFrame(snow))
    assert r is not None
    (aw, ae), (sw, se) = r["all"], r["soil"]
    assert aw / ae == pytest.approx(2.0)
    assert sw / se == pytest.approx(2.0, rel=0.25)
    assert r["events"] and all(e["rain_w"] > e["rain_e"] for e in r["events"] if e["rain_w"] is not None)


def test_summary_matches_the_analysis_and_stays_json_ready():
    """The page reads summary(); it must agree with the analysis it summarises."""
    import json as _json
    from secchi.analysis import transect_rain as TR

    days = pd.date_range("2025-09-01", "2026-05-31", freq="D")
    gauges = []
    for site, mm in (("Ward Creek #3", 20.0), ("Rubicon #2", 10.0), ("Marlette Lake", 8.0)):
        for d in days:
            gauges += [{"site": site, "variable": "PRCP", "timestamp": d, "value": mm if d.day == 1 else 0.0,
                        "uuid": f"{site}P{d}"},
                       {"site": site, "variable": "TAVG", "timestamp": d, "value": 2.0, "uuid": f"{site}T{d}"}]
    from secchi.analysis.transect import SOIL_VARIABLE, TRANSECT_PAIR
    idx = pd.date_range("2025-09-01", "2026-05-31", freq="15min")
    soil = []
    for site, size in zip(TRANSECT_PAIR, (0.05, 0.02)):
        v = np.full(len(idx), 0.08)
        for s in pd.date_range("2025-10-01", "2026-05-01", freq="MS"):
            age = (idx - s).total_seconds() / 86400
            v += np.where(age >= 0, size * np.exp(-np.clip(age, 0, None) / 4), 0)
        soil.append(pd.DataFrame({"uuid": [f"{site}{i}" for i in range(len(idx))], "site": site,
                                  "variable": SOIL_VARIABLE, "timestamp": idx, "value": v,
                                  "sensor_type": "SoilEnvironmentalConditions"}))
    r = TR.analyse(df=pd.concat(soil, ignore_index=True), snotel=pd.DataFrame(gauges))
    s = TR.summary(r)
    _json.dumps(s)                                         # no timestamps or numpy left in it
    assert s["events"] == len(s["storms"]) == s["rain_events"] + s["melt_events"]
    assert s["precip_ratio"] == pytest.approx(2.5, rel=0.01)
    alts = {a["gauge"]: a["ratio"] for a in s["west_alternatives"]}
    assert alts["Rubicon #2"] == pytest.approx(1.25, rel=0.01)   # the range the page shows
