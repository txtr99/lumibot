"""
MGC Strategy 0.55081 - Dual MACD Signal Crossover with EMA Filter
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- MACD1 signal crosses above MACD2 signal
- EMA(WeightedClose) < EMA(Low)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "macd1_fast": 40,
        "macd1_slow": 85,
        "macd_smooth": 45,
        "macd2_fast": 60,
        "macd2_slow": 130,
        "ema1_period": 70,
        "ema2_period": 250,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 16.0,
        "sl_mult": 1.9,
    },
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for dual MACD signal crossover with EMA filter.
    """
    import pandas_ta as ta

    indicators = {}

    macd1_fast = params.get("macd1_fast", 8)
    macd1_slow = params.get("macd1_slow", 17)
    macd_smooth = params.get("macd_smooth", 9)
    macd2_fast = params.get("macd2_fast", 12)
    macd2_slow = params.get("macd2_slow", 26)
    ema1_period = params.get("ema1_period", 14)
    ema2_period = params.get("ema2_period", 50)

    # MACD1 signal
    macd1_result = ta.macd(df["close"], fast=macd1_fast, slow=macd1_slow, signal=macd_smooth)
    if macd1_result is not None:
        indicators["macd1_signal"] = macd1_result.iloc[:, 2]

    # MACD2 signal
    macd2_result = ta.macd(df["close"], fast=macd2_fast, slow=macd2_slow, signal=macd_smooth)
    if macd2_result is not None:
        indicators["macd2_signal"] = macd2_result.iloc[:, 2]

    # Weighted close = (High + Low + Close*2) / 4
    weighted_close = (df["high"] + df["low"] + df["close"] * 2) / 4

    # EMA of weighted close
    indicators["ema_wc"] = ta.ema(weighted_close, length=ema1_period)

    # EMA of Low
    indicators["ema_low"] = ta.ema(df["low"], length=ema2_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - MACD1 signal crosses above MACD2 signal
    - EMA(WeightedClose) < EMA(Low) (oversold condition)
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    macd1_signal = indicators.get("macd1_signal")
    macd2_signal = indicators.get("macd2_signal")
    ema_wc = indicators.get("ema_wc")
    ema_low = indicators.get("ema_low")

    if any(x is None for x in [macd1_signal, macd2_signal, ema_wc, ema_low]):
        return False
    if any(len(x) < 3 for x in [macd1_signal, macd2_signal, ema_wc, ema_low]):
        return False

    # MACD signal crossover
    macd1_prev2 = macd1_signal.iloc[-3]
    macd2_prev2 = macd2_signal.iloc[-3]
    macd1_prev1 = macd1_signal.iloc[-2]
    macd2_prev1 = macd2_signal.iloc[-2]

    macd_crossover = (macd1_prev2 < macd2_prev2) and (macd1_prev1 > macd2_prev1)

    # EMA filter (oversold)
    ema_wc_below_low = ema_wc.iloc[-2] < ema_low.iloc[-2]

    return macd_crossover and ema_wc_below_low


def go_short(state, df) -> bool:
    """Long-only strategy - no short entries."""
    return False


def generate_signal(state, df) -> str:
    """Generate trading signal."""
    if go_long(state, df):
        return "BUY"
    return "HOLD"
