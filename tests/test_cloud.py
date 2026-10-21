"""The SQL Server copy's checks, on the DuckDB side.

The CI workflow (cloud.yml) runs both sides against a real SQL Server.
These tests pin down the parts that can be checked without one: that the
DuckDB fingerprint means what the T-SQL one means, and that the comparison
flags what it should.
"""

from __future__ import annotations

import hashlib

import duckdb

from secchi.cloud.__main__ import compare, geojson_wkt
from secchi.cloud.tables import TABLES, Table, fingerprint_duck, fingerprint_tsql

ROW = Table(
    name="t", view="t", about="", fed_by="", read_by="",
    key_duck="concat(uuid, '|', variable)", key_tsql="",
    ts_duck="timestamp", ts_tsql="ts", value_duck="value", value_tsql="value",
    from_tsql="", export_sql="",
)


def _md5_head(s: str) -> int:
    """What SQL Server computes: CAST(SUBSTRING(HASHBYTES('MD5', k), 1, 4) AS bigint)."""
    return int.from_bytes(hashlib.md5(s.encode()).digest()[:4], "big")


def _con():
    con = duckdb.connect()
    con.execute("""CREATE TABLE t AS SELECT * FROM (VALUES
        ('a1', 'Temp', TIMESTAMP '2025-01-31 23:45:00', 4.25),
        ('a2', 'Temp', TIMESTAMP '2025-02-01 00:00:00', -3.26e26),
        ('a2', 'Do_mgL', TIMESTAMP '2025-02-01 00:00:00', NULL)
    ) AS v(uuid, variable, timestamp, value)""")
    return con


def test_key_hash_matches_sql_servers_definition():
    rel = _con().sql(fingerprint_duck(ROW))
    rows = {r[1]: dict(zip(rel.columns, r, strict=True)) for r in rel.fetchall()}
    assert set(rows) == {"2025-01", "2025-02"}
    assert rows["2025-01"]["key_hash"] == _md5_head("a1|Temp")
    assert rows["2025-02"]["key_hash"] == _md5_head("a2|Temp") + _md5_head("a2|Do_mgL")


def test_fingerprint_counts_nulls_and_keeps_outliers_out_of_the_sum():
    rel = _con().sql(fingerprint_duck(ROW))
    feb = dict(zip(rel.columns, rel.fetchall()[1], strict=True))
    assert feb["n_rows"] == 2 and feb["n_keys"] == 2 and feb["v_null"] == 1
    assert feb["v_min"] == -3.26e26          # still compared, exactly
    assert feb["v_sum"] is None              # but not summed


def _fp(**kw):
    base = {"tbl": "t", "part": "2025-01", "n_rows": 10, "n_keys": 10, "key_hash": 123,
            "ts_min": "2025-01-01 00:00:00.000000", "ts_max": "2025-01-31 00:00:00.000000",
            "v_min": "1.5", "v_max": "9.25", "v_sum": "50.0000", "v_null": 0}
    return base | kw


def test_compare_matches_when_equal_even_if_formatted_differently():
    server = _fp(ts_min="2025-01-01 00:00:00", v_min="1.5000000000000000E+0",
                 n_rows="10", key_hash="123")
    [r] = compare([_fp()], [server])
    assert r["ok"], r


def test_compare_flags_a_missing_row_and_a_missing_month():
    res = compare([_fp(), _fp(part="2025-02")], [_fp(n_rows=9, key_hash=99)])
    assert [r["ok"] for r in res] == [False, False]
    assert any("missing in database" in p for p in res[1]["problems"])


def test_compare_allows_rounding_in_the_sum_but_says_so():
    [r] = compare([_fp()], [_fp(v_sum="50.0001")])
    assert r["ok"] and r["problems"][0].startswith("note:")
    [r] = compare([_fp()], [_fp(v_sum="51.0000")])
    assert not r["ok"]


def test_tsql_shapes():
    by = {t.name: t for t in TABLES}
    assert "GROUP BY" not in fingerprint_tsql(by["terc"])        # one row, no grouping
    assert "BIN2_UTF8" in fingerprint_tsql(by["terc"])           # the note is UTF-8
    assert "BIN2" in fingerprint_tsql(by["obs"])                 # keys are case-sensitive
    assert "GROUP BY CONVERT(char(7), ts, 120)" in fingerprint_tsql(by["obs"])


def test_every_table_is_in_the_schema():
    from secchi.cloud.__main__ import SQL_DIR

    schema = (SQL_DIR / "01_schema.sql").read_text()
    for t in TABLES:
        assert f"CREATE TABLE secchi.{t.name} (" in schema, t.name


def test_wkt():
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert geojson_wkt(poly) == "POLYGON ((0 0, 1 0, 1 1, 0 0))"
    multi = {"type": "MultiPolygon", "coordinates": [poly["coordinates"]]}
    assert geojson_wkt(multi) == "MULTIPOLYGON (((0 0, 1 0, 1 1, 0 0)))"


def test_tables_have_as_many_columns_as_their_files():
    """bcp maps file fields to table columns one for one; a table with an
    extra column makes it read past the end of the line."""
    import re

    from secchi.cloud.__main__ import SQL_DIR

    schema = (SQL_DIR / "01_schema.sql").read_text()
    width = {}
    for name, body in re.findall(r"CREATE TABLE secchi\.(\w+) \((.*?)\n\);", schema, re.S):
        body = re.sub(r"--[^\n]*", "", body)
        width[name] = len([c for c in re.split(r",(?![^(]*\))", body) if c.strip()])
    con = _con()
    for name in ("site", "sensor_type", "variable"):
        assert name in width
    assert width["site"] == 4 and width["sensor_type"] == 2 and width["variable"] == 2
    by = {t.name: t for t in TABLES}
    expect = {"obs": 6, "usgs": 14, "snotel": 9, "asos": 9, "smoke": 8, "assets": 9,
              "terc": 7, "cssl_climo": 4, "watershed_stage": 4}
    for name, n in expect.items():
        assert width[name] == n, (name, width[name])
    for name in ("snotel", "asos", "smoke", "assets", "terc", "cssl_climo"):
        cols = re.search(r"SELECT(.*?)FROM", by[name].export_sql, re.S).group(1)
        assert len(re.split(r",(?![^(]*\))", cols)) == expect[name], name
    con.close()


def test_month_bounds_roll_over_the_year():
    from secchi.cloud.__main__ import month_bounds

    assert month_bounds("2026-02") == ("2026-02-01", "2026-03-01")
    assert month_bounds("2025-12") == ("2025-12-01", "2026-01-01")


def test_sync_reloads_only_what_differs():
    from secchi.cloud.__main__ import MAX_MONTHS, plan_sync

    ok = {"tbl": "obs", "part": "2026-01", "ok": True}
    bad = {"tbl": "obs", "part": "2026-10", "ok": False}
    assert plan_sync([ok]) == ("none", [])
    assert plan_sync([ok, bad]) == ("months", [bad])
    assert plan_sync([{"tbl": "watershed", "part": "all", "ok": False}])[0] == "full"
    many = [dict(bad, part=f"m{i}") for i in range(MAX_MONTHS + 1)]
    assert plan_sync(many)[0] == "full"


def test_tsql_literals_are_quoted():
    from secchi.cloud.__main__ import _q

    assert _q("Lake's edge") == "'Lake''s edge'"
    assert _q(None) == "NULL" and _q(True) == "1" and _q(3) == "3"


def test_quality_view_uses_the_projects_rules():
    from secchi.cloud.quality import BLOCKING, PORTED, quality_sql
    from secchi.sources.watch import IMPOSSIBLE

    sql = quality_sql()
    for _inst, var, lo, hi in IMPOSSIBLE:
        assert f"n.variable = '{var}' AND (n.value < {lo} OR n.value > {hi})" in sql
    assert set(BLOCKING) <= set(PORTED)
    assert "CREATE OR ALTER VIEW secchi.obs_clean" in sql
