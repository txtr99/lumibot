"""
MGC Strategy 0.10618 - Dual RSI Momentum
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi1_period": 35,
        "rsi2_period": 70,
        "rsi_oversold": 30,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 11.2,
        "sl_mult": 1.7,
    },
    "time_exit": {"max_bars": 120},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for dual RSI strategy.
    """
    import pandas_ta as ta

    indicators = {}

    rsi1_period = params.get("rsi1_period", 7)
    rsi2_period = params.get("rsi2_period", 14)

    # Fast RSI
    indicators["rsi1"] = ta.rsi(df["close"], length=rsi1_period)

    # Slow RSI
    indicators["rsi2"] = ta.rsi(df["close"], length=rsi2_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - Fast RSI crosses above slow RSI (momentum shift)
    - Both RSIs recovering from oversold area
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    rsi1 = indicators.get("rsi1")
    rsi2 = indicators.get("rsi2")
    rsi_oversold = state.params.get("rsi_oversold", 30)

    if rsi1 is None or rsi2 is None:
        return False
    if len(rsi1) < 3 or len(rsi2) < 3:
        return False

    # RSI crossover
    rsi1_prev2 = rsi1.iloc[-3]
    rsi2_prev2 = rsi2.iloc[-3]
    rsi1_prev1 = rsi1.iloc[-2]
    rsi2_prev1 = rsi2.iloc[-2]

    rsi_crossover = (rsi1_prev2 < rsi2_prev2) and (rsi1_prev1 > rsi2_prev1)

    # Recovery from oversold
    was_oversold = rsi2_prev2 < rsi_oversold + 10  # Near oversold area

    return rsi_crossover and was_oversold


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
        return [("RSI1>2", False), ("Cross", False), ("<OS+10", False)]

    indicators = populate_indicators(df, state.params)
    rsi1 = indicators.get("rsi1")
    rsi2 = indicators.get("rsi2")
    rsi_oversold = state.params.get("rsi_oversold", 30)

    if rsi1 is None or rsi2 is None:
        return [("RSI1>2", False), ("Cross", False), ("<OS+10", False)]

    rsi1_above = rsi1.iloc[-2] > rsi2.iloc[-2] if len(rsi1) > 1 else False
    near_oversold = rsi2.iloc[-3] < rsi_oversold + 10 if len(rsi2) > 2 else False

    crossed = False
    if len(rsi1) >= 3 and len(rsi2) >= 3:
        crossed = (rsi1.iloc[-3] < rsi2.iloc[-3]) and (rsi1.iloc[-2] > rsi2.iloc[-2])

    return [
        ("RSI1>2", rsi1_above),
        ("Cross", crossed),
        ("<OS+10", near_oversold),
    ]
