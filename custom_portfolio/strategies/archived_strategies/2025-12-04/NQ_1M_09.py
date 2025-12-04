"""
NQ_1M_09 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Consecutive(high, 5, 0) = 1 (5 consecutive down highs)
- RSI(14) current <= RSI(14) 5 bars ago (RSI declining)
- ADX(20) current <= ADX(20) 3 bars ago (trend weakening)

Symbol: MNQ (Micro E-mini Nasdaq-100)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MNQ",
    "contracts": 1,
    "params": {
        "rsi_length": 14,
        "adx_length": 20,
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


def consecutive(series, length, direction):
    """Count consecutive up/down bars. direction: 0=down, 1=up."""
    if direction == 1:
        for i in range(1, length):
            if series.iloc[-i] <= series.iloc[-i - 1]:
                return 0
        return 1
    else:
        for i in range(1, length):
            if series.iloc[-i] >= series.iloc[-i - 1]:
                return 0
        return 1


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    rsi_length = int(params.get("rsi_length", 14))
    adx_length = int(params.get("adx_length", 20))

    # Calculate indicators
    consec_down_high = consecutive(highs, 5, 0)
    rsi = ta.rsi(closes, length=rsi_length)
    adx = ta.adx(highs, lows, closes, length=adx_length)[f"ADX_{adx_length}"]

    return {
        "consecutive_down_high": consec_down_high,
        "rsi_current": rsi.iloc[-1],
        "rsi_5ago": rsi.iloc[-6],
        "adx_current": adx.iloc[-1],
        "adx_3ago": adx.iloc[-4],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - 5 consecutive down highs
    - RSI(14) current <= RSI(14) 5 bars ago
    - ADX(20) current <= ADX(20) 3 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["consecutive_down_high"] == 1
        and indicators["rsi_current"] <= indicators["rsi_5ago"]
        and indicators["adx_current"] <= indicators["adx_3ago"]
    )

    return condition


def go_short(state, df):
    """Return True when conditions favor going short. This strategy is long-only."""
    return False


def generate_signal(state, df):
    """Required entrypoint. Returns: "BUY", "SELL", or "HOLD"."""
    if go_long(state, df):
        return "BUY"
    if go_short(state, df):
        return "SELL"
    return "HOLD"


def get_signal_visibility(state, df):
    """Return list of (label, is_true) for live status display."""
    indicators = populate_indicators(df, state.params)
    return [
        ("5Hi↓", indicators["consecutive_down_high"] == 1),
        ("RSI↓", indicators["rsi_current"] <= indicators["rsi_5ago"]),
        ("ADX↓", indicators["adx_current"] <= indicators["adx_3ago"]),
    ]
