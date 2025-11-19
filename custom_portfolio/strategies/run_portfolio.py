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
from datetime import datetime
from pathlib import Path

# Add repository root to path for custom_portfolio imports
# Path: run_portfolio.py -> strategies/ -> custom_portfolio/ -> repo_root/
repo_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(repo_root))

from custom_portfolio.strategies.portfolio_manager import PortfolioManager  # noqa: E402
from lumibot.backtesting import DataBentoDataBacktesting  # noqa: E402
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


def create_broker_for_backtesting():
    """Create a broker instance for backtesting."""
    # For backtesting, we typically don't need a real broker
    # The backtester handles order execution
    return None


def create_broker_for_live():
    """
    Create a broker instance for live trading.

    This example uses Alpaca, but you can replace with your broker.
    """
    # Example for Alpaca (replace with your credentials)
    # from lumibot.brokers import Alpaca

    # ALPACA_CONFIG = {
    #     "API_KEY": os.environ.get("ALPACA_API_KEY"),
    #     "API_SECRET": os.environ.get("ALPACA_SECRET_KEY"),
    #     "PAPER": True  # Set to False for real trading
    # }

    # broker = Alpaca(ALPACA_CONFIG)
    # return broker

    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    # For now, return None (replace with actual broker setup)
    print(TF.warning("Live trading broker not configured. Please set up your broker in run_portfolio.py"))
    return None


def create_data_source_for_backtesting():
    """Create data source for backtesting."""
    # For backtesting, data comes from the backtester
    return None


def create_data_source_for_live():
    """
    Create data source for live trading.

    This could be your broker's data feed or a separate data provider.
    """
    # Example: Use broker's data (if supported)
    # Or use a separate data provider like Polygon, IEX, etc.

    from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

    # For now, return None (replace with actual data source)
    print(TF.warning("Live data source not configured. Please set up your data source in run_portfolio.py"))
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

        def initialize(self):
            """Initialize the portfolio manager."""
            self.portfolio_manager = PortfolioManager(
                strategies_folder="custom_portfolio/strategies/active_strategies",
                broker=self.broker,
                calendar=self.trading_calendar if hasattr(self, "trading_calendar") else None,
                auto_load=True,
                cache_ttl_seconds=3600,  # 1 hour for backtesting (prevents cache expiry during slow backtests)
                min_order_delay_seconds=2.0,
                default_atr_period=20,
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
            if hasattr(self.broker, "data_source"):
                from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

                symbols = summary_df["symbol"].unique().tolist()
                symbols_str = ", ".join(symbols)
                print("")
                print(f"Prefetching data for {len(symbols)} symbols: {symbols_str}")

                from lumibot.entities import Asset

                assets = [Asset(symbol, asset_type=Asset.AssetType.CONT_FUTURE) for symbol in symbols]

                if hasattr(self.broker.data_source, "prefetch_data"):
                    self.broker.data_source.prefetch_data(assets, timestep="minute")
                    print(TF.success("Data prefetch complete"))

            self.sleeptime = "1M"  # 1-minute bars

        def on_trading_iteration(self):
            """Run one trading iteration."""
            current_time = self.get_datetime()
            self.portfolio_manager.run_iteration(current_time)

        def on_abrupt_closing(self):
            """Handle abrupt closing."""
            print("\nBacktest interrupted")

        def trace_stats(self, context, snapshot_before):
            """Generate performance statistics."""
            # Get performance report from portfolio manager
            if hasattr(self, "portfolio_manager"):
                report = self.portfolio_manager.get_performance_report()
                if report is not None:
                    print("\nPortfolio Performance Report:")
                    print(report)

    # Set up backtesting
    backtesting_start = datetime.strptime(start_date_str, "%Y-%m-%d")
    backtesting_end = datetime.strptime(end_date_str, "%Y-%m-%d")

    # Run backtest using class method (broker configured before initialize())
    # Uses DataBento for futures data (credentials read from .env file)
    results = PortfolioStrategy.backtest(
        datasource_class=DataBentoDataBacktesting,
        backtesting_start=backtesting_start,
        backtesting_end=backtesting_end,
        parameters={},
        buy_trading_fees=[TradingFee(flat_fee=0.75)],  # $0.75 per trade for futures
        sell_trading_fees=[TradingFee(flat_fee=0.75)],
    )

    print("\n" + "=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)

    # Display results
    if results:
        print("\nBacktest Results:")
        print(f"Total Return: {results.get('total_return', 0):.2%}")
        print(f"CAGR: {results.get('cagr', 0):.2%}")
        print(f"Max Drawdown: {results.get('max_drawdown', 0):.2%}")
        print(f"Sharpe Ratio: {results.get('sharpe_ratio', 0):.2f}")
        print(f"Total Trades: {results.get('total_trades', 0)}")


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

    # Confirmation prompt
    confirm = input("Are you sure you want to run in LIVE mode? (yes/no): ")
    if confirm.lower() != "yes":
        print("Live trading cancelled.")
        return

    # Create broker
    broker = create_broker_for_live()
    if broker is None:
        print("❌ Broker not configured. Cannot run live trading.")
        return

    # Create data source
    data_source = create_data_source_for_live()
    if data_source is None:
        print("❌ Data source not configured. Cannot run live trading.")
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

    # Create a minimal portfolio manager for validation
    portfolio_manager = PortfolioManager(
        strategies_folder="custom_portfolio/strategies/active_strategies",
        broker=None,  # Not needed for validation
        calendar=None,  # Not needed for validation
        auto_load=True,
    )

    # Print validation report (includes all formatting and final status)
    print(portfolio_manager.get_validation_report())

    # Print loaded strategies summary as table
    summary_df = portfolio_manager.get_loaded_strategies_summary()
    if not summary_df.empty:
        print("")
        print(TF.section_header("Strategy Details"))
        print("")

        # Create table with only key columns
        headers = ["Symbol", "Strategy ID", "Session", "Qty", "Params", "Type"]
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
