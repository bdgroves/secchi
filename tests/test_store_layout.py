"""The store's layout and its promises.

The store is committed to git, so every byte it rewrites is stored again
forever. These tests pin down the behaviour that keeps it from growing:
historical months stay single files, the current month is one file per
day, the same data always produces the same bytes, and unchanged files
are not rewritten. And the behaviour that keeps it safe: nothing is lost
or duplicated as files are converted, appended or compacted.
"""

from __future__ import annotations

import pandas as pd
import pytest

from secchi.store import (PARTITION_FILE, append_partitions, compact_partitions,
                          day_file, drop_partition, read_partitions,
                          undated_summary, write_partitions)

KEY = ["uuid", "site", "variable"]


def rows(stamps, value=1.0, site="Glenbrook", prefix="r"):
    return pd.DataFrame({
        "uuid": [f"{prefix}{i}" for i in range(len(stamps))],
        "site": site,
        "sensor_type": "ExoSensor",
        "variable": "Temp",
        "timestamp": pd.to_datetime(stamps),
        "value": value,
    })


def files(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/")
                  for p in root.rglob("*.parquet"))


def snapshot(root):
    return {p: p.read_bytes() for p in root.rglob("*.parquet")}


@pytest.fixture
def root(tmp_path):
    return tmp_path / "observations"


def test_history_is_monthly_and_october_on_is_daily(root):
    write_partitions(rows(["2026-09-30 12:00", "2026-10-01 01:00",
                           "2026-10-02 23:45"]), root, "teon", KEY)
    assert files(root) == [
        "source=teon/year=2026/month=09/part.parquet",
        "source=teon/year=2026/month=10/part.d01.parquet",
        "source=teon/year=2026/month=10/part.d02.parquet",
    ]


def test_unchanged_data_rewrites_nothing(root):
    data = rows(pd.date_range("2026-10-01", periods=200, freq="15min"))
    write_partitions(data, root, "teon", KEY)
    before = snapshot(root)
    out = write_partitions(data, root, "teon", KEY)
    assert out["partitions_touched"] == 0
    assert snapshot(root) == before


def test_same_data_in_any_order_gives_identical_bytes(tmp_path):
    stamps = ["2026-10-01 00:00"] * 5 + ["2026-10-01 00:15"] * 5   # many ties
    data = rows(stamps)
    a, b = tmp_path / "a", tmp_path / "b"
    write_partitions(data, a, "teon", KEY)
    write_partitions(data.sample(frac=1, random_state=7), b, "teon", KEY)
    fa = day_file(a / "source=teon/year=2026/month=10", 1)
    fb = day_file(b / "source=teon/year=2026/month=10", 1)
    assert fa.read_bytes() == fb.read_bytes()


def test_only_the_day_with_new_readings_is_rewritten(root):
    write_partitions(rows(["2026-10-01 06:00", "2026-10-02 06:00"]), root, "teon", KEY)
    before = snapshot(root)
    new = rows(["2026-10-02 07:00"], prefix="n")
    write_partitions(pd.concat([rows(["2026-10-01 06:00", "2026-10-02 06:00"]), new]),
                     root, "teon", KEY)
    after = snapshot(root)
    d1 = next(p for p in after if p.name == "part.d01.parquet")
    d2 = next(p for p in after if p.name == "part.d02.parquet")
    assert after[d1] == before[d1]
    assert after[d2] != before[d2]


def test_a_corrected_value_replaces_the_stored_one(root):
    write_partitions(rows(["2026-10-01 06:00"], value=1.0), root, "teon", KEY)
    write_partitions(rows(["2026-10-01 06:00"], value=2.0), root, "teon", KEY)
    stored = read_partitions(root)
    assert len(stored) == 1 and stored["value"].iloc[0] == 2.0


def test_an_old_monthly_october_file_converts_itself(root):
    target = root / "source=teon/year=2026/month=10"
    target.mkdir(parents=True)
    legacy = rows(["2026-10-01 01:00", "2026-10-01 02:00", "2026-10-03 05:00"])
    legacy.to_parquet(target / PARTITION_FILE, index=False)

    write_partitions(rows(["2026-10-03 06:00"], prefix="n"), root, "teon", KEY)

    assert not (target / PARTITION_FILE).exists()
    assert sorted(p.name for p in target.glob("*.parquet")) == [
        "part.d01.parquet", "part.d03.parquet"]
    stored = read_partitions(root)
    assert len(stored) == 4 and stored["uuid"].is_unique


def test_append_then_compact_in_a_daily_month(root):
    write_partitions(rows(["2026-10-01 01:00"], prefix="old"), root, "teon", KEY)
    append_partitions(rows(["2026-10-01 01:00", "2026-10-02 01:00"], prefix="old"),
                      root, "teon")
    append_partitions(rows(["2026-10-02 02:00"], prefix="new"), root, "teon")
    compact_partitions(root, KEY)
    names = sorted(p.name for p in (root / "source=teon/year=2026/month=10").glob("*.parquet"))
    assert names == ["part.d01.parquet", "part.d02.parquet"]
    stored = read_partitions(root)
    assert len(stored) == 3 and not stored.duplicated(KEY).any()


def test_append_then_compact_in_a_historical_month_stays_one_file(root):
    append_partitions(rows(["2025-06-01 01:00", "2025-06-20 01:00"]), root, "teon")
    append_partitions(rows(["2025-06-21 01:00"], prefix="b"), root, "teon")
    compact_partitions(root, KEY)
    assert files(root) == ["source=teon/year=2025/month=06/part.parquet"]
    assert len(read_partitions(root)) == 3


def test_undated_rows_are_kept_not_lost(root):
    df = rows(["2026-10-01 01:00", None])
    write_partitions(df, root, "teon", KEY)
    assert "source=teon/year=0000/month=00/part.parquet" in files(root)
    assert len(read_partitions(root)) == 2


def test_drop_partition_removes_every_day_file(root):
    write_partitions(rows(["2026-10-01 01:00", "2026-10-05 01:00"]), root, "teon", KEY)
    out = drop_partition(root, "teon", 2026, 10)
    assert out["dropped"] and out["rows"] == 2
    assert read_partitions(root).empty


def test_undated_duplicates_are_told_apart_from_undated_originals(root):
    write_partitions(rows(["2026-09-01 01:00", "2026-09-01 02:00"]), root, "teon", KEY)
    copy = rows(["2026-09-01 01:00", "2026-09-01 02:00"]); copy["timestamp"] = pd.NaT
    write_partitions(copy, root, "teon", KEY)
    assert undated_summary(root) == {"undated": 2, "only_undated": 0}
    orphan = rows([None], prefix="orphan")
    write_partitions(orphan, root, "teon", KEY)
    assert undated_summary(root) == {"undated": 3, "only_undated": 1}
