"""
Strategy 003 (NQ): Bar color entry (very permissive).
BUY if close > open; SELL if close < open. No filters.
"""

import pandas as pd

STRATEGY_CONFIG = {
    "strategy_id": "nq_bar_color_003",
    "symbol": "NQ",
    "contracts": 1,
    "params": {
        "atr_period": 20,
        "profit_target_mult": 2.5,
        "stop_loss_mult": 1.0,
        "use_atr_profit": True,
        "use_atr_stop": True,
        "max_bars": 90,
    },
    "allowed_sessions": ["New_York"],
}


def generate_signal(strategy_state, market_data):
    if len(market_data) < 1:
        return "HOLD"
    last = market_data.iloc[-1]
    c, o = last["close"], last["open"]
    if pd.isna(c) or pd.isna(o):
        return "HOLD"
    if c > o:
        return "BUY"
    if c < o:
        return "SELL"
    return "HOLD"


def validate_config():
    return True, ""
