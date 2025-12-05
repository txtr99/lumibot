"""
===========================================================================================
RUN PORTFOLIO - Multi-Strategy Portfolio Runner for LumiBot
===========================================================================================

OVERVIEW
--------
This script is the main entry point for running a dynamic multi-strategy trading portfolio.
It automatically discovers, validates, and executes multiple trading strategies concurrently
using LumiBot's backtesting or live trading infrastructure.

LOCATION IN PROJECT
-------------------
File: custom_portfolio/strategies/run_portfolio.py
All custom portfolio code lives in the custom_portfolio/ directory to keep it separate
from core LumiBot files.

USAGE
-----
Validation Mode (default):
    python custom_portfolio/strategies/run_portfolio.py --mode validate

Backtesting:
    python custom_portfolio/strategies/run_portfolio.py --mode backtest
    Note: Start/end dates are read from .env file (BACKTESTING_START, BACKTESTING_END)

Live Trading:
    python custom_portfolio/strategies/run_portfolio.py --mode live

Archive Strategies:
    python custom_portfolio/strategies/run_portfolio.py --mode archive

SYSTEM ARCHITECTURE
-------------------
This system consists of the following components:

1. RUNNER (THIS FILE)
   - Location: custom_portfolio/strategies/run_portfolio.py
   - Role: Entry point that creates broker, data source, and PortfolioManager
   - For backtesting: Wraps PortfolioManager in a LumiBot Strategy for backtest execution

2. PORTFOLIO MANAGER
   - Location: custom_portfolio/strategies/portfolio_manager.py
   - Role: Discovers and validates strategy files, creates MultiStrategyExecutorEnhanced
   - Dependencies:
     * custom_portfolio.multi_strategy_executor_enhanced.MultiStrategyExecutorEnhanced

3. MULTI-STRATEGY EXECUTOR
   - Location: custom_portfolio/multi_strategy_executor_enhanced.py
   - Role: Orchestrates execution of all strategies at each time step
   - Execution Model: Round-robin (sequential, not parallel)
   - Process:
     a. Fetch market data ONCE for all symbols
     b. For each strategy: evaluate logic, generate signals, create orders
     c. Submit all orders with rate limiting
   - Dependencies:
     * custom_portfolio.tools.strategy_state.StrategyState
     * custom_portfolio.tools.shared_data_manager.SharedDataManager
     * custom_portfolio.tools.strategy_attribution.StrategyAttribution
     * custom_portfolio.tools.global_rate_limiter.GlobalRateLimiter

4. CUSTOM TOOLS
   - Location: custom_portfolio/tools/
   - Files:
     * strategy_state.py - Manages state for each strategy instance
     * shared_data_manager.py - Caches and shares market data across strategies
     * strategy_attribution.py - Tracks performance attribution per strategy
     * global_rate_limiter.py - Prevents broker API rate limit violations
     * base_strategy_logic.py - Base class for strategy logic implementations

5. STRATEGY FILES
   - Location: custom_portfolio/strategies/active_strategies/
   - Format: Python files containing strategy configuration
   - Each file must return a dict with:
     * 'symbol': str (e.g., 'ES', 'NQ')
     * 'qty': int (contract quantity)
     * 'logic_class': BaseStrategyLogic subclass
     * 'params': dict (strategy-specific parameters)
     * 'sessions': list (optional, e.g., ['RTH', 'ETH'])

6. LUMIBOT CORE (UNMODIFIED)
   - This system does NOT modify core LumiBot files
   - Uses standard LumiBot components:
     * lumibot.backtesting.DataBentoDataBacktesting
     * lumibot.strategies.Strategy
     * lumibot.entities (Asset, Order, TradingFee)
     * lumibot.brokers (Alpaca, IBKR, etc.)

DATA FLOW (Backtesting)
-----------------------
1. run_portfolio.py creates a PortfolioStrategy (LumiBot Strategy subclass)
2. PortfolioStrategy.initialize() creates PortfolioManager
3. PortfolioManager discovers strategies in active_strategies/ folder
4. PortfolioManager creates MultiStrategyExecutorEnhanced with all strategies
5. On each trading iteration (every 1 minute):
   a. PortfolioStrategy.on_trading_iteration() calls portfolio_manager.run_iteration()
   b. MultiStrategyExecutorEnhanced fetches data for all symbols ONCE
   c. For each strategy: run logic, generate signal, create order
   d. Submit all orders with rate limiting
6. At end: PortfolioManager.get_performance_report() shows attribution

TROUBLESHOOTING
---------------
If the backtest hangs or fails:

1. CHECK STRATEGY FILES
   - Location: custom_portfolio/strategies/active_strategies/
   - Validate with: python run_portfolio.py --mode validate

2. CHECK DATA SOURCE
   - For DataBento: Ensure DATABENTO_API_KEY is in .env file
   - Check date range is valid (not requesting future data)

3. CHECK IMPORTS
   - All custom code imports from custom_portfolio.* (not lumibot.*)
   - Example: from custom_portfolio.tools.strategy_state import StrategyState

4. CHECK LUMIBOT CORE
   - This system should NOT modify core LumiBot files
   - If issues persist, check git diff on lumibot/ directory

5. CHECK EXECUTION MODEL
   - Strategies run SEQUENTIALLY (round-robin), not in parallel threads
   - All strategies share the same broker and data source

ADDING NEW STRATEGIES
---------------------
1. Create new Python file in custom_portfolio/strategies/active_strategies/
2. Implement logic class inheriting from BaseStrategyLogic
3. File must return dict with required keys (see STRATEGY FILES above)
4. Validate: python run_portfolio.py --mode validate
5. Test: python run_portfolio.py --mode backtest --start YYYY-MM-DD --end YYYY-MM-DD

ARCHIVING STRATEGIES
--------------------
To rotate strategies:
1. Archive current: python run_portfolio.py --mode archive
2. Remove old files from active_strategies/
3. Add new strategy files
4. Validate before running

Author: LumiBot Multi-Strategy Team
Date: 2025-11-19
===========================================================================================
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from datetime import time as dtime
from functools import lru_cache
from pathlib import Path

import numpy as np

# Add repository root to path for custom_portfolio imports
# Path: run_portfolio.py -> strategies/ -> custom_portfolio/ -> repo_root/
repo_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(repo_root))

import matplotlib  # noqa: E402
import pandas as pd  # noqa: E402

matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from custom_portfolio.strategies.portfolio_manager import PortfolioManager  # noqa: E402
from lumibot.backtesting import (  # noqa: E402
    DataBentoDataBacktestingPandas,
)
from lumibot.entities import TradingFee  # noqa: E402
from lumibot.strategies import Strategy  # noqa: E402

# Global flag for interrupt handling
_interrupted = False
_run_mode = "backtest"  # "backtest" or "live" - set by run_backtest/run_live

# Trend sentiment cache (refreshes every 5 minutes)
_trend_sentiment_cache = {"data": None, "timestamp": 0}
_TREND_REFRESH_SECONDS = 300  # 5 minutes

# Market closed detection settings
_MARKET_CLOSED_ERROR_THRESHOLD = 3  # Consecutive errors before pausing
_MARKET_CLOSED_PAUSE_MINUTES = 30  # Minutes to pause when market detected as closed
_MARKET_CLOSED_ERROR_PATTERNS = [
    "market is currently closed",
    "market closed",
    "outside trading hours",
]


def _is_market_closed_error(error_msg: str) -> bool:
    """Check if an error message indicates the market is closed."""
    if not error_msg:
        return False
    lowered = error_msg.lower()
    return any(pattern in lowered for pattern in _MARKET_CLOSED_ERROR_PATTERNS)


def _get_market_closed_errors_from_executor(executor) -> list[str]:
    """
    Check executor's strategies for recent market closed errors.

    Returns a list of error messages that match market closed patterns.
    """
    errors = []
    if executor is None:
        return errors

    for state in executor.strategies:
        # Check for recent rejected orders with market closed errors
        # The executor stores last_order_error on state (if available)
        last_error = getattr(state, "last_order_error", None)
        if last_error and _is_market_closed_error(last_error):
            errors.append(last_error)

    return errors


def get_trend_sentiment_table(symbols: list[str], force_refresh: bool = False) -> str:
    """
    Get color-coded trend sentiment table for display.

    Args:
        symbols: List of symbols to analyze (e.g., ["MES", "MNQ", "MGC"])
        force_refresh: Force refresh even if cache is valid

    Returns:
        Formatted string with color-coded table
    """
    import time as time_module

    from custom_portfolio.tools.llm_trend_sentiment import build_vibes

    global _trend_sentiment_cache

    now = time_module.time()
    cache_age = now - _trend_sentiment_cache["timestamp"]

    # Use cache if valid and not forcing refresh
    if not force_refresh and _trend_sentiment_cache["data"] and cache_age < _TREND_REFRESH_SECONDS:
        vibes = _trend_sentiment_cache["data"]
    else:
        # Fetch fresh data (suppress fetch messages)
        import io
        import sys

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            fresh_vibes, _ = build_vibes(
                symbols,
                lookback_minutes=480,  # 8 hours - enough for MGC's limited overnight bars
                use_sim=False,
                latency_minutes=45,  # Databento historical can lag 30-45 min
                dataset="GLBX.MDP3",
                schema="ohlcv-1m",
            )
        finally:
            sys.stdout = old_stdout

        # Check if we got real data
        has_real_data = any(v.get("vibe") != "no data" for v in fresh_vibes.values())

        if has_real_data:
            # Fresh data is good - cache it
            vibes = fresh_vibes
            _trend_sentiment_cache["data"] = vibes
            _trend_sentiment_cache["timestamp"] = now
        elif _trend_sentiment_cache["data"]:
            # Fresh fetch failed but we have old cached data - use that
            old_has_data = any(v.get("vibe") != "no data" for v in _trend_sentiment_cache["data"].values())
            if old_has_data:
                vibes = _trend_sentiment_cache["data"]
                # Don't update timestamp - shows how old the data really is
            else:
                # Old cache is also bad - use fresh (failed) and update timestamp to prevent spam
                vibes = fresh_vibes
                _trend_sentiment_cache["data"] = vibes
                _trend_sentiment_cache["timestamp"] = now
        else:
            # No cache at all - use fresh (failed) data
            vibes = fresh_vibes
            _trend_sentiment_cache["data"] = vibes
            _trend_sentiment_cache["timestamp"] = now

    # Build color-coded table
    # ANSI colors
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"
    RESET = "\033[0m"

    lines = []
    lines.append(f"{CYAN}┌────────┬────────┬──────┬─────────────┐{RESET}")
    lines.append(f"{CYAN}│ Symbol │ Trend  │ Vol  │ Vibe        │{RESET}")
    lines.append(f"{CYAN}├────────┼────────┼──────┼─────────────┤{RESET}")

    for symbol in symbols:
        v = vibes.get(symbol, {"trend": "?", "vol": "?", "vibe": "no data"})
        trend = v["trend"]
        vol = v["vol"]
        vibe = v["vibe"]

        # Color trend
        if trend == "up":
            trend_col = f"{GREEN}{trend.center(6)}{RESET}"
        elif trend == "down":
            trend_col = f"{RED}{trend.center(6)}{RESET}"
        else:
            trend_col = f"{GRAY}{trend.center(6)}{RESET}"

        # Color vol
        if vol == "high":
            vol_col = f"{RED}{vol.center(4)}{RESET}"
        elif vol == "low":
            vol_col = f"{GRAY}{vol.center(4)}{RESET}"
        else:
            vol_col = f"{YELLOW}{vol.center(4)}{RESET}"

        # Color vibe
        vibe_colors = {
            "surging": GREEN,
            "climbing": GREEN,
            "calm rise": GREEN,
            "crashing": RED,
            "sliding": RED,
            "drifting": YELLOW,
            "choppy": YELLOW,
            "ranging": GRAY,
            "quiet": GRAY,
        }
        vibe_color = vibe_colors.get(vibe, GRAY)
        vibe_col = f"{vibe_color}{vibe.ljust(11)}{RESET}"

        row = f"{CYAN}│{RESET} {symbol:<6} {CYAN}│{RESET} {trend_col} {CYAN}│{RESET}"
        row += f" {vol_col} {CYAN}│{RESET} {vibe_col} {CYAN}│{RESET}"
        lines.append(row)

    lines.append(f"{CYAN}└────────┴────────┴──────┴─────────────┘{RESET}")

    # Add cache age indicator (recalculate from current timestamp)
    actual_age = time_module.time() - _trend_sentiment_cache["timestamp"]
    cache_mins = int(actual_age // 60)
    cache_secs = int(actual_age % 60)
    if cache_mins > 0:
        lines.append(f"{GRAY}  (data age: {cache_mins}m {cache_secs}s){RESET}")
    else:
        lines.append(f"{GRAY}  (data age: {cache_secs}s){RESET}")

    return "\n".join(lines)


def signal_handler(signum, frame):
    """Handle CTRL-C gracefully with mode-specific messages."""
    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    global _interrupted
    _interrupted = True
    print("")

    if _run_mode == "live":
        print(TF.warning("Interrupt received (CTRL-C), stopping live trading..."))
        print("")
        print(TF.error("⚠️  IMPORTANT: Don't forget to monitor open positions!"))
        print(TF.error("    Check the exchange and manually close any open positions."))
        print("")
    else:
        print(TF.warning("Interrupt received (CTRL-C), stopping backtest..."))

    sys.exit(1)


def setup_logging(log_level: str = "INFO") -> None:
    """
    Set up logging configuration.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    # Main log file (all levels)
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("portfolio_runner.log")],
    )

    # Separate error-only log file (always captures ERROR and CRITICAL)
    error_handler = logging.FileHandler("portfolio_errors.log")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(error_handler)


def _env_flag(name: str, default: bool = True) -> bool:
    """Parse a boolean-ish environment variable with a default (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _snapshot_config():
    enable_snapshots = os.environ.get("PORTFOLIO_SNAPSHOTS", "true").lower() == "true"
    try:
        max_snapshots = int(os.environ.get("PORTFOLIO_MAX_SNAPSHOTS", "200000"))
    except Exception:
        print("Invalid PORTFOLIO_MAX_SNAPSHOTS; defaulting to 200000")
        max_snapshots = 200000
    if max_snapshots <= 0:
        # Treat non-positive as disabling snapshots
        enable_snapshots = False
        max_snapshots = 1
    timestep = "minute"
    return enable_snapshots, max_snapshots, timestep


def _simulate_fills_config():
    """Read DRY_RUN env to control simulated fills (default true for backtest)."""
    return os.environ.get("DRY_RUN", "true").lower() == "true"


def _live_dry_run_config():
    """
    Read DRY_RUN env for live trading mode.

    In LIVE mode, the default is FALSE (real trading).
    User must explicitly set DRY_RUN=true to enable dry-run mode.

    Returns:
        bool: True if dry-run mode enabled, False for real trading
    """
    raw = os.environ.get("DRY_RUN", "").strip().lower()
    # Only enable dry-run if explicitly set to true
    return raw in ("true", "1", "yes", "y", "on")


def _capital_config():
    """Shared starting capital for the portfolio (applied to all strategies for attribution)."""
    try:
        return float(os.environ.get("TOTAL_INITIAL_CAPITAL", "150000"))
    except Exception:
        return 150000.0


def _debug_config():
    """Enable verbose portfolio debug logs (prefetch + iterations)."""
    return os.environ.get("PORTFOLIO_DEBUG", "true").lower() == "true"


def _qa_disabled():
    """Check if backtest data QA is disabled via environment toggle."""
    return os.environ.get("DISABLE_BACKTEST_DATA_QA", "false").lower() in ("1", "true", "yes", "y", "on")


def _qa_timezone():
    """Timezone to display QA results in (default UTC)."""
    return os.environ.get("QA_TZ", "UTC")


DAILY_MISSING_TOLERANCE_MINUTES = 10  # allow a small buffer before flagging a day


def _maintenance_window_utc():
    """Return daily maintenance window in UTC (approximate CME Globex daily break)."""
    return dtime(hour=21, minute=0), dtime(hour=22, minute=0)  # 60-minute window


def _qa_json_path():
    """Return QA JSON output path (env override)."""
    return Path(os.environ.get("QA_JSON_PATH", "logs/data_qa_report.json"))


@lru_cache(maxsize=1)
def _cme_holiday_calendar():
    """Return a pandas holiday calendar covering major CME US holidays (approximate)."""
    from pandas.tseries.holiday import (
        AbstractHolidayCalendar,
        GoodFriday,
        Holiday,
        USLaborDay,
        USMemorialDay,
        USPresidentsDay,
        USThanksgivingDay,
        nearest_workday,
    )

    class _CMEHolidayCalendar(AbstractHolidayCalendar):
        rules = [
            Holiday("NewYearsDay", month=1, day=1, observance=nearest_workday),
            USPresidentsDay,
            USMemorialDay,
            USLaborDay,
            USThanksgivingDay,
            GoodFriday,
            Holiday("IndependenceDay", month=7, day=4, observance=nearest_workday),
            Holiday("Christmas", month=12, day=25, observance=nearest_workday),
        ]

    return _CMEHolidayCalendar()


def _compute_expected_minutes_totals(expected_per_day: dict):
    """Sum expected minutes across all days from the per-day map."""
    eth_total = sum(v[0] for v in expected_per_day.values())
    rth_total = sum(v[1] for v in expected_per_day.values())
    return eth_total, rth_total


def _expected_minutes_by_day(start_dt, end_dt, eth_minutes_per_day: int = 1335, rth_minutes_per_day: int = 390):
    """
    Expected minutes per day (ETH/RTH) with weekend/holiday skips, maintenance deduction,
    and clipping to the backtest window (handles partial first/last day).

    Rules:
    - Sunday: only count trading after maintenance window ends (no pre-maintenance Sunday trading).
    - Monday-Thursday: full ETH minus maintenance overlap.
    - Friday: only count trading up to maintenance start (does not reopen after).
    These match CME Globex behavior for futures (data source is CME via Databento).
    """
    start_ts = pd.Timestamp(start_dt)
    end_ts = pd.Timestamp(end_dt)
    days = pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")
    cal = _cme_holiday_calendar()
    holidays = set(pd.to_datetime(cal.holidays(start=days.min(), end=days.max())).date)
    maint_start, maint_end = _maintenance_window_utc()

    def _overlap_minutes(a_start, a_end, b_start, b_end):
        start_o = max(a_start, b_start)
        end_o = min(a_end, b_end)
        if end_o <= start_o:
            return 0
        return int((end_o - start_o).total_seconds() // 60)

    expected = {}
    for day in days:
        day_date = day.date()
        if day.weekday() >= 5 or day_date in holidays:
            expected[day_date] = (0, 0)
            continue

        day_start = pd.Timestamp(day_date)
        day_end = day_start + pd.Timedelta(days=1)
        clip_start = max(day_start, start_ts)
        clip_end = min(day_end, end_ts)
        if clip_end <= clip_start:
            expected[day_date] = (0, 0)
            continue

        weekday = day.weekday()
        # Sunday (6): trade only after maintenance ends
        if weekday == 6:
            session_start = pd.Timestamp(datetime.combine(day_date, maint_end))
            clip_start = max(clip_start, session_start)
            total_minutes = int(max(0, (clip_end - clip_start).total_seconds() // 60))
            maint_minutes = 0  # already enforced by start time
        # Friday (4): trade only until maintenance starts
        elif weekday == 4:
            session_end = pd.Timestamp(datetime.combine(day_date, maint_start))
            clip_end = min(clip_end, session_end)
            if clip_end <= clip_start:
                expected[day_date] = (0, 0)
                continue
            total_minutes = int((clip_end - clip_start).total_seconds() // 60)
            maint_minutes = 0  # we cut off before maintenance
        else:
            total_minutes = int((clip_end - clip_start).total_seconds() // 60)
            maint_start_dt = pd.Timestamp(datetime.combine(day_date, maint_start))
            maint_end_dt = pd.Timestamp(datetime.combine(day_date, maint_end))
            maint_minutes = _overlap_minutes(clip_start, clip_end, maint_start_dt, maint_end_dt)

        expected_eth = max(0, total_minutes - maint_minutes)
        expected_rth = max(0, min(rth_minutes_per_day, total_minutes) - maint_minutes)
        expected[day_date] = (expected_eth, expected_rth)
    return expected


def _gap_ranges_from_missing(missing_index, limit: int = 5):
    """Return up to `limit` gap ranges from a missing DatetimeIndex."""
    if len(missing_index) == 0:
        return []
    missing_sorted = missing_index.sort_values()
    ranges = []
    start = missing_sorted[0]
    prev = start
    for ts in missing_sorted[1:]:
        if (ts - prev) > pd.Timedelta(minutes=1):
            ranges.append((start, prev))
            if len(ranges) >= limit:
                break
            start = ts
        prev = ts
    if len(ranges) < limit:
        ranges.append((start, prev))
    return ranges[:limit]


def _window_df(df, start_dt, end_dt):
    """Slice df between start_dt and end_dt, aligning timezones if needed."""
    if df is None or df.empty:
        return df
    idx = df.index
    start_ts = pd.Timestamp(start_dt)
    end_ts = pd.Timestamp(end_dt)
    if hasattr(idx, "tz") and idx.tz is not None:
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize(idx.tz)
            end_ts = end_ts.tz_localize(idx.tz)
        else:
            start_ts = start_ts.tz_convert(idx.tz)
            end_ts = end_ts.tz_convert(idx.tz)
    elif start_ts.tzinfo is not None:
        # If index is naive but start_dt is tz-aware, drop tz to compare
        start_ts = start_ts.tz_localize(None)
        end_ts = end_ts.tz_localize(None)
    return df.loc[(idx >= start_ts) & (idx <= end_ts)]


def _run_backtest_data_qa(data_source, start_dt, end_dt, qa_tz: str = "UTC"):
    """
    Emit a data quality report for prefetched data (backtest only).

    - Read-only: uses existing pandas_data; no API calls, no cache mutation.
    - Reports per-symbol coverage vs ETH (22.25h) and RTH (6.5h) baselines.
    - Highlights top missing-minute gaps.
    """
    store = getattr(data_source, "pandas_data", None)
    if not store or not isinstance(store, dict):
        print("[DATA-QA] No pandas_data store available; skipping QA")
        return

    expected_per_day = _expected_minutes_by_day(start_dt, end_dt)
    total_days_expected_eth, total_days_expected_rth = _compute_expected_minutes_totals(expected_per_day)
    qa_records = []

    print("[DATA-QA] Starting backtest data quality report")
    print(f"[DATA-QA] Window: {start_dt} -> {end_dt} | QA_TZ={qa_tz}")
    print(
        f"[DATA-QA] Baseline minutes: ETH/day=1335, RTH/day=390 | "
        f"ETH total={total_days_expected_eth}, RTH total={total_days_expected_rth}"
    )

    maint_start, maint_end = _maintenance_window_utc()

    for key, data_obj in store.items():
        symbol = None
        try:
            asset = key[0] if isinstance(key, tuple) else key
            symbol = getattr(asset, "symbol", "UNKNOWN")
            df = getattr(data_obj, "df", None)
            if df is None or df.empty:
                print(f"[DATA-QA] {symbol}: no data (empty)")
                continue
            windowed = _window_df(df, start_dt, end_dt)
            if windowed is None or windowed.empty:
                print(f"[DATA-QA] {symbol}: no data in window")
                continue

            idx = windowed.index.sort_values().unique()
            if qa_tz and hasattr(idx, "tz") and idx.tz is not None:
                windowed = windowed.copy()
                windowed.index = windowed.index.tz_convert(qa_tz)
                idx = windowed.index.sort_values().unique()

            observed_minutes = len(idx)
            first_ts = idx[0]
            last_ts = idx[-1]

            observed_minute_count = len(idx)
            expected_eth = total_days_expected_eth
            expected_rth = total_days_expected_rth
            coverage_eth = (observed_minute_count / expected_eth * 100) if expected_eth else 0.0
            coverage_rth = (observed_minute_count / expected_rth * 100) if expected_rth else 0.0

            # Filter out maintenance window for gap analysis and per-day counts
            times = idx.time
            mask = (times < maint_start) | (times >= maint_end)
            idx_no_maint = idx[mask]

            # Per-day coverage (exclude maintenance window)
            # Per-day coverage (exclude maintenance window). Use pandas value_counts then map keys to date for lookup.
            per_day_counts = idx_no_maint.normalize().value_counts().to_dict()
            per_day_counts = {ts.date(): count for ts, count in per_day_counts.items()}

            # Gap detection (numpy delta; tiny loop only for samples); ignore days with expected=0
            missing_count = 0
            gap_ranges = []
            if len(idx_no_maint) >= 2:
                arr = idx_no_maint
                delta_min = (arr[1:].asi8 - arr[:-1].asi8) // 60_000_000_000  # ns -> minutes
                day_arr = np.array([ts.date() for ts in arr])
                same_day = day_arr[1:] == day_arr[:-1]
                exp_arr = np.fromiter(
                    (expected_per_day.get(d, (0, 0))[0] for d in day_arr[1:]), dtype=int, count=len(day_arr) - 1
                )
                gap_mask = (delta_min > 1) & same_day & (exp_arr > 0)
                if gap_mask.any():
                    missing_count = int(np.sum(delta_min[gap_mask] - 1))
                    gap_indices = np.nonzero(gap_mask)[0]
                    for i in gap_indices:
                        start_ts = arr[i] + pd.Timedelta(minutes=1)
                        end_ts = arr[i + 1] - pd.Timedelta(minutes=1)
                        gap_len = int(delta_min[i] - 1)
                        gap_ranges.append((start_ts, end_ts, gap_len))
            missing = missing_count

            flagged_days = []
            for d, (exp_eth_day, _) in expected_per_day.items():
                observed_day = per_day_counts.get(d, 0)
                if exp_eth_day == 0:
                    continue
                cov = observed_day / exp_eth_day
                # Allow a small tolerance before flagging
                if observed_day + DAILY_MISSING_TOLERANCE_MINUTES < exp_eth_day:
                    flagged_days.append((d, observed_day, exp_eth_day, cov))

            print(
                f"[DATA-QA] {symbol}: rows={observed_minutes} minutes={observed_minute_count} "
                f"coverage_eth={coverage_eth:.1f}% coverage_rth={coverage_rth:.1f}% "
                f"first={first_ts} last={last_ts}"
            )
            if missing == 0:
                print(f"[DATA-QA] {symbol}: no missing minutes detected in window (excl. maintenance)")
            else:
                print(f"[DATA-QA] {symbol}: missing_minutes={missing} (excl. maintenance); sample gaps:")
                for start_gap, end_gap, gap_len in gap_ranges:
                    print(f"           gap {start_gap} -> {end_gap} ({gap_len} mins)")
            if flagged_days:
                print(f"[DATA-QA] {symbol}: days with <95% ETH coverage (excl. maintenance):")
                for d, obs, exp, cov in flagged_days:
                    print(f"           {d}: observed={obs} expected={exp} coverage={cov:.1%}")
            qa_records.append(
                {
                    "symbol": symbol,
                    "rows": int(observed_minutes),
                    "minutes": int(observed_minute_count),
                    "coverage_eth_pct": coverage_eth,
                    "coverage_rth_pct": coverage_rth,
                    "first_ts": str(first_ts),
                    "last_ts": str(last_ts),
                    "missing_minutes": int(missing),
                    "gaps": [
                        {"start": str(start_gap), "end": str(end_gap), "minutes": int(gap_len)}
                        for start_gap, end_gap, gap_len in gap_ranges
                    ],
                    "flagged_days": [
                        {"date": str(d), "observed": int(obs), "expected": int(exp), "coverage": cov}
                        for d, obs, exp, cov in flagged_days
                    ],
                }
            )
        except Exception as e:
            print(f"[DATA-QA] {symbol if symbol else key}: QA error: {e}")
            qa_records.append({"symbol": symbol if symbol else str(key), "error": str(e)})

    # Export QA summary to JSON for downstream analysis
    try:
        qa_path = _qa_json_path()
        qa_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "window": {"start": str(start_dt), "end": str(end_dt), "tz": qa_tz},
            "baseline": {
                "eth_per_day": 1335,
                "rth_per_day": 390,
                "eth_total": total_days_expected_eth,
                "rth_total": total_days_expected_rth,
                "daily_tolerance": DAILY_MISSING_TOLERANCE_MINUTES,
            },
            "records": qa_records,
        }
        with qa_path.open("w") as f:
            json.dump(payload, f, indent=2)
        print(f"[DATA-QA] JSON report saved to {qa_path}")
    except Exception as e:
        print(f"[DATA-QA] Failed to write JSON report: {e}")


def _deep_debug_config():
    """Enable deep executor logging (only when explicitly requested)."""
    return os.environ.get("DEEP_PORTFOLIO_DEBUG", "false").lower() == "true"


def _visual_config():
    """Read visualization toggles from env with defaults of True when unset."""
    show_plot = _env_flag("SHOW_PLOT", True)
    show_tearsheet = _env_flag("SHOW_TEARSHEET", True)
    show_indicators = _env_flag("SHOW_INDICATORS", True)
    return show_plot, show_tearsheet, show_indicators


def _load_validation_data(symbol: str, min_bars: int = 300, max_rows: int = 2000):
    """
    Load sample validation data for a strategy.

    Priority order:
      1. custom data matching symbol: validation_data/{symbol}.csv
      2. shared fall-back file: validation_data/validation.csv

    Args:
        symbol: Symbol to load data for
        min_bars: Minimum number of bars required
        max_rows: Maximum number of rows to use (to keep validation fast)
    """
    base_dir = Path("custom_portfolio/strategies/templates/validation_data")
    specific_path = base_dir / f"{symbol}.csv"
    fallback_path = base_dir / "validation.csv"

    if specific_path.exists():
        candidate_paths = [specific_path]
    else:
        candidate_paths = []
    if fallback_path.exists():
        candidate_paths.append(fallback_path)

    if not candidate_paths:
        print(f"[VALIDATE] Missing validation data for {symbol}: tried {specific_path} and {fallback_path}")
        return None

    try:
        df = pd.read_csv(candidate_paths[0])
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        else:
            # assume first column is datetime
            df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0])
            df = df.set_index(df.columns[0])

        # Limit data size for faster validation (use most recent data)
        if len(df) > max_rows:
            print(f"[VALIDATE] Limiting {symbol} data from {len(df)} to {max_rows} rows for faster validation")
            df = df.tail(max_rows)

        # basic sanity check
        if len(df) < min_bars:
            print(f"[VALIDATE] Not enough rows in {candidate_paths[0]} (len={len(df)}); need >= {min_bars}")
            return None
        if candidate_paths[0] == fallback_path and not specific_path.exists():
            print(
                f"[VALIDATE] Using shared validation.csv for symbol {symbol} "
                f"(add {specific_path.name} to override)."
            )
        return df
    except Exception as exc:
        print(f"[VALIDATE] Failed to read {candidate_paths[0]}: {exc}")
        return None


def run_signal_validation(portfolio_manager: PortfolioManager, plot_signals: bool = False, plot_symbols: bool = False):
    """
    For each loaded strategy, run over sample data and count BUY/SELL signals.
    Optionally plot price + signals to PNG under signal-plots/.
    """
    min_bars = 300
    loaded = portfolio_manager.loaded_strategies
    if not loaded:
        print("[VALIDATE] No strategies loaded; skipping signal validation.")
        return

    # prepare plot folder
    plot_dir = Path("signal-plots")
    if plot_signals or plot_symbols:
        plot_dir.mkdir(parents=True, exist_ok=True)
        for f in plot_dir.glob("*.png"):
            try:
                f.unlink()
            except Exception:
                pass

    symbol_data_cache = {}

    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    print("\n[VALIDATE] Signal counts on sample data:")
    summary_rows = []

    for sid, cfg in loaded.items():
        symbol = cfg.get("symbol")
        sig_fn = cfg.get("_generate_signal_func") or cfg.get("generate_signal_func")
        if not callable(sig_fn):
            print(f"- {sid}: missing generate_signal; skipping")
            continue

        # load data once per symbol
        if symbol in symbol_data_cache:
            df = symbol_data_cache[symbol]
        else:
            df = _load_validation_data(symbol, min_bars=min_bars)
            symbol_data_cache[symbol] = df
        if df is None:
            print(f"- {sid}: no data for {symbol}; skipping")
            continue

        # merge params with bracket/time for convenience
        params = {}
        params.update(cfg.get("params", {}))
        params.update(cfg.get("bracket_orders") or cfg.get("bracket_config") or {})
        params.update(cfg.get("time_exit") or cfg.get("exit_config") or {})

        class _State:
            def __init__(self, params):
                self.params = params

        state = _State(params)

        buy_times = []
        sell_times = []
        buy_prices = []
        sell_prices = []
        buys = sells = 0

        closes = df["close"]
        for i in range(min_bars, len(df)):
            window = df.iloc[: i + 1]
            signal = sig_fn(state, window)
            price = closes.iloc[i]
            ts = window.index[-1]
            if signal == "BUY":
                buys += 1
                buy_times.append(ts)
                buy_prices.append(price)
            elif signal == "SELL":
                sells += 1
                sell_times.append(ts)
                sell_prices.append(price)

        print(f"- {sid} [{symbol}]: BUY={buys} SELL={sells} rows={len(df)}")
        holds = max(len(df) - buys - sells, 0)
        summary_rows.append(
            {
                "strategy_id": sid,
                "symbol": symbol,
                "buy": buys,
                "sell": sells,
                "hold": holds,
                "rows": len(df),
            }
        )

        if (plot_signals or plot_symbols) and len(df) > 0:
            plt.figure(figsize=(10, 4))
            plt.plot(df.index, df["close"], label="Close", color="blue", linewidth=1.0)
            if plot_signals:
                if buy_times:
                    plt.scatter(buy_times, buy_prices, marker="^", color="green", label="BUY", s=25)
                if sell_times:
                    plt.scatter(sell_times, sell_prices, marker="v", color="red", label="SELL", s=25)
            plt.legend()
            plt.title(f"{sid} ({symbol}) signals")
            plt.tight_layout()
            out_path = plot_dir / f"{sid}.png"
            try:
                plt.savefig(out_path)
            except Exception as exc:
                print(f"[VALIDATE] Failed to save plot {out_path}: {exc}")
            plt.close()

    if summary_rows:
        print("")
        print(TF.section_header("Signal Summary"))
        headers = ["Symbol", "Strategy ID", "BUY", "SELL", "HOLD", "Rows"]
        rows = [
            [
                r["symbol"],
                r["strategy_id"],
                str(r["buy"]),
                str(r["sell"]),
                str(r["hold"]),
                str(r["rows"]),
            ]
            for r in summary_rows
        ]
        print(TF.table(headers, rows))


def create_broker_for_backtesting():
    """Create a broker instance for backtesting."""
    # For backtesting, we typically don't need a real broker
    # The backtester handles order execution
    return None


def create_broker_for_live():
    """
    Create a broker instance for live trading.

    Uses ProjectX broker with TopStepX (or other supported firms).
    Configuration is loaded automatically from environment variables:
    - PROJECTX_TOPSTEPX_USERNAME
    - PROJECTX_TOPSTEPX_API_KEY
    - PROJECTX_TOPSTEPX_PREFERRED_ACCOUNT_NAME
    """
    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF
    from lumibot.brokers import ProjectX
    from lumibot.data_sources import ProjectXData

    try:
        # Create data source first (required by broker)
        # ProjectXData will automatically detect configuration from environment variables
        data_source = ProjectXData(
            config=None,  # Auto-detect from environment variables
            firm=None,  # Auto-detect firm from environment variables
        )
        print(TF.success("ProjectXData source initialized successfully"))

        # Create broker with the data source
        # ProjectX will automatically load configuration from environment variables
        # It detects the firm (e.g., TOPSTEPX) based on the env var prefix
        broker = ProjectX(
            config=None,  # Auto-detect from environment variables
            data_source=data_source,  # Pass the data source
            connect_stream=True,  # Enable streaming for live data
            max_workers=20,  # Thread pool size
            firm=None,  # Auto-detect firm from environment variables
        )

        print(TF.success(f"ProjectX broker initialized successfully (firm: {broker.firm})"))
        return broker

    except Exception as e:
        print(TF.error(f"Failed to initialize ProjectX broker: {e}"))
        import traceback

        print(traceback.format_exc())
        return None


def create_data_source_for_backtesting():
    """Create data source for backtesting."""
    # For backtesting, data comes from the backtester
    return None


def create_data_source_for_live():
    """
    Create data source for live trading.

    Uses ProjectXData which connects to the same ProjectX broker for market data.
    Configuration is loaded automatically from environment variables.
    """
    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF
    from lumibot.data_sources import ProjectXData

    try:
        # ProjectXData will automatically use the same configuration as the broker
        # It will detect the firm and use the appropriate credentials
        data_source = ProjectXData(
            config=None,  # Auto-detect from environment variables
            firm=None,  # Auto-detect firm from environment variables
        )

        print(TF.success("ProjectXData source initialized successfully"))
        return data_source

    except Exception as e:
        print(TF.error(f"Failed to initialize ProjectXData source: {e}"))
        import traceback

        print(traceback.format_exc())
        return None


def create_calendar(is_live: bool = False):
    """
    Create trading calendar with Central Time as source of truth.

    All session times defined in CT (America/Chicago) internally.
    User-facing display will convert to MT (America/Denver).

    CRITICAL: In live mode, calendar creation failure causes HARD FAIL.
    Never trade without calendar - violates TopStepX compliance rules.

    Args:
        is_live: True if running in live trading mode, False for backtest/validation

    Returns:
        TradingCalendar instance configured for TopStepX, or None if creation fails

    Raises:
        SystemExit: In live mode, if calendar creation fails (safety requirement)
    """
    import os
    import traceback

    from custom_portfolio.strategies.portfolio_manager import (
        TOPSTEPX_PLATFORM_CONFIG,
        TRADING_SESSIONS,
    )
    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF
    from custom_portfolio.tools.timezone_utils import CENTRAL_TZ
    from lumibot.tools.trading_calendar import TradingCalendar

    # Check for emergency override (NEVER use in production)
    emergency_disable = os.getenv("CALENDAR_EMERGENCY_DISABLE", "false").lower() == "true"

    if emergency_disable:
        warning_msg = TF.warning(
            "⚠️  CALENDAR EMERGENCY OVERRIDE ACTIVE ⚠️\n"
            "Calendar system disabled via CALENDAR_EMERGENCY_DISABLE env var.\n"
            "This should NEVER be used in live trading - TopStepX rule violations may occur!"
        )
        print(warning_msg)
        return None

    try:
        # Create calendar with Central Time (source of truth)
        calendar = TradingCalendar(timezone=CENTRAL_TZ, platform_config=TOPSTEPX_PLATFORM_CONFIG)

        # Register all trading sessions
        calendar.register_sessions(TRADING_SESSIONS)

        print(
            TF.success(
                f"Trading calendar created successfully with {len(TRADING_SESSIONS)} sessions:\n"
                f"  Sessions: {', '.join(TRADING_SESSIONS.keys())}\n"
                f"  Timezone: Central Time (America/Chicago)\n"
                f"  Platform: TopStepX compliance enabled"
            )
        )

        return calendar

    except Exception as e:
        error_msg = f"Failed to create trading calendar: {e}\n{traceback.format_exc()}"

        if is_live:
            # LIVE MODE: Hard fail - never trade without calendar
            print(
                TF.error(
                    "❌ CRITICAL ERROR: Calendar creation failed in LIVE MODE\n\n"
                    f"{error_msg}\n\n"
                    "ABORTING: Cannot start live trading without calendar system.\n"
                    "TopStepX compliance requires session enforcement.\n\n"
                    "Emergency override (NOT RECOMMENDED): Set CALENDAR_EMERGENCY_DISABLE=true"
                )
            )
            import sys

            sys.exit(1)  # Hard fail - abort startup

        else:
            # BACKTEST/VALIDATION MODE: Warn and continue with None
            print(
                TF.warning(
                    f"⚠️  Calendar creation failed in backtest/validation mode:\n{error_msg}\n\n"
                    "Continuing without calendar (sessions not enforced).\n"
                    "This is acceptable for backtesting but would fail in live mode."
                )
            )
            return None


def run_backtest(args):
    """
    Run the portfolio in backtest mode.

    Args:
        args: Command line arguments
    """
    # Register signal handler for CTRL-C
    signal.signal(signal.SIGINT, signal_handler)

    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    # Read backtest dates from environment variables
    start_date_str = os.environ.get("BACKTESTING_START")
    end_date_str = os.environ.get("BACKTESTING_END")
    enable_snapshots, max_snapshots, timestep = _snapshot_config()
    simulate_fills = _simulate_fills_config()
    shared_initial_capital = _capital_config()
    debug_logs = _debug_config()
    deep_portfolio_debug = _deep_debug_config()
    show_plot, show_tearsheet, show_indicators = _visual_config()
    print(f"[SETTINGS] plot={show_plot} tearsheet={show_tearsheet} indicators={show_indicators}")

    if not start_date_str or not end_date_str:
        print(TF.error("BACKTESTING_START and BACKTESTING_END must be set in .env file"))
        print("Example:")
        print("  BACKTESTING_START=2024-01-01")
        print("  BACKTESTING_END=2024-12-31")
        return

    print("")
    print(TF.horizontal_rule())
    print("")
    print(TF.section_header("Running Portfolio Backtest"))
    print(TF.key_value("Start date", start_date_str))
    print(TF.key_value("End date", end_date_str))
    print("")

    # Create a wrapper strategy for backtesting
    class PortfolioStrategy(Strategy):
        """Wrapper strategy for portfolio backtesting."""

        # Keep reference to last manager for exports
        _last_manager = None
        _base_capital = 0.0

        def initialize(self):
            """Initialize the portfolio manager."""
            PortfolioStrategy._base_capital = shared_initial_capital

            # CRITICAL: Set market to us_futures for 24-hour trading
            # Without this, backtest only iterates during NYSE hours (09:30-16:00 ET)
            # which causes phantom P&L when futures trade 23/6 (Sun 6pm - Fri 5pm ET)
            self.set_market("us_futures")
            # Track backtest window for progress logging (use private attrs to avoid clashing with properties)
            self._progress_start = backtesting_start
            self._progress_end = backtesting_end
            # Align progress timestamps to the strategy clock timezone to avoid naive/aware subtraction
            try:
                sample_dt = self.get_datetime()
            except Exception:
                sample_dt = None
            if sample_dt is not None and isinstance(sample_dt, datetime):
                if self._progress_start.tzinfo is None and sample_dt.tzinfo is not None:
                    self._progress_start = self._progress_start.replace(tzinfo=sample_dt.tzinfo)
                if self._progress_end.tzinfo is None and sample_dt.tzinfo is not None:
                    self._progress_end = self._progress_end.replace(tzinfo=sample_dt.tzinfo)
            # Use the broker-provided backtest data source (created by the engine) to avoid duplicate connections
            ds = getattr(self.broker, "data_source", None)
            if ds is None:
                raise RuntimeError("Backtest broker did not provide a data_source; expected engine to supply one.")
            self.portfolio_data_source = ds
            self.debug_logs_enabled = debug_logs
            self._iteration_counter = 0

            # ========================================================================
            # TRADING CALENDAR SETUP (Phase 4: Wire into Backtest)
            # ========================================================================
            # Create trading calendar for session enforcement
            import os

            from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

            # Create calendar (will return None if creation fails in backtest mode)
            calendar = create_calendar(is_live=False)

            # Read enforcement flag from environment
            # Default: ENFORCE_SESSIONS_IN_BACKTEST=true (realistic backtest mode)
            # Override: ENFORCE_SESSIONS_IN_BACKTEST=false (signal exploration mode)
            enforce_sessions_str = os.getenv("ENFORCE_SESSIONS_IN_BACKTEST", "true").lower()
            enforce_sessions = enforce_sessions_str in ["true", "1", "yes", "on"]

            # Calculate ignore_calendar flag (inverse logic)
            # ignore_calendar=True means "ignore the calendar" (no enforcement)
            # ignore_calendar=False means "enforce the calendar" (realistic mode)
            ignore_calendar = not enforce_sessions

            if calendar is not None:
                if enforce_sessions:
                    print(
                        TF.success(
                            "✓ Session enforcement ENABLED for backtest\n"
                            "  Trading restricted to strategy-defined sessions\n"
                            "  Platform maintenance windows enforced (14:00-16:00 CT)\n"
                            "  Weekend blackouts enforced (Fri 14:00 - Sun 16:00 CT)\n"
                            "  Set ENFORCE_SESSIONS_IN_BACKTEST=false to disable"
                        )
                    )
                else:
                    print(
                        TF.warning(
                            "⚠️  Session enforcement DISABLED for backtest\n"
                            "  All signals will be processed regardless of session times\n"
                            "  Useful for signal exploration but unrealistic for live trading\n"
                            "  Set ENFORCE_SESSIONS_IN_BACKTEST=true for realistic results"
                        )
                    )
            else:
                print(
                    TF.warning(
                        "⚠️  Calendar creation failed - sessions not enforced\n" "  This would fail in live trading mode"
                    )
                )
                ignore_calendar = True  # Force disable if calendar is None

            # ========================================================================

            self.portfolio_manager = PortfolioManager(
                strategies_folder="custom_portfolio/strategies/active_strategies",
                broker=self.broker,
                calendar=calendar,
                data_source=self.portfolio_data_source,
                auto_load=True,
                cache_ttl_seconds=3600,  # 1 hour for backtesting (prevents cache expiry during slow backtests)
                # No throttling needed in backtests; keep orders fast
                min_order_delay_seconds=0.0,
                default_atr_period=20,
                enable_snapshots=enable_snapshots,
                max_snapshots=max_snapshots,
                timestep=timestep,
                simulate_fills=simulate_fills,
                shared_initial_capital=shared_initial_capital,
                broker_strategy_name=getattr(self, "name", "PortfolioStrategy"),
                deep_portfolio_debug=deep_portfolio_debug,
                ignore_calendar=ignore_calendar,  # Controlled by ENFORCE_SESSIONS_IN_BACKTEST env var
            )

            # Print validation report
            print(self.portfolio_manager.get_validation_report())

            # Print loaded strategies summary table
            summary_df = self.portfolio_manager.get_loaded_strategies_summary()
            if not summary_df.empty:
                from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

                print("")
                print(TF.section_header("Strategy Details"))
                print("")

                # Convert DataFrame to table
                headers = [col.replace("_", " ").title() for col in summary_df.columns]
                rows = summary_df.values.tolist()
                print(TF.table(headers, rows))

            # Prefetch all data upfront to avoid lazy loading during backtest
            if summary_df.empty:
                if self.debug_logs_enabled:
                    print("[DEBUG] No strategies/symbols loaded; skipping prefetch.")
            if hasattr(self, "portfolio_data_source") and not summary_df.empty:
                from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF
                from lumibot.entities import Asset

                symbols = summary_df["symbol"].unique().tolist()
                symbols_str = ", ".join(symbols)
                print("")
                if self.debug_logs_enabled:
                    print(
                        f"[DEBUG] Prefetch starting for {len(symbols)} symbols "
                        f"({symbols_str}) | window {backtesting_start} -> {backtesting_end}"
                    )
                start_prefetch = time.perf_counter()

                assets = [Asset(symbol, asset_type=Asset.AssetType.CONT_FUTURE) for symbol in symbols]

                if hasattr(self.portfolio_data_source, "initialize_data_for_backtest"):
                    self.portfolio_data_source.initialize_data_for_backtest(assets, timestep="minute")
                    elapsed = time.perf_counter() - start_prefetch
                    print(
                        TF.success(
                            f"Data initialized for backtest (prefetch) in {elapsed:.2f}s for {len(symbols)} symbols"
                        )
                    )
                elif hasattr(self.portfolio_data_source, "prefetch_data"):
                    self.portfolio_data_source.prefetch_data(assets, timestep="minute")
                    elapsed = time.perf_counter() - start_prefetch
                    print(TF.success(f"Data prefetch complete in {elapsed:.2f}s for {len(symbols)} symbols"))
                # TODO: allow timestep override beyond 'minute' if multi-timeframe support is added later

                # Backtest-only data QA (read-only)
                if not _qa_disabled():
                    qa_tz = _qa_timezone()
                    try:
                        _run_backtest_data_qa(self.portfolio_data_source, backtesting_start, backtesting_end, qa_tz)
                    except Exception as e:
                        print(f"[DATA-QA] QA check failed: {e}")

            self.sleeptime = "1M"  # 1-minute bars

            # Store manager for export after backtest
            PortfolioStrategy._last_manager = self.portfolio_manager

        def get_portfolio_value(self):
            """Override to surface attribution-based portfolio value during backtest."""
            mgr = getattr(self, "portfolio_manager", None)
            if mgr and getattr(mgr, "executor", None):
                attr = mgr.executor.attribution.generate_report()
                if attr is not None and not attr.empty:
                    equity_base = (
                        float(attr["initial_capital"].sum())
                        if "initial_capital" in attr.columns
                        else float(getattr(mgr.executor, "total_initial_capital", PortfolioStrategy._base_capital))
                    )
                    total_pnl = float(attr["total_pnl"].sum())
                    return equity_base + total_pnl
            return super().get_portfolio_value()

        def _log_progress_debug(self, current_time: datetime):
            """Emit a derived progress percentage for debugging."""
            if not self.debug_logs_enabled:
                return
            # Throttle progress debug to every 50 iterations to avoid I/O slowdown
            if self._iteration_counter % 50 != 1:
                return
            try:
                start_dt = self._progress_start
                end_dt = self._progress_end
                ct = current_time
                if ct.tzinfo is not None and start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=ct.tzinfo)
                    end_dt = end_dt.replace(tzinfo=ct.tzinfo) if end_dt.tzinfo is None else end_dt
                elif ct.tzinfo is None and start_dt.tzinfo is not None:
                    ct = ct.replace(tzinfo=start_dt.tzinfo)
                elif ct.tzinfo is not None and start_dt.tzinfo is not None and ct.tzinfo != start_dt.tzinfo:
                    start_dt = start_dt.astimezone(ct.tzinfo)
                    end_dt = end_dt.astimezone(ct.tzinfo)
                span = (end_dt - start_dt).total_seconds()
                elapsed = (ct - start_dt).total_seconds()
                pct = max(0.0, min(100.0, (elapsed / span) * 100 if span > 0 else 100.0))
                print(f"[DEBUG] Progress (calculated): {pct:.2f}%")
            except Exception:
                pass

        def on_trading_iteration(self):
            """Run one trading iteration."""
            current_time = self.get_datetime()
            self._iteration_counter += 1
            if self.debug_logs_enabled:
                print(f"[DEBUG] Iteration {self._iteration_counter} at {current_time.isoformat()}", flush=True)
                self._log_progress_debug(current_time)
            # Heartbeat every 25 iterations so we can see forward progress even with quiet logs
            if self._iteration_counter % 25 == 0:
                start_dt = self._progress_start
                end_dt = self._progress_end if hasattr(self, "_progress_end") else None
                ct = current_time
                if end_dt is not None:
                    if ct.tzinfo is not None and start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=ct.tzinfo)
                        end_dt = end_dt.replace(tzinfo=ct.tzinfo) if end_dt.tzinfo is None else end_dt
                    elif ct.tzinfo is None and start_dt.tzinfo is not None:
                        ct = ct.replace(tzinfo=start_dt.tzinfo)
                    elif ct.tzinfo is not None and start_dt.tzinfo is not None and ct.tzinfo != start_dt.tzinfo:
                        start_dt = start_dt.astimezone(ct.tzinfo)
                        end_dt = end_dt.astimezone(ct.tzinfo)
                span = (end_dt - start_dt).total_seconds() if end_dt else 0
                if span > 0:
                    elapsed = (ct - start_dt).total_seconds()
                    pct = max(0.0, min(100.0, (elapsed / span) * 100))
                    logging.getLogger(__name__).info(
                        f"Heartbeat: iteration {self._iteration_counter} at {current_time.isoformat()} "
                        f"(calc progress {pct:.2f}%)"
                    )
            self.portfolio_manager.run_iteration(current_time)
            if self.debug_logs_enabled:
                print(
                    f"[DEBUG] Iteration {self._iteration_counter} completed at {datetime.now().isoformat()}", flush=True
                )

        def on_abrupt_closing(self):
            """Handle abrupt closing."""
            print("\nBacktest interrupted")

        def trace_stats(self, context, snapshot_before):
            """Return attribution-based portfolio value for tearsheet equity curve."""
            mgr = getattr(self, "portfolio_manager", None)
            if mgr and getattr(mgr, "executor", None):
                executor = mgr.executor
                # Get initial capital
                initial_capital = getattr(executor, "shared_initial_capital", 0.0) or 0.0

                # Calculate total realized P&L
                total_realized_pnl = sum(s.realized_pnl for s in executor.strategies)

                # Calculate total unrealized P&L
                total_unrealized_pnl = 0.0
                current_time = self.get_datetime()
                for strategy_state in executor.strategies:
                    pos = strategy_state.tracker.get_position(strategy_state.symbol)
                    if pos and pos.quantity != 0 and strategy_state.entry_price is not None:
                        try:
                            df = executor.shared_data.get_data_at_time(
                                strategy_state.symbol, current_time, executor.max_lookback, executor.timestep
                            )
                            if df is not None and len(df) > 0:
                                current_price = df["close"].iloc[-1]
                                from custom_portfolio.data.futures_metadata import get_multiplier

                                multiplier = get_multiplier(strategy_state.symbol)
                                unrealized = (current_price - strategy_state.entry_price) * pos.quantity * multiplier
                                total_unrealized_pnl += unrealized
                        except Exception:
                            pass

                portfolio_value = initial_capital + total_realized_pnl + total_unrealized_pnl
                return {"portfolio_value": portfolio_value}
            return {"report": None}

    # Set up backtesting
    backtesting_start = datetime.strptime(start_date_str, "%Y-%m-%d")
    backtesting_end = datetime.strptime(end_date_str, "%Y-%m-%d")

    # Run backtest using class method (broker configured before initialize())
    # Uses DataBento for futures data (credentials read from .env file)
    try:
        results = PortfolioStrategy.backtest(
            datasource_class=DataBentoDataBacktestingPandas,
            backtesting_start=backtesting_start,
            backtesting_end=backtesting_end,
            parameters={},
            buy_trading_fees=[TradingFee(flat_fee=0.75)],  # $0.75 per trade for futures
            sell_trading_fees=[TradingFee(flat_fee=0.75)],
            budget=shared_initial_capital,
            show_plot=show_plot,
            show_tearsheet=show_tearsheet,
            show_indicators=show_indicators,
            save_tearsheet=show_tearsheet,  # Must be True to generate tearsheet file
            show_progress_bar=True,
        )
    except Exception as e:
        import numpy.linalg

        # If visualization fails (e.g., KDE on empty returns), retry once without plots/tearsheet to finish the run
        if isinstance(e, numpy.linalg.LinAlgError):
            print(f"[WARN] Visualization failed ({e}); retrying without plots/tearsheet.")
            results = PortfolioStrategy.backtest(
                datasource_class=DataBentoDataBacktestingPandas,
                backtesting_start=backtesting_start,
                backtesting_end=backtesting_end,
                parameters={},
                buy_trading_fees=[TradingFee(flat_fee=0.75)],
                sell_trading_fees=[TradingFee(flat_fee=0.75)],
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                save_tearsheet=False,
                show_progress_bar=True,
            )
        else:
            raise

    print("\n" + "=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)

    # Force-flatten any residual positions so attribution closes the book
    manager = PortfolioStrategy._last_manager
    if manager and getattr(manager, "executor", None):
        try:
            close_count, forced_details = manager.executor.force_flatten()
            if forced_details:
                print("\nForced-close summary (attribution):")
                for item in forced_details:
                    print(
                        f"- {item['strategy_id']} {item['symbol']} qty={item['qty']} "
                        f"entry={item['entry_price']} last={item['last_price']} "
                        f"mult={item['multiplier']} gross={item['gross_pnl']} "
                        f"fees_pending={item['fees_pending']} net={item['net_pnl']}"
                    )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Force flatten failed: {e}")

    # Attribution-based stats (uses simulated fills inside executor)
    attr_report = None
    if manager and getattr(manager, "executor", None):
        attr_report = manager.executor.attribution.generate_report()
        if not attr_report.empty:
            print("\nAttribution (simulated fills):")
            print(attr_report.to_string(index=False))
            print(f"Simulated total trades: {int(attr_report['trade_count'].sum())}")

            # If lumibot backtest returns are empty, mirror attribution so user sees activity
            try:
                total_trades_attr = int(attr_report["trade_count"].sum())
                total_pnl_attr = float(attr_report["total_pnl"].sum())
                total_fees_attr = float(attr_report["total_fees"].sum()) if "total_fees" in attr_report.columns else 0.0
                # Sum initial capital across strategies (executor registers per-strategy share)
                equity_base = (
                    float(attr_report["initial_capital"].sum())
                    if "initial_capital" in attr_report.columns
                    else float(
                        getattr(
                            manager.executor, "total_initial_capital", manager.executor.shared_initial_capital or 1.0
                        )
                    )
                )
                total_return_attr = total_pnl_attr / equity_base if equity_base else 0.0
                if results is not None:
                    results["total_trades"] = total_trades_attr
                    results["total_return"] = total_return_attr
                    results["portfolio_value"] = equity_base + total_pnl_attr
                    results["total_fees"] = total_fees_attr
            except Exception:
                pass

    # Display results
    if results:

        def _fmt_pct(val):
            try:
                return f"{float(val):.2%}"
            except Exception:
                return str(val)

        def _fmt_num(val):
            try:
                return f"{float(val):.2f}"
            except Exception:
                return str(val)

        print("\nBacktest Results:")
        print(f"Total Return: {_fmt_pct(results.get('total_return', 0))}")
        print(f"CAGR: {_fmt_pct(results.get('cagr', 0))}")
        print(f"Max Drawdown: {_fmt_pct(results.get('max_drawdown', 0))}")
        print(f"Sharpe Ratio: {_fmt_num(results.get('sharpe_ratio', 0))}")
        print(f"Total Trades: {results.get('total_trades', 0)}")
        if "total_fees" in results:
            print(f"Total Fees: {_fmt_num(results.get('total_fees', 0))}")
        if "portfolio_value" in results:
            print(f"Portfolio Value (attribution): {_fmt_num(results.get('portfolio_value', 0))}")

    # Export snapshots if enabled
    if manager:
        if enable_snapshots and max_snapshots > 0:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            folder = Path("snapshots") / f"backtest_{stamp}"
            out_path = manager.export_snapshots(folder)
            if out_path:
                print(f"\nSnapshot log written to {out_path}")


def _print_shutdown_report(bracket_manager, portfolio_manager):
    """Print final stats on shutdown."""
    if bracket_manager is not None:
        print(f"\nBracket Manager Stats: {bracket_manager.stats}")

    if portfolio_manager is not None:
        report = portfolio_manager.get_performance_report()
        if report is not None:
            print("\nFinal Portfolio Performance:")
            print(report)


def run_live(args):
    """
    Run the portfolio in live trading mode.

    Args:
        args: Command line arguments

    Environment Variables:
        DRY_RUN: Set to 'true' to enable dry-run mode (no real orders).
                 Default is FALSE for live trading (real orders).
    """
    global _run_mode, _interrupted
    _run_mode = "live"
    _interrupted = False  # Reset on fresh run

    # Register signal handler for CTRL-C to ensure clean shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Clear log files for fresh start
    for log_file in ["portfolio_errors.log", "portfolio_runner.log"]:
        if os.path.exists(log_file):
            open(log_file, "w").close()
            print(f"Cleared {log_file} for fresh session")

    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    enable_snapshots, max_snapshots, timestep = _snapshot_config()
    dry_run_mode = _live_dry_run_config()
    shared_initial_capital = _capital_config()
    deep_portfolio_debug = _deep_debug_config()

    print("=" * 70)
    if dry_run_mode:
        print("RUNNING PORTFOLIO IN DRY-RUN MODE")
        print("=" * 70)
        print(
            TF.warning(
                "DRY-RUN MODE ACTIVE\n"
                "  - Orders will be SIMULATED, not submitted to exchange\n"
                "  - Virtual positions tracked locally\n"
                "  - Useful for testing strategy logic without real trades\n"
                "  - Set DRY_RUN=false to enable real trading"
            )
        )
    else:
        print("RUNNING PORTFOLIO LIVE TRADING")
        print("=" * 70)
        print(TF.error("⚠️  LIVE TRADING MODE - REAL ORDERS WILL BE PLACED"))
    print("")

    # Confirmation prompt (different for dry-run vs real)
    if dry_run_mode:
        confirm = input("Start dry-run mode? (yes/no): ")
        if confirm.lower() != "yes":
            print("Dry-run cancelled.")
            return
    else:
        confirm = input("Are you sure you want to run in LIVE mode with REAL orders? (yes/no): ")
        if confirm.lower() != "yes":
            print("Live trading cancelled.")
            return

    # Create broker (which includes its own data source)
    broker = create_broker_for_live()
    if broker is None:
        print("❌ Broker not configured. Cannot run live trading.")
        return

    # Use the broker's data source (already created during broker initialization)
    data_source = broker.data_source
    if data_source is None:
        print("❌ Data source not available from broker.")
        return

    # ========================================================================
    # TRADING CALENDAR SETUP (Phase 5: Wire into Live)
    # ========================================================================
    # CRITICAL: Create calendar with HARD FAIL enabled for live trading
    # Calendar creation failure will abort startup (TopStepX compliance)
    import sys

    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    print("")
    print(TF.section_header("Trading Calendar Initialization"))
    print("")

    # Create calendar with is_live=True (hard fail on errors)
    calendar = create_calendar(is_live=True)

    # Verify calendar was created successfully
    if calendar is None:
        # This should never happen in live mode (create_calendar should sys.exit(1))
        # But add additional safety check just in case
        print(
            TF.error(
                "❌ CRITICAL ERROR: Calendar is None in LIVE MODE\n\n"
                "This is a safety violation - cannot proceed with live trading.\n"
                "Calendar system is required for TopStepX compliance.\n\n"
                "ABORTING live trading startup."
            )
        )
        sys.exit(1)

    # Confirm session enforcement (always enabled in live mode)
    print(
        TF.success(
            "✓ Session enforcement ENABLED for live trading\n"
            "  Trading restricted to strategy-defined sessions\n"
            "  Platform maintenance windows enforced (14:00-16:00 CT)\n"
            "  Weekend blackouts enforced (Fri 14:00 - Sun 16:00 CT)\n"
            "  TopStepX compliance rules active"
        )
    )
    print("")
    # ========================================================================

    # ========================================================================
    # BRACKET ORDER MANAGER SETUP (Phase 2: REST-based orphan cleanup)
    # ========================================================================
    # BracketOrderManager provides safety-net orphan cleanup via REST polling
    # This complements the broker-level streaming-based bracket handling
    # Must be created BEFORE PortfolioManager for race-safe close pattern
    bracket_manager = None
    try:
        from tools.bracket_order_manager import BracketOrderManager

        # Ensure broker is connected (sets account_id)
        if hasattr(broker, "connect") and not broker.account_id:
            broker.connect()

        # Access the underlying ProjectX client and account_id from broker
        if hasattr(broker, "client") and broker.account_id:
            bracket_manager = BracketOrderManager(
                client=broker.client,
                account_id=broker.account_id,
            )
            # Wire bracket_manager to broker so bracket children get registered
            broker.bracket_manager = bracket_manager

            # CRITICAL: Cancel all stale BRK_* orders from previous runs
            # This prevents "tag already in use" errors
            cleanup_result = bracket_manager.startup_cleanup()
            if cleanup_result["cancelled"] > 0:
                print(
                    TF.warning(
                        f"⚠️  Startup cleanup: cancelled {cleanup_result['cancelled']} stale bracket orders\n"
                        "  These were orphaned from previous bot runs"
                    )
                )

            print(
                TF.success(
                    "✓ BracketOrderManager initialized\n"
                    "  Startup cleanup complete\n"
                    "  Stateless orphan cleanup enabled (REST polling)\n"
                    "  Race-safe close pattern enabled"
                )
            )
        else:
            print(
                TF.warning(
                    "⚠️  BracketOrderManager not initialized\n"
                    "  Broker does not expose client/account_id\n"
                    "  Relying on broker-level bracket handling only"
                )
            )
    except ImportError as e:
        print(
            TF.warning(f"⚠️  BracketOrderManager import failed: {e}\n" "  Relying on broker-level bracket handling only")
        )
    print("")
    # ========================================================================

    # ========================================================================
    # ORDER REGISTRY SETUP (Bulletproof Order Management)
    # ========================================================================
    # OrderRegistry provides centralized order tracking with:
    # - Unique tags (timestamp + UUID) to prevent duplicate tag errors
    # - Position sync checks to prevent double-close race conditions
    # - Central registration of all orders from creation to fill
    order_registry = None
    try:
        from tools.order_registry import OrderRegistry

        order_registry = OrderRegistry()

        # Wire to broker (for order submission registration)
        if broker:
            broker.order_registry = order_registry

        # Wire to bracket_manager (for emergency close and bracket recreation)
        if bracket_manager:
            bracket_manager.order_registry = order_registry

        print(
            TF.success(
                "✓ OrderRegistry initialized\n"
                "  Unique tags enabled (timestamp + UUID)\n"
                "  Position sync checks enabled\n"
                "  Central order tracking active"
            )
        )
    except ImportError as e:
        print(TF.warning(f"⚠️  OrderRegistry import failed: {e}\n" "  Using legacy order tracking"))
    print("")
    # ========================================================================

    # Create portfolio manager
    portfolio_manager = PortfolioManager(
        strategies_folder="custom_portfolio/strategies/active_strategies",
        broker=broker,
        calendar=calendar,
        data_source=data_source,
        auto_load=True,
        cache_ttl_seconds=60,
        min_order_delay_seconds=2.0,
        default_atr_period=20,
        enable_snapshots=enable_snapshots,
        max_snapshots=max_snapshots,
        timestep=timestep,
        simulate_fills=dry_run_mode,  # In live mode: dry_run_mode=True means simulate, False means real orders
        shared_initial_capital=shared_initial_capital,
        deep_portfolio_debug=deep_portfolio_debug,
        ignore_calendar=False,  # ALWAYS enforce calendar in live mode (TopStepX compliance)
        bracket_manager=bracket_manager,  # For race-safe close pattern
        order_registry=order_registry,  # For bulletproof order tracking
    )

    # ========================================================================
    # INJECT STRATEGY STATES INTO BRACKET MANAGER (Phase 11)
    # ========================================================================
    # After PortfolioManager creates executor, inject strategy_states reference
    # This enables fill processing and bracket recreation in poll_cycle()
    if bracket_manager is not None and portfolio_manager.executor is not None:
        bracket_manager.strategy_states = portfolio_manager.executor.strategy_states

        # Initialize contract_id for each strategy state (required for bracket orders)
        # Batch by unique symbols to avoid N+1 queries
        contract_ids_set = 0
        if hasattr(broker, "client"):
            # Get unique symbols that need contract_id resolution
            symbols_to_resolve = {
                state.symbol for state in portfolio_manager.executor.strategies if not state.contract_id
            }
            # Resolve each unique symbol once
            symbol_to_contract: dict[str, str] = {}
            for symbol in symbols_to_resolve:
                try:
                    contract_id = broker.client.find_contract_by_symbol(symbol)
                    if contract_id:
                        symbol_to_contract[symbol] = contract_id
                except Exception as e:
                    print(TF.warning(f"⚠️  Could not resolve contract_id for {symbol}: {e}"))

            # Apply to all strategy states
            for state in portfolio_manager.executor.strategies:
                if not state.contract_id and state.symbol in symbol_to_contract:
                    state.contract_id = symbol_to_contract[state.symbol]
                    contract_ids_set += 1

        print(
            TF.success(
                f"✓ BracketOrderManager linked to {len(bracket_manager.strategy_states)} strategy states\n"
                f"  Contract IDs resolved: {contract_ids_set}/{len(bracket_manager.strategy_states)}\n"
                "  Fill processing and bracket recreation enabled"
            )
        )
    # ========================================================================

    # Print validation report
    print("\n" + portfolio_manager.get_validation_report())

    # Print dry-run mode status prominently
    if dry_run_mode:
        print("")
        print(TF.warning("=" * 50))
        print(TF.warning("  DRY-RUN MODE: No real orders will be placed"))
        print(TF.warning("=" * 50))
    else:
        print("")
        print(TF.success("=" * 50))
        print(TF.success("  LIVE MODE: Real orders WILL be placed"))
        print(TF.success("=" * 50))

    # Print loaded strategies summary
    summary_df = portfolio_manager.get_loaded_strategies_summary()
    if not summary_df.empty:
        print("\nLoaded Strategies Summary:")
        print(summary_df.to_string())

    print("\n" + "=" * 70)
    print("Starting live trading loop...")
    print("Press Ctrl+C to stop")
    print("=" * 70 + "\n")

    # ========================================================================
    # FAST LOOP TRADING PATTERN
    # ========================================================================
    # Single fast loop (2s intervals) with minute boundary detection:
    # - Every tick: poll for bracket orphans
    # - On new minute: run full trading iteration
    # - Every N minutes: reconcile exchange vs virtual positions
    # This ensures we never miss a candle close due to API lag
    # ========================================================================
    POLL_INTERVAL_SECONDS = 2
    # Reconciliation interval configurable via env var (default 5 minutes)
    reconcile_minutes = int(os.environ.get("RECONCILE_INTERVAL_MINUTES", "5"))
    RECONCILE_INTERVAL_POLLS = (reconcile_minutes * 60) // POLL_INTERVAL_SECONDS
    last_iteration_minute = None
    poll_iteration = 0
    strategy_iteration = 0
    last_reconcile_poll = 0
    last_bracket_scanned = 0  # Track for heartbeat display

    # Market closed detection state
    market_closed_error_count = 0
    market_closed_pause_until = None  # datetime when pause expires

    # Main trading loop
    try:
        while not _interrupted:
            poll_iteration += 1
            current_time = datetime.now()
            current_minute = current_time.replace(second=0, microsecond=0)

            # ================================================================
            # MARKET CLOSED PAUSE: Skip iterations if in pause mode
            # ================================================================
            if market_closed_pause_until is not None:
                if current_time < market_closed_pause_until:
                    # Still in pause mode - show warning and skip iteration
                    remaining = market_closed_pause_until - current_time
                    remaining_mins = int(remaining.total_seconds() // 60)
                    remaining_secs = int(remaining.total_seconds() % 60)

                    # Only print warning every 5 minutes (150 poll iterations)
                    if poll_iteration % 150 == 1:
                        print(
                            f"\033[93m⚠️  MARKETS POTENTIALLY CLOSED - WAITING {remaining_mins}m {remaining_secs}s "
                            f"(until {market_closed_pause_until.strftime('%H:%M:%S')})\033[0m",
                            flush=True,
                        )
                        print(
                            f"\033[93m   Last error: {market_closed_error_count} consecutive "
                            f"'market closed' rejections\033[0m",
                            flush=True,
                        )

                    # Reduced heartbeat during pause (every 5 minutes instead of 30s)
                    if poll_iteration % 150 == 0:
                        print(
                            f"[HEARTBEAT-PAUSED] {current_time.strftime('%H:%M:%S')} | "
                            f"waiting for market to open ({remaining_mins}m {remaining_secs}s remaining)",
                            flush=True,
                        )

                    # Sleep and continue to next iteration (skip strategy execution)
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
                else:
                    # Pause expired - attempt to resume
                    print(
                        f"\033[92m✓ Market closed pause expired at {current_time.strftime('%H:%M:%S')} - "
                        f"attempting to resume trading...\033[0m",
                        flush=True,
                    )
                    market_closed_pause_until = None
                    # Don't reset error count yet - wait for successful order

            # ================================================================
            # NEW CANDLE: Run full trading iteration on minute boundary
            # ================================================================
            if last_iteration_minute is None or current_minute > last_iteration_minute:
                strategy_iteration += 1
                last_iteration_minute = current_minute

                print(
                    f"\n--- Strategy Iteration {strategy_iteration} at {current_time.strftime('%H:%M:%S')} ---",
                    flush=True,
                )

                # Run one trading iteration
                result = portfolio_manager.run_iteration(current_time)

                # ============================================================
                # MARKET CLOSED ERROR DETECTION
                # ============================================================
                if result:
                    rejected_errors = result.get("rejected_errors", [])
                    orders_submitted = result.get("orders_submitted", 0)

                    # Check for market closed errors in rejected orders
                    market_closed_errors = [err for err in rejected_errors if _is_market_closed_error(err)]

                    if market_closed_errors:
                        market_closed_error_count += len(market_closed_errors)
                        print(
                            f"\033[93m⚠️  Market closed error detected: {market_closed_errors[0]}\033[0m",
                            flush=True,
                        )
                        print(
                            f"\033[93m   Consecutive market closed errors: "
                            f"{market_closed_error_count}/{_MARKET_CLOSED_ERROR_THRESHOLD}\033[0m",
                            flush=True,
                        )

                        # Check if threshold reached - enter pause mode
                        if market_closed_error_count >= _MARKET_CLOSED_ERROR_THRESHOLD:
                            from datetime import timedelta

                            market_closed_pause_until = current_time + timedelta(minutes=_MARKET_CLOSED_PAUSE_MINUTES)
                            print(
                                f"\n\033[93m{'='*60}\033[0m",
                                flush=True,
                            )
                            print(
                                "\033[93m⚠️  MARKETS POTENTIALLY CLOSED\033[0m",
                                flush=True,
                            )
                            print(
                                f"\033[93m   Pausing for {_MARKET_CLOSED_PAUSE_MINUTES} minutes\033[0m",
                                flush=True,
                            )
                            print(
                                f"\033[93m   Will resume at: "
                                f"{market_closed_pause_until.strftime('%H:%M:%S')}\033[0m",
                                flush=True,
                            )
                            print(
                                f"\033[93m{'='*60}\033[0m\n",
                                flush=True,
                            )

                    elif orders_submitted > 0:
                        # Orders succeeded - reset the error counter
                        if market_closed_error_count > 0:
                            print(
                                f"\033[92m✓ Order succeeded - resetting market closed "
                                f"error counter (was {market_closed_error_count})\033[0m",
                                flush=True,
                            )
                        market_closed_error_count = 0
                # ============================================================

                # Print summary
                if result and not result.get("error"):
                    processed = result.get("strategies_processed", 0)
                    signals = result.get("signals_generated", 0)
                    orders = result.get("orders_submitted", 0)
                    time_exits = result.get("time_exits_triggered", 0)
                    status_parts = [f"strategies={processed}"]
                    if signals > 0:
                        status_parts.append(f"signals={signals}")
                    if orders > 0:
                        status_parts.append(f"orders={orders}")
                    if time_exits > 0:
                        status_parts.append(f"time_exits={time_exits}")
                    print(f"  {' | '.join(status_parts)}", flush=True)

                    # Print live status table
                    status_table = portfolio_manager.get_live_status_table()
                    print(status_table, flush=True)

                    # Print trend sentiment table (cached, refreshes every 5 min)
                    try:
                        sentiment_table = get_trend_sentiment_table(["MES", "MNQ", "MGC"])
                        print(sentiment_table, flush=True)
                    except Exception as e:
                        print(f"  (trend sentiment unavailable: {e})", flush=True)

                    # Plot side-by-side instrument charts (uses plotext)
                    try:
                        if portfolio_manager.executor:
                            portfolio_manager.executor.plot_instrument_charts(bars=60, height=20)
                    except Exception as e:
                        print(f"  (charts unavailable: {e})", flush=True)
                elif result and result.get("error"):
                    print(f"  Error: {result['error']}", flush=True)

            # ================================================================
            # EVERY TICK: Full bracket poll cycle (fill processing + recreation + orphan cleanup)
            # ================================================================
            last_bracket_pairs = 0
            if bracket_manager is not None:
                try:
                    # Build current prices dict for all traded symbols (batch by unique symbols)
                    current_prices = {}
                    if portfolio_manager.executor and portfolio_manager.executor.shared_data:
                        # Get unique symbols to avoid redundant fetches for multiple strategies on same symbol
                        unique_symbols = {state.symbol for state in portfolio_manager.executor.strategy_states.values()}
                        for symbol in unique_symbols:
                            try:
                                # Use shared_data.get_cached_data() - returns a Bars object with .df property
                                bars_obj = portfolio_manager.executor.shared_data.get_cached_data(
                                    symbol,
                                    portfolio_manager.executor.max_lookback,
                                    portfolio_manager.executor.timestep,
                                )
                                if bars_obj is not None:
                                    # Bars object has .df attribute; fallback to direct use if it's a DataFrame
                                    df = bars_obj.df if hasattr(bars_obj, "df") else bars_obj
                                    if df is not None and len(df) > 0:
                                        current_prices[symbol] = df["close"].iloc[-1]
                            except Exception:
                                pass  # Skip symbols with no cached data yet

                    # Run full poll cycle with calendar gating
                    poll_result = bracket_manager.poll_cycle(current_prices, calendar)

                    # Update heartbeat tracking
                    last_bracket_scanned = len(bracket_manager.brackets)
                    last_bracket_pairs = sum(1 for b in bracket_manager.brackets.values() if b.active)

                    # Log significant events
                    if poll_result.get("fills_processed"):
                        for fill in poll_result["fills_processed"]:
                            if fill["type"] == "exit" and fill.get("exit_type"):
                                # Exit fill - show TP/SL type and P&L
                                exit_type = fill.get("exit_type", "?")
                                pnl = fill.get("pnl")
                                pnl_str = f"${pnl:+.2f}" if pnl is not None else "?"
                                # Color code: green for profit, red for loss
                                if pnl is not None and pnl >= 0:
                                    pnl_color = "\033[92m"  # Green
                                else:
                                    pnl_color = "\033[91m"  # Red
                                reset = "\033[0m"
                                print(
                                    f"[BRACKET] {exit_type} filled: {fill['strategy']} @ {fill['price']} "
                                    f"(P&L: {pnl_color}{pnl_str}{reset})",
                                    flush=True,
                                )
                            elif fill["type"] == "entry":
                                print(
                                    f"[BRACKET] Entry filled: {fill['strategy']} @ {fill['price']}",
                                    flush=True,
                                )
                            elif fill["type"] == "exit_ignored":
                                # Exit ignored - already flat, but order did fill on exchange
                                reason = fill.get("reason", "unknown")
                                price = fill.get("price", "?")
                                print(
                                    f"[BRACKET] Exit filled but ignored ({reason}): {fill['strategy']} @ {price}",
                                    flush=True,
                                )
                            else:
                                fill_price = fill.get("price", "?")
                                print(
                                    f"[BRACKET] Processed {fill['type']} fill: {fill['strategy']} @ {fill_price}",
                                    flush=True,
                                )

                    if poll_result.get("brackets_recreated"):
                        for tag in poll_result["brackets_recreated"]:
                            print(TF.warning(f"[BRACKET] Recreated missing bracket: {tag}"), flush=True)

                    if poll_result.get("positions_closed"):
                        for sid in poll_result["positions_closed"]:
                            print(TF.error(f"[BRACKET] Emergency close triggered: {sid}"), flush=True)

                    if poll_result.get("orphans_cancelled"):
                        print(
                            TF.warning(f"[BRACKET] Cleaned up {len(poll_result['orphans_cancelled'])} orphan orders"),
                            flush=True,
                        )
                        for item in poll_result["orphans_cancelled"]:
                            print(f"  - {item['type']} order {item['order_id']} ({item['reason']})", flush=True)

                    # POSITION SYNC CHECK: Run every iteration for immediate desync detection
                    if True:  # Always run - position sync is critical
                        try:
                            sync_result = bracket_manager.check_position_sync()
                            if not sync_result.get("synced"):
                                if sync_result.get("error"):
                                    # API error - log but don't alarm
                                    pass  # Already logged by bracket_manager
                                elif sync_result.get("non_cooldown_discrepancies"):
                                    # Position mismatch detected (excluding symbols in cooldown)
                                    print(
                                        TF.error("⚠️ POSITION DESYNC DETECTED! Attempting auto-repair..."),
                                        flush=True,
                                    )
                                    for d in sync_result["non_cooldown_discrepancies"]:
                                        diff_str = f"{d['diff']:+.0f}" if d["diff"] != 0 else "0"
                                        print(
                                            f"   {d['symbol']}: exchange={d['exchange_qty']:.0f}, "
                                            f"virtual={d['virtual_qty']:.0f}, diff={diff_str}",
                                            flush=True,
                                        )

                                    # AUTO-REPAIR: Zero out phantom positions
                                    repair_result = bracket_manager.repair_position_desync(sync_result)

                                    if repair_result.get("repairs_made"):
                                        print(TF.warning("🔧 AUTO-REPAIR completed:"), flush=True)
                                        for r in repair_result["repairs_made"]:
                                            print(
                                                f"   Zeroed {r['strategy']}: {r['old_qty']:.0f} -> 0",
                                                flush=True,
                                            )
                                        if repair_result.get("brackets_cancelled"):
                                            print(
                                                f"   Cancelled {len(repair_result['brackets_cancelled'])} brackets",
                                                flush=True,
                                            )

                                    if repair_result.get("warnings"):
                                        for w in repair_result["warnings"]:
                                            print(
                                                TF.error(f"⚠️ MANUAL ACTION NEEDED: {w['message']}"),
                                                flush=True,
                                            )
                        except Exception:
                            # Non-fatal - just skip this sync check
                            pass

                except Exception as e:
                    # Don't crash the loop on bracket manager errors
                    if poll_iteration % 30 == 1:  # Log every ~60s
                        print(TF.warning(f"[BRACKET] Poll cycle error (non-fatal): {e}"), flush=True)

            # ================================================================
            # POSITION RECONCILIATION: Every 5 minutes (150 polls)
            # ================================================================
            if poll_iteration - last_reconcile_poll >= RECONCILE_INTERVAL_POLLS:
                last_reconcile_poll = poll_iteration
                try:
                    reconcile_result = portfolio_manager.reconcile_positions()
                    if reconcile_result.get("error"):
                        print(TF.warning(f"[RECONCILE] Error: {reconcile_result['error']}"))
                    elif reconcile_result.get("matched"):
                        # Only show success message if we have positions to check
                        exchange_count = len(reconcile_result.get("exchange_positions", {}))
                        virtual_count = len(reconcile_result.get("virtual_positions", {}))
                        if exchange_count > 0 or virtual_count > 0:
                            print(
                                f"[RECONCILE] Positions matched | "
                                f"exchange={exchange_count} symbols, virtual={virtual_count} symbols"
                            )
                    else:
                        # Position mismatch detected - this is critical
                        print(TF.error("[RECONCILE] POSITION MISMATCH DETECTED:"))
                        for mismatch in reconcile_result.get("mismatches", []):
                            print(
                                f"  {mismatch['symbol']}: "
                                f"exchange={mismatch['exchange_qty']:+.1f} "
                                f"virtual={mismatch['virtual_qty']:+.1f} "
                                f"delta={mismatch['delta']:+.1f}"
                            )
                except Exception as e:
                    print(TF.warning(f"[RECONCILE] Reconciliation error (non-fatal): {e}"))

            # ================================================================
            # HEARTBEAT: Show we're alive (every 15 poll iterations = ~30s)
            # ================================================================
            if poll_iteration % 15 == 0:
                bracket_info = f" bracket_scan={last_bracket_scanned}/{last_bracket_pairs}" if bracket_manager else ""
                print(
                    f"[HEARTBEAT] {current_time.strftime('%H:%M:%S')} | "
                    f"strategy_iter={strategy_iteration} poll_iter={poll_iteration}{bracket_info}",
                    flush=True,
                )

            # Sleep until next poll (interruptible - check flag every 0.1s)
            for _ in range(int(POLL_INTERVAL_SECONDS * 10)):
                if _interrupted:
                    break
                time.sleep(0.1)

        # Loop exited via _interrupted flag
        if _interrupted:
            print("\n\nLive trading stopped by user (CTRL-C).")
            _print_shutdown_report(bracket_manager, portfolio_manager)

    except KeyboardInterrupt:
        print("\n\nLive trading stopped by user.")
        _print_shutdown_report(bracket_manager, portfolio_manager)

    except Exception as e:
        print(f"\n❌ Error in live trading: {e}")
        import traceback

        print(traceback.format_exc())
        _print_shutdown_report(bracket_manager, portfolio_manager)


def validate_only(args):
    """
    Validate strategies without running them.

    Runs calendar unit tests FIRST before strategy validation.
    Tests must pass before strategies are loaded.

    Args:
        args: Command line arguments
    """
    import subprocess
    import sys

    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    # ========================================================================
    # PHASE 8: INTEGRATE CALENDAR TESTS INTO VALIDATE MODE
    # ========================================================================
    # Run calendar unit tests BEFORE strategy loading
    # If tests fail, abort validation (prevent trading with broken calendar)
    # ========================================================================

    print("")
    print(TF.horizontal_rule())
    print(TF.section_header("Calendar Unit Tests"))
    print(TF.horizontal_rule())
    print("")

    # Run pytest on calendar tests
    test_file = "tests/test_trading_calendar_sessions.py"
    print(TF.key_value("Test file", test_file))
    print("")

    try:
        # Run pytest with verbose output
        result = subprocess.run(
            ["python", "-m", "pytest", test_file, "-v", "--tb=short", "--color=yes"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Display pytest output
        print(result.stdout)
        if result.stderr:
            print(result.stderr)

        # Check if tests passed
        if result.returncode != 0:
            print("")
            print(
                TF.error(
                    "❌ CALENDAR TESTS FAILED\n\n"
                    "Calendar unit tests must pass before strategy validation.\n"
                    "Please fix the failing tests and try again.\n\n"
                    f"Test file: {test_file}\n"
                    f"Exit code: {result.returncode}"
                )
            )
            print("")
            sys.exit(1)

        # Tests passed
        print("")
        print(TF.success("✓ All calendar unit tests passed!"))
        print("")

    except subprocess.TimeoutExpired:
        print(
            TF.error(
                "❌ CALENDAR TESTS TIMEOUT\n\n"
                "Calendar unit tests timed out after 60 seconds.\n"
                "Please check for infinite loops or hanging tests."
            )
        )
        sys.exit(1)

    except FileNotFoundError:
        print(
            TF.warning(
                f"⚠️  Calendar test file not found: {test_file}\n\n"
                "Skipping calendar tests (file does not exist).\n"
                "This is acceptable for development but tests should exist in production."
            )
        )
        print("")

    except Exception as e:
        print(
            TF.error(f"❌ ERROR RUNNING CALENDAR TESTS\n\n" f"Error: {e}\n\n" "Skipping calendar tests due to error.")
        )
        print("")

    # ========================================================================
    # STRATEGY VALIDATION
    # ========================================================================

    print(TF.horizontal_rule())
    print(TF.section_header("Strategy Validation"))
    print(TF.horizontal_rule())
    print("")

    enable_snapshots, max_snapshots, timestep = _snapshot_config()
    simulate_fills = _simulate_fills_config()
    shared_initial_capital = _capital_config()
    deep_portfolio_debug = _deep_debug_config()

    # Create a minimal portfolio manager for validation
    portfolio_manager = PortfolioManager(
        strategies_folder="custom_portfolio/strategies/active_strategies",
        broker=None,  # Not needed for validation
        calendar=None,  # Not needed for validation
        auto_load=True,
        enable_snapshots=enable_snapshots,
        max_snapshots=max_snapshots,
        timestep=timestep,
        simulate_fills=simulate_fills,
        shared_initial_capital=shared_initial_capital,
        deep_portfolio_debug=deep_portfolio_debug,
    )

    # Print validation report (includes all formatting and final status)
    print(portfolio_manager.get_validation_report())

    # Run sample-data signal sweep and optional plotting
    if args.plot_signals or args.plot_symbols:
        run_signal_validation(portfolio_manager, plot_signals=args.plot_signals, plot_symbols=args.plot_symbols)

    # Print loaded strategies summary as table
    summary_df = portfolio_manager.get_loaded_strategies_summary()
    if not summary_df.empty:
        print("")
        print(TF.section_header("Strategy Details"))
        print("")

        # Create table with only key columns
        headers = ["Symbol", "Strategy ID", "Session", "Qty", "Params", "MinBars", "Type", "Direction"]
        rows = []
        for _, row in summary_df.iterrows():
            rows.append(
                [
                    str(row["symbol"]),
                    str(row["strategy_id"]),
                    str(row["sessions"]),
                    str(row["qty"]),
                    str(row["params"]),
                    str(row.get("min_bars", 70)),
                    str(row.get("type", "unknown")),
                    str(row.get("direction", "n/a")),
                ]
            )

        print(TF.table(headers, rows))
        print("")

    # Exit with appropriate status code
    if portfolio_manager.load_errors:
        sys.exit(1)
    else:
        sys.exit(0)


def archive_strategies(args):
    """
    Archive current strategies to a timestamped folder.

    Args:
        args: Command line arguments
    """
    print("=" * 70)
    print("ARCHIVING CURRENT STRATEGIES")
    print("=" * 70)

    # Create archive folder name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_folder = f"strategies/archived_strategies/archive_{timestamp}"

    # Create portfolio manager
    portfolio_manager = PortfolioManager(
        strategies_folder="custom_portfolio/strategies/active_strategies",
        broker=None,
        calendar=None,
        auto_load=False,  # Don't load strategies
    )

    # Archive strategies
    count = portfolio_manager.archive_strategies(archive_folder)

    print(f"\n✅ Archived {count} strategy files to {archive_folder}")


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Run Multi-Strategy Trading Portfolio")

    # Mode selection
    parser.add_argument(
        "--mode",
        choices=["backtest", "live", "validate", "archive"],
        default="validate",
        help="Operation mode (default: validate)",
    )
    parser.add_argument(
        "--plot-signals",
        action="store_true",
        help="During validate: run sample data through each strategy and save signal plots",
    )
    parser.add_argument(
        "--plot-symbols",
        action="store_true",
        help="During validate: plot price-only charts from sample data; ignored otherwise",
    )

    # Logging
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="WARNING",
        help="Logging level (default: WARNING)",
    )

    args = parser.parse_args()

    # Set up logging
    setup_logging(args.log_level)

    # Execute based on mode
    if args.mode == "backtest":
        run_backtest(args)
    elif args.mode == "live":
        run_live(args)
    elif args.mode == "validate":
        validate_only(args)
    elif args.mode == "archive":
        archive_strategies(args)


if __name__ == "__main__":
    main()
