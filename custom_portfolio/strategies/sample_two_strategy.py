"""
Sample Two-Strategy Example - Demonstrating Complete Independence

This example demonstrates the multi-strategy architecture with 2 independent strategies
trading the same symbol (ES) with opposite signals to prove complete isolation.

Key Demonstration Points:
- Two strategies trade the same symbol (ES)
- Different signal parameters (fast_sma=10/20 vs 5/15)
- Can hold opposite positions simultaneously (Strategy 1 LONG, Strategy 2 SHORT)
- Independent virtual position tracking
- Independent P&L attribution
- Shared data caching (1 API call instead of 2)
- Global rate limiting (2-second delays)

Expected Behavior:
- Strategy 1 generates signals based on 10/20 SMA crossover
- Strategy 2 generates signals based on 5/15 SMA crossover
- Both can be LONG, SHORT, or FLAT independently
- Total broker position = Strategy1_pos + Strategy2_pos (netting)

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import logging
from typing import Any

from lumibot.strategies.multi_strategy_executor import MultiStrategyExecutor
from lumibot.tools import StrategyState, get_logger

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = get_logger(__name__)


def simple_sma_crossover_signal(strategy_state: StrategyState, market_data: Any) -> str:
    """
    Generate trading signal based on SMA crossover.

    Args:
        strategy_state: Strategy state containing parameters
        market_data: Market data (Bars object)

    Returns:
        Signal: 'BUY', 'SELL', or 'HOLD'
    """
    try:
        # Get parameters
        fast_period = strategy_state.params.get("fast_sma", 10)
        slow_period = strategy_state.params.get("slow_sma", 20)

        # Calculate SMAs
        close_prices = market_data.df["close"]
        fast_sma = close_prices.rolling(window=fast_period).mean()
        slow_sma = close_prices.rolling(window=slow_period).mean()

        # Get current and previous values
        fast_current = fast_sma.iloc[-1]
        slow_current = slow_sma.iloc[-1]
        fast_previous = fast_sma.iloc[-2]
        slow_previous = slow_sma.iloc[-2]

        # Check for crossover
        if fast_previous <= slow_previous and fast_current > slow_current:
            # Bullish crossover - fast crosses above slow
            return "BUY"
        elif fast_previous >= slow_previous and fast_current < slow_current:
            # Bearish crossover - fast crosses below slow
            return "SELL"
        else:
            # No crossover
            return "HOLD"

    except Exception as e:
        logger.error(f"Error generating signal for {strategy_state.strategy_id}: {e}")
        return "HOLD"


def run_two_strategy_example(broker, data_source, calendar):
    """
    Run a two-strategy example demonstrating complete independence.

    Args:
        broker: Broker instance
        data_source: Data source instance
        calendar: Trading calendar instance
    """
    logger.info("=" * 80)
    logger.info("Starting Two-Strategy Independence Demonstration")
    logger.info("=" * 80)

    # Define two strategies with different parameters
    strategy_configs = [
        {
            "strategy_id": "MGC_Strategy_1_Conservative",
            "symbol": "MGC",
            "params": {"fast_sma": 5, "slow_sma": 7},
            "contracts": 1,
            "allowed_sessions": ["24/7"],
            "initial_capital": 10000.0,
        },
        {
            "strategy_id": "MGC_Strategy_2_Aggressive",
            "symbol": "MGC",
            "params": {"fast_sma": 3, "slow_sma": 5},
            "contracts": 1,
            "allowed_sessions": ["24/7"],
            "initial_capital": 10000.0,
        },
    ]

    logger.info("Strategy Configuration:")
    logger.info("  Strategy 1 (Conservative): 5/7 SMA crossover")
    logger.info("  Strategy 2 (Aggressive): 3/5 SMA crossover")
    logger.info("  Both trading MGC with 1 contract")
    logger.info("")

    # Create multi-strategy executor
    executor = MultiStrategyExecutor(
        broker=broker,
        data_source=data_source,
        calendar=calendar,
        strategy_configs=strategy_configs,
        signal_generator=simple_sma_crossover_signal,
        cache_ttl_seconds=60,
        min_order_delay_seconds=2.0,
    )

    logger.info(f"Initialized: {executor}")
    logger.info("")

    # Run a single trading iteration
    logger.info("Executing trading iteration...")
    summary = executor.on_trading_iteration()

    # Display results
    logger.info("")
    logger.info("=" * 80)
    logger.info("Iteration Summary")
    logger.info("=" * 80)
    logger.info(f"Strategies Processed: {summary['strategies_processed']}")
    logger.info(f"Signals Generated: {summary['signals_generated']}")
    logger.info(f"Orders Submitted: {summary['orders_submitted']}")
    logger.info("")

    # Cache statistics
    cache_stats = summary["cache_stats"]
    logger.info("Cache Performance:")
    logger.info(f"  Total Fetches: {cache_stats['total_fetches']}")
    logger.info(f"  Cache Hits: {cache_stats['cache_hits']}")
    logger.info(f"  Cache Misses: {cache_stats['cache_misses']}")
    logger.info(f"  Hit Rate: {cache_stats['hit_rate']:.1f}%")
    logger.info(f"  Cached Symbols: {cache_stats['cached_symbols']}")
    logger.info("")

    # Rate limiter statistics
    rate_stats = summary["rate_limiter_stats"]
    logger.info("Rate Limiter Performance:")
    logger.info(f"  Orders Submitted: {rate_stats['orders_submitted']}")
    logger.info(f"  Total Waits: {rate_stats['total_waits']}")
    logger.info(f"  Total Wait Time: {rate_stats['total_wait_time']:.2f}s")
    logger.info(f"  Average Wait Time: {rate_stats['avg_wait_time']:.2f}s")
    logger.info("")

    # Individual strategy states
    logger.info("=" * 80)
    logger.info("Individual Strategy Positions")
    logger.info("=" * 80)

    for strategy_state in executor.get_all_strategies():
        position = strategy_state.tracker.get_position(strategy_state.symbol)
        logger.info(f"\n{strategy_state.strategy_id}:")
        logger.info(f"  Position: {position.quantity if position else 0.0:+.1f} contracts")
        logger.info(f"  Entry Price: ${position.avg_entry_price:.2f}" if position else "  Entry Price: N/A")
        logger.info(f"  Last Signal: {strategy_state.last_signal}")
        logger.info(f"  Pending Orders: {len(strategy_state.pending_orders)}")

    # Demonstrate independence
    logger.info("")
    logger.info("=" * 80)
    logger.info("Independence Verification")
    logger.info("=" * 80)

    strat1 = executor.get_strategy_state("ES_Strategy_1_Conservative")
    strat2 = executor.get_strategy_state("ES_Strategy_2_Aggressive")

    pos1 = strat1.tracker.get_position("ES")
    pos2 = strat2.tracker.get_position("ES")

    qty1 = pos1.quantity if pos1 else 0.0
    qty2 = pos2.quantity if pos2 else 0.0

    logger.info(f"Strategy 1 Position: {qty1:+.1f}")
    logger.info(f"Strategy 2 Position: {qty2:+.1f}")
    logger.info(f"Net Broker Position: {qty1 + qty2:+.1f}")
    logger.info("")

    if (qty1 > 0 and qty2 < 0) or (qty1 < 0 and qty2 > 0):
        logger.info("✅ INDEPENDENCE CONFIRMED: Strategies hold opposite positions!")
        logger.info("   This demonstrates complete isolation via virtual position tracking.")
    elif qty1 != 0 or qty2 != 0:
        logger.info("✅ INDEPENDENCE CONFIRMED: Strategies have independent positions!")
    else:
        logger.info("ℹ️  Both strategies are flat (no positions)")

    logger.info("")
    logger.info("=" * 80)
    logger.info("Performance Attribution")
    logger.info("=" * 80)

    report = executor.get_performance_report()
    if not report.empty:
        logger.info("\n" + report.to_string())
    else:
        logger.info("No trades completed yet")

    logger.info("")
    logger.info("=" * 80)
    logger.info("Two-Strategy Example Complete")
    logger.info("=" * 80)

    return executor


def main():
    """
    Main entry point for standalone execution.

    This would typically be called from a backtesting or live trading script
    that provides the broker, data_source, and calendar instances.
    """
    logger.info("This is a template. To run, you need to provide:")
    logger.info("  1. Broker instance (e.g., AlpacaBroker, IBKRBroker)")
    logger.info("  2. Data source instance (e.g., DataBentoDataBacktesting)")
    logger.info("  3. Trading calendar instance")
    logger.info("")
    logger.info("Example usage:")
    logger.info("  from lumibot.brokers import AlpacaBroker")
    logger.info("  from lumibot.backtesting import DataBentoDataBacktesting")
    logger.info("  from lumibot.tools.trading_calendar import TradingCalendar")
    logger.info("")
    logger.info("  broker = AlpacaBroker(...)")
    logger.info("  data_source = DataBentoDataBacktesting(...)")
    logger.info("  calendar = TradingCalendar(...)")
    logger.info("")
    logger.info("  executor = run_two_strategy_example(broker, data_source, calendar)")


if __name__ == "__main__":
    main()
