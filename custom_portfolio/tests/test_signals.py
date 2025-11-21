import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from custom_portfolio.strategies.active_strategies import (  # noqa: E402
    strategy_001_mgc_two_lows as mgc,
    strategy_002_es_sma_cross as es,
    strategy_003_nq_bar_color as nq,
)


class DummyState:
    def __init__(self, params):
        self.params = params


def test_mgc_two_lows_emits_buy():
    base_params = mgc.STRATEGY_CONFIG["params"].copy()
    # Use small periods to make the pattern easy to trigger
    base_params.update({"sma_period": 5, "descending_lows_count": 2})
    sma_period = base_params["sma_period"]
    rows = sma_period + 10

    # Rising closes to keep SMA rising
    idx = pd.date_range("2025-01-01", periods=rows, freq="min")

    close = pd.Series(np.linspace(2600, 2700, rows), index=idx)
    open_ = close - 0.5
    high = close + 1.0
    low = close - 1.0

    # Force descending lows on last two bars
    low.iloc[-2] = 50
    low.iloc[-1] = 40

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)

    signal = mgc.generate_signal(DummyState(base_params), df)
    assert signal == "BUY"


def test_nq_bar_color_emits_buy_and_sell():
    params = nq.STRATEGY_CONFIG["params"]
    idx = pd.date_range("2025-01-01", periods=1, freq="min")

    green = pd.DataFrame({"open": [100], "high": [101], "low": [99], "close": [102]}, index=idx)
    red = pd.DataFrame({"open": [100], "high": [101], "low": [99], "close": [98]}, index=idx)

    assert nq.generate_signal(DummyState(params), green) == "BUY"
    assert nq.generate_signal(DummyState(params), red) == "SELL"


def test_es_sma_cross_emits_buy_on_crossover_with_trend():
    base_params = es.STRATEGY_CONFIG["params"].copy()
    # Use smaller periods to force a clear crossover/trend in a short series
    base_params.update({"fast_sma_period": 3, "slow_sma_period": 5, "trend_sma_period": 8})
    fast = base_params["fast_sma_period"]
    slow = base_params["slow_sma_period"]
    trend = base_params["trend_sma_period"]
    # Build a short series that forces the crossover/trend conditions
    close_vals = [100.0] * 10 + [100.0, 99.0, 100.0, 101.0, 120.0]
    idx = pd.date_range("2025-01-01", periods=len(close_vals), freq="min")
    close = pd.Series(close_vals, index=idx)
    open_ = close - 0.5
    high = close + 1.0
    low = close - 1.0
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)

    # Sanity: ensure sufficient bars
    assert len(df) > max(fast, slow, trend)

    signal = es.generate_signal(DummyState(base_params), df)
    assert signal in ("BUY", "SELL")
    # With upward push we expect BUY; allow SELL only if logic changes
    assert signal == "BUY"
