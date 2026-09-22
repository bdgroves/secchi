"""Every --mode must have a pixi task, and vice versa.

Three times now a new ingest mode has shipped without its task, so
`pixi run <thing>` failed on something that existed and worked. The
mode list lives in argparse and the task list lives in pyproject.toml,
and nothing connected them.

This connects them. It's a fast test with no network and no fixtures,
and it would have caught all three.
"""

from __future__ import annotations

import ast
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Modes that deliberately have no task: internal defaults, or steps only
# ever run as part of a composite task.
MODES_WITHOUT_TASKS = {
    "live-exo",      # the argparse default, not a workflow
    "all-sensors",   # reached via ingest-history
    "all-live",      # reached via ingest-all
}

# Tasks that don't wrap a mode: tooling, serving, composites.
TASKS_WITHOUT_MODES = {
    "format", "lint", "test", "lab", "serve", "transform",
    "pipeline", "ingest", "ingest-all", "ingest-history",
    "watch-update",  # same mode as `watch`, different flag
}


def _declared_modes() -> set[str]:
    """The --mode choices, read from the argparse call."""
    tree = ast.parse((ROOT / "src" / "secchi" / "ingest.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(isinstance(k, ast.keyword) and k.arg == "choices"
                   for k in node.keywords):
            continue
        args = [a for a in node.args
                if isinstance(a, ast.Constant) and a.value == "--mode"]
        if not args:
            continue
        for k in node.keywords:
            if k.arg == "choices":
                return {e.value for e in k.value.elts
                        if isinstance(e, ast.Constant)}
    raise AssertionError("could not find the --mode choices in ingest.py")


def _declared_tasks() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return set(data["tool"]["pixi"]["tasks"])


def test_every_mode_has_a_task():
    modes = _declared_modes() - MODES_WITHOUT_TASKS
    tasks = _declared_tasks()
    missing = sorted(modes - tasks)
    assert not missing, (
        f"these --mode values have no pixi task: {missing}. "
        f"Add them to [tool.pixi.tasks], or to MODES_WITHOUT_TASKS if "
        f"they're deliberately internal."
    )


def test_every_task_maps_to_something_real():
    tasks = _declared_tasks() - TASKS_WITHOUT_MODES
    modes = _declared_modes()
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = data["tool"]["pixi"]["tasks"]

    stale = []
    for name in sorted(tasks):
        cmd = declared[name]
        cmd = cmd if isinstance(cmd, str) else cmd.get("cmd", "")
        if "--mode" not in cmd:
            continue
        used = cmd.split("--mode", 1)[1].strip().split()[0]
        if used not in modes:
            stale.append(f"{name} -> --mode {used}")
    assert not stale, (
        f"these tasks invoke a --mode that no longer exists: {stale}"
    )


def test_backfill_stages_are_not_restated():
    """The --stage choices must be derived, not copied.

    They were hardcoded separately from backfill.STAGES, so
    `--stage live` was rejected while the stage existed. This keeps them
    derived.

    Checked by parsing rather than importing: the test should run in a
    bare checkout with no dependencies installed, and importing
    `secchi.backfill` pulls in pandas and the whole config module.
    """
    src = (ROOT / "src" / "secchi" / "ingest.py").read_text()
    assert "tuple(_BACKFILL_STAGES)" in src, (
        "--stage choices should be derived from backfill.STAGES, not "
        "restated in ingest.py"
    )

    # The literal tuple that WAS there must be gone. A stage name
    # appearing on its own is fine — `stage or "manual"` is a legitimate
    # default — so check for the restated LIST, not for any mention.
    assert '"manual", "nearshore"' not in src, (
        "ingest.py still contains a hardcoded list of stage names"
    )
