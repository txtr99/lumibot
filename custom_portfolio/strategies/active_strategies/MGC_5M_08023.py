"""
MGC Strategy 0.8023 - RSI Rising with EMA Filter
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi_period": 70,
        "ema_period": 150,
        "rising_count": 15,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 13.8,
        "sl_mult": 1.8,
    },
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for RSI rising strategy.
    """
    import pandas_ta as ta

    indicators = {}

    rsi_period = params.get("rsi_period", 14)
    ema_period = params.get("ema_period", 30)

    # RSI
    indicators["rsi"] = ta.rsi(df["close"], length=rsi_period)

    # EMA
    indicators["ema"] = ta.ema(df["close"], length=ema_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI is rising (making higher values) for N consecutive bars
    - Close above EMA
    """
    if len(df) < 6:
        return False

    indicators = populate_indicators(df, state.params)
    rsi = indicators.get("rsi")
    ema = indicators.get("ema")
    rising_count = state.params.get("rising_count", 3)

    if rsi is None or ema is None:
        return False
    if len(rsi) < rising_count + 2 or len(ema) < 3:
        return False

    # Check if RSI is rising for consecutive bars
    rsi_rising = True
    for i in range(rising_count):
        if rsi.iloc[-2 - i] <= rsi.iloc[-3 - i]:
            rsi_rising = False
            break

    # Close above EMA
    close_above_ema = df["close"].iloc[-2] > ema.iloc[-2]

    return rsi_rising and close_above_ema


def go_short(state, df) -> bool:
    """Long-only strategy - no short entries."""
    return False


def generate_signal(state, df) -> str:
    """Generate trading signal."""
    if go_long(state, df):
        return "BUY"
    return "HOLD"
