"""
GC_1M_07 - Mean Reversion Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- Low < SMA(3) (pullback below short-term average)
- Momentum(5) current < Momentum(5) 4 bars ago (momentum declining)
- SMA(8) current < SMA(8) 2 bars ago (short-term declining)
- Hurst(20) > 0.65 (persistence in trend)

Symbol: MGC (Micro Gold)
Direction: Long only
Strategy Type: Mean reversion
"""

import numpy as np

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "sma_fast": 3,
        "sma_slow": 8,
        "momentum_length": 5,
        "hurst_length": 20,
        "hurst_threshold": 0.65,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 8.0,
    },
    "time_exit": {
        "max_bars": 120,
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


def hurst_exponent(closes, length=20):
    """
    Simple Hurst Exponent approximation using R/S analysis
    """
    if len(closes) < length:
        return 0.5

    data = closes.iloc[-length:].values
    lags = range(2, min(length // 2, 20))
    tau = []
    lagvec = []

    for lag in lags:
        pp = np.subtract(data[lag:], data[:-lag])
        lagvec.append(lag)
        tau.append(np.sqrt(np.std(pp)))

    if len(tau) < 2:
        return 0.5

    try:
        poly = np.polyfit(np.log(lagvec), np.log(tau), 1)
        return poly[0] * 2.0
    except (ValueError, np.linalg.LinAlgError):
        return 0.5


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    lows = df["low"]

    sma_fast = int(params.get("sma_fast", 3))
    sma_slow = int(params.get("sma_slow", 8))
    momentum_length = int(params.get("momentum_length", 5))
    hurst_length = int(params.get("hurst_length", 20))

    # Calculate indicators
    sma_3 = closes.rolling(sma_fast).mean()
    sma_8 = closes.rolling(sma_slow).mean()

    mom_current = momentum(closes, length=momentum_length)
    closes_4ago = closes.iloc[:-4]
    mom_4ago = momentum(closes_4ago, length=momentum_length)

    hurst = hurst_exponent(closes, length=hurst_length)

    return {
        "low": lows.iloc[-1],
        "sma_3": sma_3.iloc[-1],
        "sma_8_current": sma_8.iloc[-1],
        "sma_8_2ago": sma_8.iloc[-3],
        "mom_current": mom_current,
        "mom_4ago": mom_4ago,
        "hurst": hurst,
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Low < SMA(3)
    - Momentum(5) current < Momentum(5) 4 bars ago
    - SMA(8) current < SMA(8) 2 bars ago
    - Hurst(20) > 0.65
    """
    indicators = populate_indicators(df, state.params)
    hurst_threshold = state.params.get("hurst_threshold", 0.65)

    condition = (
        indicators["low"] < indicators["sma_3"]
        and indicators["mom_current"] < indicators["mom_4ago"]
        and indicators["sma_8_current"] < indicators["sma_8_2ago"]
        and indicators["hurst"] > hurst_threshold
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
    hurst_threshold = state.params.get("hurst_threshold", 0.65)
    return [
        ("L<SMA3", indicators["low"] < indicators["sma_3"]),
        ("Mom↓", indicators["mom_current"] < indicators["mom_4ago"]),
        ("SMA8↓", indicators["sma_8_current"] < indicators["sma_8_2ago"]),
        (f"Hurst>{hurst_threshold}", indicators["hurst"] > hurst_threshold),
    ]
