"""
MGC Strategy 0.71227 - Dual RSI Crossover with Dual EMA Crossover
Converted from StrategyQuant X EasyLanguage
Long-only strategy for MGC (Micro Gold Futures)

Original logic:
- RSI(High) crosses above RSI(Close)
- EMA(Open) crosses above Close
"""

import logging

_logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "strategy_id": "",  # Use filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        # Indicator parameters
        "rsi1_period": 100,
        "rsi2_period": 200,
        "ema_period": 150,
        # ATR for bracket orders
        "atr_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "resample_minutes": 5,
        "pt_mult": 16.0,
        "sl_mult": 1.8,
    },
    "time_exit": {"max_bars": 250},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Calculate indicators for dual RSI crossover with EMA crossover.
    """
    import pandas_ta as ta

    indicators = {}

    rsi1_period = params.get("rsi1_period", 20)
    rsi2_period = params.get("rsi2_period", 40)
    ema_period = params.get("ema_period", 30)

    # RSI on High
    indicators["rsi_high"] = ta.rsi(df["high"], length=rsi1_period)

    # RSI on Close
    indicators["rsi_close"] = ta.rsi(df["close"], length=rsi2_period)

    # EMA of Open
    indicators["ema_open"] = ta.ema(df["open"], length=ema_period)

    if debug and indicators.get("rsi_high") is not None and len(indicators["rsi_high"]) >= 3:
        rh = indicators["rsi_high"]
        rc = indicators["rsi_close"]
        eo = indicators.get("ema_open")
        _logger.info(
            f"[INDICATOR] {strategy_id}: rsi_h={rh.iloc[-2]:.2f}, "
            f"rsi_c={(rc.iloc[-2] if rc is not None else 0):.2f}, ema_o={(eo.iloc[-2] if eo is not None else 0):.2f}"
        )

    return indicators


def go_long(state, df) -> bool:
    """
    Long entry signal:
    - RSI(High) crosses above RSI(Close)
    - EMA(Open) crosses above Close (was above, now below - price rallying through EMA)
    """
    debug = getattr(state, "debug_indicators", False)

    if len(df) < 4:
        return False

    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)
    rsi_high = indicators.get("rsi_high")
    rsi_close = indicators.get("rsi_close")
    ema_open = indicators.get("ema_open")

    if any(x is None for x in [rsi_high, rsi_close, ema_open]):
        return False
    if any(len(x) < 3 for x in [rsi_high, rsi_close, ema_open]):
        return False

    # RSI crossover
    rsi_high_prev2 = rsi_high.iloc[-3]
    rsi_close_prev2 = rsi_close.iloc[-3]
    rsi_high_prev1 = rsi_high.iloc[-2]
    rsi_close_prev1 = rsi_close.iloc[-2]

    rsi_crossover = (rsi_high_prev2 < rsi_close_prev2) and (rsi_high_prev1 > rsi_close_prev1)

    # EMA(Open) crosses above Close (was above close, now below = price broke above)
    ema_prev2 = ema_open.iloc[-3]
    close_prev2 = df["close"].iloc[-3]
    ema_prev1 = ema_open.iloc[-2]
    close_prev1 = df["close"].iloc[-2]

    ema_cross = (ema_prev2 < close_prev2) and (ema_prev1 > close_prev1)

    result = rsi_crossover and ema_cross

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: rsi_x={rsi_crossover}, " f"ema_x={ema_cross} → go_long={result}"
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
        return [("RH>RC", False), ("RSIx", False), ("EMAx", False)]

    indicators = populate_indicators(df, state.params)
    rsi_high = indicators.get("rsi_high")
    rsi_close = indicators.get("rsi_close")
    ema_open = indicators.get("ema_open")

    if any(x is None for x in [rsi_high, rsi_close, ema_open]):
        return [("RH>RC", False), ("RSIx", False), ("EMAx", False)]

    rh_above_rc = rsi_high.iloc[-2] > rsi_close.iloc[-2] if len(rsi_high) > 1 else False

    rsi_crossed = False
    if len(rsi_high) >= 3 and len(rsi_close) >= 3:
        rsi_crossed = (rsi_high.iloc[-3] < rsi_close.iloc[-3]) and (rsi_high.iloc[-2] > rsi_close.iloc[-2])

    ema_crossed = False
    if len(ema_open) >= 3:
        ema_crossed = (ema_open.iloc[-3] < df["close"].iloc[-3]) and (ema_open.iloc[-2] > df["close"].iloc[-2])

    return [
        ("RH>RC", rh_above_rc),
        ("RSIx", rsi_crossed),
        ("EMAx", ema_crossed),
    ]
