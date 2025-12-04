"""
GC_1M_01 - Mean Reversion Strategy for MGC (Micro Gold Futures)
Converted from EasyLanguage

Entry Logic:
- RSI(14) current <= RSI(14) 5 bars ago (RSI declining)
- RSI(2) >= 90 (extreme overbought)
- EMA(20) current > EMA(20) 3 bars ago (short-term EMA rising)
- EMA(50) current > EMA(50) 4 bars ago (medium-term EMA rising)

Symbol: MGC (Micro Gold Futures)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "rsi_long_length": 14,
        "rsi_short_length": 2,
        "ema_fast": 20,
        "ema_slow": 50,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 8.0,
    },
    "time_exit": {
        "max_bars": 180,
    },
    "allowed_sessions": ["24/7"],  # 24/7 market
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

    rsi_long_length = int(params.get("rsi_long_length", 14))
    rsi_short_length = int(params.get("rsi_short_length", 2))
    ema_fast = int(params.get("ema_fast", 20))
    ema_slow = int(params.get("ema_slow", 50))

    # Calculate RSI(14)
    rsi_14 = ta.rsi(closes, length=rsi_long_length)

    # Calculate RSI(2)
    rsi_2 = ta.rsi(closes, length=rsi_short_length)

    # Calculate EMAs
    ema_20 = closes.ewm(span=ema_fast).mean()
    ema_50 = closes.ewm(span=ema_slow).mean()

    return {
        "rsi_14_current": rsi_14.iloc[-1],
        "rsi_14_5ago": rsi_14.iloc[-6],  # 5 bars ago
        "rsi_2": rsi_2.iloc[-1],
        "ema_20_current": ema_20.iloc[-1],
        "ema_20_3ago": ema_20.iloc[-4],  # 3 bars ago
        "ema_50_current": ema_50.iloc[-1],
        "ema_50_4ago": ema_50.iloc[-5],  # 4 bars ago
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - RSI(14) current <= RSI(14) 5 bars ago
    - RSI(2) >= 90 (extreme overbought)
    - EMA(20) rising (current > 3 bars ago)
    - EMA(50) rising (current > 4 bars ago)
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["rsi_14_current"] <= indicators["rsi_14_5ago"]
        and indicators["rsi_2"] >= 90
        and indicators["ema_20_current"] > indicators["ema_20_3ago"]
        and indicators["ema_50_current"] > indicators["ema_50_4ago"]
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
    """
    Optional: Return list of (label, is_true) tuples for live status display.

    Each tuple is displayed as a colored column in the live status table:
    - Cyan = condition is True
    - Gray = condition is False
    """
    indicators = populate_indicators(df, state.params)

    return [
        ("RSI↓", indicators["rsi_14_current"] <= indicators["rsi_14_5ago"]),
        ("RSI2>90", indicators["rsi_2"] >= 90),
        ("EMA20↑", indicators["ema_20_current"] > indicators["ema_20_3ago"]),
        ("EMA50↑", indicators["ema_50_current"] > indicators["ema_50_4ago"]),
    ]
