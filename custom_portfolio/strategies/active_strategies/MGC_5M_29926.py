"""
MGC Strategy 0.29926 - RSI Above MA with EMA Lower Count Filter
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI above its MA
- EMA of TypicalPrice is lower than EMA of Low for 2 bars
- RSI crossover (RSI on Low crosses above RSI on MedianPrice)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "indicator_ma_period": 100,
        "rsi1_period": 70,
        "ema1_period": 150,
        "ema2_period": 250,
        "rsi2_period": 150,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 10.8,
        "sl_mult": 1.9,
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
    Calculate indicators for complex RSI/EMA strategy.
    """
    import pandas_ta as ta

    indicators = {}

    indicator_ma_period = params.get("indicator_ma_period", 20)
    rsi1_period = params.get("rsi1_period", 14)
    ema1_period = params.get("ema1_period", 30)
    ema2_period = params.get("ema2_period", 50)
    rsi2_period = params.get("rsi2_period", 30)

    # Typical price for RSI
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    median_price = (df["high"] + df["low"]) / 2

    # RSI on typical price
    indicators["rsi_typical"] = ta.rsi(typical_price, length=rsi1_period)

    # MA of RSI for "indicator above MA" condition
    rsi_typical = ta.rsi(typical_price, length=rsi1_period)
    if rsi_typical is not None:
        indicators["rsi_ma"] = ta.sma(rsi_typical, length=indicator_ma_period)

    # EMA on typical price and low
    indicators["ema_typical"] = ta.ema(typical_price, length=ema1_period)
    indicators["ema_low"] = ta.ema(df["low"], length=ema2_period)

    # RSI on Low and MedianPrice for crossover
    indicators["rsi_low"] = ta.rsi(df["low"], length=rsi1_period)
    indicators["rsi_median"] = ta.rsi(median_price, length=rsi2_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI above its moving average
    - EMA of TypicalPrice lower than EMA of Low (for 2 bars)
    - RSI(Low) crosses above RSI(MedianPrice)
    """
    if len(df) < 5:
        return False

    indicators = populate_indicators(df, state.params)
    rsi_typical = indicators.get("rsi_typical")
    rsi_ma = indicators.get("rsi_ma")
    ema_typical = indicators.get("ema_typical")
    ema_low = indicators.get("ema_low")
    rsi_low = indicators.get("rsi_low")
    rsi_median = indicators.get("rsi_median")

    if any(x is None for x in [rsi_typical, rsi_ma, ema_typical, ema_low, rsi_low, rsi_median]):
        return False
    if any(len(x) < 4 for x in [rsi_typical, rsi_ma, ema_typical, ema_low, rsi_low, rsi_median]):
        return False

    # RSI above MA
    rsi_above_ma = rsi_typical.iloc[-2] > rsi_ma.iloc[-2]

    # EMA typical < EMA low for at least 1 bar (SQ_IsLowerCount with count=2)
    ema_lower = ema_typical.iloc[-2] < ema_low.iloc[-2]

    # RSI crossover: RSI(Low) crosses above RSI(MedianPrice)
    rsi_low_prev2 = rsi_low.iloc[-3]
    rsi_median_prev2 = rsi_median.iloc[-3]
    rsi_low_prev1 = rsi_low.iloc[-2]
    rsi_median_prev1 = rsi_median.iloc[-2]

    rsi_crossover = (rsi_low_prev2 < rsi_median_prev2) and (rsi_low_prev1 > rsi_median_prev1)

    return rsi_above_ma and ema_lower and rsi_crossover


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
    if len(df) < 5:
        return [("RSI>MA", False), ("EMA<L", False), ("RSIx", False)]

    indicators = populate_indicators(df, state.params)
    rsi_typical = indicators.get("rsi_typical")
    rsi_ma = indicators.get("rsi_ma")
    ema_typical = indicators.get("ema_typical")
    ema_low = indicators.get("ema_low")
    rsi_low = indicators.get("rsi_low")
    rsi_median = indicators.get("rsi_median")

    if any(x is None for x in [rsi_typical, rsi_ma, ema_typical, ema_low, rsi_low, rsi_median]):
        return [("RSI>MA", False), ("EMA<L", False), ("RSIx", False)]

    rsi_above_ma = rsi_typical.iloc[-2] > rsi_ma.iloc[-2] if len(rsi_typical) > 1 else False
    ema_lower = ema_typical.iloc[-2] < ema_low.iloc[-2] if len(ema_typical) > 1 else False

    crossed = False
    if len(rsi_low) >= 3 and len(rsi_median) >= 3:
        crossed = (rsi_low.iloc[-3] < rsi_median.iloc[-3]) and (rsi_low.iloc[-2] > rsi_median.iloc[-2])

    return [
        ("RSI>MA", rsi_above_ma),
        ("EMA<L", ema_lower),
        ("RSIx", crossed),
    ]
