"""
ES_1M_08 - Trend Following Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- EMA(20) > EMA(200) (fast EMA above slow EMA)
- EMA(5) current > EMA(5) 5 bars ago (short-term trend accelerating)
- EMA(100) current > EMA(100) 4 bars ago (medium-term trend rising)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Trend following
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "ema_fast": 5,
        "ema_medium_fast": 20,
        "ema_medium": 100,
        "ema_slow": 200,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 8.0,
        "sl_mult": 8.0,
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

    ema_fast = int(params.get("ema_fast", 5))
    ema_medium_fast = int(params.get("ema_medium_fast", 20))
    ema_medium = int(params.get("ema_medium", 100))
    ema_slow = int(params.get("ema_slow", 200))

    # Calculate EMAs
    ema_5 = closes.ewm(span=ema_fast).mean()
    ema_20 = closes.ewm(span=ema_medium_fast).mean()
    ema_100 = closes.ewm(span=ema_medium).mean()
    ema_200 = closes.ewm(span=ema_slow).mean()

    return {
        "ema_20": ema_20.iloc[-1],
        "ema_200": ema_200.iloc[-1],
        "ema_5_current": ema_5.iloc[-1],
        "ema_5_5ago": ema_5.iloc[-6],
        "ema_100_current": ema_100.iloc[-1],
        "ema_100_4ago": ema_100.iloc[-5],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - EMA(20) > EMA(200)
    - EMA(5) current > EMA(5) 5 bars ago
    - EMA(100) current > EMA(100) 4 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["ema_20"] > indicators["ema_200"]
        and indicators["ema_5_current"] > indicators["ema_5_5ago"]
        and indicators["ema_100_current"] > indicators["ema_100_4ago"]
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
    return [
        ("E20>E200", indicators["ema_20"] > indicators["ema_200"]),
        ("EMA5↑", indicators["ema_5_current"] > indicators["ema_5_5ago"]),
        ("EMA100↑", indicators["ema_100_current"] > indicators["ema_100_4ago"]),
    ]
