"""
MGC Strategy 0.12389 - EMA Crossover with RSI Confirmation
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "ema1_period": 50,
        "ema2_period": 150,
        "rsi_period": 70,
        "rsi_threshold": 45,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 14.3,
        "sl_mult": 1.8,
    },
    "time_exit": {"max_bars": 200},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for EMA crossover with RSI confirmation.
    """
    import pandas_ta as ta

    indicators = {}

    ema1_period = params.get("ema1_period", 10)
    ema2_period = params.get("ema2_period", 30)
    rsi_period = params.get("rsi_period", 14)

    # EMAs
    indicators["ema1"] = ta.ema(df["close"], length=ema1_period)
    indicators["ema2"] = ta.ema(df["close"], length=ema2_period)

    # RSI
    indicators["rsi"] = ta.rsi(df["close"], length=rsi_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - Fast EMA crosses above slow EMA
    - RSI above threshold
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    ema1 = indicators.get("ema1")
    ema2 = indicators.get("ema2")
    rsi = indicators.get("rsi")
    rsi_threshold = state.params.get("rsi_threshold", 45)

    if any(x is None for x in [ema1, ema2, rsi]):
        return False
    if any(len(x) < 3 for x in [ema1, ema2, rsi]):
        return False

    # EMA crossover
    ema1_prev2 = ema1.iloc[-3]
    ema2_prev2 = ema2.iloc[-3]
    ema1_prev1 = ema1.iloc[-2]
    ema2_prev1 = ema2.iloc[-2]

    ema_crossover = (ema1_prev2 < ema2_prev2) and (ema1_prev1 > ema2_prev1)

    # RSI confirmation
    rsi_confirmed = rsi.iloc[-2] > rsi_threshold

    return ema_crossover and rsi_confirmed


def go_short(state, df) -> bool:
    """Long-only strategy - no short entries."""
    return False


def generate_signal(state, df) -> str:
    """Generate trading signal."""
    if go_long(state, df):
        return "BUY"
    return "HOLD"


def get_signal_visibility(state, df):
    """Return list of (label, is_true) tuples for live status display."""
    if len(df) < 4:
        return [("EMA1>2", False), ("Cross", False), ("RSI>th", False)]

    indicators = populate_indicators(df, state.params)
    ema1 = indicators.get("ema1")
    ema2 = indicators.get("ema2")
    rsi = indicators.get("rsi")
    rsi_threshold = state.params.get("rsi_threshold", 45)

    if any(x is None for x in [ema1, ema2, rsi]):
        return [("EMA1>2", False), ("Cross", False), ("RSI>th", False)]

    ema1_above = ema1.iloc[-2] > ema2.iloc[-2] if len(ema1) > 1 else False
    rsi_confirmed = rsi.iloc[-2] > rsi_threshold if len(rsi) > 1 else False

    crossed = False
    if len(ema1) >= 3 and len(ema2) >= 3:
        crossed = (ema1.iloc[-3] < ema2.iloc[-3]) and (ema1.iloc[-2] > ema2.iloc[-2])

    return [
        ("EMA1>2", ema1_above),
        ("Cross", crossed),
        ("RSI>th", rsi_confirmed),
    ]
