"""Capabilities that once existed must keep existing.

Every other test in this project checks **internal consistency** — modes
match tasks, `--stage` choices match the stage definitions. A consistent
*subset* of the real feature set passes all of them, so deleting a
feature together with its task is invisible.

That has now happened twice, both times because a bundle was assembled
from a stale copy of a file:

  * `write_web_watersheds` vanished from transform.py, and the catchment
    layer silently stopped rendering.
  * `--mode oxygen-check` vanished from ingest.py, along with its task,
    and nothing noticed until someone tried to run it.

This test is the other direction: a plain list of what must exist. It
does not check behaviour and makes no claim about correctness — only
that the capability is still wired up.

Adding to the list is deliberate. Removing from it should be a conscious
decision recorded in a commit message, not a side effect of a copy.
"""

from __future__ import annotations

import ast
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Every --mode that must remain available. Grouped by what it's for, so
# a gap is legible rather than a wall of strings.
REQUIRED_MODES = {
    # Ingest
    "live-exo", "all-live", "all-sensors", "usgs", "prune",
    # Probes — write nothing, exist to check assumptions
    "probe", "probe-live", "usgs-probe", "usgs-discover", "usgs-params",
    "camera-probe", "record-shape", "reference-inspect", "terc-discover",
    # Reference data and analysis
    "reference", "catchment-join", "oxygen-check", "transect",
    "glenbrook", "station-health",
    # The store
    "backfill", "store-status", "drop-undated", "purge-hidden",
    "repair-sensor-types",
    # Monitoring
    "watch",
}

# Modules that must remain importable, with a callable each. Catches a
# whole file being dropped, which no mode list would notice.
REQUIRED_ENTRY_POINTS = [
    ("secchi.analysis.oxygen", "check_saturation_basis"),
    ("secchi.analysis.transect", "analyse"),
    ("secchi.analysis.glenbrook", "analyse"),
    ("secchi.analysis.station_health", "analyse"),
    ("secchi.store", "write_partitions"),
    ("secchi.store", "repair_sensor_types"),
    ("secchi.backfill", "run_backfill"),
    ("secchi.probe_shape", "probe_record_shapes"),
    ("secchi.sources.reference", "reproject_geojson"),
    ("secchi.sources.simplify", "simplify_collection"),
    ("secchi.sources.watch", "diff_state"),
]

# Functions that must remain in transform.py. write_web_watersheds was
# deleted once and took the whole catchment layer with it.
REQUIRED_TRANSFORM_FUNCTIONS = {
    "write_web_watersheds",
    "build_manual_sonde_cards",
    "build_map_points",
    "count_held_records",
    "_local_do_saturation",
    "_record_timestamp",
    "build_upload_alert",
}


def _declared_modes() -> set[str]:
    tree = ast.parse((ROOT / "src" / "secchi" / "ingest.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(isinstance(a, ast.Constant) and a.value == "--mode"
                   for a in node.args):
            continue
        for k in node.keywords:
            if k.arg == "choices":
                return {e.value for e in k.value.elts
                        if isinstance(e, ast.Constant)}
    raise AssertionError("could not find the --mode choices in ingest.py")


def test_required_modes_still_exist():
    missing = sorted(REQUIRED_MODES - _declared_modes())
    assert not missing, (
        f"these --mode values have disappeared: {missing}. If a removal "
        f"was intended, take it out of REQUIRED_MODES in the same commit."
    )


def test_required_transform_functions_still_exist():
    tree = ast.parse((ROOT / "src" / "secchi" / "transform.py").read_text())
    present = {n.name for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)}
    missing = sorted(REQUIRED_TRANSFORM_FUNCTIONS - present)
    assert not missing, (
        f"these transform functions have disappeared: {missing}. "
        f"write_web_watersheds went this way once and took the catchment "
        f"map layer with it."
    )


def test_required_modules_still_import():
    """Checked by PARSING, so it runs without the data stack installed."""
    missing = []
    for module, symbol in REQUIRED_ENTRY_POINTS:
        path = ROOT / "src" / pathlib.Path(module.replace(".", "/") + ".py")
        if not path.exists():
            missing.append(f"{module} (file missing)")
            continue
        tree = ast.parse(path.read_text())
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef))}
        if symbol not in names:
            missing.append(f"{module}.{symbol}")
    assert not missing, f"these entry points have disappeared: {missing}"


# Dashboard features that must remain in web/index.html. The Python
# checks above could not see the page, so when index.html was rebuilt
# from a copy predating the offline-station marker, the transform kept
# sending collection: "offline" and the page silently drew dead stations
# amber, as if merely slow. Glenbrook 2 sat orange for a day because of
# it. Plain string checks: each is a class, element id or branch that a
# feature depends on.
REQUIRED_HTML = {
    # map pin states
    'pin-offline': "offline-station pin style",
    'p.collection === "offline"': "pinClass branch for offline stations",
    'station offline</span>': "legend entry for offline stations",
    'pin-manual-pending': "hand-collected, data waiting",
    'pin-manual': "hand-collected, up to date",
    'pin-hidden': "hidden by TEON",
    # sections and features
    'id="manual-cards"': "hand-collected coverage cards",
    'id="upload-alert"': "new-upload banner",
    'renderManualCards': "coverage card renderer",
    'c.variable_order': "per-card variable order (MiniDOT/HOBO readings)",
    'upload_alert': "banner payload consumer",
    'function convert(': "unit conversion",
    'isDelta': "delta-aware unit conversion",
}


def test_required_html_features_still_exist():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    missing = [f"{token!r} ({why})" for token, why in REQUIRED_HTML.items()
               if token not in html]
    assert not missing, (
        "these dashboard features have disappeared from web/index.html:\n  "
        + "\n  ".join(missing)
    )
