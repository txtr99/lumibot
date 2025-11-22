"""
ES_1M_03 - Mean Reversion Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- Low > SMA(200) (price above long-term trend)
- RSI(2) crosses below 95 (extreme overbought crossing down)
- Rate of Change(10) declining (current < 2 bars ago)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "sma_length": 200,
        "rsi_length": 2,
        "roc_length": 10,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 5.0,
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
    lows = df["low"]

    sma_length = int(params.get("sma_length", 200))
    rsi_length = int(params.get("rsi_length", 2))
    roc_length = int(params.get("roc_length", 10))

    # Calculate SMA(200)
    sma_200 = closes.rolling(sma_length).mean()

    # Calculate RSI(2)
    rsi_2 = ta.rsi(closes, length=rsi_length)

    # Calculate Rate of Change
    roc = ((closes - closes.shift(roc_length)) / closes.shift(roc_length)) * 100

    return {
        "low": lows.iloc[-1],
        "sma_200": sma_200.iloc[-1],
        "rsi_2_current": rsi_2.iloc[-1],
        "rsi_2_prev": rsi_2.iloc[-2],
        "roc_current": roc.iloc[-1],
        "roc_2ago": roc.iloc[-3],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Low > SMA(200)
    - RSI(2) crosses below 95 (was >= 95, now < 95)
    - ROC(10) declining
    """
    indicators = populate_indicators(df, state.params)

    # RSI crosses below 95
    rsi_cross = indicators["rsi_2_prev"] >= 95 and indicators["rsi_2_current"] < 95

    condition = (
        indicators["low"] > indicators["sma_200"] and rsi_cross and indicators["roc_current"] < indicators["roc_2ago"]
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
