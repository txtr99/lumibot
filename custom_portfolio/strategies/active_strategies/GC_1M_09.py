"""
GC_1M_09 - Trend Following Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- KaufmanEfficiencyRatio(10) < 0.90 (efficiency below threshold)
- KaufmanEfficiencyRatio(10) crosses below 0.80 (efficiency crossing down)
- EMA(8) current < EMA(8) 4 bars ago (short-term declining)
- MACD Histogram current > MACD Histogram 5 bars ago (momentum improving)

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
        "ema_length": 8,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 5.0,
    },
    "time_exit": {
        "max_bars": 120,
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

    ker_length = int(params.get("ker_length", 10))
    ema_length = int(params.get("ema_length", 8))

    # Calculate KER for cross detection
    ker_current = kaufman_efficiency_ratio(closes, length=ker_length)
    closes_prev = closes.iloc[:-1]
    ker_prev = kaufman_efficiency_ratio(closes_prev, length=ker_length)

    ema_8 = closes.ewm(span=ema_length).mean()
    macd_data = ta.macd(closes, fast=12, slow=26, signal=9)
    macd_hist = macd_data["MACDh_12_26_9"]

    return {
        "ker_current": ker_current,
        "ker_prev": ker_prev,
        "ema_8_current": ema_8.iloc[-1],
        "ema_8_4ago": ema_8.iloc[-5],
        "macd_hist_current": macd_hist.iloc[-1],
        "macd_hist_5ago": macd_hist.iloc[-6],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - KER(10) < 0.90
    - KER(10) crosses below 0.80
    - EMA(8) current < EMA(8) 4 bars ago
    - MACD Histogram current > MACD Histogram 5 bars ago
    """
    indicators = populate_indicators(df, state.params)

    # KER crosses below 0.80
    ker_cross = indicators["ker_prev"] >= 0.80 and indicators["ker_current"] < 0.80

    condition = (
        indicators["ker_current"] < 0.90
        and ker_cross
        and indicators["ema_8_current"] < indicators["ema_8_4ago"]
        and indicators["macd_hist_current"] > indicators["macd_hist_5ago"]
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
    ker_cross = indicators["ker_prev"] >= 0.80 and indicators["ker_current"] < 0.80
    return [
        ("KER<.9", indicators["ker_current"] < 0.90),
        ("KERx.8", ker_cross),
        ("EMA8↓", indicators["ema_8_current"] < indicators["ema_8_4ago"]),
        ("MACD↑", indicators["macd_hist_current"] > indicators["macd_hist_5ago"]),
    ]
