"""
NQ_1M_04 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Close > EMA(20) (above exponential average)
- High <= Lowest(High, 10) (at or below 10-bar low of highs)
- RSI(14) current > RSI(14) previous (RSI rising)

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
        "ema_length": 20,
        "rsi_length": 14,
        "lookback_period": 10,
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
    """Compute and return a dict of indicators."""
    closes = df["close"]
    highs = df["high"]

    ema_length = int(params.get("ema_length", 20))
    rsi_length = int(params.get("rsi_length", 14))
    lookback_period = int(params.get("lookback_period", 10))

    # Calculate indicators
    ema_20 = closes.ewm(span=ema_length).mean()
    lowest_high = highs.rolling(lookback_period).min()
    rsi = ta.rsi(closes, length=rsi_length)

    return {
        "close": closes.iloc[-1],
        "high": highs.iloc[-1],
        "ema_20": ema_20.iloc[-1],
        "lowest_high": lowest_high.iloc[-1],
        "rsi_current": rsi.iloc[-1],
        "rsi_prev": rsi.iloc[-2],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Close > EMA(20)
    - High <= Lowest(High, 10)
    - RSI(14) current > RSI(14) previous
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["close"] > indicators["ema_20"]
        and indicators["high"] <= indicators["lowest_high"]
        and indicators["rsi_current"] > indicators["rsi_prev"]
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
        ("C>EMA20", indicators["close"] > indicators["ema_20"]),
        ("H≤LoH", indicators["high"] <= indicators["lowest_high"]),
        ("RSI↑", indicators["rsi_current"] > indicators["rsi_prev"]),
    ]
