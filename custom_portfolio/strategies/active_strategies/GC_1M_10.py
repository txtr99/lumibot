"""
GC_1M_10 - Mean Reversion Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- KaufmanEfficiencyRatio(10) < 0.95 (efficiency below threshold)
- KaufmanEfficiencyRatio(10) current < KER(10) previous (efficiency declining)
- RSI(14) > 70 and RSI(14) <= 80 (overbought range)
- Momentum(3) current < Momentum(3) 2 bars ago (momentum declining)

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
        "ker_length": 10,
        "rsi_length": 14,
        "momentum_length": 3,
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


def kaufman_efficiency_ratio(closes, length=10):
    """Calculate Kaufman Efficiency Ratio"""
    change = abs(closes.iloc[-1] - closes.iloc[-1 - length])
    volatility = closes.diff().abs().rolling(length).sum().iloc[-1]
    if volatility == 0:
        return 0
    return change / volatility


def momentum(closes, length=10):
    """Calculate Momentum"""
    return closes.iloc[-1] - closes.iloc[-1 - length]


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]

    ker_length = int(params.get("ker_length", 10))
    rsi_length = int(params.get("rsi_length", 14))
    momentum_length = int(params.get("momentum_length", 3))

    # Calculate KER
    ker_current = kaufman_efficiency_ratio(closes, length=ker_length)
    closes_prev = closes.iloc[:-1]
    ker_prev = kaufman_efficiency_ratio(closes_prev, length=ker_length)

    rsi = ta.rsi(closes, length=rsi_length)

    mom_current = momentum(closes, length=momentum_length)
    closes_2ago = closes.iloc[:-2]
    mom_2ago = momentum(closes_2ago, length=momentum_length)

    return {
        "ker_current": ker_current,
        "ker_prev": ker_prev,
        "rsi": rsi.iloc[-1],
        "mom_current": mom_current,
        "mom_2ago": mom_2ago,
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - KER(10) < 0.95
    - KER(10) current < KER(10) previous
    - RSI(14) > 70 and <= 80
    - Momentum(3) current < Momentum(3) 2 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["ker_current"] < 0.95
        and indicators["ker_current"] < indicators["ker_prev"]
        and indicators["rsi"] > 70
        and indicators["rsi"] <= 80
        and indicators["mom_current"] < indicators["mom_2ago"]
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
        ("KER<.95", indicators["ker_current"] < 0.95),
        ("KER↓", indicators["ker_current"] < indicators["ker_prev"]),
        ("RSI70-80", indicators["rsi"] > 70 and indicators["rsi"] <= 80),
        ("Mom↓", indicators["mom_current"] < indicators["mom_2ago"]),
    ]
