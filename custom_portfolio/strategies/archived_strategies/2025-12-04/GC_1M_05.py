"""
GC_1M_05 - Mean Reversion Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- Close < SMA(5) (pullback below short-term average)
- RSI(14) > 70 and RSI(14) <= 80 (overbought range)
- Rate of Change(3) current < ROC(3) 3 bars ago (momentum declining)
- SMA(5) current > SMA(5) 4 bars ago (short-term trend rising)

Symbol: MGC (Micro Gold)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "sma_length": 5,
        "rsi_length": 14,
        "roc_length": 3,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 5.0,
        "sl_mult": 5.0,
    },
    "time_exit": {
        "max_bars": 180,
    },
    "allowed_sessions": ["24/7"],  # 24/7 market
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]

    sma_length = int(params.get("sma_length", 5))
    rsi_length = int(params.get("rsi_length", 14))
    roc_length = int(params.get("roc_length", 3))

    # Calculate indicators
    sma_5 = closes.rolling(sma_length).mean()
    rsi = ta.rsi(closes, length=rsi_length)
    roc = ((closes - closes.shift(roc_length)) / closes.shift(roc_length)) * 100

    return {
        "close": closes.iloc[-1],
        "sma_5": sma_5.iloc[-1],
        "sma_5_current": sma_5.iloc[-1],
        "sma_5_4ago": sma_5.iloc[-5],
        "rsi": rsi.iloc[-1],
        "roc_current": roc.iloc[-1],
        "roc_3ago": roc.iloc[-4],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Close < SMA(5)
    - RSI(14) > 70 and <= 80
    - ROC(3) current < ROC(3) 3 bars ago
    - SMA(5) current > SMA(5) 4 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["close"] < indicators["sma_5"]
        and indicators["rsi"] > 70
        and indicators["rsi"] <= 80
        and indicators["roc_current"] < indicators["roc_3ago"]
        and indicators["sma_5_current"] > indicators["sma_5_4ago"]
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
    return [
        ("C<SMA5", indicators["close"] < indicators["sma_5"]),
        ("RSI70-80", indicators["rsi"] > 70 and indicators["rsi"] <= 80),
        ("ROC↓", indicators["roc_current"] < indicators["roc_3ago"]),
        ("SMA5↑", indicators["sma_5_current"] > indicators["sma_5_4ago"]),
    ]
