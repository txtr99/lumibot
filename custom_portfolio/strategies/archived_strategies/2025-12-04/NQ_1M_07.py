"""
NQ_1M_07 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Close <= Lowest(Close, 5) (at or below 5-bar low)
- RSI(2) current > RSI(2) 3 bars ago (short-term RSI rising)
- RSI(14) > 30 and RSI(14) <= 40 (medium-term RSI in range)

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
        "rsi_fast": 2,
        "rsi_slow": 14,
        "lookback_period": 5,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 5.0,
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
    """Compute and return a dict of indicators."""
    closes = df["close"]

    rsi_fast = int(params.get("rsi_fast", 2))
    rsi_slow = int(params.get("rsi_slow", 14))
    lookback_period = int(params.get("lookback_period", 5))

    # Calculate indicators
    lowest_close = closes.rolling(lookback_period).min()
    rsi_2 = ta.rsi(closes, length=rsi_fast)
    rsi_14 = ta.rsi(closes, length=rsi_slow)

    return {
        "close": closes.iloc[-1],
        "lowest_close": lowest_close.iloc[-1],
        "rsi_2_current": rsi_2.iloc[-1],
        "rsi_2_3ago": rsi_2.iloc[-4],
        "rsi_14": rsi_14.iloc[-1],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Close <= Lowest(Close, 5)
    - RSI(2) current > RSI(2) 3 bars ago
    - RSI(14) > 30 and <= 40
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["close"] <= indicators["lowest_close"]
        and indicators["rsi_2_current"] > indicators["rsi_2_3ago"]
        and indicators["rsi_14"] > 30
        and indicators["rsi_14"] <= 40
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
        ("C≤Lo5", indicators["close"] <= indicators["lowest_close"]),
        ("RSI2↑", indicators["rsi_2_current"] > indicators["rsi_2_3ago"]),
        ("RSI30-40", indicators["rsi_14"] > 30 and indicators["rsi_14"] <= 40),
    ]
