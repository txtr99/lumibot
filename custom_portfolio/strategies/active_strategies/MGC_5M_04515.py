"""
MGC Strategy 0.4515 - EMA Cross with MACD Filter
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "ema1_period": 100,
        "ema2_period": 250,
        "macd_fast": 60,
        "macd_slow": 130,
        "macd_smooth": 45,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 10.6,
        "sl_mult": 2.1,
    },
    "time_exit": {"max_bars": 300},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for EMA crossover with MACD filter.
    """
    import pandas_ta as ta

    indicators = {}

    ema1_period = params.get("ema1_period", 20)
    ema2_period = params.get("ema2_period", 50)
    macd_fast = params.get("macd_fast", 12)
    macd_slow = params.get("macd_slow", 26)
    macd_smooth = params.get("macd_smooth", 9)

    # EMA indicators
    indicators["ema1"] = ta.ema(df["close"], length=ema1_period)
    indicators["ema2"] = ta.ema(df["close"], length=ema2_period)

    # MACD
    macd_result = ta.macd(df["close"], fast=macd_fast, slow=macd_slow, signal=macd_smooth)
    if macd_result is not None:
        indicators["macd_line"] = macd_result.iloc[:, 0]  # MACD line
        indicators["macd_signal"] = macd_result.iloc[:, 2]  # Signal line

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - EMA1 crosses above EMA2
    - MACD line > Signal line (bullish confirmation)
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    ema1 = indicators.get("ema1")
    ema2 = indicators.get("ema2")
    macd_line = indicators.get("macd_line")
    macd_signal = indicators.get("macd_signal")

    if any(x is None for x in [ema1, ema2, macd_line, macd_signal]):
        return False
    if any(len(x) < 3 for x in [ema1, ema2, macd_line, macd_signal]):
        return False

    # EMA crossover: EMA1 was below EMA2, now above
    ema1_prev2 = ema1.iloc[-3]
    ema2_prev2 = ema2.iloc[-3]
    ema1_prev1 = ema1.iloc[-2]
    ema2_prev1 = ema2.iloc[-2]

    ema_crossover = (ema1_prev2 < ema2_prev2) and (ema1_prev1 > ema2_prev1)

    # MACD bullish
    macd_bullish = macd_line.iloc[-2] > macd_signal.iloc[-2]

    return ema_crossover and macd_bullish


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
        return [("EMA1>2", False), ("Cross", False), ("MACD+", False)]

    indicators = populate_indicators(df, state.params)
    ema1 = indicators.get("ema1")
    ema2 = indicators.get("ema2")
    macd_line = indicators.get("macd_line")
    macd_signal = indicators.get("macd_signal")

    if any(x is None for x in [ema1, ema2, macd_line, macd_signal]):
        return [("EMA1>2", False), ("Cross", False), ("MACD+", False)]

    ema1_above = ema1.iloc[-2] > ema2.iloc[-2] if len(ema1) > 1 else False
    macd_bullish = macd_line.iloc[-2] > macd_signal.iloc[-2] if len(macd_line) > 1 else False

    crossed = False
    if len(ema1) >= 3 and len(ema2) >= 3:
        crossed = (ema1.iloc[-3] < ema2.iloc[-3]) and (ema1.iloc[-2] > ema2.iloc[-2])

    return [
        ("EMA1>2", ema1_above),
        ("Cross", crossed),
        ("MACD+", macd_bullish),
    ]
