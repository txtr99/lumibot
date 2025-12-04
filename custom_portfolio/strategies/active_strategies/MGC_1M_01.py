"""
MGC_1M_01 - Micro Gold MACD Cross Strategy

Converted from: custom_portfolio/strategies/easylanguage/mgc_001.el
Source: StrategyQuant X Build 143

Entry Logic:
- Long when MACD (on median price) crosses below its 245-period MA
- Original: 5M bars with 49-period MA, converted to 1M with 5x scaling

Parameters scaled from 5M to 1M:
- MACD Fast: 16 -> 80
- MACD Slow: 37 -> 185
- MACD Signal: 10 -> 50
- Cross MA Period: 49 -> 245
- Max Bars: 180 -> 900
"""

import pandas_ta as ta

STRATEGY_CONFIG = {
    "strategy_id": "",  # Auto-fill from filename
    "symbol": "MGC",
    "contracts": 1,
    "params": {
        "macd_fast_length": 80,
        "macd_slow_length": 185,
        "macd_signal_length": 50,
        "cross_ma_period": 245,
    },
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 15.5,
        "sl_mult": 2.1,
    },
    "time_exit": {"max_bars": 900},
    "allowed_sessions": ["24/7"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Calculate MACD on median price and its moving average for cross detection.

    The original EasyLanguage uses:
    - SQ_MACD(MedianPrice, fast, slow, signal, 0) - MACD line using median price
    - SQ_IndicatorCrossesBelowMA(indicator, period, method) - cross detection

    Returns dict with current and previous values for cross detection.
    """
    # Use median price instead of close (as per original strategy)
    median_price = (df["high"] + df["low"]) / 2

    # Get parameters
    fast = int(params.get("macd_fast_length", 80))
    slow = int(params.get("macd_slow_length", 185))
    signal = int(params.get("macd_signal_length", 50))
    cross_period = int(params.get("cross_ma_period", 245))

    # Calculate MACD on median price
    macd_data = ta.macd(median_price, fast=fast, slow=slow, signal=signal)

    # Get MACD line (not signal or histogram)
    macd_col = f"MACD_{fast}_{slow}_{signal}"
    macd_line = macd_data[macd_col]

    # Calculate MA of MACD line for cross detection
    macd_ma = macd_line.rolling(cross_period).mean()

    return {
        # Current bar values
        "macd": macd_line.iloc[-1],
        "macd_ma": macd_ma.iloc[-1],
        # Previous bar values (for cross detection)
        "macd_prev": macd_line.iloc[-2],
        "macd_ma_prev": macd_ma.iloc[-2],
        # 2 bars ago (original signal uses [1] which is 1 bar ago in EL)
        "macd_2ago": macd_line.iloc[-3],
        "macd_ma_2ago": macd_ma.iloc[-3],
    }


def go_long(state, df):
    """
    Return True when MACD crosses below its MA (1 bar ago).

    Original EasyLanguage:
    LongEntrySignal = SQ_IndicatorCrossesBelowMA(SQ_MACD(...)[1], period, 3)[1]

    The [1] indices mean we check the cross from 1 bar ago:
    - 2 bars ago: MACD was >= MA
    - 1 bar ago: MACD dropped below MA
    """
    indicators = populate_indicators(df, state.params)

    # Values from 2 bars ago
    macd_2ago = indicators["macd_2ago"]
    macd_ma_2ago = indicators["macd_ma_2ago"]

    # Values from 1 bar ago
    macd_prev = indicators["macd_prev"]
    macd_ma_prev = indicators["macd_ma_prev"]

    # Cross below detection: was above or equal, now below
    crossed_below = macd_2ago >= macd_ma_2ago and macd_prev < macd_ma_prev

    return crossed_below


def go_short(state, df):
    """Long-only strategy - no short entries."""
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
    """
    Return list of (label, is_true) tuples for live status display.
    """
    indicators = populate_indicators(df, state.params)

    macd = indicators["macd"]
    macd_ma = indicators["macd_ma"]
    macd_prev = indicators["macd_prev"]
    macd_ma_prev = indicators["macd_ma_prev"]
    macd_2ago = indicators["macd_2ago"]
    macd_ma_2ago = indicators["macd_ma_2ago"]

    # Check if cross happened 1 bar ago
    crossed = macd_2ago >= macd_ma_2ago and macd_prev < macd_ma_prev

    return [
        ("MACD<MA", macd < macd_ma),
        ("Cross", crossed),
        ("MACD-", macd < macd_prev),  # MACD declining
    ]
