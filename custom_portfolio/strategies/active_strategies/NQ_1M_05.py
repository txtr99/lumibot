"""
NQ_1M_05 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- RSI(2) >= 30 and RSI(2) <= 40 (RSI in specific range)
- RSI(2) crosses below 90 (coming down from extreme)
- EMA(20) current > EMA(20) previous (trend rising)

Symbol: MNQ (Micro E-mini Nasdaq-100)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MNQ",
    "contracts": 1,
    "params": {
        "rsi_length": 2,
        "ema_length": 20,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,
        "sl_mult": 8.0,
    },
    "time_exit": {
        "max_bars": 120,
    },
    "allowed_sessions": ["New_York"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]

    rsi_length = int(params.get("rsi_length", 2))
    ema_length = int(params.get("ema_length", 20))

    # Calculate indicators
    rsi = ta.rsi(closes, length=rsi_length)
    ema_20 = closes.ewm(span=ema_length).mean()

    return {
        "rsi_current": rsi.iloc[-1],
        "rsi_prev": rsi.iloc[-2],
        "ema_20_current": ema_20.iloc[-1],
        "ema_20_prev": ema_20.iloc[-2],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - RSI(2) >= 30 and <= 40
    - RSI(2) crosses below 90
    - EMA(20) current > EMA(20) previous
    """
    indicators = populate_indicators(df, state.params)

    # RSI crosses below 90
    rsi_cross = indicators["rsi_prev"] >= 90 and indicators["rsi_current"] < 90

    condition = (
        indicators["rsi_current"] >= 30
        and indicators["rsi_current"] <= 40
        and rsi_cross
        and indicators["ema_20_current"] > indicators["ema_20_prev"]
    )

    return condition


def go_short(state, df):
    """Return True when conditions favor going short. This strategy is long-only."""
    return False


def generate_signal(state, df):
    """Required entrypoint. Returns: "BUY", "SELL", or "HOLD"."""
    if go_long(state, df):
        return "BUY"
    if go_short(state, df):
        return "SELL"
    return "HOLD"


def get_signal_visibility(state, df):
    """Return list of (label, is_true) for live status display."""
    indicators = populate_indicators(df, state.params)
    rsi_cross = indicators["rsi_prev"] >= 90 and indicators["rsi_current"] < 90
    return [
        ("RSI30-40", indicators["rsi_current"] >= 30 and indicators["rsi_current"] <= 40),
        ("RSIx90", rsi_cross),
        ("EMA20↑", indicators["ema_20_current"] > indicators["ema_20_prev"]),
    ]
