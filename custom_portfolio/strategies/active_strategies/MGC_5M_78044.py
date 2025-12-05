"""
MGC Strategy 0.78044 - RSI Crosses Above MA with MACD Crossover
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI crosses above its MA
- MACD signal crosses above MACD line
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "cross_ma_period": 190,
        "rsi_period": 150,
        "macd_fast": 60,
        "macd_slow": 130,
        "macd_smooth": 45,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 7.5,
        "sl_mult": 1.5,
    },
    "time_exit": {"max_bars": 240},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for RSI cross above MA with MACD crossover.
    """
    import pandas_ta as ta

    indicators = {}

    cross_ma_period = params.get("cross_ma_period", 38)
    rsi_period = params.get("rsi_period", 30)
    macd_fast = params.get("macd_fast", 12)
    macd_slow = params.get("macd_slow", 26)
    macd_smooth = params.get("macd_smooth", 9)

    # RSI
    indicators["rsi"] = ta.rsi(df["close"], length=rsi_period)

    # MA of RSI
    rsi = ta.rsi(df["close"], length=rsi_period)
    if rsi is not None:
        indicators["rsi_ma"] = ta.sma(rsi, length=cross_ma_period)

    # MACD
    macd_result = ta.macd(df["close"], fast=macd_fast, slow=macd_slow, signal=macd_smooth)
    if macd_result is not None:
        indicators["macd_line"] = macd_result.iloc[:, 0]
        indicators["macd_signal"] = macd_result.iloc[:, 2]

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI crosses above its MA
    - MACD signal crosses above MACD line
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    rsi = indicators.get("rsi")
    rsi_ma = indicators.get("rsi_ma")
    macd_line = indicators.get("macd_line")
    macd_signal = indicators.get("macd_signal")

    if any(x is None for x in [rsi, rsi_ma, macd_line, macd_signal]):
        return False
    if any(len(x) < 3 for x in [rsi, rsi_ma, macd_line, macd_signal]):
        return False

    # RSI crosses above MA
    rsi_prev2 = rsi.iloc[-3]
    rsi_ma_prev2 = rsi_ma.iloc[-3]
    rsi_prev1 = rsi.iloc[-2]
    rsi_ma_prev1 = rsi_ma.iloc[-2]

    rsi_cross_ma = (rsi_prev2 < rsi_ma_prev2) and (rsi_prev1 > rsi_ma_prev1)

    # MACD signal crosses above MACD line
    signal_prev2 = macd_signal.iloc[-3]
    line_prev2 = macd_line.iloc[-3]
    signal_prev1 = macd_signal.iloc[-2]
    line_prev1 = macd_line.iloc[-2]

    macd_crossover = (signal_prev2 > line_prev2) and (signal_prev1 < line_prev1)

    return rsi_cross_ma and macd_crossover


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
        return [("RSI>MA", False), ("RSIx", False), ("MACDx", False)]

    indicators = populate_indicators(df, state.params)
    rsi = indicators.get("rsi")
    rsi_ma = indicators.get("rsi_ma")
    macd_line = indicators.get("macd_line")
    macd_signal = indicators.get("macd_signal")

    if any(x is None for x in [rsi, rsi_ma, macd_line, macd_signal]):
        return [("RSI>MA", False), ("RSIx", False), ("MACDx", False)]

    rsi_above_ma = rsi.iloc[-2] > rsi_ma.iloc[-2] if len(rsi) > 1 else False

    rsi_crossed = False
    if len(rsi) >= 3 and len(rsi_ma) >= 3:
        rsi_crossed = (rsi.iloc[-3] < rsi_ma.iloc[-3]) and (rsi.iloc[-2] > rsi_ma.iloc[-2])

    macd_crossed = False
    if len(macd_signal) >= 3 and len(macd_line) >= 3:
        macd_crossed = (macd_signal.iloc[-3] > macd_line.iloc[-3]) and (macd_signal.iloc[-2] < macd_line.iloc[-2])

    return [
        ("RSI>MA", rsi_above_ma),
        ("RSIx", rsi_crossed),
        ("MACDx", macd_crossed),
    ]
