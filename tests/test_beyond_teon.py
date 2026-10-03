"""The Beyond-TEON sources added 2026-10-02: TERC Secchi, the Snow Lab, airports.

Fixtures in tests/fixtures/ are trimmed copies of what each service
actually returned to a GitHub runner on 2026-10-02 (this workspace can't
reach them), so these pin the observed shape, not a documented one. As
with SNOTEL, anything else must fail loudly rather than store nothing.
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest

import secchi.sources.asos as A
import secchi.sources.cssl as C
import secchi.sources.snotel as S
import secchi.sources.terc as T

FX = pathlib.Path(__file__).parent / "fixtures"


# --- TERC Secchi -------------------------------------------------------------

def test_terc_parses_both_stations_in_metres_with_local_times():
    ltp = T.parse_csv((FX / "terc_secchi_ltp.csv").read_text(encoding="utf-8"), "Secchi_LTP.csv")
    mltp = T.parse_csv((FX / "terc_secchi_mltp.csv").read_text(encoding="utf-8"), "Secchi_MLTP.csv")
    assert set(ltp["station"]) == {"LTP"} and set(mltp["station"]) == {"MLTP"}
    # 1967's first reading has a bare date, later ones a time: both parse.
    first = ltp.iloc[0]
    assert first["date_time_local"] == pd.Timestamp("1967-07-28")
    assert first["secchi_m"] == pytest.approx(29.26)
    assert ltp.iloc[-1]["date_time_local"] == pd.Timestamp("2025-12-10 12:10")
    # Secchi is the mean of disappear and reappear (TERC's definition).
    row = mltp.iloc[-1]
    assert row["secchi_m"] == pytest.approx((row["disappear_m"] + row["reappear_m"]) / 2)
    assert ltp["date_time_local"].dt.tz is None          # naive local, like the store


def test_terc_renamed_column_fails_loudly():
    text = (FX / "terc_secchi_ltp.csv").read_text(encoding="utf-8").replace('"Secchi",', '"SD",', 1)
    with pytest.raises(T.TercShapeError):
        T.parse_csv(text, "Secchi_LTP.csv")


def test_terc_depth_in_feet_would_be_caught():
    text = (FX / "terc_secchi_ltp.csv").read_text(encoding="utf-8").replace(",29.26,", ",96.0,", 1)
    with pytest.raises(T.TercShapeError):
        T.parse_csv(text, "Secchi_LTP.csv")


def test_terc_tries_revisions_newer_than_the_index_lists_first():
    fx = json.loads((FX / "dataone_edi1340.json").read_text())
    newest, cands = T.candidate_pids(fx["data"], fx["meta"])
    # Metadata says revision 17 exists; indexed data files stop earlier.
    assert newest == 17
    for pids in cands.values():
        assert pids[0].startswith("https://pasta.lternet.edu/package/data/eml/edi/1340/17/")
        revs = [int(p.split("/")[-2]) for p in pids]
        assert revs == sorted(revs, reverse=True)
        # Same entity hash in every candidate: a file keeps its hash across revisions.
        assert len({p.split("/")[-1] for p in pids}) == 1


def test_terc_missing_station_file_fails_loudly():
    fx = json.loads((FX / "dataone_edi1340.json").read_text())
    only_ltp = [d for d in fx["data"] if d["fileName"] == "Secchi_LTP.csv"]
    with pytest.raises(T.TercShapeError):
        T.candidate_pids(only_ltp, fx["meta"])


# --- The Snow Lab ----------------------------------------------------------------

def test_cssl_climatology_parses_147_years_and_keeps_na_missing():
    df = C.parse(json.loads((FX / "cssl_snowclimo.json").read_text()))
    assert len(df) == 147
    assert df["water_year"].iloc[0] == 1879 and df["water_year"].iloc[-1] == 2025
    wy2020 = df[df["water_year"] == 2020].iloc[0]
    assert pd.isna(wy2020["snowfall_cm"])                  # "NA" is not zero
    assert wy2020["max_depth_cm"] == 254
    assert df.loc[df["water_year"] == 2023, "snowfall_cm"].iloc[0] == 1914


def test_cssl_changed_table_fails_loudly():
    rows = json.loads((FX / "cssl_snowclimo.json").read_text())
    for r in rows:
        r.pop("Snowfall (cm)")
    with pytest.raises(C.CsslShapeError):
        C.parse(rows)


def test_cssl_only_accepts_the_public_anon_key():
    import base64

    def token(role):
        body = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
        return f"eyJhbGciOiJIUzI1NiJ9.{body}.c2lnbmF0dXJlc2ln"
    assert C._jwt_role(token("anon")) == "anon"
    assert C._jwt_role(token("service_role")) == "service_role"
    assert C._jwt_role("not.a.token") is None


def test_snow_lab_snotel_snow_depth_is_centimetres():
    payload = json.loads((FX / "snotel_428.json").read_text())
    stations = {"Css Lab": {"triplet": "428:CA:SNTL", "lat": 39.33, "lng": -120.37, "side": "snowlab"}}
    df = S.parse(payload, stations)
    assert {"PRCP", "PREC", "TAVG", "WTEQ", "SNWD"} <= set(df["variable"])
    assert set(df.loc[df["variable"] == "SNWD", "unit"]) == {"cm"}
    assert S._convert("SNWD", "in", 10.0) == (pytest.approx(25.4), "cm")


def test_snow_lab_is_never_a_shore():
    assert S.SNOTEL_STATIONS["Css Lab"] not in S.SHORE_SIDES


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload):
        self._p = payload

    def get(self, *_a, **_k):
        return _Resp(self._p)


SHORES = [{"stationTriplet": "848:CA:SNTL", "name": "Ward Creek #3"},
          {"stationTriplet": "724:CA:SNTL", "name": "Rubicon #2"},
          {"stationTriplet": "615:NV:SNTL", "name": "Marlette Lake"}]


def test_snow_lab_resolves_by_its_snotel_name(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATION_CACHE", tmp_path / "s.json")
    meta = json.loads((FX / "snotel_428.json").read_text())  # data payload; meta is from the probe
    assert meta[0]["stationTriplet"] == "428:CA:SNTL"
    lab = {"stationTriplet": "428:CA:SNTL", "name": "Css Lab", "latitude": 39.32565,
           "longitude": -120.36807, "elevation": 6890.0}
    out = S.resolve_stations(_Client(SHORES + [lab]))
    assert out["Css Lab"]["side"] == "snowlab" and out["Css Lab"]["triplet"] == "428:CA:SNTL"


def test_a_missing_snow_lab_does_not_stop_the_shores(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATION_CACHE", tmp_path / "s.json")
    out = S.resolve_stations(_Client(SHORES))
    assert set(out) == {"Ward Creek #3", "Rubicon #2", "Marlette Lake"}


# --- Airports --------------------------------------------------------------------

def test_asos_parses_iem_daily_to_metric():
    df = A.parse((FX / "iem_daily_tvl.csv").read_text(encoding="utf-8"))
    assert set(df["site"]) == {"South Lake Tahoe airport"}
    day = df[df["timestamp"] == pd.Timestamp("2026-09-20")].set_index("variable")["value"]
    assert day["TMAX"] == pytest.approx((72 - 32) * 5 / 9, abs=1e-3)
    assert day["PRCP"] == 0.0
    assert df["uuid"].is_unique


def test_asos_blank_stays_missing_and_trace_is_not_zero():
    text = ("station,day,max_temp_f,min_temp_f,precip_in\n"
            "TVL,2026-01-01,30,,T\n")
    df = A.parse(text).set_index("variable")["value"]
    assert "TMIN" not in df.index
    assert 0 < df["PRCP"] < 0.01


def test_asos_unexpected_reply_fails_loudly():
    with pytest.raises(A.AsosShapeError):
        A.parse("ERROR: server over capacity, please try later\n")
