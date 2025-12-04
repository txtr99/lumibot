"""
ES_1M_09 - Trend Following Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- KaufmanEfficiencyRatio(10) crosses below 0.50 (efficiency decreasing)
- SMA(3) current < SMA(3) 2 bars ago (short-term declining)
- EMA(100) current > EMA(100) previous (medium-term rising)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Trend following
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "ker_length": 10,
        "sma_length": 3,
        "ema_length": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 8.0,
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


def kaufman_efficiency_ratio(closes, length=10):
    """
    Calculate Kaufman Efficiency Ratio
    """
    change = abs(closes.iloc[-1] - closes.iloc[-1 - length])
    volatility = closes.diff().abs().rolling(length).sum().iloc[-1]

    if volatility == 0:
        return 0

    return change / volatility


def populate_indicators(df, params):
    """
    Compute and return a dict of indicators.
    """
    closes = df["close"]

    ker_length = int(params.get("ker_length", 10))
    sma_length = int(params.get("sma_length", 3))
    ema_length = int(params.get("ema_length", 100))

    # Calculate indicators
    ker_current = kaufman_efficiency_ratio(closes, length=ker_length)
    # For cross detection, calculate KER for previous bar
    closes_prev = closes.iloc[:-1]
    ker_prev = kaufman_efficiency_ratio(closes_prev, length=ker_length)

    sma_3 = closes.rolling(sma_length).mean()
    ema_100 = closes.ewm(span=ema_length).mean()

    return {
        "ker_current": ker_current,
        "ker_prev": ker_prev,
        "sma_3_current": sma_3.iloc[-1],
        "sma_3_2ago": sma_3.iloc[-3],
        "ema_100_current": ema_100.iloc[-1],
        "ema_100_prev": ema_100.iloc[-2],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - KER(10) crosses below 0.50
    - SMA(3) current < SMA(3) 2 bars ago
    - EMA(100) current > EMA(100) previous
    """
    indicators = populate_indicators(df, state.params)

    # KER crosses below 0.50
    ker_cross = indicators["ker_prev"] >= 0.50 and indicators["ker_current"] < 0.50

    condition = (
        ker_cross
        and indicators["sma_3_current"] < indicators["sma_3_2ago"]
        and indicators["ema_100_current"] > indicators["ema_100_prev"]
    )

    return condition


def go_short(state, df):
    """
    Return True when conditions favor going short.
    This strategy is long-only.
    """
    return False


def generate_signal(state, df):
    """
    Required entrypoint. Returns: "BUY", "SELL", or "HOLD".
    """
    if go_long(state, df):
        return "BUY"
    if go_short(state, df):
        return "SELL"
    return "HOLD"


def get_signal_visibility(state, df):
    """Return list of (label, is_true) for live status display."""
    indicators = populate_indicators(df, state.params)
    ker_cross = indicators["ker_prev"] >= 0.50 and indicators["ker_current"] < 0.50
    return [
        ("KERx.5", ker_cross),
        ("SMA3↓", indicators["sma_3_current"] < indicators["sma_3_2ago"]),
        ("EMA100↑", indicators["ema_100_current"] > indicators["ema_100_prev"]),
    ]
