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

def test_airports_parse_acis_to_metric_and_keep_missing_missing():
    tvl = A.parse(json.loads((FX / "acis_tvl.json").read_text()), "TVL")
    assert set(tvl["site"]) == {"South Lake Tahoe airport"}
    day = tvl[tvl["timestamp"] == pd.Timestamp("2024-06-01")].set_index("variable")["value"]
    assert day["TMAX"] == pytest.approx((73 - 32) * 5 / 9, abs=1e-3)
    assert day["PRCP"] == 0.0
    # South Lake Tahoe doesn't report snow ("M"): absent, not zero.
    assert "SNOW" not in set(tvl["variable"])
    # A day that is all "M" stores nothing at all.
    assert not (tvl["timestamp"] == pd.Timestamp("2026-10-02")).any()
    assert tvl["uuid"].is_unique


def test_truckee_reports_snow_in_centimetres():
    trk = A.parse(json.loads((FX / "acis_trk.json").read_text()), "TRK")
    assert {"SNOW", "SNWD"} <= set(trk["variable"])
    assert set(trk.loc[trk["variable"].isin(["SNOW", "SNWD"]), "unit"]) == {"cm"}


def test_a_trace_is_not_zero():
    df = A.parse({"data": [["2026-01-01", "30", "20", "T", "T", "1"]]}, "TRK").set_index("variable")["value"]
    assert 0 < df["PRCP"] < 0.1 and 0 < df["SNOW"] < 0.1
    assert df["SNWD"] == pytest.approx(2.54)


@pytest.mark.parametrize("bad", [
    {"error": "Unknown sid"},
    {"data": [["2026-01-01", "30"]]},
    {"data": [["2026-01-01", "30", "20", "lots", "0", "0"]]},
])
def test_an_unexpected_airport_reply_fails_loudly(bad):
    with pytest.raises(A.AsosShapeError):
        A.parse(bad, "TVL")


def test_airports_never_store_the_day_in_progress():
    from datetime import date
    # ingest asks for days up to yesterday at the lake, whatever UTC says.
    today = date(2026, 10, 3)
    got = []
    A.fetch, real = (lambda code, b, e: got.append((b, e)) or {"data": []}), A.fetch
    try:
        A.ingest(since=date(2026, 10, 1), today=today)
    finally:
        A.fetch = real
    assert got and all(e == date(2026, 10, 2) for _, e in got)


def test_yearly_means_average_months_first():
    # Three readings in January at 30 m and one in July at 10 m: averaging
    # months first gives 20 m, a plain mean would give 25 m.
    d = pd.DataFrame({"station": "LTP", "secchi_m": [30.0, 30.0, 30.0, 10.0],
                      "date_time_local": pd.to_datetime(["2024-01-03", "2024-01-15", "2024-01-29",
                                                         "2024-07-10"])})
    y = T.yearly_means_m(d).set_index("year")
    assert y.loc[2024, "mean_m"] == pytest.approx(20.0)
    assert y.loc[2024, "n"] == 4 and y.loc[2024, "months"] == 2
