"""
ES_1M_02 - Mean Reversion Strategy for MES (Micro E-mini S&P 500)
Converted from EasyLanguage

Entry Logic:
- Low > EMA(100) (price above long-term trend)
- RSI(2) between 80-90 (overbought)
- MACD Histogram declining (current <= previous)

Symbol: MES (Micro E-mini S&P 500)
Direction: Long only
Strategy Type: Mean reversion
"""

import pandas_ta as ta

# Debug logging toggle (set to False to disable)
DEBUG_LOGGING = False

STRATEGY_CONFIG = {
    "strategy_id": "",
    "symbol": "MES",
    "contracts": 1,
    "params": {
        "ema_length": 100,
        "rsi_length": 2,
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
        "strategy_type": "mean_reversion",
        "direction": "long",
    },
}


def populate_indicators(df, params):
    """
    Compute and return a dict of indicators.
    """
    closes = df["close"]
    lows = df["low"]

    ema_length = int(params.get("ema_length", 100))
    rsi_length = int(params.get("rsi_length", 2))

    # Calculate EMA(100)
    ema_100 = closes.ewm(span=ema_length).mean()

    # Calculate RSI(2)
    rsi_2 = ta.rsi(closes, length=rsi_length)

    # Calculate MACD Histogram
    macd_data = ta.macd(closes, fast=12, slow=26, signal=9)
    macd_hist = macd_data["MACDh_12_26_9"]

    indicators = {
        "low": lows.iloc[-1],
        "ema_100": ema_100.iloc[-1],
        "rsi_2": rsi_2.iloc[-1],
        "macd_hist_current": macd_hist.iloc[-1],
        "macd_hist_prev": macd_hist.iloc[-2],
    }

    if DEBUG_LOGGING:
        import sys

        print("\n[ES_1M_02] Indicator Values:", file=sys.stderr, flush=True)
        print(f"  Low:                 {indicators['low']:.2f}", file=sys.stderr, flush=True)
        print(f"  EMA(100):            {indicators['ema_100']:.2f}", file=sys.stderr, flush=True)
        print(f"  RSI(2):              {indicators['rsi_2']:.2f}", file=sys.stderr, flush=True)
        print(f"  MACD Hist (current): {indicators['macd_hist_current']:.6f}", file=sys.stderr, flush=True)
        print(f"  MACD Hist (prev):    {indicators['macd_hist_prev']:.6f}", file=sys.stderr, flush=True)

    return indicators


def go_long(state, df):
    """
    Return True when conditions favor going long.

    Entry conditions:
    - Low > EMA(100)
    - RSI(2) >= 80 and <= 90
    - MACD Histogram declining
    """
    indicators = populate_indicators(df, state.params)

    # Evaluate each condition
    cond1 = indicators["low"] > indicators["ema_100"]
    cond2 = indicators["rsi_2"] >= 80
    cond3 = indicators["rsi_2"] <= 90
    cond4 = indicators["macd_hist_current"] <= indicators["macd_hist_prev"]

    if DEBUG_LOGGING:
        import sys

        print("\n[ES_1M_02] Entry Condition Checks:", file=sys.stderr, flush=True)
        print(f"  Condition 1 - Low > EMA(100): {cond1}", file=sys.stderr, flush=True)
        low_diff = indicators["low"] - indicators["ema_100"]
        print(
            f"    {indicators['low']:.2f} > {indicators['ema_100']:.2f} (diff: {low_diff:.2f})",
            file=sys.stderr,
            flush=True,
        )

        print(f"  Condition 2 - RSI(2) >= 80: {cond2}", file=sys.stderr, flush=True)
        rsi_diff_lower = indicators["rsi_2"] - 80
        print(
            f"    {indicators['rsi_2']:.2f} >= 80 (diff from threshold: {rsi_diff_lower:.2f})",
            file=sys.stderr,
            flush=True,
        )

        print(f"  Condition 3 - RSI(2) <= 90: {cond3}", file=sys.stderr, flush=True)
        rsi_diff_upper = 90 - indicators["rsi_2"]
        print(
            f"    {indicators['rsi_2']:.2f} <= 90 (diff from threshold: {rsi_diff_upper:.2f})",
            file=sys.stderr,
            flush=True,
        )

        print(f"  Condition 4 - MACD Hist declining: {cond4}", file=sys.stderr, flush=True)
        macd_change = indicators["macd_hist_current"] - indicators["macd_hist_prev"]
        print(
            f"    {indicators['macd_hist_current']:.6f} <= {indicators['macd_hist_prev']:.6f} "
            f"(change: {macd_change:.6f})",
            file=sys.stderr,
            flush=True,
        )

    condition = cond1 and cond2 and cond3 and cond4

    if DEBUG_LOGGING:
        import sys

        print(f"\n  ALL CONDITIONS MET: {condition}", file=sys.stderr, flush=True)

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
    if DEBUG_LOGGING:
        import sys

        print("\n" + "=" * 60, file=sys.stderr, flush=True)
        print("[ES_1M_02] GENERATE_SIGNAL CALLED", file=sys.stderr, flush=True)
        print("=" * 60, file=sys.stderr, flush=True)

    if go_long(state, df):
        signal = "BUY"
    elif go_short(state, df):
        signal = "SELL"
    else:
        signal = "HOLD"

    if DEBUG_LOGGING:
        import sys

        print(f"\n[ES_1M_02] Final Signal: {signal}", file=sys.stderr, flush=True)
        print("=" * 60 + "\n", file=sys.stderr, flush=True)

    return signal


def get_signal_visibility(state, df):
    """Return list of (label, is_true) for live status display."""
    indicators = populate_indicators(df, state.params)
    return [
        ("L>EMA", indicators["low"] > indicators["ema_100"]),
        ("RSI80-90", indicators["rsi_2"] >= 80 and indicators["rsi_2"] <= 90),
        ("MACD↓", indicators["macd_hist_current"] <= indicators["macd_hist_prev"]),
    ]
