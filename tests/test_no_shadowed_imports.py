"""No function may re-import a name the module already imports.

On 2026-09-22 a local `from secchi.sources.teon import TeonClient`
inside one branch of `run()` shadowed the module-level import for the
whole function. Every other branch then raised UnboundLocalError, and
the hourly cron failed for hours.

What makes this worth a test rather than a code review note:

  * it is not a syntax error
  * the module imports cleanly
  * the branch containing the local import works perfectly
  * only the OTHER branches break, at runtime

Nothing in the existing suite could see it. This can.

Local imports in general are fine and used deliberately here to keep
optional dependencies out of the import path. The rule is narrower:
don't locally import a name that's ALSO imported at module scope.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "secchi"


def _module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:                      # top level only
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _local_imports(tree: ast.Module) -> list[tuple[str, str, int]]:
    """(function, name, lineno) for every import inside a function."""
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    out.append((fn.name, bound, node.lineno))
    return out


def test_no_local_import_shadows_a_module_import():
    offences = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_names = _module_level_names(tree)
        for fn_name, bound, lineno in _local_imports(tree):
            if bound in module_names:
                offences.append(
                    f"{path.relative_to(SRC)}:{lineno} — {fn_name}() "
                    f"re-imports {bound!r}, which is already imported at "
                    f"module level. This makes {bound!r} local to the whole "
                    f"function and raises UnboundLocalError in every other "
                    f"branch."
                )
    assert not offences, "shadowed imports:\n  " + "\n  ".join(offences)
