"""
NQ_1M_06 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Close > EMA(100) (above long-term exponential average)
- Low <= Lowest(Low, 20) (at or below 20-bar low)
- Momentum(3) current < Momentum(3) 5 bars ago (momentum declining)

Symbol: MNQ (Micro E-mini Nasdaq-100)
Direction: Long only
Strategy Type: Mean reversion
"""

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MNQ",
    "contracts": 1,
    "params": {
        "ema_length": 100,
        "momentum_length": 3,
        "lookback_period": 20,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,
        "sl_mult": 8.0,
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


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    lows = df["low"]

    ema_length = int(params.get("ema_length", 100))
    momentum_length = int(params.get("momentum_length", 3))
    lookback_period = int(params.get("lookback_period", 20))

    # Calculate indicators
    ema_100 = closes.ewm(span=ema_length).mean()
    lowest_low = lows.rolling(lookback_period).min()

    mom_current = momentum(closes, length=momentum_length)
    closes_5ago = closes.iloc[:-5]
    mom_5ago = momentum(closes_5ago, length=momentum_length)

    return {
        "close": closes.iloc[-1],
        "low": lows.iloc[-1],
        "ema_100": ema_100.iloc[-1],
        "lowest_low": lowest_low.iloc[-1],
        "mom_current": mom_current,
        "mom_5ago": mom_5ago,
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Close > EMA(100)
    - Low <= Lowest(Low, 20)
    - Momentum(3) current < Momentum(3) 5 bars ago
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["close"] > indicators["ema_100"]
        and indicators["low"] <= indicators["lowest_low"]
        and indicators["mom_current"] < indicators["mom_5ago"]
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
