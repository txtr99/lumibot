"""
ES_1M_01 - Mean Reversion Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- RSI(14) >= 70 (overbought condition)
- Rate of Change(5) current < Rate of Change(5) 3 bars ago (momentum declining)
- Stochastics(14) rising (current > previous)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "rsi_length": 14,
        "roc_length": 5,
        "stoch_length": 14,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,
        "sl_mult": 8.0,
    },
    "time_exit": {
        "max_bars": 120,
    },
    "allowed_sessions": ["New_York"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Compute and return a dict of indicators.
    """
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    rsi_length = int(params.get("rsi_length", 14))
    roc_length = int(params.get("roc_length", 5))
    stoch_length = int(params.get("stoch_length", 14))

    # Calculate RSI
    rsi = ta.rsi(closes, length=rsi_length)

    # Calculate Rate of Change
    roc = ((closes - closes.shift(roc_length)) / closes.shift(roc_length)) * 100

    # Calculate Stochastics
    stoch_data = ta.stoch(highs, lows, closes, k=stoch_length, d=3)
    stoch_k = stoch_data["STOCHk_14_3_3"]

    return {
        "rsi": rsi.iloc[-1],
        "roc_current": roc.iloc[-1],
        "roc_3ago": roc.iloc[-4],  # 3 bars ago
        "stoch_current": stoch_k.iloc[-1],
        "stoch_prev": stoch_k.iloc[-2],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - RSI(14) >= 70 (overbought)
    - ROC(5) current < ROC(5) 3 bars ago (momentum declining)
    - Stochastics(14) current > previous (rising)
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["rsi"] >= 70
        and indicators["roc_current"] < indicators["roc_3ago"]
        and indicators["stoch_current"] > indicators["stoch_prev"]
    )

    return condition


def go_short(state, df):
    """
    Return True when conditions favor going short.
    This strategy is long-only.
    """
    return False


def generate_signal(state, df):
    """
    Required entrypoint. Returns: "BUY", "SELL", or "HOLD".
    """
    if go_long(state, df):
        return "BUY"
    if go_short(state, df):
        return "SELL"
    return "HOLD"


def get_signal_visibility(state, df):
    """Return list of (label, is_true) for live status display."""
    indicators = populate_indicators(df, state.params)
    return [
        ("RSI≥70", indicators["rsi"] >= 70),
        ("ROC↓", indicators["roc_current"] < indicators["roc_3ago"]),
        ("Stoch↑", indicators["stoch_current"] > indicators["stoch_prev"]),
    ]
