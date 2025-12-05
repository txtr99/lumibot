"""
MGC Strategy 0.84756 - RSI Threshold Cross
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI(Open) crosses above threshold (58.8)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi_period": 200,
        "rsi_threshold": 58.8,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 17.6,
        "sl_mult": 2.0,
    },
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for RSI threshold cross.
    """
    import pandas_ta as ta

    indicators = {}

    rsi_period = params.get("rsi_period", 40)

    # RSI on Open
    indicators["rsi_open"] = ta.rsi(df["open"], length=rsi_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI(Open) crosses above threshold (from below to above)
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    rsi_open = indicators.get("rsi_open")
    rsi_threshold = state.params.get("rsi_threshold", 58.8)

    if rsi_open is None:
        return False
    if len(rsi_open) < 3:
        return False

    # RSI crosses above threshold
    rsi_prev2 = rsi_open.iloc[-3]
    rsi_prev1 = rsi_open.iloc[-2]

    rsi_cross_threshold = (rsi_prev2 < rsi_threshold) and (rsi_prev1 > rsi_threshold)

    return rsi_cross_threshold


def go_short(state, df) -> bool:
    """Long-only strategy - no short entries."""
    return False


def generate_signal(state, df) -> str:
    """Generate trading signal."""
    if go_long(state, df):
        return "BUY"
    return "HOLD"
