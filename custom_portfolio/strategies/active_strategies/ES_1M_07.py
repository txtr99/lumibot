"""
ES_1M_07 - Mean Reversion Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- Open > EMA(100) (opening above exponential average)
- RSI(2) >= 35 and RSI(2) <= 45 (RSI in specific range)
- EMA(200) current > EMA(200) 2 bars ago (long-term trend rising)

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
        "ema_fast": 100,
        "ema_slow": 200,
        "rsi_length": 2,
        "rsi_lower": 35,
        "rsi_upper": 45,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
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
    opens = df["open"]

    ema_fast = int(params.get("ema_fast", 100))
    ema_slow = int(params.get("ema_slow", 200))
    rsi_length = int(params.get("rsi_length", 2))

    # Calculate indicators
    ema_100 = closes.ewm(span=ema_fast).mean()
    ema_200 = closes.ewm(span=ema_slow).mean()
    rsi = ta.rsi(closes, length=rsi_length)

    return {
        "open": opens.iloc[-1],
        "ema_100": ema_100.iloc[-1],
        "ema_200_current": ema_200.iloc[-1],
        "ema_200_2ago": ema_200.iloc[-3],
        "rsi": rsi.iloc[-1],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Open > EMA(100)
    - RSI(2) >= 35 and <= 45
    - EMA(200) current > EMA(200) 2 bars ago
    """
    indicators = populate_indicators(df, state.params)

    rsi_lower = state.params.get("rsi_lower", 35)
    rsi_upper = state.params.get("rsi_upper", 45)

    condition = (
        indicators["open"] > indicators["ema_100"]
        and indicators["rsi"] >= rsi_lower
        and indicators["rsi"] <= rsi_upper
        and indicators["ema_200_current"] > indicators["ema_200_2ago"]
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
