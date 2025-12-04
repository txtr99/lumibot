"""
ES_1M_05 - Mean Reversion Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- RSI(14) declining (current <= 4 bars ago)
- RSI(14) in range 70-80 (overbought)
- SMA(8) rising (current > previous)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "rsi_length": 14,
        "sma_length": 8,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,
        "sl_mult": 8.0,
    },
    "time_exit": {
        "max_bars": 180,
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

    rsi_length = int(params.get("rsi_length", 14))
    sma_length = int(params.get("sma_length", 8))

    # Calculate RSI(14)
    rsi_14 = ta.rsi(closes, length=rsi_length)

    # Calculate SMA(8)
    sma_8 = closes.rolling(sma_length).mean()

    return {
        "rsi_14_current": rsi_14.iloc[-1],
        "rsi_14_4ago": rsi_14.iloc[-5],
        "sma_8_current": sma_8.iloc[-1],
        "sma_8_prev": sma_8.iloc[-2],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - RSI(14) current <= RSI(14) 4 bars ago
    - RSI(14) > 70 and <= 80
    - SMA(8) rising
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["rsi_14_current"] <= indicators["rsi_14_4ago"]
        and indicators["rsi_14_current"] > 70
        and indicators["rsi_14_current"] <= 80
        and indicators["sma_8_current"] > indicators["sma_8_prev"]
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
        ("RSI↓", indicators["rsi_14_current"] <= indicators["rsi_14_4ago"]),
        ("RSI70-80", indicators["rsi_14_current"] > 70 and indicators["rsi_14_current"] <= 80),
        ("SMA8↑", indicators["sma_8_current"] > indicators["sma_8_prev"]),
    ]
