"""The page's "Download the data" section, run as the browser would run it.

The section reads GitHub's public releases API and draws what it finds.
These tests run the page's own JavaScript in Node (no jsdom: the section is
string-building plus one fetch), with the API mocked, and cover the ways
it could go wrong without anyone noticing: no release yet, GitHub
unreachable, a link that points somewhere it shouldn't, markup in a file
name. They also syntax-check every inline script, because one typo in a
single 2,800-line page takes every section down together.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

BASE = "https://github.com/bdgroves/secchi/releases/download/data-2026-10-07/"

HARNESS = r"""
const fs = require('fs');
const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const block = fs.readFileSync(process.argv[3], 'utf8');
const esc = (s) => String(s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtDay = (y) => "D(" + y + ")";
const el = { innerHTML: "" };
const document = { getElementById: (id) => id === "downloads-panel" ? el : null };
globalThis.fetch = async () => {
  if (scenario.reject) throw new Error("offline");
  const status = scenario.status || 200;
  return { ok: status === 200, status, json: async () => scenario.body };
};
const code = block + "\n;return loadDownloads();";
new Function("esc", "fmtDay", "document", code)(esc, fmtDay, document)
  .then(() => console.log(JSON.stringify({ html: el.innerHTML })));
"""


def _block() -> str:
    m = re.search(r"// downloads:start\n(.*?)// downloads:end", PAGE, re.S)
    assert m, "the downloads block markers are missing from web/index.html"
    return m.group(1)


def _asset(name, size=1_000_000, url=None):
    return {"name": name, "size": size, "browser_download_url": url or BASE + name}


def _release(assets, tag="data-2026-10-07", **kw):
    r = {"tag_name": tag, "draft": False, "published_at": "2026-10-08T01:02:03Z",
         "html_url": "https://github.com/bdgroves/secchi/releases/tag/" + tag,
         "assets": assets}
    r.update(kw)
    return r


FULL = [
    _asset("forest.parquet", 166_952_146), _asset("forest_2025.csv.gz", 85_357_027),
    _asset("forest_2026.csv.gz", 107_642_955), _asset("forest_2024.csv.gz", 34_937_080),
    _asset("lake_exo.parquet", 28_030_590), _asset("lake_exo_2025.csv.gz", 21_475_654),
    _asset("lake_exo_2026.csv.gz", 33_574_326),
    _asset("terc_secchi.csv", 278_591),
    _asset("README.md", 5_000), _asset("DISCLAIMER.md", 2_000), _asset("SOURCES.md", 2_000),
    _asset("CITATION.cff", 600), _asset("manifest.json", 16_000), _asset("SHA256SUMS", 2_400),
]


def _run(tmp_path, body=None, **scenario) -> str:
    (tmp_path / "scenario.json").write_text(json.dumps({"body": body, **scenario}))
    (tmp_path / "block.js").write_text(_block(), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS)
    out = subprocess.run([NODE, "harness.js", "scenario.json", "block.js"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])["html"]


# ---------------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------------

@needs_node
def test_every_inline_script_parses(tmp_path):
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", PAGE, re.S)
    assert scripts, "no inline scripts found"
    for i, js in enumerate(scripts):
        f = tmp_path / f"s{i}.js"
        f.write_text(js, encoding="utf-8")
        out = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        assert out.returncode == 0, f"inline script {i} does not parse:\n{out.stderr}"


def test_the_section_has_its_slot_and_a_disclaimer_that_does_not_depend_on_javascript():
    assert 'id="downloads-panel"' in PAGE and 'id="downloads"' in PAGE
    notice = re.search(r'<div class="dl-notice">(.*?)</div>', PAGE, re.S).group(1).lower()
    for phrase in ("provisional", "not an official product", "not quality control",
                   "without warranty"):
        assert phrase in notice, f"static notice does not say {phrase!r}"


def test_downloads_load_independently_of_the_snapshot():
    """Called from load(), after the snapshot's try/catch, never from render()."""
    load = PAGE[PAGE.index("async function load()"):]
    assert load.rstrip().count("loadDownloads();") >= 1
    render = PAGE[PAGE.index("function render(snap)"):PAGE.index("// downloads:start")]
    assert "loadDownloads" not in render and "renderDownloads" not in render


# ---------------------------------------------------------------------------
# What it draws
# ---------------------------------------------------------------------------

@needs_node
def test_lists_each_dataset_with_parquet_and_csv_by_year(tmp_path):
    html = _run(tmp_path, [_release(FULL)])
    assert 'href="' + BASE + 'lake_exo.parquet"' in html
    assert 'href="' + BASE + 'lake_exo_2025.csv.gz"' in html
    assert "167 MB" in html and "28 MB" in html           # sizes, humanised
    assert "Data through D(2026-10-07)" in html and "Published D(2026-10-08)" in html
    # Known datasets in the page's order, not the API's: lake_exo before forest.
    assert html.index("lake_exo") < html.index("forest") < html.index("terc_secchi")


@needs_node
def test_csv_years_are_in_order_whatever_order_the_api_lists_them(tmp_path):
    html = _run(tmp_path, [_release(FULL)])
    forest = html[html.index("forest"):html.index("terc_secchi")]
    assert forest.index(">2024<") < forest.index(">2025<") < forest.index(">2026<")


@needs_node
def test_the_documents_are_linked_under_read_before_using(tmp_path):
    html = _run(tmp_path, [_release(FULL)])
    docs = html[html.index("Read before using"):]
    for name in ("README.md", "DISCLAIMER.md", "SOURCES.md", "SHA256SUMS"):
        assert BASE + name in docs
    assert docs.index("README") < docs.index("Disclaimer") < docs.index("Sources")


@needs_node
def test_a_dataset_the_page_does_not_know_about_still_appears_after_the_known_ones(tmp_path):
    html = _run(tmp_path, [_release(FULL + [_asset("newthing_2026.csv.gz")])])
    assert "newthing" in html and html.index("terc_secchi") < html.index("newthing")


@needs_node
def test_a_direct_read_snippet_points_at_the_real_file(tmp_path):
    html = _run(tmp_path, [_release(FULL)])
    assert "pd.read_parquet" in html and BASE + "lake_exo.parquet" in html


# ---------------------------------------------------------------------------
# What it says when there is nothing to draw
# ---------------------------------------------------------------------------

@needs_node
@pytest.mark.parametrize("body", [
    [],
    [_release(FULL, tag="v1.0")],                          # a code release, not data
    [_release(FULL, draft=True)],
    {"message": "not a list"},
])
def test_no_data_release_says_so_plainly(tmp_path, body):
    assert "No data release has been published yet" in _run(tmp_path, body)


@needs_node
def test_the_first_data_release_wins_and_code_releases_are_skipped(tmp_path):
    html = _run(tmp_path, [_release(FULL, tag="v1.0"),
                           _release([_asset("lake_exo.parquet", url=BASE.replace("2026-10-07", "2026-10-14") + "lake_exo.parquet")],
                                    tag="data-2026-10-14")])
    assert "data-2026-10-14" in html and "Data through D(2026-10-14)" in html


@needs_node
@pytest.mark.parametrize("scenario", [{"reject": True}, {"status": 403}, {"status": 500}])
def test_an_unreachable_github_says_so_instead_of_leaving_a_spinner(tmp_path, scenario):
    html = _run(tmp_path, [], **scenario)
    assert "Couldn't reach GitHub" in html and "Looking for" not in html


@needs_node
def test_a_release_with_no_files_says_so(tmp_path):
    assert "no" in _run(tmp_path, [_release([_asset("README.md")])]).lower()


# ---------------------------------------------------------------------------
# Hostile or malformed input
# ---------------------------------------------------------------------------

@needs_node
@pytest.mark.parametrize("url", [
    "https://evil.example.com/lake_exo.parquet",
    "javascript:alert(1)",
    "https://github.com/someone-else/secchi/releases/download/data-2026-10-07/lake_exo.parquet",
    "https://github.com/bdgroves/secchi/releases/download/data-2000-01-01/lake_exo.parquet",
    None,
])
def test_a_link_that_does_not_point_into_this_release_is_never_drawn(tmp_path, url):
    assets = [_asset("lake_exo.parquet", url=url) if url else
              {"name": "lake_exo.parquet", "size": 1, "browser_download_url": None},
              _asset("forest.parquet")]
    html = _run(tmp_path, [_release(assets)])
    assert "evil.example.com" not in html and "javascript:" not in html
    assert "someone-else" not in html and "data-2000-01-01" not in html
    assert "forest.parquet" in html                       # the good one survives


@needs_node
def test_markup_in_a_file_name_is_escaped(tmp_path):
    nasty = "<img src=x onerror=alert(1)>.parquet"
    html = _run(tmp_path, [_release([_asset(nasty, url=BASE + "x.parquet")])])
    assert "<img" not in html and "&lt;img" in html


@needs_node
def test_a_malformed_release_tag_is_refused_not_drawn(tmp_path):
    html = _run(tmp_path, [_release(FULL, tag="data-<script>")])
    assert "<script>" not in html and "Couldn't reach GitHub" in html
