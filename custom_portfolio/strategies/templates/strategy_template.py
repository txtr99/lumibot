"""
Strategy template for `custom_portfolio/strategies/active_strategies/`.

================================================================================
REQUIRED COMPONENTS (must be present for strategy to work)
================================================================================
1. STRATEGY_CONFIG dict - defines symbol, params, brackets, time exit, sessions
2. populate_indicators(df, params) - computes all indicators, returns dict
3. go_long(state, df) - returns True when long entry conditions met
4. go_short(state, df) - returns True when short entry conditions met (or False if long-only)
5. generate_signal(state, df) - returns "BUY", "SELL", or "HOLD"
6. get_signal_visibility(state, df) - returns list of (label, is_true) tuples for live display

================================================================================
SYMBOLS
================================================================================
See `custom_portfolio/data/futures_metadata.py` (FUTURES_METADATA keys).
Examples: "ES", "MES", "NQ", "MNQ", "GC", "MGC", "6E", etc.

================================================================================
TRADING SESSIONS
================================================================================
Session enforcement restricts trading to specific time windows and enforces
TopStepX platform rules (maintenance windows, weekend blackouts).

Available sessions (defined in `custom_portfolio/strategies/portfolio_manager.py`):
- "24/7": No restrictions (trades at all times) - use for commodities like gold
- "Australia": 17:00-02:00 CT (overnight session)
- "Asia": 18:00-03:00 CT (overnight session)
- "London": 02:00-11:00 CT (European session)
- "New_York": 07:30-14:00 CT (CME regular trading hours) - use for ES, NQ

Timezone Strategy:
- **Internal calculations**: Central Time (America/Chicago) - SOURCE OF TRUTH
- **User-facing display**: Mountain Time (America/Denver) - Logs, configs, UI

Platform Rules (TopStepX - Verified 2025-11-24):
- Daily position closure: Must be flat by 15:10 CT (3:10 PM) Monday-Friday
- Trading resumes: 17:00 CT (5:00 PM) same day
- Weekend blackout: Friday 15:10 CT - Sunday 17:00 CT (no trading)
- CBOT Commodity pause: 07:45-08:30 AM CST (no orders during this window)

================================================================================
STRATEGY CONFIGURATION (STRATEGY_CONFIG)
================================================================================
- strategy_id: Leave empty to auto-use filename (recommended)
- symbol: Futures symbol from futures_metadata.py
- contracts: Number of contracts to trade (usually 1)
- params: All indicator parameters - IMPORTANT for min_bars calculation!
  * Use descriptive names ending in _length, _period, or _lookback
  * The executor auto-calculates min_bars_required from these
  * Example: "sma_length": 200 tells executor this strategy needs 200+ bars
- bracket_orders: ATR-based take profit and stop loss
  * atr_period: ATR lookback (usually 20)
  * pt_mult: profit target = ATR * pt_mult
  * sl_mult: stop loss = ATR * sl_mult
- time_exit: max_bars before forced exit
- allowed_sessions: List of session names, or ["24/7"] for no restrictions
- metadata: strategy_type and direction (for documentation/filtering)

================================================================================
INDICATOR PARAMETERS - NAMING CONVENTION (CRITICAL!)
================================================================================
The executor auto-calculates min_bars_required by scanning params for:
- Any key containing: _length, _period, lookback
- Common names: sma_fast, sma_slow, ema_fast, ema_slow, ema_medium, rsi_fast, rsi_slow

GOOD parameter names (will be detected):
- "sma_length": 200      -> detected, needs 200 bars
- "rsi_period": 14       -> detected, needs 14 bars
- "lookback_period": 252 -> detected, needs 252 bars
- "ema_slow": 100        -> detected, needs 100 bars

BAD parameter names (will NOT be detected):
- "fast": 20             -> NOT detected (use "fast_length" instead)
- "slow": 50             -> NOT detected (use "slow_length" instead)

The executor adds a 50-bar buffer to the longest period found.
Example: If your longest indicator is SMA(200), min_bars_required = 250

================================================================================
SIGNAL VISIBILITY (get_signal_visibility)
================================================================================
This function provides real-time visibility into which entry conditions are
true/false for each strategy in the live status table.

Format: Returns list of (label, is_true) tuples
- label: Short string (max ~8 chars) describing the condition
- is_true: Boolean indicating if condition is currently met

Display in live table:
- Cyan color: Condition is TRUE
- Gray color: Condition is FALSE
- Up to 5 conditions displayed per strategy

Label conventions:
- Use arrows: "RSI↓" (declining), "EMA↑" (rising)
- Use comparisons: "C>SMA200" (close above SMA 200)
- Use crosses: "RSIx70" (RSI crosses 70), "KERx.5" (KER crosses 0.5)
- Use ranges: "RSI70-80" (RSI between 70 and 80)
- Keep labels SHORT - they must fit in table columns

================================================================================
DATA STATUS COLUMN
================================================================================
The live status table shows a DATA column with format: actual/required

Colors:
- Cyan: Sufficient bars (e.g., "350/250") - strategy can calculate
- Red: Insufficient bars (e.g., "89/250") - strategy skipped
- Yellow: "NaN" - calculation produced NaN values (check indicators!)

The required bars are auto-calculated from your params - see naming convention above.

================================================================================
BEST PRACTICES
================================================================================
1. Call populate_indicators() ONCE in go_long/go_short, not multiple times
2. Use iloc[-1] for current bar, iloc[-2] for previous, iloc[-N] for N bars ago
3. For "X bars ago" comparisons, use iloc[-(N+1)] (0-indexed)
4. For crosses, compare current vs previous: prev >= threshold and current < threshold
5. Always handle potential NaN in custom indicator functions (return 0 or 0.5 as default)
6. Keep get_signal_visibility() conditions in same order as entry logic for clarity
7. Test with sufficient historical data before live trading

================================================================================
DEBUG LOGGING (for troubleshooting signal generation)
================================================================================
Enable with: DEBUG_INDICATORS=true python run_portfolio.py --mode backtest

When enabled, strategies log indicator values and condition checks:
- [INDICATOR] strategy_id: indicator_name=value, ...
- [SIGNAL-CHECK] strategy_id: condition1=True/False, ...

This is controlled by state.debug_indicators flag set by the executor.
To add debug logging to your strategy, check getattr(state, "debug_indicators", False).

================================================================================
"""

import logging

import pandas_ta as ta  # noqa: F401 - commonly used, import at top

# Logger for debug output (controlled by DEBUG_INDICATORS env var)
_logger = logging.getLogger(__name__)

STRATEGY_CONFIG = {
    "strategy_id": "",  # Leave empty to auto-fill from filename
    "symbol": "MES",  # Symbol from futures_metadata.py
    "contracts": 1,
    "params": {
        # IMPORTANT: Use _length, _period, or _lookback suffixes!
        # These are auto-detected for min_bars calculation
        "sma_fast_length": 20,
        "sma_slow_length": 50,
        "rsi_length": 14,
    },
    "bracket_orders": {
        "atr_period": 20,  # ATR lookback period
        "pt_mult": 2.0,  # profit target = ATR * pt_mult
        "sl_mult": 1.0,  # stop loss = ATR * sl_mult
    },
    "time_exit": {
        "max_bars": 120,  # Max bars in trade before forced exit
    },
    "allowed_sessions": ["New_York"],  # Or ["24/7"] for no restrictions
    "metadata": {
        "strategy_type": "trend_following",  # trend_following, mean_reversion, breakout
        "direction": "both",  # long, short, or both
    },
}


def populate_indicators(df, params, debug=False, strategy_id=""):
    """
    Compute and return a dict of ALL indicators needed for entry logic.

    IMPORTANT:
    - Calculate each indicator ONCE here, not in go_long/go_short
    - Use iloc[-1] for current bar value
    - Use iloc[-N] for N-1 bars ago (0-indexed)
    - Include both current and historical values needed for comparisons

    Args:
        df: DataFrame with OHLCV data (columns: open, high, low, close, volume)
        params: Strategy parameters from STRATEGY_CONFIG["params"]
        debug: If True, log indicator values (controlled by DEBUG_INDICATORS env var)
        strategy_id: Strategy identifier for debug logging

    Returns:
        dict of indicator values
    """
    closes = df["close"]
    # highs = df["high"]   # Uncomment if needed
    # lows = df["low"]     # Uncomment if needed
    # opens = df["open"]   # Uncomment if needed

    # Get parameters (with defaults matching STRATEGY_CONFIG)
    sma_fast_len = int(params.get("sma_fast_length", 20))
    sma_slow_len = int(params.get("sma_slow_length", 50))
    rsi_len = int(params.get("rsi_length", 14))

    # Calculate indicators
    sma_fast = closes.rolling(sma_fast_len).mean()
    sma_slow = closes.rolling(sma_slow_len).mean()
    rsi = ta.rsi(closes, length=rsi_len)

    indicators = {
        # Current values
        "sma_fast": sma_fast.iloc[-1],
        "sma_slow": sma_slow.iloc[-1],
        "rsi_current": rsi.iloc[-1],
        # Historical values for comparisons (if needed)
        "rsi_prev": rsi.iloc[-2],
        "sma_fast_prev": sma_fast.iloc[-2],
    }

    # Debug logging if enabled
    if debug:
        _logger.info(
            f"[INDICATOR] {strategy_id}: sma_fast={indicators['sma_fast']:.4f}, "
            f"sma_slow={indicators['sma_slow']:.4f}, rsi={indicators['rsi_current']:.2f}"
        )

    return indicators


def go_long(state, df):
    """
    Return True when conditions favor going long.

    IMPORTANT:
    - Call populate_indicators() to get all indicator values
    - Combine conditions with 'and' for all-must-be-true logic
    - Document the entry logic in the docstring

    Entry conditions (example):
    - SMA(20) > SMA(50) (fast above slow - uptrend)
    - RSI(14) > 50 (momentum confirmation)
    """
    debug = getattr(state, "debug_indicators", False)
    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)

    cond_sma = indicators["sma_fast"] > indicators["sma_slow"]
    cond_rsi = indicators["rsi_current"] > 50
    condition = cond_sma and cond_rsi

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: sma_fast>slow={cond_sma}, rsi>50={cond_rsi} → go_long={condition}"
        )

    return condition


def go_short(state, df):
    """
    Return True when conditions favor going short.

    For long-only strategies, simply return False.
    """
    debug = getattr(state, "debug_indicators", False)
    indicators = populate_indicators(df, state.params, debug=debug, strategy_id=state.strategy_id)

    cond_sma = indicators["sma_fast"] < indicators["sma_slow"]
    cond_rsi = indicators["rsi_current"] < 50
    condition = cond_sma and cond_rsi

    if debug:
        _logger.info(
            f"[SIGNAL-CHECK] {state.strategy_id}: sma_fast<slow={cond_sma}, rsi<50={cond_rsi} → go_short={condition}"
        )

    return condition


def generate_signal(state, df):
    """
    Required entrypoint. Returns: "BUY", "SELL", or "HOLD".

    DO NOT MODIFY this function's structure - the executor expects this exact interface.
    """
    if go_long(state, df):
        return "BUY"
    if go_short(state, df):
        return "SELL"
    return "HOLD"


def get_signal_visibility(state, df):
    """
    Return list of (label, is_true) tuples for live status display.

    This function provides real-time visibility into entry conditions.
    Each tuple shows a condition label and whether it's currently true.

    IMPORTANT:
    - Keep labels SHORT (max ~8 chars) - they must fit in table columns
    - Match conditions to your go_long/go_short logic
    - Use consistent label conventions:
      * Arrows: "RSI↓" (declining), "EMA↑" (rising)
      * Comparisons: "C>SMA50" (close > SMA 50)
      * Crosses: "RSIx70" (RSI crosses 70)
      * Ranges: "RSI50-70" (RSI between 50 and 70)
    - Max 5 conditions will be displayed (but you can return more)

    Returns:
        List of (label: str, is_true: bool) tuples
    """
    indicators = populate_indicators(df, state.params)

    return [
        ("F>S", indicators["sma_fast"] > indicators["sma_slow"]),
        ("RSI>50", indicators["rsi_current"] > 50),
        ("RSI↑", indicators["rsi_current"] > indicators["rsi_prev"]),
    ]
