"""
NQ_1M_02 - Trend Following Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Consecutive(close, 4, 0) = 1 (4 consecutive down closes)
- Momentum(10) current > Momentum(10) previous (momentum increasing)
- ADX(20) current <= ADX(20) 5 bars ago (trend weakening)

Symbol: MNQ (Micro E-mini Nasdaq-100)
Direction: Long only
Strategy Type: Trend following
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MNQ",
    "contracts": 1,
    "params": {
        "momentum_length": 10,
        "adx_length": 20,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,
        "sl_mult": 5.0,
    },
    "time_exit": {
        "max_bars": 120,
    },
    "allowed_sessions": ["New_York"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def consecutive(series, length, direction):
    """Count consecutive up/down bars. direction: 0=down, 1=up. Returns 1 if condition met, 0 otherwise."""
    if direction == 1:
        for i in range(1, length):
            if series.iloc[-i] <= series.iloc[-i - 1]:
                return 0
        return 1
    else:
        for i in range(1, length):
            if series.iloc[-i] >= series.iloc[-i - 1]:
                return 0
        return 1


def momentum(closes, length=10):
    """Calculate Momentum"""
    return closes.iloc[-1] - closes.iloc[-1 - length]


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    momentum_length = int(params.get("momentum_length", 10))
    adx_length = int(params.get("adx_length", 20))

    # Calculate indicators
    consec_down = consecutive(closes, 4, 0)
    mom_current = momentum(closes, length=momentum_length)
    closes_prev = closes.iloc[:-1]
    mom_prev = momentum(closes_prev, length=momentum_length)

    adx = ta.adx(highs, lows, closes, length=adx_length)[f"ADX_{adx_length}"]

    return {
        "consecutive_down": consec_down,
        "mom_current": mom_current,
        "mom_prev": mom_prev,
        "adx_current": adx.iloc[-1],
        "adx_5ago": adx.iloc[-6],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - 4 consecutive down closes
    - Momentum(10) current > Momentum(10) previous
    - ADX(20) current <= ADX(20) 5 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["consecutive_down"] == 1
        and indicators["mom_current"] > indicators["mom_prev"]
        and indicators["adx_current"] <= indicators["adx_5ago"]
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
