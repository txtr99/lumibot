"""
GC_1M_06 - Breakout Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- Low >= Highest(Low, 252) (at or above 252-bar high of lows)
- Momentum(5) current > Momentum(5) 3 bars ago (momentum increasing)
- Momentum(5) current < Momentum(5) 2 bars ago (momentum pullback)
- ADX(20) > 25 (strong trend)

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
        "momentum_length": 5,
        "lookback_period": 252,
        "adx_length": 20,
        "adx_threshold": 25,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 5.0,
    },
    "time_exit": {
        "max_bars": 180,
    },
    "metadata": {
        "strategy_type": "breakout",
        "direction": "long",
    },
}


def momentum(closes, length=10):
    """Calculate Momentum (difference from N bars ago)"""
    return closes.iloc[-1] - closes.iloc[-1 - length]


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    momentum_length = int(params.get("momentum_length", 5))
    lookback_period = int(params.get("lookback_period", 252))
    adx_length = int(params.get("adx_length", 20))

    # Calculate indicators
    highest_low = lows.rolling(lookback_period).max()
    adx = ta.adx(highs, lows, closes, length=adx_length)[f"ADX_{adx_length}"]

    # Momentum for current and historical
    mom_current = momentum(closes, length=momentum_length)
    closes_3ago = closes.iloc[:-3]
    mom_3ago = momentum(closes_3ago, length=momentum_length)
    closes_2ago = closes.iloc[:-2]
    mom_2ago = momentum(closes_2ago, length=momentum_length)

    return {
        "low": lows.iloc[-1],
        "highest_low": highest_low.iloc[-1],
        "mom_current": mom_current,
        "mom_3ago": mom_3ago,
        "mom_2ago": mom_2ago,
        "adx": adx.iloc[-1],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Low >= Highest(Low, 252)
    - Momentum(5) current > Momentum(5) 3 bars ago
    - Momentum(5) current < Momentum(5) 2 bars ago
    - ADX(20) > 25
    """
    indicators = populate_indicators(df, state.params)
    adx_threshold = state.params.get("adx_threshold", 25)

    condition = (
        indicators["low"] >= indicators["highest_low"]
        and indicators["mom_current"] > indicators["mom_3ago"]
        and indicators["mom_current"] < indicators["mom_2ago"]
        and indicators["adx"] > adx_threshold
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
