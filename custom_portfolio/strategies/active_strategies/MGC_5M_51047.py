"""
MGC Strategy 0.51047 - Dual MACD Line Crossover
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- MACD1(Low) line crosses above MACD2(Close) signal
"""

import logging

_logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "macd1_fast": 85,
        "macd1_slow": 150,
        "macd1_smooth": 230,
        "macd2_fast": 15,
        "macd2_slow": 50,
        "macd2_smooth": 80,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 16.1,
        "sl_mult": 2.0,
    },
    "time_exit": {"max_bars": 70},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for dual MACD crossover.
    """
    import pandas_ta as ta

    indicators = {}

    macd1_fast = params.get("macd1_fast", 17)
    macd1_slow = params.get("macd1_slow", 30)
    macd1_smooth = params.get("macd1_smooth", 46)
    macd2_fast = params.get("macd2_fast", 3)
    macd2_slow = params.get("macd2_slow", 10)
    macd2_smooth = params.get("macd2_smooth", 16)

    # MACD1 on Low
    macd1_result = ta.macd(df["low"], fast=macd1_fast, slow=macd1_slow, signal=macd1_smooth)
    if macd1_result is not None:
        indicators["macd1_line"] = macd1_result.iloc[:, 0]

    # MACD2 on Close
    macd2_result = ta.macd(df["close"], fast=macd2_fast, slow=macd2_slow, signal=macd2_smooth)
    if macd2_result is not None:
        indicators["macd2_signal"] = macd2_result.iloc[:, 2]

    if debug and indicators.get("macd1_line") is not None and len(indicators["macd1_line"]) >= 3:
        m1 = indicators["macd1_line"]
        m2 = indicators.get("macd2_signal")
        _logger.info(
            f"[INDICATOR] {strategy_id}: macd1[-2]={m1.iloc[-2]:.2f}, "
            f"macd2_sig[-2]={(m2.iloc[-2] if m2 is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - MACD1(Low) line crosses above MACD2(Close) signal
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    macd1_line = indicators.get("macd1_line")
    macd2_signal = indicators.get("macd2_signal")

    if macd1_line is None or macd2_signal is None:
        return False
    if len(macd1_line) < 3 or len(macd2_signal) < 3:
        return False

    # MACD1 crosses above MACD2 signal
    macd1_prev2 = macd1_line.iloc[-3]
    macd2_prev2 = macd2_signal.iloc[-3]
    macd1_prev1 = macd1_line.iloc[-2]
    macd2_prev1 = macd2_signal.iloc[-2]

    macd_crossover = (macd1_prev2 < macd2_prev2) and (macd1_prev1 > macd2_prev1)

    if debug:
        _logger.info(f"[SIGNAL-CHECK] {state.strategy_id}: macd_cross={macd_crossover} → go_long={macd_crossover}")

    return macd_crossover


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
        return [("M1>M2", False), ("Cross", False), ("M1+", False)]

    indicators = populate_indicators(df, state.params)
    macd1_line = indicators.get("macd1_line")
    macd2_signal = indicators.get("macd2_signal")

    if macd1_line is None or macd2_signal is None:
        return [("M1>M2", False), ("Cross", False), ("M1+", False)]

    macd1_above = macd1_line.iloc[-2] > macd2_signal.iloc[-2] if len(macd1_line) > 1 else False
    macd1_positive = macd1_line.iloc[-2] > 0 if len(macd1_line) > 1 else False

    crossed = False
    if len(macd1_line) >= 3 and len(macd2_signal) >= 3:
        crossed = (macd1_line.iloc[-3] < macd2_signal.iloc[-3]) and (macd1_line.iloc[-2] > macd2_signal.iloc[-2])

    return [
        ("M1>M2", macd1_above),
        ("Cross", crossed),
        ("M1+", macd1_positive),
    ]
