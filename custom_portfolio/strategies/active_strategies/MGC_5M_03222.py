"""
MGC Strategy 0.3222 - RSI Crossover with EMA Falling Filter
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi1_period": 100,
        "rsi2_period": 250,
        "indicator_cross_ma_period": 45,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 15.1,
        "sl_mult": 1.9,
    },
    "time_exit": {"max_bars": 300},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for RSI crossover strategy.
    Entry: RSI crossover (RSI1 crosses above RSI2) AND Low < Close AND Close crosses above SMA
    """
    import pandas_ta as ta

    indicators = {}

    rsi1_period = params.get("rsi1_period", 20)
    rsi2_period = params.get("rsi2_period", 50)
    cross_ma_period = params.get("indicator_cross_ma_period", 9)

    # RSI on MedianPrice
    median_price = (df["high"] + df["low"]) / 2
    indicators["rsi1"] = ta.rsi(median_price, length=rsi1_period)
    indicators["rsi2"] = ta.rsi(median_price, length=rsi2_period)

    # SMA of close for crossover detection
    indicators["sma_close"] = ta.sma(df["close"], length=cross_ma_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI1 crosses above RSI2 (RSI1[2] < RSI2[2] AND RSI1[1] > RSI2[1])
    - Low < Close (on previous bar)
    - Close crosses above SMA
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    rsi1 = indicators.get("rsi1")
    rsi2 = indicators.get("rsi2")
    sma_close = indicators.get("sma_close")

    if rsi1 is None or rsi2 is None or sma_close is None:
        return False
    if len(rsi1) < 3 or len(rsi2) < 3 or len(sma_close) < 3:
        return False

    # RSI crossover condition: RSI1 was below RSI2 two bars ago, now above
    rsi1_prev2 = rsi1.iloc[-3]
    rsi2_prev2 = rsi2.iloc[-3]
    rsi1_prev1 = rsi1.iloc[-2]
    rsi2_prev1 = rsi2.iloc[-2]

    rsi_crossover = (rsi1_prev2 < rsi2_prev2) and (rsi1_prev1 > rsi2_prev1)

    # Low < Close on previous bar
    low_prev = df["low"].iloc[-2]
    close_prev = df["close"].iloc[-2]
    low_below_close = low_prev < close_prev

    # Close crosses above SMA
    close_prev2 = df["close"].iloc[-3]
    sma_prev2 = sma_close.iloc[-3]
    sma_prev1 = sma_close.iloc[-2]
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


def get_signal_visibility(state, df):
    """Return list of (label, is_true) tuples for live status display."""
    if len(df) < 4:
        return [("RSI1>2", False), ("L<C", False), ("CxSMA", False)]

    indicators = populate_indicators(df, state.params)
    rsi1 = indicators.get("rsi1")
    rsi2 = indicators.get("rsi2")
    sma_close = indicators.get("sma_close")

    if rsi1 is None or rsi2 is None or sma_close is None:
        return [("RSI1>2", False), ("L<C", False), ("CxSMA", False)]

    # Current state indicators
    rsi1_above_rsi2 = rsi1.iloc[-2] > rsi2.iloc[-2] if len(rsi1) > 1 else False
    low_below_close = df["low"].iloc[-2] < df["close"].iloc[-2]

    # Cross detection
    crossed = False
    if len(rsi1) >= 3 and len(rsi2) >= 3:
        crossed = (rsi1.iloc[-3] < rsi2.iloc[-3]) and (rsi1.iloc[-2] > rsi2.iloc[-2])

    return [
        ("RSI1>2", rsi1_above_rsi2),
        ("L<C", low_below_close),
        ("Cross", crossed),
    ]
