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
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

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


def signal_handler(signum, frame):
    """Handle CTRL-C gracefully."""
    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    global _interrupted
    _interrupted = True
    print("")
    print(TF.warning("Interrupt received (CTRL-C), stopping backtest..."))
    sys.exit(1)


def setup_logging(log_level: str = "INFO") -> None:
    """
    Set up logging configuration.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("portfolio_runner.log")],
    )


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
    """Read DRY_RUN env to control simulated fills (default true)."""
    return os.environ.get("DRY_RUN", "true").lower() == "true"


def _capital_config():
    """Shared starting capital for the portfolio (applied to all strategies for attribution)."""
    try:
        return float(os.environ.get("TOTAL_INITIAL_CAPITAL", "150000"))
    except Exception:
        return 150000.0


def _debug_config():
    """Enable verbose portfolio debug logs (prefetch + iterations)."""
    return os.environ.get("PORTFOLIO_DEBUG", "true").lower() == "true"


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


def create_calendar():
    """
    Create a trading calendar instance.

    For futures, you might want to use a custom calendar.
    """
    # Example: Create futures calendar
    # from lumibot.tools import FuturesCalendar
    # return FuturesCalendar()

    # For now, return None (will use default)
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

            self.portfolio_manager = PortfolioManager(
                strategies_folder="custom_portfolio/strategies/active_strategies",
                broker=self.broker,
                calendar=self.trading_calendar if hasattr(self, "trading_calendar") else None,
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
                ignore_calendar=True,  # For backtests, always process signals regardless of session gating
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
                    print(
                        f"[INFO] Heartbeat: iteration {self._iteration_counter} at {current_time.isoformat()} "
                        f"(calc progress {pct:.2f}%)",
                        flush=True,
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
            """Silence per-iteration stats to avoid I/O overhead; let final results handle reporting."""
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
            save_tearsheet=False,
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


def run_live(args):
    """
    Run the portfolio in live trading mode.

    Args:
        args: Command line arguments
    """
    print("=" * 70)
    print("RUNNING PORTFOLIO LIVE TRADING")
    print("=" * 70)
    print("⚠️  LIVE TRADING MODE - REAL ORDERS WILL BE PLACED")
    print("")
    enable_snapshots, max_snapshots, timestep = _snapshot_config()
    simulate_fills = _simulate_fills_config()
    shared_initial_capital = _capital_config()
    deep_portfolio_debug = _deep_debug_config()

    # Confirmation prompt
    confirm = input("Are you sure you want to run in LIVE mode? (yes/no): ")
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

    # Create calendar
    calendar = create_calendar()

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
        simulate_fills=simulate_fills,
        shared_initial_capital=shared_initial_capital,
        deep_portfolio_debug=deep_portfolio_debug,
    )

    # Print validation report
    print("\n" + portfolio_manager.get_validation_report())

    # Print loaded strategies summary
    summary_df = portfolio_manager.get_loaded_strategies_summary()
    if not summary_df.empty:
        print("\nLoaded Strategies Summary:")
        print(summary_df.to_string())

    print("\n" + "=" * 70)
    print("Starting live trading loop...")
    print("Press Ctrl+C to stop")
    print("=" * 70 + "\n")

    # Main trading loop
    try:
        iteration = 0
        while True:
            iteration += 1
            current_time = datetime.now()

            print(f"\n--- Iteration {iteration} at {current_time} ---")

            # Run one iteration
            result = portfolio_manager.run_iteration(current_time)

            # Print summary
            if result and "orders_submitted" in result:
                print(f"Orders submitted: {result['orders_submitted']}")

            # Sleep for 1 minute (or your desired interval)
            import time

            time.sleep(60)

    except KeyboardInterrupt:
        print("\n\nLive trading stopped by user.")

        # Final performance report
        report = portfolio_manager.get_performance_report()
        if report is not None:
            print("\nFinal Portfolio Performance:")
            print(report)

    except Exception as e:
        print(f"\n❌ Error in live trading: {e}")
        import traceback

        print(traceback.format_exc())


def validate_only(args):
    """
    Validate strategies without running them.

    Args:
        args: Command line arguments
    """
    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

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
        headers = ["Symbol", "Strategy ID", "Session", "Qty", "Params", "Type", "Direction"]
        rows = []
        for _, row in summary_df.iterrows():
            rows.append(
                [
                    str(row["symbol"]),
                    str(row["strategy_id"]),
                    str(row["sessions"]),
                    str(row["qty"]),
                    str(row["params"]),
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
        default="INFO",
        help="Logging level (default: INFO)",
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
