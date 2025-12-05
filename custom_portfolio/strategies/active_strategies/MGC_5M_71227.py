"""
MGC Strategy 0.71227 - Dual RSI Crossover with Dual EMA Crossover
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI(High) crosses above RSI(Close)
- EMA(Open) crosses above Close
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi1_period": 100,
        "rsi2_period": 200,
        "ema_period": 150,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 16.0,
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
    Calculate indicators for dual RSI crossover with EMA crossover.
    """
    import pandas_ta as ta

    indicators = {}

    rsi1_period = params.get("rsi1_period", 20)
    rsi2_period = params.get("rsi2_period", 40)
    ema_period = params.get("ema_period", 30)

    # RSI on High
    indicators["rsi_high"] = ta.rsi(df["high"], length=rsi1_period)

    # RSI on Close
    indicators["rsi_close"] = ta.rsi(df["close"], length=rsi2_period)

    # EMA of Open
    indicators["ema_open"] = ta.ema(df["open"], length=ema_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI(High) crosses above RSI(Close)
    - EMA(Open) crosses above Close (was above, now below - price rallying through EMA)
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    rsi_high = indicators.get("rsi_high")
    rsi_close = indicators.get("rsi_close")
    ema_open = indicators.get("ema_open")

    if any(x is None for x in [rsi_high, rsi_close, ema_open]):
        return False
    if any(len(x) < 3 for x in [rsi_high, rsi_close, ema_open]):
        return False

    # RSI crossover
    rsi_high_prev2 = rsi_high.iloc[-3]
    rsi_close_prev2 = rsi_close.iloc[-3]
    rsi_high_prev1 = rsi_high.iloc[-2]
    rsi_close_prev1 = rsi_close.iloc[-2]

    rsi_crossover = (rsi_high_prev2 < rsi_close_prev2) and (rsi_high_prev1 > rsi_close_prev1)

    # EMA(Open) crosses above Close (was above close, now below = price broke above)
    ema_prev2 = ema_open.iloc[-3]
    close_prev2 = df["close"].iloc[-3]
    ema_prev1 = ema_open.iloc[-2]
    close_prev1 = df["close"].iloc[-2]

    ema_cross = (ema_prev2 < close_prev2) and (ema_prev1 > close_prev1)

    return rsi_crossover and ema_cross


def go_short(state, df) -> bool:
    """Long-only strategy - no short entries."""
    return False


def generate_signal(state, df) -> str:
    """Generate trading signal."""
    if go_long(state, df):
        return "BUY"
    return "HOLD"
