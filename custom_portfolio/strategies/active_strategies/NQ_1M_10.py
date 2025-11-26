"""
NQ_1M_10 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Momentum(10) < 0 (negative momentum)
- EMA(100) current > EMA(100) previous (long-term trend rising)
- Hurst(20) crosses below 0.50 (trend persistence weakening)

Symbol: MNQ (Micro E-mini Nasdaq-100)
Direction: Long only
Strategy Type: Mean reversion
"""

import numpy as np

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MNQ",
    "contracts": 1,
    "params": {
        "momentum_length": 10,
        "ema_length": 100,
        "hurst_length": 20,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 5.0,
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


def momentum(closes, length=10):
    """Calculate Momentum"""
    return closes.iloc[-1] - closes.iloc[-1 - length]


def hurst_exponent(closes, length=20):
    """Simple Hurst Exponent approximation using R/S analysis"""
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

    momentum_length = int(params.get("momentum_length", 10))
    ema_length = int(params.get("ema_length", 100))
    hurst_length = int(params.get("hurst_length", 20))

    # Calculate indicators
    mom = momentum(closes, length=momentum_length)
    ema_100 = closes.ewm(span=ema_length).mean()

    # Hurst for cross detection
    hurst_current = hurst_exponent(closes, length=hurst_length)
    closes_prev = closes.iloc[:-1]
    hurst_prev = hurst_exponent(closes_prev, length=hurst_length)

    return {
        "momentum": mom,
        "ema_100_current": ema_100.iloc[-1],
        "ema_100_prev": ema_100.iloc[-2],
        "hurst_current": hurst_current,
        "hurst_prev": hurst_prev,
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Momentum(10) < 0
    - EMA(100) current > EMA(100) previous
    - Hurst(20) crosses below 0.50
    """
    indicators = populate_indicators(df, state.params)

    # Hurst crosses below 0.50
    hurst_cross = indicators["hurst_prev"] >= 0.50 and indicators["hurst_current"] < 0.50

    condition = (
        indicators["momentum"] < 0 and indicators["ema_100_current"] > indicators["ema_100_prev"] and hurst_cross
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
    hurst_cross = indicators["hurst_prev"] >= 0.50 and indicators["hurst_current"] < 0.50
    return [
        ("Mom<0", indicators["momentum"] < 0),
        ("EMA100↑", indicators["ema_100_current"] > indicators["ema_100_prev"]),
        ("Hx.5", hurst_cross),
    ]
