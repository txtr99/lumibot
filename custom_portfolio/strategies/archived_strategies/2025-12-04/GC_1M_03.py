"""
GC_1M_03 - Trend Following Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- KaufmanEfficiencyRatio(10) crosses below 0.10 (efficiency decreasing)
- SMA(8) current < SMA(8) 2 bars ago (short-term declining)
- EMA(20) current > EMA(20) 5 bars ago (medium-term rising)
- ADX(20) > 30 (strong trend)

Symbol: MGC (Micro Gold)
Direction: Long only
Strategy Type: Trend following
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "ker_length": 10,
        "sma_length": 8,
        "ema_length": 20,
        "adx_length": 20,
        "adx_threshold": 30,
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
        "strategy_type": "trend_following",
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


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    ker_length = int(params.get("ker_length", 10))
    sma_length = int(params.get("sma_length", 8))
    ema_length = int(params.get("ema_length", 20))
    adx_length = int(params.get("adx_length", 20))

    # Calculate KER for cross detection
    ker_current = kaufman_efficiency_ratio(closes, length=ker_length)
    closes_prev = closes.iloc[:-1]
    ker_prev = kaufman_efficiency_ratio(closes_prev, length=ker_length)

    sma_8 = closes.rolling(sma_length).mean()
    ema_20 = closes.ewm(span=ema_length).mean()
    adx = ta.adx(highs, lows, closes, length=adx_length)[f"ADX_{adx_length}"]

    return {
        "ker_current": ker_current,
        "ker_prev": ker_prev,
        "sma_8_current": sma_8.iloc[-1],
        "sma_8_2ago": sma_8.iloc[-3],
        "ema_20_current": ema_20.iloc[-1],
        "ema_20_5ago": ema_20.iloc[-6],
        "adx": adx.iloc[-1],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - KER(10) crosses below 0.10
    - SMA(8) current < SMA(8) 2 bars ago
    - EMA(20) current > EMA(20) 5 bars ago
    - ADX(20) > 30
    """
    indicators = populate_indicators(df, state.params)
    adx_threshold = state.params.get("adx_threshold", 30)

    # KER crosses below 0.10
    ker_cross = indicators["ker_prev"] >= 0.10 and indicators["ker_current"] < 0.10

    condition = (
        ker_cross
        and indicators["sma_8_current"] < indicators["sma_8_2ago"]
        and indicators["ema_20_current"] > indicators["ema_20_5ago"]
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


def get_signal_visibility(state, df):
    """Return list of (label, is_true) for live status display."""
    indicators = populate_indicators(df, state.params)
    adx_threshold = state.params.get("adx_threshold", 30)
    ker_cross = indicators["ker_prev"] >= 0.10 and indicators["ker_current"] < 0.10
    return [
        ("KERx.1", ker_cross),
        ("SMA8↓", indicators["sma_8_current"] < indicators["sma_8_2ago"]),
        ("EMA20↑", indicators["ema_20_current"] > indicators["ema_20_5ago"]),
        (f"ADX>{adx_threshold}", indicators["adx"] > adx_threshold),
    ]
