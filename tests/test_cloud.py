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
    assert "UTF8" in fingerprint_tsql(by["terc"])                # the note is UTF-8
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
