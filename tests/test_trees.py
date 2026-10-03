"""The dendrometer analysis (analysis.trees): daily shrinkage and channel checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from secchi.analysis.trees import analyse, channel_qc, daily_shrinkage, summary, tree_rows


def _stem(site, tree, days=40, amp=30.0, base=2000.0, start="2026-07-01", noise=0.0, peak_hour=7):
    ts = pd.date_range(start, periods=days * 96, freq="15min")
    hour = ts.hour + ts.minute / 60
    # Largest at peak_hour, smallest ~9 h later: a stem that breathes.
    v = base + amp / 2 * np.cos((hour - peak_hour) / 24 * 2 * np.pi * 1.0)
    v = v + np.random.default_rng(tree).normal(0, noise, len(ts))
    return pd.DataFrame({"site": site, "sensor_type": "TreeStressAndGrowth",
                         "variable": f"Tree_{tree}_diameter_change",
                         "timestamp": ts, "value": np.round(v)})


def test_daily_shrinkage_recovers_a_known_cycle():
    t = tree_rows(_stem("Glenbrook 5", 1, amp=30))
    d = daily_shrinkage(t)
    assert d["mds"].median() == pytest.approx(30, abs=4)


def test_channel_checks_catch_still_noisy_and_out_of_step_channels():
    frames = [_stem("X", 1), _stem("X", 2, amp=1), _stem("X", 3, peak_hour=19)]
    noisy = _stem("X", 4)
    noisy.loc[noisy.index[::40], "value"] += 300           # repeated jumps
    qc = channel_qc(tree_rows(pd.concat(frames + [noisy]))).set_index("tree")
    assert bool(qc.loc[1, "ok"])
    assert "barely moves" in qc.loc[2, "why"]
    assert "out of step" in qc.loc[3, "why"]
    assert "jumps" in qc.loc[4, "why"]


def test_a_flat_channel_day_is_not_zero_shrinkage():
    flat = _stem("X", 1)
    flat["value"] = 2500.0
    assert daily_shrinkage(tree_rows(flat)).empty


def test_summary_uses_only_good_trees():
    df = pd.concat([_stem("Homewood", 1, amp=10), _stem("Homewood", 2, amp=1)])
    s = summary(analyse(df))
    st = s["stations"][0]
    assert st["trees_ok"] == 1 and st["trees"] == 2
    assert st["last7_um"] == pytest.approx(10, abs=3)
    assert "Homewood" in s["profile"]


def test_a_station_day_needs_enough_trees():
    # Four good trees, but on one day only one reports: that day is dropped.
    frames = [_stem("X", k, days=10) for k in range(1, 5)]
    df = pd.concat(frames)
    gap = (df["variable"] != "Tree_1_diameter_change") & (df["timestamp"].dt.day == 5)
    r = analyse(df[~gap])
    assert pd.Timestamp("2026-07-05") not in set(r["station"]["day"])
    assert pd.Timestamp("2026-07-06") in set(r["station"]["day"])


def test_an_unengaged_channel_reading_near_zero_is_dropped():
    s = _stem("X", 1, base=0.0, amp=0.04)          # -0.02..0.02: not engaged
    assert daily_shrinkage(tree_rows(s)).empty
