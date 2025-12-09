"""
MGC Strategy 0.38080 - MACD Above MA with Dual MACD Crossover
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- MACD line above its MA
- MACD1 line crosses above MACD2 signal
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
        "indicator_ma_period": 90,
        "macd1_fast": 60,
        "macd1_slow": 130,
        "macd_smooth": 45,
        "macd2_fast": 120,
        "macd2_slow": 260,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 17.4,
        "sl_mult": 1.9,
    },
    "time_exit": {"max_bars": 140},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for MACD above MA with dual MACD crossover.
    """
    import pandas_ta as ta

    indicators = {}

    indicator_ma_period = params.get("indicator_ma_period", 18)
    macd1_fast = params.get("macd1_fast", 12)
    macd1_slow = params.get("macd1_slow", 26)
    macd_smooth = params.get("macd_smooth", 9)
    macd2_fast = params.get("macd2_fast", 24)
    macd2_slow = params.get("macd2_slow", 52)

    # MACD1
    macd1_result = ta.macd(df["close"], fast=macd1_fast, slow=macd1_slow, signal=macd_smooth)
    if macd1_result is not None:
        indicators["macd1_line"] = macd1_result.iloc[:, 0]
        # MA of MACD1 line
        indicators["macd1_ma"] = ta.sma(macd1_result.iloc[:, 0], length=indicator_ma_period)

    # MACD2
    macd2_result = ta.macd(df["close"], fast=macd2_fast, slow=macd2_slow, signal=macd_smooth)
    if macd2_result is not None:
        indicators["macd2_signal"] = macd2_result.iloc[:, 2]

    # Debug logging if enabled
    if debug and indicators.get("macd1_line") is not None and len(indicators["macd1_line"]) >= 3:
        macd1 = indicators["macd1_line"]
        macd1_ma = indicators.get("macd1_ma")
        macd2_sig = indicators.get("macd2_signal")
        _logger.info(
            f"[INDICATOR] {strategy_id}: macd1[-2]={macd1.iloc[-2]:.2f}, "
            f"macd1_ma[-2]={(macd1_ma.iloc[-2] if macd1_ma is not None else 0):.2f}, "
            f"macd2_sig[-2]={(macd2_sig.iloc[-2] if macd2_sig is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - MACD1 line above its MA
    - MACD1 line crosses above MACD2 signal
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    macd1_line = indicators.get("macd1_line")
    macd1_ma = indicators.get("macd1_ma")
    macd2_signal = indicators.get("macd2_signal")

    if any(x is None for x in [macd1_line, macd1_ma, macd2_signal]):
        return False
    if any(len(x) < 3 for x in [macd1_line, macd1_ma, macd2_signal]):
        return False

    # MACD1 above its MA
    macd_above_ma = macd1_line.iloc[-2] > macd1_ma.iloc[-2]

    # MACD1 crosses above MACD2 signal
    macd1_prev2 = macd1_line.iloc[-3]
    macd2_prev2 = macd2_signal.iloc[-3]
    macd1_prev1 = macd1_line.iloc[-2]
    macd2_prev1 = macd2_signal.iloc[-2]

    macd_crossover = (macd1_prev2 < macd2_prev2) and (macd1_prev1 > macd2_prev1)

    result = macd_above_ma and macd_crossover

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: macd_above_ma={macd_above_ma}, "
            f"macd_crossover={macd_crossover} → go_long={result}"
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
        return [("M>MA", False), ("M1>M2", False), ("Cross", False)]

    indicators = populate_indicators(df, state.params)
    macd1_line = indicators.get("macd1_line")
    macd1_ma = indicators.get("macd1_ma")
    macd2_signal = indicators.get("macd2_signal")

    if any(x is None for x in [macd1_line, macd1_ma, macd2_signal]):
        return [("M>MA", False), ("M1>M2", False), ("Cross", False)]

    macd_above_ma = macd1_line.iloc[-2] > macd1_ma.iloc[-2] if len(macd1_line) > 1 else False
    macd1_above_macd2 = macd1_line.iloc[-2] > macd2_signal.iloc[-2] if len(macd1_line) > 1 else False

    crossed = False
    if len(macd1_line) >= 3 and len(macd2_signal) >= 3:
        crossed = (macd1_line.iloc[-3] < macd2_signal.iloc[-3]) and (macd1_line.iloc[-2] > macd2_signal.iloc[-2])

    return [
        ("M>MA", macd_above_ma),
        ("M1>M2", macd1_above_macd2),
        ("Cross", crossed),
    ]
