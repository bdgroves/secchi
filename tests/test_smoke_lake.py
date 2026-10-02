"""The smoke comparison: a real effect is found, a seasonal trend is not."""

from __future__ import annotations

import numpy as np
import pandas as pd

from secchi.analysis import smoke_lake as SL


def calendar(late: bool):
    """Two summers; a five-day smoke episode in each month, early or late."""
    days = pd.date_range("2025-06-01", "2026-09-30", freq="D")
    s = pd.Series(np.nan, index=days)
    for yr in (2025, 2026):
        for mo in (6, 7, 8, 9):
            month = days[(days.year == yr) & (days.month == mo)]
            s[month] = 0.0
            start = 22 if late else 8
            s[month[start:start + 5]] = 1.0
    return s


def sonde(smoke, effect=0.0, trend=0.0, seed=1):
    rng = np.random.default_rng(seed)
    idx = smoke.dropna().index
    day_of_month = idx.day.to_numpy(float)
    v = 1.0 + trend * day_of_month + rng.normal(0, 0.3, len(idx)) + effect * (smoke[idx].to_numpy() > 0)
    return pd.Series(v, index=idx)


def test_a_real_effect_is_found_with_or_without_trend_removal():
    smoke = calendar(late=False)
    y = sonde(smoke, effect=1.0)
    rng = np.random.default_rng(0)
    assert SL.test(y, smoke, 0, rng)["p"] < 0.01
    assert SL.test(SL.detrend(y), smoke, 0, rng)["p"] < 0.01


def test_a_seasonal_rise_is_not_mistaken_for_smoke():
    """The trap from 2026-10-02: no smoke effect, a rising month, late smoke."""
    smoke = calendar(late=True)
    y = sonde(smoke, effect=0.0, trend=0.05)
    rng = np.random.default_rng(0)
    assert SL.test(y, smoke, 0, rng)["p"] < 0.05            # the raw test is fooled
    assert SL.test(SL.detrend(y), smoke, 0, rng)["p"] > 0.2  # trend removal isn't


def test_detrend_removes_a_straight_line_within_each_month():
    idx = pd.date_range("2025-08-01", "2025-08-31", freq="D")
    y = pd.Series(np.arange(len(idx), dtype=float) * 0.1 + 5, index=idx)
    assert np.allclose(SL.detrend(y).to_numpy(), 0, atol=1e-9)
