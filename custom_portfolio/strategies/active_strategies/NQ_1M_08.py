"""
NQ_1M_08 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Open > SMA(200) (opening above long-term average)
- RSI(2) <= 20 (extremely oversold)
- Stochastics(14) crosses above 10 (momentum turning up)

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
        "sma_length": 200,
        "rsi_length": 2,
        "stoch_length": 14,
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
    highs = df["high"]
    lows = df["low"]
    opens = df["open"]

    sma_length = int(params.get("sma_length", 200))
    rsi_length = int(params.get("rsi_length", 2))
    stoch_length = int(params.get("stoch_length", 14))

    # Calculate indicators
    sma_200 = closes.rolling(sma_length).mean()
    rsi = ta.rsi(closes, length=rsi_length)
    stoch_data = ta.stoch(highs, lows, closes, k=stoch_length, d=3)
    stoch_k = stoch_data[f"STOCHk_{stoch_length}_3_3"]

    return {
        "open": opens.iloc[-1],
        "sma_200": sma_200.iloc[-1],
        "rsi": rsi.iloc[-1],
        "stoch_current": stoch_k.iloc[-1],
        "stoch_prev": stoch_k.iloc[-2],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Open > SMA(200)
    - RSI(2) <= 20
    - Stochastics(14) crosses above 10
    """
    indicators = populate_indicators(df, state.params)

    # Stochastics crosses above 10
    stoch_cross = indicators["stoch_prev"] <= 10 and indicators["stoch_current"] > 10

    condition = indicators["open"] > indicators["sma_200"] and indicators["rsi"] <= 20 and stoch_cross

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
