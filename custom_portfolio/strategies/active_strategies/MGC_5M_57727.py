"""
MGC Strategy 0.57727 - RSI Lower Count Signal
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI(Low) was below RSI(TypicalPrice) for N bars, now equals or higher
"""

import logging

_logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi1_period": 160,
        "rsi2_period": 185,
        "lower_count": 50,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 14.0,
        "sl_mult": 2.1,
    },
    "time_exit": {"max_bars": 235},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for RSI lower count signal.
    """
    import pandas_ta as ta

    indicators = {}

    rsi1_period = params.get("rsi1_period", 32)
    rsi2_period = params.get("rsi2_period", 37)

    # Typical price = (H + L + C) / 3
    typical_price = (df["high"] + df["low"] + df["close"]) / 3

    # RSI on Low
    indicators["rsi_low"] = ta.rsi(df["low"], length=rsi1_period)

    # RSI on Typical Price
    indicators["rsi_typical"] = ta.rsi(typical_price, length=rsi2_period)

    if debug and indicators.get("rsi_low") is not None and len(indicators["rsi_low"]) >= 3:
        rl = indicators["rsi_low"]
        rt = indicators["rsi_typical"]
        _logger.info(
            f"[INDICATOR] {strategy_id}: rsi_low[-2]={rl.iloc[-2]:.2f}, "
            f"rsi_typ[-2]={(rt.iloc[-2] if rt is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI(Low) was below RSI(TypicalPrice) for N consecutive bars
    - Now RSI(Low) >= RSI(TypicalPrice) (crossover occurred)
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 15:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    rsi_low = indicators.get("rsi_low")
    rsi_typical = indicators.get("rsi_typical")
    lower_count = state.params.get("lower_count", 10)

    if rsi_low is None or rsi_typical is None:
        return False
    if len(rsi_low) < lower_count + 3 or len(rsi_typical) < lower_count + 3:
        return False

    # Check if RSI(Low) was below RSI(TypicalPrice) for N consecutive bars
    was_lower_count = 0
    for i in range(2, lower_count + 2):  # Start from 2 bars ago
        if rsi_low.iloc[-i] < rsi_typical.iloc[-i]:
            was_lower_count += 1
        else:
            break

    # Now RSI(Low) crosses above or equals RSI(TypicalPrice)
    rsi_crossover = (rsi_low.iloc[-3] < rsi_typical.iloc[-3]) and (rsi_low.iloc[-2] >= rsi_typical.iloc[-2])

    result = was_lower_count >= lower_count - 1 and rsi_crossover

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: cnt={was_lower_count}, " f"rsi_x={rsi_crossover} → go_long={result}"
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
    if len(df) < 15:
        return [("RSL<TP", False), ("Cross", False), ("Count", False)]

    indicators = populate_indicators(df, state.params)
    rsi_low = indicators.get("rsi_low")
    rsi_typical = indicators.get("rsi_typical")
    lower_count = state.params.get("lower_count", 10)

    if rsi_low is None or rsi_typical is None:
        return [("RSL<TP", False), ("Cross", False), ("Count", False)]

    rsl_below = rsi_low.iloc[-2] < rsi_typical.iloc[-2] if len(rsi_low) > 1 else False

    crossed = False
    if len(rsi_low) >= 3 and len(rsi_typical) >= 3:
        crossed = (rsi_low.iloc[-3] < rsi_typical.iloc[-3]) and (rsi_low.iloc[-2] >= rsi_typical.iloc[-2])

    # Count consecutive bars below
    count = 0
    if len(rsi_low) >= lower_count + 3 and len(rsi_typical) >= lower_count + 3:
        for i in range(2, lower_count + 2):
            if rsi_low.iloc[-i] < rsi_typical.iloc[-i]:
                count += 1
            else:
                break

    return [
        ("RSL<TP", rsl_below),
        ("Cross", crossed),
        (f"Cnt{count}", count >= lower_count - 1),
    ]
