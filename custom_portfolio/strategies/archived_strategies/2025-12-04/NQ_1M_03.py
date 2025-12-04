"""
NQ_1M_03 - Mean Reversion Strategy for MNQ (Micro E-mini Nasdaq-100)
Converted from EasyLanguage

Entry Logic:
- Open > SMA(50) (opening above medium-term average)
- Consecutive(open, 4, 0) = 1 (4 consecutive down opens)
- Open <= Lowest(Open, 10) (at or below 10-bar low of opens)

Symbol: MNQ (Micro E-mini Nasdaq-100)
Direction: Long only
Strategy Type: Mean reversion
"""

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MNQ",
    "contracts": 1,
    "params": {
        "sma_length": 50,
        "lookback_period": 10,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,
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


def consecutive(series, length, direction):
    """Count consecutive up/down bars. direction: 0=down, 1=up."""
    if direction == 1:
        for i in range(1, length):
            if series.iloc[-i] <= series.iloc[-i - 1]:
                return 0
        return 1
    else:
        for i in range(1, length):
            if series.iloc[-i] >= series.iloc[-i - 1]:
                return 0
        return 1


def populate_indicators(df, params):
    """Compute and return a dict of indicators."""
    closes = df["close"]
    opens = df["open"]

    sma_length = int(params.get("sma_length", 50))
    lookback_period = int(params.get("lookback_period", 10))

    # Calculate indicators
    sma_50 = closes.rolling(sma_length).mean()
    consec_down_open = consecutive(opens, 4, 0)
    lowest_open = opens.rolling(lookback_period).min()

    return {
        "open": opens.iloc[-1],
        "sma_50": sma_50.iloc[-1],
        "consecutive_down_open": consec_down_open,
        "lowest_open": lowest_open.iloc[-1],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Open > SMA(50)
    - 4 consecutive down opens
    - Open <= Lowest(Open, 10)
    """
    indicators = populate_indicators(df, state.params)

    condition = (
        indicators["open"] > indicators["sma_50"]
        and indicators["consecutive_down_open"] == 1
        and indicators["open"] <= indicators["lowest_open"]
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
    return [
        ("O>SMA50", indicators["open"] > indicators["sma_50"]),
        ("4Opn↓", indicators["consecutive_down_open"] == 1),
        ("O≤Lo10", indicators["open"] <= indicators["lowest_open"]),
    ]
