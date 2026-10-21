"""The C# side (cloud/dotnet) must describe the same tables as the SQL schema.

Layouts.cs repeats cloud/sql/01_schema.sql on purpose, so the loader can
compare it with the live table. This keeps the two in step without needing
.NET or a database: parse both, compare column names, order and types.
"""

import re
import shutil
import subprocess

import pytest

from secchi.config import REPO_ROOT

SCHEMA = REPO_ROOT / "cloud" / "sql" / "01_schema.sql"
LAYOUTS = REPO_ROOT / "cloud" / "dotnet" / "Secchi.Cloud.Core" / "Layouts.cs"
CORE = REPO_ROOT / "cloud" / "dotnet" / "Secchi.Cloud.Core"

SQL_TO_LAYOUT = {"uniqueidentifier": "G", "smallint": "I2", "int": "I4", "bigint": "I8",
                 "float": "F", "datetime2": "DT", "datetimeoffset": "DTO", "varchar": "T"}


def schema_tables() -> dict[str, list[tuple[str, str]]]:
    text = re.sub(r"--[^\n]*", "", SCHEMA.read_text(encoding="utf-8"))
    out = {}
    for name, body in re.findall(r"CREATE TABLE secchi\.(\w+) \((.*?)\n\);", text, re.S):
        cols = []
        for part in re.split(r",\s*(?![^()]*\))", body):
            m = re.match(r"\s*(\w+)\s+(\w+)", part)
            if m and m.group(2).lower() in {*SQL_TO_LAYOUT, "geometry"}:
                cols.append((m.group(1), SQL_TO_LAYOUT.get(m.group(2).lower(), "geometry")))
        out[name] = cols
    return out


def layout_tables() -> dict[str, list[tuple[str, str]]]:
    text = LAYOUTS.read_text(encoding="utf-8")
    wide = {True: [], False: []}
    for unit, body in re.findall(r"=> unit\s*\?(.*?):(.*?);", text, re.S)[:1]:
        wide[True] = re.findall(r'C\("(\w+)", (\w+)\)', unit)
        wide[False] = re.findall(r'C\("(\w+)", (\w+)\)', body)
    out = {}
    for name, body in re.findall(r'\["(\w+)"\] = new\("\w+", (\[.*?\]|Wide\(unit: \w+\))\),', text, re.S):
        if "Wide(unit: true)" in body:
            out[name] = wide[True]
        elif "Wide(unit: false)" in body:
            out[name] = wide[False]
        else:
            out[name] = re.findall(r'C\("(\w+)", (\w+)\)', body)
    return out


def test_every_exported_table_has_a_layout_matching_the_schema():
    schema, layouts = schema_tables(), layout_tables()
    assert set(layouts) == set(schema) - {"watershed"}       # watershed loads via watershed_stage
    for name, cols in layouts.items():
        assert cols == schema[name], name


def test_wide_layouts_were_parsed():
    layouts = layout_tables()
    assert [c for c, _ in layouts["snotel"]][-1] == "unit"
    assert [c for c, _ in layouts["smoke"]][-1] == "value"


@pytest.mark.skipif(not shutil.which("dotnet"), reason=".NET SDK not installed")
def test_core_builds(tmp_path):
    # The core library has no package references, so it builds offline.
    feed = tmp_path / "feed"
    feed.mkdir()
    r = subprocess.run(["dotnet", "build", str(CORE), "-c", "Release", "--source", str(feed),
                        "-o", str(tmp_path / "out"), "-nologo", "-v", "q"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
