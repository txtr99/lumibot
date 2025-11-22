"""
NQ_1M_01 - Mean Reversion Strategy for MNQ (Micro Nasdaq E-mini)
Converted from EasyLanguage

Entry Logic:
- 5 consecutive declining highs (consecutive(high, 5, 0) = 1)
- EMA(5) current < EMA(5) 2 bars ago (short-term EMA declining)
- EMA(50) current > EMA(50) 2 bars ago (long-term EMA rising)

Symbol: MNQ (Micro Nasdaq E-mini)
Direction: Long only
Strategy Type: Mean reversion
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "MNQ",
    "contracts": 1,
    "params": {
        "consecutive_length": 5,
        "ema_fast": 5,
        "ema_slow": 50,
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

    Args:
        series: Price series (high, low, close, etc.)
        length: Number of consecutive bars to check

    Returns:
        True if all bars in length are decreasing, False otherwise
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
    highs = df["high"]

    consecutive_length = int(params.get("consecutive_length", 5))
    ema_fast = int(params.get("ema_fast", 5))
    ema_slow = int(params.get("ema_slow", 50))

    # Calculate EMAs
    ema_5 = closes.ewm(span=ema_fast).mean()
    ema_50 = closes.ewm(span=ema_slow).mean()

    # Check for consecutive declining highs
    consecutive_high_down = consecutive_down(highs, consecutive_length)

    return {
        "consecutive_high_down": consecutive_high_down,
        "ema_5_current": ema_5.iloc[-1],
        "ema_5_2ago": ema_5.iloc[-3],  # 2 bars ago
        "ema_50_current": ema_50.iloc[-1],
        "ema_50_2ago": ema_50.iloc[-3],  # 2 bars ago
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - 5 consecutive declining highs
    - EMA(5) declining (current < 2 bars ago)
    - EMA(50) rising (current > 2 bars ago)
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["consecutive_high_down"]
        and indicators["ema_5_current"] < indicators["ema_5_2ago"]
        and indicators["ema_50_current"] > indicators["ema_50_2ago"]
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
