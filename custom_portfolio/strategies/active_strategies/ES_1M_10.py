"""
ES_1M_10 - Mean Reversion Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- RSI(14) current <= RSI(14) 3 bars ago (RSI declining)
- RSI(14) crosses below 70 (coming down from overbought)
- Rate of Change(10) current < Rate of Change(10) 4 bars ago (momentum declining)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "rsi_length": 14,
        "roc_length": 10,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,
        "sl_mult": 8.0,
    },
    "time_exit": {
        "max_bars": 180,
    },
    "allowed_sessions": ["New_York"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Compute and return a dict of indicators.
    """
    closes = df["close"]

    rsi_length = int(params.get("rsi_length", 14))
    roc_length = int(params.get("roc_length", 10))

    # Calculate RSI
    rsi = ta.rsi(closes, length=rsi_length)

    # Calculate Rate of Change
    roc = ((closes - closes.shift(roc_length)) / closes.shift(roc_length)) * 100

    return {
        "rsi_current": rsi.iloc[-1],
        "rsi_3ago": rsi.iloc[-4],
        "rsi_prev": rsi.iloc[-2],
        "roc_current": roc.iloc[-1],
        "roc_4ago": roc.iloc[-5],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - RSI(14) current <= RSI(14) 3 bars ago
    - RSI(14) crosses below 70
    - ROC(10) current < ROC(10) 4 bars ago
    """
    indicators = populate_indicators(df, state.params)

    # RSI crosses below 70
    rsi_cross = indicators["rsi_prev"] >= 70 and indicators["rsi_current"] < 70

    condition = (
        indicators["rsi_current"] <= indicators["rsi_3ago"]
        and rsi_cross
        and indicators["roc_current"] < indicators["roc_4ago"]
    )

    return condition


def go_short(state, df):
    """
    Return True when conditions favor going short.
    This strategy is long-only.
    """
    return False


def generate_signal(state, df):
    """
    Required entrypoint. Returns: "BUY", "SELL", or "HOLD".
    """
    if go_long(state, df):
        return "BUY"
    if go_short(state, df):
        return "SELL"
    return "HOLD"
