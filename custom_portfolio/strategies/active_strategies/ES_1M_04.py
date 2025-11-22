"""
ES_1M_04 - Mean Reversion Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- 5 consecutive declining closes
- EMA(8) declining (current < previous)
- EMA(100) rising (current > previous)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Mean reversion
"""

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "consecutive_length": 5,
        "ema_fast": 8,
        "ema_slow": 100,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 5.0,
        "sl_mult": 5.0,
    },
    "time_exit": {
        "max_bars": 180,
    },
    "allowed_sessions": ["New_York"],
    "metadata": {
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def consecutive_down(series, length):
    """
    Check if series has 'length' consecutive declining bars.
    """
    for i in range(1, length):
        if series.iloc[-i] >= series.iloc[-i - 1]:
            return False
    return True


def populate_indicators(df, params):
    """
    Compute and return a dict of indicators.
    """
    closes = df["close"]

    consecutive_length = int(params.get("consecutive_length", 5))
    ema_fast = int(params.get("ema_fast", 8))
    ema_slow = int(params.get("ema_slow", 100))

    # Calculate EMAs
    ema_8 = closes.ewm(span=ema_fast).mean()
    ema_100 = closes.ewm(span=ema_slow).mean()

    # Check for consecutive declining closes
    consecutive_close_down = consecutive_down(closes, consecutive_length)

    return {
        "consecutive_close_down": consecutive_close_down,
        "ema_8_current": ema_8.iloc[-1],
        "ema_8_prev": ema_8.iloc[-2],
        "ema_100_current": ema_100.iloc[-1],
        "ema_100_prev": ema_100.iloc[-2],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - 5 consecutive declining closes
    - EMA(8) declining
    - EMA(100) rising
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["consecutive_close_down"]
        and indicators["ema_8_current"] < indicators["ema_8_prev"]
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
