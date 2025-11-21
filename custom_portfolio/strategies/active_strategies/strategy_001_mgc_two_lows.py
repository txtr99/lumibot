"""
Strategy 001 (MGC): Two descending lows + rising SMA200 (very permissive).
Only long; no RSI filter.
"""

import pandas as pd

STRATEGY_CONFIG = {
    "strategy_id": "mgc_two_lows_001",
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "sma_period": 200,
        "descending_lows_count": 2,
        "atr_period": 20,
        "profit_target_mult": 5.0,
        "stop_loss_mult": 2.0,
        "use_atr_profit": True,
        "use_atr_stop": True,
        "max_bars": 180,
    },
    "allowed_sessions": ["24/7"],
}


def has_descending_lows(low: pd.Series, count: int = 2) -> bool:
    if len(low) < count:
        return False
    vals = low.iloc[-count:].values
    return all(vals[i] < vals[i - 1] for i in range(1, len(vals)))


def generate_signal(strategy_state, market_data):
    p = strategy_state.params
    sma_period = p.get("sma_period", 200)
    n = p.get("descending_lows_count", 2)

    if len(market_data) < max(sma_period + 1, n):
        return "HOLD"

    sma = market_data["close"].rolling(window=sma_period).mean()
    sma_now, sma_prev = sma.iloc[-1], sma.iloc[-2]
    if pd.isna(sma_now) or pd.isna(sma_prev):
        return "HOLD"

    lows_desc = has_descending_lows(market_data["low"], count=n)
    sma_rising = sma_now > sma_prev

    return "BUY" if lows_desc and sma_rising else "HOLD"


def validate_config():
    cfg = STRATEGY_CONFIG
    params = cfg.get("params", {})
    if params.get("sma_period", 0) <= 0:
        return False, "sma_period must be positive"
    if params.get("descending_lows_count", 0) <= 0:
        return False, "descending_lows_count must be positive"
    return True, ""
