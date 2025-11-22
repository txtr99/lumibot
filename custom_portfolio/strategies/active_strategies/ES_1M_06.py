"""
ES_1M_06 - Trend Following Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- High < SMA(50) (price below medium-term average)
- High > SMA(200) (price above long-term average - overall uptrend)
- High > EMA(100) (price above medium-term exponential average)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Trend following
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "sma_fast": 50,
        "sma_slow": 200,
        "ema_period": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 5.0,
    },
    "time_exit": {
        "max_bars": 180,
    },
    "allowed_sessions": ["New_York"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Compute and return a dict of indicators.
    """
    closes = df["close"]
    highs = df["high"]

    sma_fast = int(params.get("sma_fast", 50))
    sma_slow = int(params.get("sma_slow", 200))
    ema_period = int(params.get("ema_period", 100))

    # Calculate moving averages
    sma_50 = closes.rolling(sma_fast).mean()
    sma_200 = closes.rolling(sma_slow).mean()
    ema_100 = closes.ewm(span=ema_period).mean()

    return {
        "high": highs.iloc[-1],
        "sma_50": sma_50.iloc[-1],
        "sma_200": sma_200.iloc[-1],
        "ema_100": ema_100.iloc[-1],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - High < SMA(50) (pullback below medium-term MA)
    - High > SMA(200) (above long-term MA)
    - High > EMA(100) (above exponential MA)
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["high"] < indicators["sma_50"]
        and indicators["high"] > indicators["sma_200"]
        and indicators["high"] > indicators["ema_100"]
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
