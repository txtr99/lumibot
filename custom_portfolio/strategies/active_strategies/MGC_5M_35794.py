"""
MGC Strategy 0.35794 - High Crosses Below MA with EMA Cross
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- High crosses below its MA
- Close crosses above EMA (reversal)
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
        "cross_ma_period": 205,
        "ema_period": 100,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 10.3,
        "sl_mult": 1.5,
    },
    "time_exit": {"max_bars": 255},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for High cross below MA with EMA reversal.
    """
    import pandas_ta as ta

    indicators = {}

    cross_ma_period = params.get("cross_ma_period", 41)
    ema_period = params.get("ema_period", 20)

    # SMA of High
    indicators["sma_high"] = ta.sma(df["high"], length=cross_ma_period)

    # EMA of Close
    indicators["ema"] = ta.ema(df["close"], length=ema_period)

    # Debug logging if enabled
    if debug and indicators.get("sma_high") is not None and len(indicators["sma_high"]) >= 3:
        sma_high = indicators["sma_high"]
        ema = indicators["ema"]
        _logger.info(
            f"[INDICATOR] {strategy_id}: sma_high[-2]={sma_high.iloc[-2]:.2f}, "
            f"ema[-2]={(ema.iloc[-2] if ema is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - High crosses below its SMA
    - Close crosses above EMA (reversal confirmation)
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    sma_high = indicators.get("sma_high")
    ema = indicators.get("ema")

    if sma_high is None or ema is None:
        return False
    if len(sma_high) < 3 or len(ema) < 3:
        return False

    # High crosses below SMA
    high_prev2 = df["high"].iloc[-3]
    high_prev1 = df["high"].iloc[-2]
    sma_prev2 = sma_high.iloc[-3]
    sma_prev1 = sma_high.iloc[-2]
    high_cross_below_sma = (high_prev2 > sma_prev2) and (high_prev1 < sma_prev1)

    # Close crosses above EMA
    close_prev2 = df["close"].iloc[-3]
    close_prev1 = df["close"].iloc[-2]
    ema_prev2 = ema.iloc[-3]
    ema_prev1 = ema.iloc[-2]
    close_cross_ema = (close_prev2 < ema_prev2) and (close_prev1 > ema_prev1)

    result = high_cross_below_sma and close_cross_ema

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: high_cross_below_sma={high_cross_below_sma}, "
            f"close_cross_ema={close_cross_ema} → go_long={result}"
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
        return [("H<SMA", False), ("HxSMA", False), ("C>EMA", False)]

    indicators = populate_indicators(df, state.params)
    sma_high = indicators.get("sma_high")
    ema = indicators.get("ema")

    if sma_high is None or ema is None:
        return [("H<SMA", False), ("HxSMA", False), ("C>EMA", False)]

    high_below_sma = df["high"].iloc[-2] < sma_high.iloc[-2] if len(sma_high) > 1 else False
    close_above_ema = df["close"].iloc[-2] > ema.iloc[-2] if len(ema) > 1 else False

    high_crossed = False
    if len(sma_high) >= 3:
        high_crossed = (df["high"].iloc[-3] > sma_high.iloc[-3]) and (df["high"].iloc[-2] < sma_high.iloc[-2])

    return [
        ("H<SMA", high_below_sma),
        ("HxSMA", high_crossed),
        ("C>EMA", close_above_ema),
    ]
