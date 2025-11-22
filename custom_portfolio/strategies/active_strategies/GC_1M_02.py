"""
GC_1M_02 - Trend Following Strategy for MGC (Micro Gold)
Converted from EasyLanguage

Entry Logic:
- KaufmanEfficiencyRatio(10) current < KER(10) 2 bars ago (efficiency declining)
- SMA(200) current > SMA(200) 3 bars ago (long-term trend rising)
- ADX(20) > 30 (strong trend)
- MACD Histogram current > MACD Histogram 3 bars ago (momentum increasing)

Symbol: MGC (Micro Gold)
Direction: Long only
Strategy Type: Trend following
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "ker_length": 10,
        "sma_length": 200,
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
    sma_length = int(params.get("sma_length", 200))
    adx_length = int(params.get("adx_length", 20))

    # Calculate indicators
    ker_current = kaufman_efficiency_ratio(closes, length=ker_length)
    closes_2ago = closes.iloc[:-2]
    ker_2ago = kaufman_efficiency_ratio(closes_2ago, length=ker_length)

    sma_200 = closes.rolling(sma_length).mean()
    adx = ta.adx(highs, lows, closes, length=adx_length)[f"ADX_{adx_length}"]
    macd_data = ta.macd(closes, fast=12, slow=26, signal=9)
    macd_hist = macd_data["MACDh_12_26_9"]

    return {
        "ker_current": ker_current,
        "ker_2ago": ker_2ago,
        "sma_200_current": sma_200.iloc[-1],
        "sma_200_3ago": sma_200.iloc[-4],
        "adx": adx.iloc[-1],
        "macd_hist_current": macd_hist.iloc[-1],
        "macd_hist_3ago": macd_hist.iloc[-4],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - KER(10) current < KER(10) 2 bars ago
    - SMA(200) current > SMA(200) 3 bars ago
    - ADX(20) > 30
    - MACD Histogram current > MACD Histogram 3 bars ago
    """
    indicators = populate_indicators(df, state.params)
    adx_threshold = state.params.get("adx_threshold", 30)

    condition = (
        indicators["ker_current"] < indicators["ker_2ago"]
        and indicators["sma_200_current"] > indicators["sma_200_3ago"]
        and indicators["adx"] > adx_threshold
        and indicators["macd_hist_current"] > indicators["macd_hist_3ago"]
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
