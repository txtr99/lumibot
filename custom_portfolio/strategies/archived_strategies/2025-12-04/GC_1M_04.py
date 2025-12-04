"""
GC_1M_04 - Breakout Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- Close > EMA(10) (price above short-term average)
- Low > Bollinger Band Lower (price above lower band)
- SMA(200) current > SMA(200) 3 bars ago (long-term rising)
- EMA(5) current > EMA(5) 3 bars ago (short-term rising)

Symbol: MGC (Micro Gold)
Direction: Long only
Strategy Type: Breakout
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "ema_fast": 5,
        "ema_medium": 10,
        "sma_slow": 200,
        "bb_length": 20,
        "bb_std": 2,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 8.0,
    },
    "time_exit": {
        "max_bars": 180,
    },
    "allowed_sessions": ["24/7"],  # 24/7 market
    "metadata": {
        "strategy_type": "breakout",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    lows = df["low"]

    ema_fast = int(params.get("ema_fast", 5))
    ema_medium = int(params.get("ema_medium", 10))
    sma_slow = int(params.get("sma_slow", 200))
    bb_length = int(params.get("bb_length", 20))
    bb_std = float(params.get("bb_std", 2))

    # Calculate indicators
    ema_5 = closes.ewm(span=ema_fast).mean()
    ema_10 = closes.ewm(span=ema_medium).mean()
    sma_200 = closes.rolling(sma_slow).mean()
    bb_data = ta.bbands(closes, length=bb_length, std=bb_std)
    bb_lower = bb_data[f"BBL_{bb_length}_{bb_std}_{bb_std}"]

    return {
        "close": closes.iloc[-1],
        "low": lows.iloc[-1],
        "ema_10": ema_10.iloc[-1],
        "bb_lower": bb_lower.iloc[-1],
        "sma_200_current": sma_200.iloc[-1],
        "sma_200_3ago": sma_200.iloc[-4],
        "ema_5_current": ema_5.iloc[-1],
        "ema_5_3ago": ema_5.iloc[-4],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Close > EMA(10)
    - Low > Bollinger Band Lower
    - SMA(200) current > SMA(200) 3 bars ago
    - EMA(5) current > EMA(5) 3 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["close"] > indicators["ema_10"]
        and indicators["low"] > indicators["bb_lower"]
        and indicators["sma_200_current"] > indicators["sma_200_3ago"]
        and indicators["ema_5_current"] > indicators["ema_5_3ago"]
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
        ("C>EMA10", indicators["close"] > indicators["ema_10"]),
        ("L>BBL", indicators["low"] > indicators["bb_lower"]),
        ("SMA200↑", indicators["sma_200_current"] > indicators["sma_200_3ago"]),
        ("EMA5↑", indicators["ema_5_current"] > indicators["ema_5_3ago"]),
    ]
