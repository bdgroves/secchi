"""The export: what it must refuse, what it must flag, what it must count.

Most bugs in this project reported success while doing nothing, so these
check the written files, not the function's return value: rows are
re-read from the Parquet and CSV on disk, hidden sites are searched for in
the output, and the raw values are compared against the input.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd
import pytest

from secchi import export
from secchi.export import ExportRefused, build_bundle, load_disabled, verify_bundle
from secchi.sources.teon import VisibilityUnavailable

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# A small store that contains one of every case the export has an opinion on
# ---------------------------------------------------------------------------

def _row(uuid, site, sensor, var, ts, value):
    return {"uuid": uuid, "source": "TEON", "site": site, "sensor_type": sensor,
            "timestamp": pd.Timestamp(ts), "lat": 39.0, "lng": -120.0,
            "variable": var, "value": float(value)}


def _exo(site, ts, uuid, **channels):
    return [_row(uuid, site, "ExoSensor", k, ts, v) for k, v in channels.items()]


def _store(tmp_path) -> pathlib.Path:
    rows = []
    # Sunnyside: the whole sonde scrambled (saturation in the temperature field).
    rows += _exo("Sunnyside", "2026-05-10 12:00", "s1", Temp=90.0, Do_mgL=7.8,
                 Do_percent=20.0, Turbidity=1.0, BattV_Min=12.1)
    # 4H Camp: only the optical channels scrambled; temperature is real.
    rows += _exo("4H Camp", "2026-07-18 12:00", "h1", Temp=20.0, Do_mgL=85.8,
                 Do_percent=0.6, Chl_a=7.8)
    # 4H Camp, a normal reading in the same week: not a scramble.
    rows += _exo("4H Camp", "2026-07-23 15:00", "h2", Temp=20.5, Do_mgL=7.8,
                 Do_percent=85.0)
    # Blackwood 3 right after a service visit: low oxygen from a sonde being
    # handled. Impossible-range, but NOT a channel scramble. (An early version
    # triggered on saturation < 50 and mislabelled these.)
    rows += _exo("Blackwood 3", "2026-04-30 15:30", "b1", Temp=12.0, Do_mgL=7.0,
                 Do_percent=45.0)
    # Turbidity below zero, a dead pH channel, a healthy pH.
    rows += _exo("Glenbrook", "2026-06-01 12:00", "g1", Turbidity=-2.0, pH=0.0,
                 Chl_a=-0.12, phycocyanin=-0.30)
    rows += _exo("Glenbrook", "2026-06-01 12:15", "g2", Turbidity=0.4, pH=7.9,
                 Chl_a=0.0, phycocyanin=0.05)
    # A nearshore logger reading 50 C.
    rows.append(_row("m1", "Lakeside", "MiniDotSensor", "Temperature", "2026-06-01 12:00", 50.0))
    rows.append(_row("m2", "Lakeside", "MiniDotSensor", "Temperature", "2026-06-01 12:10", 14.0))
    # Forest: a duplicate pair (same value, different record ids) and a
    # conflicting pair (same timestamp, different values).
    rows.append(_row("f1", "Glenbrook 4", "SoilEnvironmentalConditions", "Soil_VWC", "2026-01-05 12:00", 0.077))
    rows.append(_row("f2", "Glenbrook 4", "SoilEnvironmentalConditions", "Soil_VWC", "2026-01-05 12:00", 0.077))
    rows.append(_row("f3", "Glenbrook 4", "SoilEnvironmentalConditions", "Soil_VWC", "2026-01-05 12:15", 0.080))
    rows.append(_row("f4", "Glenbrook 4", "SoilEnvironmentalConditions", "Soil_VWC", "2026-01-05 12:15", 0.090))
    rows.append(_row("f5", "Homewood", "AirTemperatureRelativeHumidity", "Air_Temp", "2026-10-02 08:00", 9.1))
    # A site TEON hides.
    rows += _exo("Hidden Lake", "2026-06-01 12:00", "x1", Temp=15.0, Do_mgL=8.0, Do_percent=90.0)
    # A sensor type nobody planned for: must land in `forest`, not vanish.
    rows.append(_row("n1", "Homewood", "SomethingNew", "Widget", "2026-10-02 08:00", 1.0))

    df = pd.DataFrame(rows)
    root = tmp_path / "processed" / "observations" / "source=teon"
    for (year, month), part in df.groupby([df.timestamp.dt.year, df.timestamp.dt.month]):
        d = root / f"year={year}" / f"month={month:02d}"
        d.mkdir(parents=True)
        # Both layouts the store really has: one file, and a day file.
        name = "part.d02.parquet" if (year, month) == (2026, 10) else "part.parquet"
        part.to_parquet(d / name, index=False)
    return tmp_path / "processed"


def _reference(tmp_path) -> pathlib.Path:
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "terc_secchi.csv").write_text("station,date_time_local,secchi_m\nLTP,1967-07-28 00:00,29.26\n")
    (ref / "cssl_snow_climatology.csv").write_text("water_year,snowfall_cm\n1879,1130\n")
    (ref / "terc_package.json").write_text(json.dumps({"package": "edi.1340", "newest_seen": 17}))
    return ref


@pytest.fixture()
def bundle(tmp_path):
    processed, ref = _store(tmp_path), _reference(tmp_path)
    out = build_bundle(processed_dir=processed, reference_dir=ref,
                       out_root=tmp_path / "exports", disabled={"hiddenlake"},
                       visibility_source="test", now=NOW)
    return out, processed


def _all_teon(bundle_dir: pathlib.Path) -> pd.DataFrame:
    frames = [pd.read_parquet(bundle_dir / f"{n}.parquet")
              for n in ("lake_exo", "lake_nearshore", "forest")]
    return pd.concat(frames, ignore_index=True)


def _flags(df, site, var, ts=None):
    q = df[(df.site == site) & (df.variable == var)]
    if ts is not None:
        q = q[q.timestamp == pd.Timestamp(ts)]
    assert len(q), f"no row for {site} {var} {ts}"
    return {None if pd.isna(f) else f for f in q.quality_flag}


def _flagset(df, site, var, ts):
    (f,) = _flags(df, site, var, ts)
    return set() if f is None else set(f.split(";"))


# ---------------------------------------------------------------------------
# Visibility: fail closed
# ---------------------------------------------------------------------------

class _Client:
    def __init__(self, slugs=None, error=None):
        self._slugs, self._error = slugs, error

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def disabled_sites(self):
        if self._error:
            raise self._error
        return self._slugs


def test_live_visibility_failure_refuses_rather_than_assuming_nothing_is_hidden():
    with pytest.raises(ExportRefused, match="visibility"):
        load_disabled(live=True, client_factory=_Client(error=VisibilityUnavailable("timeout")))


def test_live_visibility_is_slugified():
    slugs, source = load_disabled(live=True, client_factory=_Client(slugs={"4hcamp", "Hidden Lake"}))
    assert slugs == {"4hcamp", "hiddenlake"} and source == "live"


def _baseline(ref, **kw):
    data = {"captured_at": (NOW - timedelta(hours=3)).isoformat(), "disabled_sites": []}
    data.update(kw)
    (ref / "watch_baseline.json").write_text(json.dumps(data))


def test_offline_visibility_uses_a_fresh_baseline(tmp_path):
    ref = _reference(tmp_path)
    _baseline(ref, disabled_sites=["hiddenlake"])
    slugs, source = load_disabled(live=False, reference_dir=ref, now=NOW)
    assert slugs == {"hiddenlake"} and "baseline" in source


def test_offline_visibility_refuses_a_stale_baseline(tmp_path):
    ref = _reference(tmp_path)
    _baseline(ref, captured_at=(NOW - timedelta(hours=100)).isoformat())
    with pytest.raises(ExportRefused, match="old"):
        load_disabled(live=False, reference_dir=ref, now=NOW)


@pytest.mark.parametrize("content", [None, "not json", "{}", '{"captured_at": "2026-10-07T00:00:00+00:00"}'])
def test_offline_visibility_refuses_a_missing_or_malformed_baseline(tmp_path, content):
    ref = _reference(tmp_path)
    if content is not None:
        (ref / "watch_baseline.json").write_text(content)
    with pytest.raises(ExportRefused):
        load_disabled(live=False, reference_dir=ref, now=NOW)


def test_hidden_site_is_absent_from_every_output_file(bundle):
    out, _ = bundle
    con = duckdb.connect()
    for p in list(out.glob("*.parquet")) + list(out.glob("*.csv.gz")):
        reader = (f"read_parquet('{p.as_posix()}')" if p.suffix == ".parquet"
                  else f"read_csv('{p.as_posix()}', all_varchar = true)")
        n = con.execute(f"SELECT count(*) FROM {reader} WHERE site = 'Hidden Lake'").fetchone()[0]
        assert n == 0, f"{p.name} contains a hidden site"


def test_a_leak_past_the_filter_is_caught_on_the_written_file(tmp_path, monkeypatch):
    """If the filter ever stopped working, the written output must say so."""
    monkeypatch.setattr(export, "_visible_predicate", lambda disabled: "TRUE")
    with pytest.raises(ExportRefused, match="hides"):
        build_bundle(processed_dir=_store(tmp_path), reference_dir=_reference(tmp_path),
                     out_root=tmp_path / "exports", disabled={"hiddenlake"},
                     visibility_source="test", now=NOW)


# ---------------------------------------------------------------------------
# Counts and values
# ---------------------------------------------------------------------------

def test_every_visible_row_lands_in_exactly_one_dataset(bundle):
    out, processed = bundle
    src = duckdb.sql(
        f"SELECT count(*) FROM read_parquet('{(processed / 'observations').as_posix()}/**/part*.parquet', "
        f"hive_partitioning = true) WHERE site <> 'Hidden Lake'").fetchone()[0]
    assert len(_all_teon(out)) == src
    # The sensor type nobody planned for went to `forest`.
    forest = pd.read_parquet(out / "forest.parquet")
    assert "SomethingNew" in set(forest.sensor_type)


def test_raw_values_are_exactly_as_stored(bundle):
    out, processed = bundle
    got = _all_teon(out)
    src = pd.concat([pd.read_parquet(p) for p in (processed / "observations").rglob("part*.parquet")])
    src = src[src.site != "Hidden Lake"]
    cols = ["uuid", "site", "sensor_type", "variable", "timestamp", "value"]
    a = got[cols].sort_values(cols).reset_index(drop=True)
    b = src[cols].sort_values(cols).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_manifest_row_counts_match_the_files_on_disk(bundle):
    out, _ = bundle
    manifest = json.loads((out / "manifest.json").read_text())
    con = duckdb.connect()
    for d in manifest["datasets"]:
        pq = [f for f in d["files"] if f["format"] == "parquet"]
        csv = [f for f in d["files"] if f["format"] == "csv.gz"]
        assert len(pq) == 1
        on_disk = con.execute(f"SELECT count(*) FROM read_parquet('{(out / pq[0]['name']).as_posix()}')").fetchone()[0]
        assert on_disk == pq[0]["rows"] == d["rows"]
        csv_rows = sum(con.execute(
            f"SELECT count(*) FROM read_csv('{(out / f['name']).as_posix()}', all_varchar = true)").fetchone()[0]
            for f in csv)
        assert csv_rows == d["rows"]


# ---------------------------------------------------------------------------
# Quality flags
# ---------------------------------------------------------------------------

def test_a_fully_scrambled_sonde_flags_every_channel_but_battery(bundle):
    df = _all_teon(bundle[0])
    t = "2026-05-10 12:00"
    for var in ("Temp", "Do_mgL", "Do_percent", "Turbidity"):
        assert "channel_scramble" in _flagset(df, "Sunnyside", var, t), var
    assert "channel_scramble" not in _flagset(df, "Sunnyside", "BattV_Min", t)


def test_optical_scramble_leaves_temperature_alone(bundle):
    df = _all_teon(bundle[0])
    t = "2026-07-18 12:00"
    for var in ("Do_mgL", "Do_percent", "Chl_a"):
        assert "channel_scramble" in _flagset(df, "4H Camp", var, t), var
    assert "channel_scramble" not in _flagset(df, "4H Camp", "Temp", t)


def test_a_normal_reading_in_the_same_week_is_not_flagged_as_scrambled(bundle):
    df = _all_teon(bundle[0])
    t = "2026-07-23 15:00"
    for var in ("Temp", "Do_mgL", "Do_percent"):
        assert "channel_scramble" not in _flagset(df, "4H Camp", var, t), var


def test_low_oxygen_at_a_service_visit_is_impossible_but_not_a_scramble(bundle):
    flags = _flagset(_all_teon(bundle[0]), "Blackwood 3", "Do_percent", "2026-04-30 15:30")
    assert "impossible_range" in flags
    assert "channel_scramble" not in flags


def test_other_flags(bundle):
    df = _all_teon(bundle[0])
    assert "negative_turbidity" in _flagset(df, "Glenbrook", "Turbidity", "2026-06-01 12:00")
    assert "negative_turbidity" not in _flagset(df, "Glenbrook", "Turbidity", "2026-06-01 12:15")
    assert "negative_chlorophyll" in _flagset(df, "Glenbrook", "Chl_a", "2026-06-01 12:00")
    assert "negative_phycocyanin" in _flagset(df, "Glenbrook", "phycocyanin", "2026-06-01 12:00")
    # Exactly zero is not below zero.
    assert _flagset(df, "Glenbrook", "Chl_a", "2026-06-01 12:15") == set()
    assert _flagset(df, "Glenbrook", "phycocyanin", "2026-06-01 12:15") == set()
    assert "ph_zero" in _flagset(df, "Glenbrook", "pH", "2026-06-01 12:00")
    assert _flagset(df, "Glenbrook", "pH", "2026-06-01 12:15") == set()
    assert "impossible_range" in _flagset(df, "Lakeside", "Temperature", "2026-06-01 12:00")
    assert _flagset(df, "Lakeside", "Temperature", "2026-06-01 12:10") == set()
    # Saturation is flagged as sea-level-referenced for every EXO reading;
    # the concentration is not.
    assert "exo_sat_sea_level" in _flagset(df, "4H Camp", "Do_percent", "2026-07-23 15:00")
    assert "exo_sat_sea_level" not in _flagset(df, "4H Camp", "Do_mgL", "2026-07-23 15:00")


def test_duplicates_are_flagged_not_removed(bundle):
    df = _all_teon(bundle[0])
    g4 = df[(df.site == "Glenbrook 4") & (df.variable == "Soil_VWC")]
    assert len(g4) == 4                                   # nothing dropped
    same = g4[g4.timestamp == pd.Timestamp("2026-01-05 12:00")]
    diff = g4[g4.timestamp == pd.Timestamp("2026-01-05 12:15")]
    assert set(same.quality_flag) == {"duplicate_reading"}
    assert set(diff.quality_flag) == {"conflicting_duplicate"}


def test_every_flag_the_sql_can_write_is_defined():
    """The README is built from QUALITY_FLAGS; an undefined flag would ship unexplained."""
    import re
    src = pathlib.Path(export.__file__).read_text()
    # Every literal the SQL can emit: THEN 'flag' and ELSE 'flag'.
    used = set(re.findall(r"(?:THEN|ELSE)\s+'(\w+)'", src))
    assert used and used == set(export.QUALITY_FLAGS)


# ---------------------------------------------------------------------------
# Disclaimers
# ---------------------------------------------------------------------------

# What a downloader must be told, whichever file they open first.
MUST_SAY = ("provisional", "not an official product", "without warranty",
            "not quality control", "MIT licence")


@pytest.mark.parametrize("name", ["README.md", "SOURCES.md", "DISCLAIMER.md"])
def test_the_disclaimer_is_in_every_document_a_person_reads(bundle, name):
    text = (bundle[0] / name).read_text().lower()
    for phrase in MUST_SAY:
        assert phrase.lower() in text, f"{name} does not say {phrase!r}"


def test_the_disclaimer_travels_in_the_manifest_and_the_checksums(bundle):
    out, _ = bundle
    assert json.loads((out / "manifest.json").read_text())["disclaimer"] == export.DISCLAIMER
    assert "DISCLAIMER.md" in (out / "SHA256SUMS").read_text()


def test_a_release_keeps_the_disclaimer(tmp_path):
    out = _build(tmp_path, release=True, teon_permission="a basis")
    assert "without warranty" in (out / "README.md").read_text()


# ---------------------------------------------------------------------------
# The release gate
# ---------------------------------------------------------------------------

def _build(tmp_path, **kw):
    return build_bundle(processed_dir=_store(tmp_path), reference_dir=_reference(tmp_path),
                        out_root=tmp_path / "exports", disabled={"hiddenlake"},
                        visibility_source="test", now=NOW, **kw)


def test_a_preview_says_it_is_a_preview(bundle):
    out, _ = bundle
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["redistribution"]["status"] == "preview"
    assert "PREVIEW" in (out / "README.md").read_text()
    assert "PREVIEW" in (out / "SOURCES.md").read_text()


@pytest.mark.parametrize("permission", [None, "", "   "])
def test_release_refuses_without_a_recorded_permission(tmp_path, permission):
    with pytest.raises(ExportRefused, match="teon-permission"):
        _build(tmp_path, release=True, teon_permission=permission)


def test_release_with_a_permission_records_it(tmp_path):
    out = _build(tmp_path, release=True, teon_permission="S. Chandra, email, 2026-10-12")
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["redistribution"] == {"status": "cleared",
                                          "teon_permission": "S. Chandra, email, 2026-10-12"}
    assert "PREVIEW" not in (out / "README.md").read_text()
    assert "S. Chandra" in (out / "SOURCES.md").read_text()


def test_a_partial_export_cannot_be_released(tmp_path):
    with pytest.raises(ExportRefused, match="only"):
        _build(tmp_path, release=True, teon_permission="x", only=["lake_exo"])


def test_a_source_with_unconfirmed_terms_is_left_out_and_says_so(bundle):
    out, _ = bundle
    manifest = json.loads((out / "manifest.json").read_text())
    assert (out / "terc_secchi.csv").exists()
    assert not (out / "cssl_snow_climatology.csv").exists()
    assert [r["name"] for r in manifest["reference"]["excluded"]] == ["cssl_snow_climatology.csv"]


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def test_bundle_verifies_and_tampering_is_caught(bundle):
    out, _ = bundle
    assert verify_bundle(out) == []
    target = out / "lake_exo_2026.csv.gz"
    target.write_bytes(target.read_bytes() + b"x")
    assert any("lake_exo_2026.csv.gz" in p for p in verify_bundle(out))


def test_no_temp_files_are_left_behind(bundle):
    out, _ = bundle
    assert not list(out.parent.glob(".building*"))
    assert not list(out.rglob("*.tmp"))


def test_the_same_store_exports_to_identical_files(tmp_path):
    a = _build(tmp_path / "a")
    b = _build(tmp_path / "b")
    for name in ("lake_exo.parquet", "forest.parquet", "lake_exo_2026.csv.gz"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_exports_are_never_committed():
    lines = {ln.strip() for ln in (ROOT / ".gitignore").read_text().splitlines()}
    assert "exports/" in lines, "exports/ must be gitignored: it would double the repo's size"
