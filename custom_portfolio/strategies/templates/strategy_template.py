"""
Strategy template for `custom_portfolio/strategies/active_strategies/`.

SYMBOLS
-------
See `custom_portfolio/data/futures_metadata.py` (FUTURES_METADATA keys).
Examples: "ES", "NQ", "MGC", "6E", etc.

TRADING SESSIONS (Updated in Phase 7)
--------------------------------------
Session enforcement restricts trading to specific time windows and enforces
TopStepX platform rules (maintenance windows, weekend blackouts).

Available sessions (defined in `custom_portfolio/strategies/portfolio_manager.py`):
- TRADING_SESSIONS: Full session definitions with start/stop times
- SESSION_ABBREVIATIONS: Session name to abbreviation mapping

Session Names:
- "24/7": No restrictions (trades at all times)
- "Australia": 17:00-02:00 CT (overnight session)
- "Asia": 18:00-03:00 CT (overnight session)
- "London": 02:00-11:00 CT (European session)
- "New_York": 07:30-14:00 CT (CME regular trading hours)

Timezone Strategy:
- **Internal calculations**: Central Time (America/Chicago) - SOURCE OF TRUTH
- **User-facing display**: Mountain Time (America/Denver) - Logs, configs, UI
- All session times in TRADING_SESSIONS are in Central Time
- Platform automatically handles DST transitions

Configuration:
- Set `allowed_sessions` in STRATEGY_CONFIG (e.g., ["New_York"])
- Omit `allowed_sessions` for 24/7 trading (no restrictions)
- Multiple sessions: ["Australia", "Asia", "London", "New_York"] for extended hours

Enforcement Control:
- Backtest: Controlled by ENFORCE_SESSIONS_IN_BACKTEST env var (default: true)
- Live: Always enforced (TopStepX compliance requirement)

Platform Rules (TopStepX):
- Daily maintenance: 14:00-16:00 CT (no trading)
- Weekend blackout: Friday 14:00 CT - Sunday 16:00 CT (no trading)
- These override session times (platform layer wins)

STRATEGY CONFIGURATION
----------------------
strategy_id: leave blank to auto-use filename (sanitized), or set explicitly;
             must be unique across active_strategies/.
Direction: label only (`long` | `short` | `both`); does not affect logic.
Brackets: always on; TP/SL distances use ATR * pt_mult/sl_mult.
"""

STRATEGY_CONFIG = {
    "strategy_id": "",  # leave empty to auto-fill from filename
    "symbol": "ES",
    "contracts": 1,
    "params": {
        # Core signal params
        "fast": 20,
        "slow": 50,
    },
    # Bracket orders are always enabled; adjust ATR period and TP/SL multiples here
    "bracket_orders": {
        "atr_period": 20,
        "pt_mult": 2.0,  # profit target = ATR * pt_mult
        "sl_mult": 1.0,  # stop loss   = ATR * sl_mult
    },
    # Time-based exit
    "time_exit": {
        "max_bars": 120,  # bars in trade before forced exit
    },
    # Optional sessions (omit for 24/7). Example: ["New_York"]
    "allowed_sessions": ["New_York"],
    "metadata": {
        "strategy_type": "trend_following",
        "direction": "both",  # long | short | both
    },
}


def populate_indicators(df, params):
    """
    Compute and return a dict of indicators (ensure each is calculated once).
    Uses the most recent closed bar (iloc[-1]).
    """
    closes = df["close"]
    fast_len = int(params.get("fast", 20))
    slow_len = int(params.get("slow", 50))
    return {
        "fast_ma": closes.rolling(fast_len).mean().iloc[-1],
        "slow_ma": closes.rolling(slow_len).mean().iloc[-1],
    }


def go_long(state, df):
    """
    Return True when conditions favor going long.
    """
    indicators = populate_indicators(df, state.params)
    return indicators["fast_ma"] > indicators["slow_ma"]


def go_short(state, df):
    """
    Return True when conditions favor going short.
    """
    indicators = populate_indicators(df, state.params)
    return indicators["fast_ma"] < indicators["slow_ma"]


def generate_signal(state, df):
    """
    Required entrypoint. Returns: "BUY", "SELL", or "HOLD".
    """
    if go_long(state, df):
        return "BUY"
    if go_short(state, df):
        return "SELL"
    return "HOLD"
