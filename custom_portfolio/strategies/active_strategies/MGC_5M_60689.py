"""
MGC Strategy 0.60689 - MACD Signal Crosses Below MA with EMA Filter
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- MACD signal crosses below its MA
- Low < EMA(Open)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "cross_ma_period": 195,
        "macd_fast": 120,
        "macd_slow": 260,
        "macd_smooth": 45,
        "ema_period": 95,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 9.6,
        "sl_mult": 1.9,
    },
    "time_exit": {"max_bars": 245},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for MACD signal cross below MA with EMA filter.
    """
    import pandas_ta as ta

    indicators = {}

    cross_ma_period = params.get("cross_ma_period", 39)
    macd_fast = params.get("macd_fast", 24)
    macd_slow = params.get("macd_slow", 52)
    macd_smooth = params.get("macd_smooth", 9)
    ema_period = params.get("ema_period", 19)

    # MACD signal
    macd_result = ta.macd(df["close"], fast=macd_fast, slow=macd_slow, signal=macd_smooth)
    if macd_result is not None:
        indicators["macd_signal"] = macd_result.iloc[:, 2]
        # MA of MACD signal
        indicators["macd_signal_ma"] = ta.sma(macd_result.iloc[:, 2], length=cross_ma_period)

    # EMA of Open
    indicators["ema_open"] = ta.ema(df["open"], length=ema_period)

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - MACD signal crosses below its MA (oversold indication)
    - Low < EMA(Open) (price pullback)
    """
    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params)
    macd_signal = indicators.get("macd_signal")
    macd_signal_ma = indicators.get("macd_signal_ma")
    ema_open = indicators.get("ema_open")

    if any(x is None for x in [macd_signal, macd_signal_ma, ema_open]):
        return False
    if any(len(x) < 3 for x in [macd_signal, macd_signal_ma, ema_open]):
        return False

    # MACD signal crosses below its MA
    signal_prev2 = macd_signal.iloc[-3]
    ma_prev2 = macd_signal_ma.iloc[-3]
    signal_prev1 = macd_signal.iloc[-2]
    ma_prev1 = macd_signal_ma.iloc[-2]

    macd_cross_below = (signal_prev2 > ma_prev2) and (signal_prev1 < ma_prev1)

    # Low < EMA(Open)
    low_below_ema = df["low"].iloc[-2] < ema_open.iloc[-2]

    return macd_cross_below and low_below_ema


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
        return [("Sig<MA", False), ("SxMA", False), ("L<EMA", False)]

    indicators = populate_indicators(df, state.params)
    macd_signal = indicators.get("macd_signal")
    macd_signal_ma = indicators.get("macd_signal_ma")
    ema_open = indicators.get("ema_open")

    if any(x is None for x in [macd_signal, macd_signal_ma, ema_open]):
        return [("Sig<MA", False), ("SxMA", False), ("L<EMA", False)]

    signal_below_ma = macd_signal.iloc[-2] < macd_signal_ma.iloc[-2] if len(macd_signal) > 1 else False
    low_below_ema = df["low"].iloc[-2] < ema_open.iloc[-2] if len(ema_open) > 1 else False

    crossed = False
    if len(macd_signal) >= 3 and len(macd_signal_ma) >= 3:
        crossed = (macd_signal.iloc[-3] > macd_signal_ma.iloc[-3]) and (macd_signal.iloc[-2] < macd_signal_ma.iloc[-2])

    return [
        ("Sig<MA", signal_below_ma),
        ("SxMA", crossed),
        ("L<EMA", low_below_ema),
    ]
