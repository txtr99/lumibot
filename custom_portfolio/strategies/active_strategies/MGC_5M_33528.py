"""
MGC Strategy 0.33528 - RSI Crossover with Price Cross MA
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI(MedianPrice, 20) crosses above RSI(MedianPrice, 50)
- Low < Close
- Close crosses above its SMA
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi1_period": 100,
        "rsi2_period": 250,
        "cross_ma_period": 45,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 15.1,
        "sl_mult": 1.9,
    },
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for RSI crossover with price cross MA.
    """
    import pandas_ta as ta

    indicators = {}

    rsi1_period = params.get("rsi1_period", 20)
    rsi2_period = params.get("rsi2_period", 50)
    cross_ma_period = params.get("cross_ma_period", 9)

    # Median price
    median_price = (df["high"] + df["low"]) / 2

    # RSI on median price
    indicators["rsi1"] = ta.rsi(median_price, length=rsi1_period)
    indicators["rsi2"] = ta.rsi(median_price, length=rsi2_period)

    # SMA for crossover
    indicators["sma"] = ta.sma(df["close"], length=cross_ma_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI1 crosses above RSI2
    - Low < Close
    - Close crosses above SMA
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    rsi1 = indicators.get("rsi1")
    rsi2 = indicators.get("rsi2")
    sma = indicators.get("sma")

    if any(x is None for x in [rsi1, rsi2, sma]):
        return False
    if any(len(x) < 3 for x in [rsi1, rsi2, sma]):
        return False

    # RSI crossover
    rsi1_prev2 = rsi1.iloc[-3]
    rsi2_prev2 = rsi2.iloc[-3]
    rsi1_prev1 = rsi1.iloc[-2]
    rsi2_prev1 = rsi2.iloc[-2]

    rsi_crossover = (rsi1_prev2 < rsi2_prev2) and (rsi1_prev1 > rsi2_prev1)

    # Low < Close
    low_prev = df["low"].iloc[-2]
    close_prev = df["close"].iloc[-2]
    low_below_close = low_prev < close_prev

    # Close crosses above SMA
    close_prev2 = df["close"].iloc[-3]
    sma_prev2 = sma.iloc[-3]
    sma_prev1 = sma.iloc[-2]
    close_cross_sma = (close_prev2 < sma_prev2) and (close_prev > sma_prev1)

    return rsi_crossover and low_below_close and close_cross_sma


def go_short(state, df) -> bool:
    """Long-only strategy - no short entries."""
    return False


def generate_signal(state, df) -> str:
    """Generate trading signal."""
    if go_long(state, df):
        return "BUY"
    return "HOLD"
