"""
MGC Strategy 0.35409 - Low Crosses Below MA with EMA Cross
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- Low crosses below its MA (weakness)
- EMA crosses above Close (reversal)
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
        "cross_ma_period": 100,
        "ema_period": 150,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 10.3,
        "sl_mult": 1.5,
    },
    "time_exit": {"max_bars": 200},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for Low cross below MA with EMA reversal.
    """
    import pandas_ta as ta

    indicators = {}

    cross_ma_period = params.get("cross_ma_period", 20)
    ema_period = params.get("ema_period", 30)

    # SMA of Low
    indicators["sma_low"] = ta.sma(df["low"], length=cross_ma_period)

    # EMA of Close
    indicators["ema"] = ta.ema(df["close"], length=ema_period)

    # Debug logging if enabled
    if debug and indicators.get("sma_low") is not None and len(indicators["sma_low"]) >= 3:
        sma_low = indicators["sma_low"]
        ema = indicators["ema"]
        _logger.info(
            f"[INDICATOR] {strategy_id}: sma_low[-2]={sma_low.iloc[-2]:.2f}, "
            f"ema[-2]={(ema.iloc[-2] if ema is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - Low crosses below its SMA (showing weakness/dip)
    - EMA crosses above Close on previous bar, now below (reversal setup)

    This is a mean reversion entry after a dip.
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    sma_low = indicators.get("sma_low")
    ema = indicators.get("ema")

    if sma_low is None or ema is None:
        return False
    if len(sma_low) < 3 or len(ema) < 3:
        return False

    # Low crosses below SMA (dip condition)
    low_prev2 = df["low"].iloc[-3]
    low_prev1 = df["low"].iloc[-2]
    sma_prev2 = sma_low.iloc[-3]
    sma_prev1 = sma_low.iloc[-2]
    low_cross_below_sma = (low_prev2 > sma_prev2) and (low_prev1 < sma_prev1)

    # EMA was above close, now below (price reversal)
    ema_prev2 = ema.iloc[-3]
    close_prev2 = df["close"].iloc[-3]
    ema_prev1 = ema.iloc[-2]
    close_prev1 = df["close"].iloc[-2]
    ema_cross = (ema_prev2 > close_prev2) and (ema_prev1 < close_prev1)

    result = low_cross_below_sma and ema_cross

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: low_cross_below_sma={low_cross_below_sma}, "
            f"ema_cross={ema_cross} → go_long={result}"
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
        return [("L<SMA", False), ("LxSMA", False), ("EMA<C", False)]

    indicators = populate_indicators(df, state.params)
    sma_low = indicators.get("sma_low")
    ema = indicators.get("ema")

    if sma_low is None or ema is None:
        return [("L<SMA", False), ("LxSMA", False), ("EMA<C", False)]

    low_below_sma = df["low"].iloc[-2] < sma_low.iloc[-2] if len(sma_low) > 1 else False
    ema_below_close = ema.iloc[-2] < df["close"].iloc[-2] if len(ema) > 1 else False

    low_crossed = False
    if len(sma_low) >= 3:
        low_crossed = (df["low"].iloc[-3] > sma_low.iloc[-3]) and (df["low"].iloc[-2] < sma_low.iloc[-2])

    return [
        ("L<SMA", low_below_sma),
        ("LxSMA", low_crossed),
        ("EMA<C", ema_below_close),
    ]
