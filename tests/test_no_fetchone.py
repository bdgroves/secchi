"""No code may call DuckDB's `.fetchone()`; use `.fetchall()`.

On 2026-10-22 the morning report ran on DuckDB 2.0.0 and its lake-level
check failed: 2.0 removed `.fetchone()` from relations (`con.sql(...)`),
because a relation keeps no result between calls. `con.execute(...)`
still has it, but 2.0 then holds the connection's result open, and the
next `con.sql(...)` on that connection raises ResourceInUseException.

`.fetchall()` behaves the same on 1.x and 2.x for both. Every query that
used `.fetchone()` here was an aggregate with no GROUP BY, which always
returns exactly one row, so `.fetchall()[0]` is an exact replacement.

The lock pins DuckDB 1.x, so nothing in the suite would fail until the
day it's upgraded. This catches it now.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_no_fetchone_calls():
    found = []
    for base in (ROOT / "src", ROOT / "tests"):
        for path in base.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Attribute) and node.attr == "fetchone":
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not found, ("use .fetchall()[0] instead of .fetchone() "
                       "(breaks on DuckDB 2.0): " + ", ".join(found))
