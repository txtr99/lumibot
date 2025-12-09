"""
MGC Strategy 0.10063 - MACD Crossover with RSI Filter
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)
"""

import logging

# Logger for debug output (controlled by DEBUG_INDICATORS env var)
_logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "macd_fast": 60,
        "macd_slow": 130,
        "macd_smooth": 45,
        "rsi_period": 70,
        "rsi_threshold": 50,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 12.5,
        "sl_mult": 2.0,
    },
    "time_exit": {"max_bars": 120},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for MACD crossover with RSI filter.
    """
    import pandas_ta as ta

    indicators = {}

    macd_fast = params.get("macd_fast", 12)
    macd_slow = params.get("macd_slow", 26)
    macd_smooth = params.get("macd_smooth", 9)
    rsi_period = params.get("rsi_period", 14)

    # MACD
    macd_result = ta.macd(df["close"], fast=macd_fast, slow=macd_slow, signal=macd_smooth)
    if macd_result is not None:
        indicators["macd_line"] = macd_result.iloc[:, 0]
        indicators["macd_signal"] = macd_result.iloc[:, 2]

    # RSI
    indicators["rsi"] = ta.rsi(df["close"], length=rsi_period)

    # Debug logging if enabled
    if debug and indicators.get("macd_line") is not None and len(indicators["macd_line"]) >= 3:
        macd_line = indicators["macd_line"]
        macd_sig = indicators["macd_signal"]
        rsi = indicators["rsi"]
        _logger.info(
            f"[INDICATOR] {strategy_id}: macd={macd_line.iloc[-2]:.2f}, "
            f"macd_sig={macd_sig.iloc[-2]:.2f}, rsi[-2]={(rsi.iloc[-2] if rsi is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - MACD line crosses above Signal line
    - RSI above threshold (confirming momentum)
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    macd_line = indicators.get("macd_line")
    macd_signal = indicators.get("macd_signal")
    rsi = indicators.get("rsi")
    rsi_threshold = state.params.get("rsi_threshold", 50)

    if any(x is None for x in [macd_line, macd_signal, rsi]):
        return False
    if any(len(x) < 3 for x in [macd_line, macd_signal, rsi]):
        return False

    # MACD crossover
    macd_prev2 = macd_line.iloc[-3]
    signal_prev2 = macd_signal.iloc[-3]
    macd_prev1 = macd_line.iloc[-2]
    signal_prev1 = macd_signal.iloc[-2]

    macd_crossover = (macd_prev2 < signal_prev2) and (macd_prev1 > signal_prev1)

    # RSI above threshold
    rsi_bullish = rsi.iloc[-2] > rsi_threshold

    result = macd_crossover and rsi_bullish

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: macd_crossover={macd_crossover}, "
            f"rsi_bullish={rsi_bullish} → go_long={result}"
        )

    return result


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
        return [("MACD>S", False), ("Cross", False), ("RSI>th", False)]

    indicators = populate_indicators(df, state.params)
    macd_line = indicators.get("macd_line")
    macd_signal = indicators.get("macd_signal")
    rsi = indicators.get("rsi")
    rsi_threshold = state.params.get("rsi_threshold", 50)

    if any(x is None for x in [macd_line, macd_signal, rsi]):
        return [("MACD>S", False), ("Cross", False), ("RSI>th", False)]

    macd_above = macd_line.iloc[-2] > macd_signal.iloc[-2] if len(macd_line) > 1 else False
    rsi_bullish = rsi.iloc[-2] > rsi_threshold if len(rsi) > 1 else False

    crossed = False
    if len(macd_line) >= 3 and len(macd_signal) >= 3:
        crossed = (macd_line.iloc[-3] < macd_signal.iloc[-3]) and (macd_line.iloc[-2] > macd_signal.iloc[-2])

    return [
        ("MACD>S", macd_above),
        ("Cross", crossed),
        ("RSI>th", rsi_bullish),
    ]
