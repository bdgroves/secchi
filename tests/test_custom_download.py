"""The custom download, end to end: webdata's files, then the page's own code.

``secchi.webdata`` cuts a small store into per-site, per-month files; the
page's JavaScript (between the custom-download markers in web/index.html)
is run in Node against those files exactly as a browser would, its zip is
opened here, and the CSV inside is compared with the rows the store says
should be there. The store is the export tests' one, so it has a hidden
site, flags of every kind and duplicates.
"""

from __future__ import annotations

import gzip
import io
import json
import pathlib
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone

import pandas as pd
import pytest

from secchi import webdata
from test_export import _reference, _store

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
NOW = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def site(tmp_path):
    """A built web/data/ in tmp, and the store it came from."""
    processed, ref = _store(tmp_path), _reference(tmp_path)
    out = tmp_path / "web" / "data"
    cat = webdata.build(out=out, processed_dir=processed, reference_dir=ref,
                        disabled={"hiddenlake"}, visibility_source="test", now=NOW)
    return out, cat, processed


def _store_rows(processed) -> pd.DataFrame:
    import duckdb
    g = (processed / "observations" / "**" / "*.parquet").as_posix()
    return duckdb.sql(f"SELECT * EXCLUDE (source, year, month) FROM read_parquet('{g}', "
                      f"hive_partitioning = true, union_by_name = true)").df()


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------

def test_every_visible_row_is_in_exactly_one_file(site):
    out, cat, processed = site
    store = _store_rows(processed)
    visible = store[store.site != "Hidden Lake"]
    total = sum(d["rows"] for d in cat["datasets"] if d["teon"])
    assert total == len(visible)
    on_disk = 0
    for f in out.rglob("*.csv.gz"):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            on_disk += sum(1 for _ in fh) - 1
    assert on_disk == len(visible)


def test_the_hidden_site_is_nowhere(site):
    out, cat, _ = site
    for f in out.rglob("*.csv.gz"):
        assert "Hidden Lake" not in gzip.open(f, "rt").read()
    assert "Hidden Lake" not in (out / "catalog.json").read_text()


def test_catalog_paths_rows_and_sizes_match_the_files(site):
    out, cat, _ = site
    for d in cat["datasets"]:
        for s in d["sites"]:
            for f in s["files"]:
                p = out.parent / f["path"]
                assert p.stat().st_size == f["bytes"]
                with gzip.open(p, "rt") as fh:
                    assert sum(1 for _ in fh) - 1 == f["rows"]
                assert re.fullmatch(r"data/[\w.-]+/[\w.-]+/\d{4}-\d{2}\.csv\.gz", f["path"])


def test_flags_and_units_come_through(site):
    out, cat, _ = site
    exo = next(d for d in cat["datasets"] if d["name"] == "lake_exo")
    assert exo["group"] == "TEON" and "quality_flag" in exo["columns"]
    text = gzip.open(out / "lake_exo" / "sunnyside" / "2026-05.csv.gz", "rt").read()
    assert "channel_scramble" in text
    forest = next(d for d in cat["datasets"] if d["name"] == "forest")
    gb4 = next(s for s in forest["sites"] if s["site"] == "Glenbrook 4")
    vwc = next(v for v in gb4["variables"] if v["name"] == "Soil_VWC")
    assert vwc["units"].startswith("fraction")
    assert "provisional" in cat["disclaimer"].lower()
    assert cat["redistribution"]["status"] == "cleared"


def test_an_unreadable_hidden_site_list_leaves_teon_out(tmp_path, monkeypatch):
    processed, ref = _store(tmp_path), _reference(tmp_path)

    def refuse(**kw):
        raise webdata.export.ExportRefused("TEON unreachable")
    monkeypatch.setattr(webdata.export, "load_disabled", refuse)
    out = tmp_path / "web" / "data"
    cat = webdata.build(out=out, processed_dir=processed, reference_dir=ref, now=NOW)
    assert not [d for d in cat["datasets"] if d["teon"]]
    for name in ("lake_exo", "lake_nearshore", "forest"):
        assert "hidden sites couldn't be read" in cat["left_out"][name]
    assert not list(out.rglob("*.csv.gz"))


# ---------------------------------------------------------------------------
# The page's code, run in Node against those files
# ---------------------------------------------------------------------------

HARNESS = r"""
const fs = require('fs'), path = require('path');
const [root, selPath, blockPath] = process.argv.slice(2);
const sel = JSON.parse(fs.readFileSync(selPath, 'utf8'));
const block = fs.readFileSync(blockPath, 'utf8');
const esc = (s) => String(s);
const fmtDay = (y) => y;
const dlBytes = (n) => String(n);
const fetchFn = async (p) => {
  const f = path.join(root, p);
  if (!fs.existsSync(f)) return { ok: false, status: 404 };
  const b = fs.readFileSync(f);
  return { ok: true, status: 200,
           arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.length) };
};
const api = new Function("esc", "fmtDay", "dlBytes",
  block + "\nreturn { cdPlan, cdRun, cdShift, cdSplit, cdFilter };")(esc, fmtDay, dlBytes);
(async () => {
  const cat = JSON.parse(fs.readFileSync(path.join(root, 'data', 'catalog.json'), 'utf8'));
  const plan = api.cdPlan(cat, sel);
  if (plan.error) { console.log(JSON.stringify({ error: plan.error })); return; }
  const out = await api.cdRun(cat, plan, fetchFn, null);
  const buf = Buffer.concat(out.zip.map(u => Buffer.from(u)));
  fs.writeFileSync(path.join(path.dirname(selPath), 'out.zip'), buf);
  console.log(JSON.stringify({ rows: out.rows, base: out.base, from: plan.from, to: plan.to,
                               files: plan.files.length }));
})().catch(e => { console.log(JSON.stringify({ crash: String(e) })); process.exit(1); });
"""


def _block() -> str:
    m = re.search(r"// custom-download:start\n(.*?)// custom-download:end", PAGE, re.S)
    assert m, "the custom-download markers are missing from web/index.html"
    return m.group(1)


def _download(tmp_path, out_dir, **sel):
    work = tmp_path / "node"
    work.mkdir(exist_ok=True)
    (work / "sel.json").write_text(json.dumps({"dropFlagged": False, **sel}))
    (work / "block.js").write_text(_block(), encoding="utf-8")
    (work / "h.js").write_text(HARNESS)
    r = subprocess.run([NODE, "h.js", str(out_dir.parent), "sel.json", "block.js"],
                       cwd=work, capture_output=True, text=True, timeout=60)
    res = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    assert r.returncode == 0 and "crash" not in res, r.stderr + r.stdout
    if "error" in res:
        return res, None, None
    z = zipfile.ZipFile(work / "out.zip")
    assert z.testzip() is None                       # CRCs check out
    names = z.namelist()
    csv = next(n for n in names if n.endswith(".csv"))
    df = pd.read_csv(io.BytesIO(z.read(csv)), keep_default_na=False)
    return res, df, z.read("README.txt").decode("utf-8")


@needs_node
def test_one_variable_one_station_custom_dates(site, tmp_path):
    out, cat, processed = site
    res, df, readme = _download(tmp_path, out, dataset="lake_exo", sites=["4H Camp"],
                                variables=["Chl_a"], period="custom",
                                **{"from": "2026-07-01", "to": "2026-07-31"})
    store = _store_rows(processed)
    want = store[(store.site == "4H Camp") & (store.variable == "Chl_a")
                 & (store.timestamp >= "2026-07-01") & (store.timestamp < "2026-08-01")]
    assert res["rows"] == len(want) == len(df) == 1
    assert df.loc[0, "value"] == 7.8 and df.loc[0, "quality_flag"] == "channel_scramble"
    assert res["base"] == "secchi_lake_exo_2026-07-01_to_2026-07-31"
    for phrase in ("PROVISIONAL DATA", "not an official product", "without warranty",
                   "Chl_a = Chlorophyll", "channel_scramble:", "4H Camp",
                   "SOURCE (cite this", "tahoeenvironmentalobservatorynetwork.org",
                   "see SOURCE below"):
        assert phrase.lower() in readme.lower(), phrase


@needs_node
def test_every_station_every_variable_everything_equals_the_store(site, tmp_path):
    out, cat, processed = site
    exo = next(d for d in cat["datasets"] if d["name"] == "lake_exo")
    vars_ = sorted({v["name"] for s in exo["sites"] for v in s["variables"]})
    res, df, _ = _download(tmp_path, out, dataset="lake_exo",
                           sites=[s["site"] for s in exo["sites"]],
                           variables=vars_, period="all")
    store = _store_rows(processed)
    want = store[(store.sensor_type == "ExoSensor") & (store.site != "Hidden Lake")]
    assert len(df) == len(want) == res["rows"]
    got = df.sort_values("uuid").set_index(["uuid", "variable"])["value"]
    exp = want.sort_values("uuid").set_index(["uuid", "variable"])["value"]
    pd.testing.assert_series_equal(got.sort_index(), exp.sort_index(), check_names=False)


@needs_node
def test_last_7_days_counts_back_from_the_newest_reading(site, tmp_path):
    out, cat, processed = site
    res, df, _ = _download(tmp_path, out, dataset="forest", sites=["Homewood"],
                           variables=["Air_Temp", "Widget"], period="7d")
    assert res["to"] == "2026-10-02 08:00:00" and res["from"] == "2026-09-25 08:00:00"
    assert sorted(df.variable) == ["Air_Temp", "Widget"]


@needs_node
def test_leaving_out_flagged_readings(site, tmp_path):
    out, cat, _ = site
    kw = dict(dataset="forest", sites=["Glenbrook 4"], variables=["Soil_VWC"], period="all")
    _, kept, _ = _download(tmp_path, out, **kw)
    _, dropped, readme = _download(tmp_path, out, dropFlagged=True, **kw)
    assert len(kept) == 4 and set(kept.quality_flag) == {"duplicate_reading", "conflicting_duplicate"}
    assert len(dropped) == 0 and "left out" in readme


@needs_node
@pytest.mark.parametrize("sel, msg", [
    (dict(dataset="nope", sites=[], variables=[], period="all"), "dataset"),
    (dict(dataset="forest", sites=[], variables=["Air_Temp"], period="all"), "station"),
    (dict(dataset="forest", sites=["Homewood"], variables=[], period="all"), "variable"),
    (dict(dataset="forest", sites=["Homewood"], variables=["Air_Temp"], period="custom",
          **{"from": "2026-10-05", "to": "2026-10-01"}), "after"),
])
def test_bad_selections_say_why(site, tmp_path, sel, msg):
    res, _, _ = _download(tmp_path, site[0], **sel)
    assert msg in res["error"]


@needs_node
def test_a_path_outside_data_is_never_fetched(site, tmp_path):
    out, cat, _ = site
    forest = next(d for d in cat["datasets"] if d["name"] == "forest")
    forest["sites"][0]["files"][0]["path"] = "../../etc/passwd"
    (out / "catalog.json").write_text(json.dumps(cat))
    s = forest["sites"][0]
    res, df, _ = _download(tmp_path, out, dataset="forest", sites=[s["site"]],
                           variables=[v["name"] for v in s["variables"]], period="all")
    assert res["files"] == len(s["files"]) - 1


@needs_node
def test_shift_and_split():
    tricky = 'a,"b,c",,"d ""q"""'
    js = _block() + (
        '\nconsole.log(JSON.stringify([cdShift("2026-03-08 01:30:00", 7), '
        'cdShift("2026-01-03 00:00:00", 7), cdSplit(' + json.dumps(tricky) + '), '
        'cdShift("bad", 1)]));')
    harness = ("const esc=s=>s, fmtDay=s=>s, dlBytes=s=>s;\n" + js)
    r = subprocess.run([NODE, "-e", harness], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    a, b, split, bad = json.loads(r.stdout)
    assert a == "2026-03-01 01:30:00" and b == "2025-12-27 00:00:00"
    assert split == ["a", "b,c", "", 'd "q"'] and bad is None


@needs_node
def test_custom_dates_cut_both_ends_within_a_month(site, tmp_path):
    # 4H Camp has Temp on 07-18 and 07-23, both in the July file.
    out = site[0]
    res, df, _ = _download(tmp_path, out, dataset="lake_exo", sites=["4H Camp"],
                           variables=["Temp"], period="custom",
                           **{"from": "2026-07-19", "to": "2026-07-31"})
    assert list(df.timestamp) == ["2026-07-23 15:00:00"]
    res, df, _ = _download(tmp_path, out, dataset="lake_exo", sites=["4H Camp"],
                           variables=["Temp"], period="custom",
                           **{"from": "2026-07-01", "to": "2026-07-20"})
    assert list(df.timestamp) == ["2026-07-18 12:00:00"]


@needs_node
def test_a_dataset_without_flags_says_so_and_names_things_in_words(tmp_path):
    processed, ref = _store(tmp_path), _reference(tmp_path)
    d = processed / "snotel_observations" / "source=snotel" / "year=2026" / "month=01"
    d.mkdir(parents=True)
    pd.DataFrame([{"uuid": "s1", "source": "snotel", "site": "Css Lab", "sensor_type": "Snotel",
                   "timestamp": pd.Timestamp("2026-01-03"), "lat": 39.3, "lng": -120.4,
                   "variable": "PRCP", "value": 58.4, "unit": "mm"}]).to_parquet(d / "part.parquet", index=False)
    out = tmp_path / "web" / "data"
    cat = webdata.build(out=out, processed_dir=processed, reference_dir=ref,
                        disabled={"hiddenlake"}, visibility_source="test", now=NOW)
    sn = next(x for x in cat["datasets"] if x["name"] == "snotel")
    assert sn["sites"][0]["label"] == "Central Sierra Snow Lab"
    assert sn["sites"][0]["variables"][0]["label"] == "Precipitation, daily"
    res, df, readme = _download(tmp_path, out, dataset="snotel", sites=["Css Lab"],
                                variables=["PRCP"], period="all")
    assert len(df) == 1 and df.loc[0, "value"] == 58.4
    assert "None: secchi flags only the TEON datasets" in readme
    assert "Flagged readings:" not in readme and "channel_scramble" not in readme
    assert "PRCP = Precipitation, daily, mm" in readme
    assert "Central Sierra Snow Lab" in readme
    z = zipfile.ZipFile(tmp_path / "node" / "out.zip")
    assert all(i.date_time[0] >= 2026 for i in z.infolist())
