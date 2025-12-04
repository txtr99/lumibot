"""
GC_1M_08 - Mean Reversion Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- Close > EMA(10) (above short-term exponential average)
- RSI(14) >= 70 (overbought)
- Stochastics(14) >= 70 (overbought)
- Momentum(10) current < Momentum(10) 5 bars ago (momentum declining)

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
        "ema_length": 10,
        "rsi_length": 14,
        "stoch_length": 14,
        "momentum_length": 10,
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
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def momentum(closes, length=10):
    """Calculate Momentum"""
    return closes.iloc[-1] - closes.iloc[-1 - length]


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    ema_length = int(params.get("ema_length", 10))
    rsi_length = int(params.get("rsi_length", 14))
    stoch_length = int(params.get("stoch_length", 14))
    momentum_length = int(params.get("momentum_length", 10))

    # Calculate indicators
    ema_10 = closes.ewm(span=ema_length).mean()
    rsi = ta.rsi(closes, length=rsi_length)
    stoch_data = ta.stoch(highs, lows, closes, k=stoch_length, d=3)
    stoch_k = stoch_data[f"STOCHk_{stoch_length}_3_3"]

    mom_current = momentum(closes, length=momentum_length)
    closes_5ago = closes.iloc[:-5]
    mom_5ago = momentum(closes_5ago, length=momentum_length)

    return {
        "close": closes.iloc[-1],
        "ema_10": ema_10.iloc[-1],
        "rsi": rsi.iloc[-1],
        "stoch": stoch_k.iloc[-1],
        "mom_current": mom_current,
        "mom_5ago": mom_5ago,
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Close > EMA(10)
    - RSI(14) >= 70
    - Stochastics(14) >= 70
    - Momentum(10) current < Momentum(10) 5 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["close"] > indicators["ema_10"]
        and indicators["rsi"] >= 70
        and indicators["stoch"] >= 70
        and indicators["mom_current"] < indicators["mom_5ago"]
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
        ("RSI≥70", indicators["rsi"] >= 70),
        ("Stoch≥70", indicators["stoch"] >= 70),
        ("Mom↓", indicators["mom_current"] < indicators["mom_5ago"]),
    ]
