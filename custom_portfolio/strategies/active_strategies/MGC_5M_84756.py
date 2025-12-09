"""
MGC Strategy 0.84756 - RSI Threshold Cross
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI(Open) crosses above threshold (58.8)
"""

import logging

_logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi_period": 200,
        "rsi_threshold": 58.8,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 17.6,
        "sl_mult": 2.0,
    },
    "time_exit": {"max_bars": 250},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for RSI threshold cross.
    """
    import pandas_ta as ta

    indicators = {}

    rsi_period = params.get("rsi_period", 40)

    # RSI on Open
    indicators["rsi_open"] = ta.rsi(df["open"], length=rsi_period)

    if debug and indicators.get("rsi_open") is not None and len(indicators["rsi_open"]) >= 3:
        ro = indicators["rsi_open"]
        _logger.info(f"[INDICATOR] {strategy_id}: rsi_open={ro.iloc[-2]:.2f}")

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI(Open) crosses above threshold (from below to above)
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    rsi_open = indicators.get("rsi_open")
    rsi_threshold = state.params.get("rsi_threshold", 58.8)

    if rsi_open is None:
        return False
    if len(rsi_open) < 3:
        return False

    # RSI crosses above threshold
    rsi_prev2 = rsi_open.iloc[-3]
    rsi_prev1 = rsi_open.iloc[-2]

    rsi_cross_threshold = (rsi_prev2 < rsi_threshold) and (rsi_prev1 > rsi_threshold)

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: rsi_x_th={rsi_cross_threshold} → go_long={rsi_cross_threshold}"
        )

    return rsi_cross_threshold


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
        return [("RSI<th", False), ("RSI>th", False), ("Cross", False)]

    indicators = populate_indicators(df, state.params)
    rsi_open = indicators.get("rsi_open")
    rsi_threshold = state.params.get("rsi_threshold", 58.8)

    if rsi_open is None:
        return [("RSI<th", False), ("RSI>th", False), ("Cross", False)]

    rsi_below = rsi_open.iloc[-3] < rsi_threshold if len(rsi_open) > 2 else False
    rsi_above = rsi_open.iloc[-2] > rsi_threshold if len(rsi_open) > 1 else False

    crossed = False
    if len(rsi_open) >= 3:
        crossed = (rsi_open.iloc[-3] < rsi_threshold) and (rsi_open.iloc[-2] > rsi_threshold)

    return [
        ("RSI<th", rsi_below),
        ("RSI>th", rsi_above),
        ("Cross", crossed),
    ]
