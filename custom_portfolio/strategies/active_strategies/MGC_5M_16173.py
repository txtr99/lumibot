"""
MGC Strategy 0.16173 - MACD Histogram Reversal
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
        "macd_fast": 40,
        "macd_slow": 105,
        "macd_smooth": 25,
        "ema_period": 250,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 11.8,
        "sl_mult": 2.0,
    },
    "time_exit": {"max_bars": 300},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for MACD histogram reversal.
    """
    import pandas_ta as ta

    indicators = {}

    macd_fast = params.get("macd_fast", 8)
    macd_slow = params.get("macd_slow", 21)
    macd_smooth = params.get("macd_smooth", 5)
    ema_period = params.get("ema_period", 50)

    # MACD
    macd_result = ta.macd(df["close"], fast=macd_fast, slow=macd_slow, signal=macd_smooth)
    if macd_result is not None:
        indicators["macd_histogram"] = macd_result.iloc[:, 1]  # Histogram

    # EMA for trend filter
    indicators["ema"] = ta.ema(df["close"], length=ema_period)

    # Debug logging if enabled
    if debug and indicators.get("macd_histogram") is not None and len(indicators["macd_histogram"]) >= 3:
        macd_hist = indicators["macd_histogram"]
        ema = indicators["ema"]
        _logger.info(
            f"[INDICATOR] {strategy_id}: macd_hist[-2]={macd_hist.iloc[-2]:.2f}, "
            f"ema[-2]={(ema.iloc[-2] if ema is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - MACD histogram turns positive (crosses above zero)
    - Price above EMA (trend filter)
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    macd_histogram = indicators.get("macd_histogram")
    ema = indicators.get("ema")

    if macd_histogram is None or ema is None:
        return False
    if len(macd_histogram) < 3 or len(ema) < 3:
        return False

    # Histogram reversal (crosses above zero)
    hist_prev2 = macd_histogram.iloc[-3]
    hist_prev1 = macd_histogram.iloc[-2]
    histogram_crossover = (hist_prev2 < 0) and (hist_prev1 > 0)

    # Trend filter
    price_above_ema = df["close"].iloc[-2] > ema.iloc[-2]

    result = histogram_crossover and price_above_ema

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: histogram_crossover={histogram_crossover}, "
            f"price_above_ema={price_above_ema} → go_long={result}"
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
        return [("Hist>0", False), ("Cross", False), ("C>EMA", False)]

    indicators = populate_indicators(df, state.params)
    macd_histogram = indicators.get("macd_histogram")
    ema = indicators.get("ema")

    if macd_histogram is None or ema is None:
        return [("Hist>0", False), ("Cross", False), ("C>EMA", False)]

    hist_positive = macd_histogram.iloc[-2] > 0 if len(macd_histogram) > 1 else False
    price_above_ema = df["close"].iloc[-2] > ema.iloc[-2] if len(ema) > 1 else False

    crossed = False
    if len(macd_histogram) >= 3:
        crossed = (macd_histogram.iloc[-3] < 0) and (macd_histogram.iloc[-2] > 0)

    return [
        ("Hist>0", hist_positive),
        ("Cross", crossed),
        ("C>EMA", price_above_ema),
    ]
