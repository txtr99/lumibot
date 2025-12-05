"""
MGC Strategy 0.48092 - EMA Falling with MACD Cross Threshold
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- EMA is falling for N bars
- MACD crosses above threshold (mean reversion)
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "ema_period": 100,
        "macd_fast": 40,
        "macd_slow": 85,
        "macd_smooth": 45,
        "macd_threshold_low": -0.8,
        "macd_threshold_high": 0.5,
        "falling_bars": 15,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 15.7,
        "sl_mult": 1.7,
    },
    "time_exit": {"max_bars": 150},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate indicators for EMA falling with MACD threshold cross.
    """
    import pandas_ta as ta

    indicators = {}

    ema_period = params.get("ema_period", 20)
    macd_fast = params.get("macd_fast", 8)
    macd_slow = params.get("macd_slow", 17)
    macd_smooth = params.get("macd_smooth", 9)

    # EMA
    indicators["ema"] = ta.ema(df["close"], length=ema_period)

    # MACD
    macd_result = ta.macd(df["close"], fast=macd_fast, slow=macd_slow, signal=macd_smooth)
    if macd_result is not None:
        indicators["macd_line"] = macd_result.iloc[:, 0]

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - EMA is falling (making lower values for N bars)
    - MACD crosses above threshold (from below -0.8 to above -0.8, still below 0.5)
    """
    if len(df) < 6:
        return False

    indicators = populate_indicators(df, state.params)
    ema = indicators.get("ema")
    macd_line = indicators.get("macd_line")
    falling_bars = state.params.get("falling_bars", 3)
    macd_threshold_low = state.params.get("macd_threshold_low", -0.8)
    macd_threshold_high = state.params.get("macd_threshold_high", 0.5)

    if ema is None or macd_line is None:
        return False
    if len(ema) < falling_bars + 2 or len(macd_line) < 3:
        return False

    # EMA is falling
    ema_falling = True
    for i in range(falling_bars):
        if ema.iloc[-2 - i] >= ema.iloc[-3 - i]:
            ema_falling = False
            break

    # MACD crosses above threshold
    macd_prev2 = macd_line.iloc[-3]
    macd_prev1 = macd_line.iloc[-2]
    macd_cross_threshold = (macd_prev2 < macd_threshold_low) and (macd_prev1 > macd_threshold_low)

    # MACD still below upper threshold
    macd_below_high = macd_prev1 < macd_threshold_high

    return ema_falling and macd_cross_threshold and macd_below_high


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
    if len(df) < 6:
        return [("EMA↓", False), ("MxTh", False), ("M<hi", False)]

    indicators = populate_indicators(df, state.params)
    ema = indicators.get("ema")
    macd_line = indicators.get("macd_line")
    macd_threshold_low = state.params.get("macd_threshold_low", -0.8)
    macd_threshold_high = state.params.get("macd_threshold_high", 0.5)

    if ema is None or macd_line is None:
        return [("EMA↓", False), ("MxTh", False), ("M<hi", False)]

    ema_falling = ema.iloc[-2] < ema.iloc[-3] if len(ema) > 2 else False
    macd_below_high = macd_line.iloc[-2] < macd_threshold_high if len(macd_line) > 1 else False

    macd_crossed = False
    if len(macd_line) >= 3:
        macd_crossed = (macd_line.iloc[-3] < macd_threshold_low) and (macd_line.iloc[-2] > macd_threshold_low)

    return [
        ("EMA↓", ema_falling),
        ("MxTh", macd_crossed),
        ("M<hi", macd_below_high),
    ]
