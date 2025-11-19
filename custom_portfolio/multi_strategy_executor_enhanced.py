"""
MultiStrategyExecutor Enhanced - Dynamic Multi-Strategy Trading System with Bracket Orders

This enhanced version adds support for:
- ATR-based bracket orders (profit target and stop loss)
- Time-based exits (max bars in trade)
- Per-strategy entry time tracking
- Dynamic strategy loading capability
- Enhanced order management

Key Features:
- Manages 30-50 independent strategies in single process
- Different strategy types (RSI, MA crossovers, Bollinger Bands, etc.)
- ATR-based bracket orders for all strategies
- Time-based exit management
- Shared data infrastructure (95%+ API call reduction)
- Global rate limiting (2-second delays)
- Independent virtual position tracking per strategy
- Per-strategy performance attribution
- Complete strategy isolation

Performance Targets:
- Memory: <10 MB for 30 strategies
- API calls: <60/min (vs 1800/min unoptimized)
- Execution time: ~60s for 30 sequential orders
- Cache hit rate: >95%

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18 (Enhanced Version)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from custom_portfolio.tools.global_rate_limiter import GlobalRateLimiter
from custom_portfolio.tools.shared_data_manager import SharedDataManager
from custom_portfolio.tools.strategy_attribution import StrategyAttribution
from custom_portfolio.tools.strategy_state import StrategyState
from lumibot.entities import Asset, Order


class EnhancedStrategyState(StrategyState):
    """Extended state with entry tracking for time-based exits."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry_time: Optional[datetime] = None
        self.entry_price: Optional[float] = None
        self.bars_in_trade: int = 0

        # ATR caching for bracket orders
        self.last_atr: Optional[float] = None
        self.last_atr_time: Optional[datetime] = None


class MultiStrategyExecutorEnhanced:
    """
    Enhanced executor with bracket orders and time-based exits.

    This enhanced version supports:
    - Different strategy types running independently
    - ATR-based bracket orders for all strategies
    - Time-based exits based on max bars in trade
    - Dynamic strategy loading and discovery

    Architecture:
        1. Shared Resource Layer
           - SharedDataManager: Caches market data
           - SharedIndicatorCache: Caches ATR/RSI calculations
           - TradingCalendar: Session/platform timing
           - GlobalRateLimiter: 2-second order delays
           - StrategyAttribution: Performance tracking

        2. Strategy Execution Loop
           - Fetch data once for all symbols
           - Calculate shared indicators (ATR)
           - Distribute to all strategies
           - Collect signals independently
           - Execute orders with bracket orders
           - Check time-based exits
           - Update virtual positions
           - Record attribution

        3. Independent Strategy States
           - Each has VirtualPositionTracker
           - Entry time tracking for max bars
           - Isolated pending orders
           - Independent P&L tracking

    Attributes:
        broker: Broker instance (shared across strategies)
        data_source: Data source instance (shared)
        calendar: Trading calendar instance (shared)
        strategies: List of EnhancedStrategyState objects
        shared_data: SharedDataManager instance
        rate_limiter: GlobalRateLimiter instance
        attribution: StrategyAttribution instance
        strategy_modules: Dict of loaded strategy modules
        logger: Logger instance

    Example:
        >>> # Define strategy configurations
        >>> strategy_configs = [
        ...     {
        ...         'strategy_id': 'rsi_mean_revert_1',
        ...         'strategy_type': 'RSIMeanRevert',  # Class name
        ...         'symbol': 'ES',
        ...         'params': {
        ...             'rsi_period': 14,
        ...             'rsi_oversold': 30,
        ...             'rsi_overbought': 70,
        ...             'atr_period': 20,
        ...             'pt_mult': 5.0,  # Profit target as ATR multiple
        ...             'sl_mult': 2.0,  # Stop loss as ATR multiple
        ...             'max_bars': 180  # Max bars in trade
        ...         },
        ...         'contracts': 1,
        ...         'allowed_sessions': ['New_York']
        ...     },
        ...     {
        ...         'strategy_id': 'ma_crossover_1',
        ...         'strategy_type': 'MACrossover',
        ...         'symbol': 'ES',
        ...         'params': {
        ...             'fast_period': 10,
        ...             'slow_period': 20,
        ...             'atr_period': 20,
        ...             'pt_mult': 4.0,
        ...             'sl_mult': 2.5,
        ...             'max_bars': 120
        ...         },
        ...         'contracts': 1,
        ...         'allowed_sessions': ['New_York']
        ...     },
        ...     # ... more strategies with different types
        ... ]
        >>>
        >>> # Create executor with dynamic strategy loading
        >>> executor = MultiStrategyExecutorEnhanced(
        ...     broker=broker,
        ...     data_source=data_source,
        ...     calendar=calendar,
        ...     strategy_configs=strategy_configs
        ... )
        >>>
        >>> # Run trading iteration
        >>> executor.on_trading_iteration()
    """

    def __init__(
        self,
        broker,
        data_source,
        calendar,
        strategy_configs: List[Dict[str, Any]],
        cache_ttl_seconds: int = 60,
        min_order_delay_seconds: float = 2.0,
        default_atr_period: int = 20,
    ):
        """
        Initialize the enhanced MultiStrategyExecutor.

        Args:
            broker: Broker instance for order submission
            data_source: Data source for market data
            calendar: Trading calendar for timing checks
            strategy_configs: List of strategy configuration dictionaries
            cache_ttl_seconds: Cache TTL for data (default: 60)
            min_order_delay_seconds: Minimum delay between orders (default: 2.0)
            default_atr_period: Default ATR period if not specified (default: 20)

        Note:
            Tick sizes are now automatically looked up per symbol from futures_metadata
        """
        self.broker = broker
        self.data_source = data_source
        self.calendar = calendar
        self.default_atr_period = default_atr_period

        # Initialize shared resources
        self.shared_data = SharedDataManager(data_source, cache_ttl_seconds)
        self.rate_limiter = GlobalRateLimiter(min_order_delay_seconds)
        self.attribution = StrategyAttribution()

        # Dictionary to hold loaded strategy modules for dynamic loading
        self.strategy_modules = {}

        # Create enhanced strategy states
        self.strategies: List[EnhancedStrategyState] = []
        for config in strategy_configs:
            state = EnhancedStrategyState(
                strategy_id=config["strategy_id"],
                symbol=config["symbol"],
                params=config["params"],
                contracts=config.get("contracts", 1),
                allowed_sessions=config.get("allowed_sessions", ["New_York"]),
            )

            # Store strategy type for dynamic loading
            state.strategy_type = config.get("strategy_type", "DefaultStrategy")

            self.strategies.append(state)

            # Register with attribution tracker
            self.attribution.register_strategy(
                config["strategy_id"], initial_capital=config.get("initial_capital", 0.0)
            )

        # Logger
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Initialized Enhanced MultiStrategyExecutor with {len(self.strategies)} strategies")

        # Statistics
        self.iteration_count = 0
        self.total_orders_submitted = 0

    def _wilder_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """Calculate Wilder's ATR (Average True Range)."""
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        return atr

    def _round_to_tick(self, price: Optional[float], symbol: str) -> Optional[float]:
        """
        Round price to nearest tick size for the given symbol.

        Args:
            price: Price to round
            symbol: Symbol to get tick_size for

        Returns:
            Rounded price
        """
        if price is None:
            return price

        # Import locally to avoid circular dependencies
        from lumibot.data.futures_metadata import get_tick_size

        tick_size = get_tick_size(symbol)
        if tick_size <= 0:
            return price

        return round(round(price / tick_size) * tick_size, 10)

    def _compute_bracket_prices(
        self, market_data: pd.DataFrame, strategy_state: EnhancedStrategyState, side: str
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Compute bracket order prices (TP and SL) based on ATR.

        Args:
            market_data: Market data DataFrame
            strategy_state: Strategy state object
            side: 'buy' or 'sell'

        Returns:
            Tuple of (take_profit_price, stop_loss_price)
        """
        # Get ATR parameters from strategy
        atr_period = int(strategy_state.params.get("atr_period", self.default_atr_period))
        pt_mult = float(strategy_state.params.get("pt_mult", 0.0))
        sl_mult = float(strategy_state.params.get("sl_mult", 0.0))
        use_tp = bool(strategy_state.params.get("use_atr_profit", True))
        use_sl = bool(strategy_state.params.get("use_atr_stop", True))

        # Check if we have enough data for ATR calculation
        if len(market_data) < atr_period + 1:
            self.logger.warning(f"Insufficient data for ATR calculation ({len(market_data)} < {atr_period + 1})")
            return None, None

        # Calculate ATR on recent data
        atr_data_length = min(atr_period + 1, len(market_data))
        high_series = market_data["high"].tail(atr_data_length)
        low_series = market_data["low"].tail(atr_data_length)
        close_series = market_data["close"].tail(atr_data_length)

        # Calculate ATR
        atr = self._wilder_atr(high_series, low_series, close_series, atr_period)

        if atr.isna().iloc[-1]:
            self.logger.warning(f"ATR calculation returned NaN for {strategy_state.strategy_id}")
            return None, None

        baseline = float(close_series.iloc[-1])
        atr_now = float(atr.iloc[-1])

        # Store ATR for reference
        strategy_state.last_atr = atr_now
        strategy_state.last_atr_time = datetime.now()

        # Calculate bracket prices based on side
        if side.lower() == "buy":
            tp = baseline + atr_now * pt_mult if use_tp and pt_mult > 0 else None
            sl = baseline - atr_now * sl_mult if use_sl and sl_mult > 0 else None
        else:  # sell
            tp = baseline - atr_now * pt_mult if use_tp and pt_mult > 0 else None
            sl = baseline + atr_now * sl_mult if use_sl and sl_mult > 0 else None

        # Round to tick size for this symbol
        tp = self._round_to_tick(tp, strategy_state.symbol)
        sl = self._round_to_tick(sl, strategy_state.symbol)

        return tp, sl

    def _check_time_exit(self, strategy_state: EnhancedStrategyState, market_data: pd.DataFrame) -> bool:
        """
        Check if position should be exited based on time (max bars in trade).

        Args:
            strategy_state: Strategy state object
            market_data: Market data DataFrame

        Returns:
            True if time exit was triggered, False otherwise
        """
        max_bars = strategy_state.params.get("max_bars", None)
        if max_bars is None:
            return False

        # Check if we have a position
        qty = strategy_state.get_current_position_qty()
        if qty == 0:
            return False

        # Check if we have an entry time
        if strategy_state.entry_time is None:
            return False

        # Count bars since entry (using DataFrame index)
        if len(market_data) == 0:
            return False

        # Find bars since entry time
        bars_since = int((market_data.index > strategy_state.entry_time).sum())
        strategy_state.bars_in_trade = bars_since

        # Check if max bars exceeded
        if bars_since >= int(max_bars):
            self.logger.info(
                f"{strategy_state.strategy_id}: Time exit triggered " f"({bars_since} bars >= {max_bars} max bars)"
            )
            return True

        return False

    def on_trading_iteration(self, current_time: datetime = None) -> Dict[str, Any]:
        """
        Main execution loop for all strategies with bracket order and time exit support.

        This enhanced version:
        1. Fetches data once for all symbols
        2. Calculates shared indicators (ATR)
        3. Checks time-based exits first
        4. Processes new signals with bracket orders
        5. Executes orders sequentially with rate limiting

        Args:
            current_time: Current datetime (defaults to now)

        Returns:
            Dictionary with iteration summary
        """
        if current_time is None:
            current_time = datetime.now()

        self.iteration_count += 1

        self.logger.info(f"=== Enhanced Multi-Strategy Iteration #{self.iteration_count} at {current_time} ===")

        # Statistics for this iteration
        strategies_processed = 0
        signals_generated = 0
        time_exits_triggered = 0
        orders_to_submit = []

        # Step 1: Fetch data once for all unique symbols
        symbols = list(set(strategy.symbol for strategy in self.strategies))
        self.logger.debug(f"Fetching data for {len(symbols)} unique symbols: {symbols}")

        # Fetch with enough history for ATR calculations
        max_lookback = max(
            strategy.params.get("atr_period", self.default_atr_period) + 10 for strategy in self.strategies
        )

        self.shared_data.fetch_for_all_strategies(
            symbols=symbols,
            length=max_lookback,
            timestep="1M",  # 1-minute bars
            asset_type=Asset.AssetType.CONT_FUTURE,
        )

        # Step 2: Process each strategy independently
        for strategy_state in self.strategies:
            try:
                # 2a. Get cached data (no API call)
                market_data = self.shared_data.get_cached_data(strategy_state.symbol, max_lookback, "1M")

                if market_data is None:
                    self.logger.warning(
                        f"No cached data for {strategy_state.strategy_id} " f"({strategy_state.symbol}), skipping"
                    )
                    continue

                # Convert to DataFrame if needed
                if hasattr(market_data, "df"):
                    df = market_data.df
                else:
                    df = market_data

                # 2b. Check for time-based exit FIRST (before new signals)
                virtual_pos = strategy_state.tracker.get_position(strategy_state.symbol)
                virtual_qty = virtual_pos.quantity if virtual_pos else 0.0

                if virtual_qty != 0 and self._check_time_exit(strategy_state, df):
                    time_exits_triggered += 1
                    close_order = self._create_close_order(strategy_state, virtual_qty)
                    if close_order:
                        orders_to_submit.append((strategy_state, close_order, True, "time_exit"))
                        # Clear entry tracking after time exit
                        strategy_state.entry_time = None
                        strategy_state.entry_price = None
                        strategy_state.bars_in_trade = 0
                    continue

                # 2c. Check calendar/timing (only if calendar is available)
                if self.calendar is not None:
                    status = self.calendar.get_status(
                        strategy_state.symbol,
                        current_time,
                        virtual_qty,
                        None,
                        allowed_sessions=strategy_state.allowed_sessions,
                    )

                    # 2d. Skip if platform closed
                    if not status.platform_open:
                        self.logger.debug(f"{strategy_state.strategy_id}: Platform closed ({status.platform_reason})")
                        continue

                    # 2e. Skip if can't enter new positions
                    if not status.can_enter_orders and virtual_qty == 0:
                        self.logger.debug(
                            f"{strategy_state.strategy_id}: Cannot enter new orders " f"(session restrictions)"
                        )
                        continue

                    # 2f. Force close if required by calendar
                    if status.must_be_flat and virtual_qty != 0:
                        self.logger.info(
                            f"{strategy_state.strategy_id}: Force closing position " f"({status.close_reason})"
                        )
                        close_order = self._create_close_order(strategy_state, virtual_qty)
                        if close_order:
                            orders_to_submit.append((strategy_state, close_order, True, "force_close"))
                        continue

                # 2g. Skip new signals if already in position
                if virtual_qty != 0:
                    continue

                # 2h. Generate signal using strategy-specific logic
                signal = self._generate_signal_for_strategy(strategy_state, df)
                strategy_state.last_signal = signal
                strategy_state.last_signal_time = current_time

                strategies_processed += 1

                # 2i. Create order with bracket if signal generated
                if signal in ["BUY", "SELL"]:
                    signals_generated += 1
                    order = self._create_bracket_order_for_strategy(strategy_state, signal, df)
                    if order:
                        orders_to_submit.append((strategy_state, order, False, "entry"))

            except Exception as e:
                self.logger.error(f"Error processing strategy {strategy_state.strategy_id}: {e}", exc_info=True)
                continue

        # Step 3: Execute all orders sequentially with rate limiting
        orders_submitted = self._execute_all_orders(orders_to_submit, current_time)

        # Step 4: Log iteration summary
        cache_stats = self.shared_data.get_cache_stats()
        rate_limiter_stats = self.rate_limiter.get_stats()

        summary = {
            "iteration": self.iteration_count,
            "timestamp": current_time,
            "strategies_processed": strategies_processed,
            "signals_generated": signals_generated,
            "time_exits_triggered": time_exits_triggered,
            "orders_submitted": orders_submitted,
            "cache_stats": cache_stats,
            "rate_limiter_stats": rate_limiter_stats,
        }

        self.logger.info(
            f"Iteration complete: {strategies_processed} strategies processed, "
            f"{signals_generated} signals, {time_exits_triggered} time exits, "
            f"{orders_submitted} orders, cache hit rate: {cache_stats['hit_rate']:.1f}%"
        )

        return summary

    def _generate_signal_for_strategy(self, strategy_state: EnhancedStrategyState, market_data: pd.DataFrame) -> str:
        """
        Generate signal for a specific strategy type.

        This is a placeholder that will be replaced by dynamic strategy loading.
        Each strategy type will have its own signal generation logic.

        Args:
            strategy_state: Strategy state object
            market_data: Market data DataFrame

        Returns:
            Signal: 'BUY', 'SELL', or 'HOLD'
        """
        # This will be replaced by dynamic strategy loading
        # For now, return HOLD
        return "HOLD"

    def _create_bracket_order_for_strategy(
        self, strategy_state: EnhancedStrategyState, signal: str, market_data: pd.DataFrame
    ) -> Optional[Order]:
        """
        Create a bracket order (market entry with TP/SL) for a strategy.

        Args:
            strategy_state: Strategy state object
            signal: Signal ('BUY' or 'SELL')
            market_data: Market data DataFrame

        Returns:
            Order object with bracket configuration or None
        """
        try:
            asset = Asset(strategy_state.symbol, asset_type=Asset.AssetType.CONT_FUTURE)

            # Determine order side
            if signal == "BUY":
                side = "buy"
            elif signal == "SELL":
                side = "sell"
            else:
                return None

            # Calculate bracket prices based on ATR
            tp_price, sl_price = self._compute_bracket_prices(market_data, strategy_state, side)

            # Create bracket order
            order = Order(
                strategy_state.strategy_id,
                asset,
                strategy_state.contracts,
                side,
                order_type=Order.OrderType.MARKET,
                order_class=Order.OrderClass.BRACKET,
                secondary_limit_price=tp_price,  # Take profit
                secondary_stop_price=sl_price,  # Stop loss
            )

            self.logger.info(
                f"Created bracket order for {strategy_state.strategy_id}: "
                f"{side} {strategy_state.contracts} {asset.symbol}, "
                f"TP={tp_price}, SL={sl_price}"
            )

            return order

        except Exception as e:
            self.logger.error(f"Failed to create bracket order for {strategy_state.strategy_id}: {e}")
            return None

    def _create_close_order(self, strategy_state: EnhancedStrategyState, current_qty: float) -> Optional[Order]:
        """
        Create an order to close current position.

        Args:
            strategy_state: Strategy state object
            current_qty: Current position quantity (signed)

        Returns:
            Order object or None
        """
        try:
            asset = Asset(strategy_state.symbol, asset_type=Asset.AssetType.CONT_FUTURE)

            # Close long position -> sell
            if current_qty > 0:
                order = Order(
                    strategy_state.strategy_id, asset, abs(current_qty), "sell", order_type=Order.OrderType.MARKET
                )
            # Close short position -> buy
            elif current_qty < 0:
                order = Order(
                    strategy_state.strategy_id, asset, abs(current_qty), "buy", order_type=Order.OrderType.MARKET
                )
            else:
                return None

            return order

        except Exception as e:
            self.logger.error(f"Failed to create close order for {strategy_state.strategy_id}: {e}")
            return None

    def _execute_all_orders(self, orders_to_submit: List[tuple], current_time: datetime) -> int:
        """
        Execute all orders sequentially with global rate limiting.

        Args:
            orders_to_submit: List of (strategy_state, order, is_close, reason) tuples
            current_time: Current timestamp

        Returns:
            Number of orders successfully submitted
        """
        orders_submitted = 0

        for strategy_state, order, is_close, reason in orders_to_submit:
            try:
                # Wait for rate limit
                wait_time = self.rate_limiter.wait_if_needed()

                if wait_time > 0:
                    self.logger.debug(
                        f"Rate limited: waited {wait_time:.2f}s before "
                        f"submitting order for {strategy_state.strategy_id}"
                    )

                # Submit order to broker
                self.logger.info(
                    f"Submitting order for {strategy_state.strategy_id} ({reason}): "
                    f"{order.side} {order.quantity} {order.asset.symbol}"
                )

                self.broker.submit_order(order)

                # Mark order as submitted for rate limiting
                self.rate_limiter.mark_order_submitted()

                # Update virtual position immediately (assume market orders fill)
                # Get current price from cached data
                market_data = self.shared_data.get_cached_data(strategy_state.symbol, 100, "1M")

                if market_data is not None:
                    if hasattr(market_data, "df"):
                        df = market_data.df
                    else:
                        df = market_data

                    # Assume fill at last close price
                    current_price = float(df["close"].iloc[-1])

                    strategy_state.tracker.execute_order(
                        strategy_state.symbol, order.quantity, order.side, current_price
                    )

                    # Track entry time and price for new positions
                    if not is_close and reason == "entry":
                        strategy_state.entry_time = current_time
                        strategy_state.entry_price = current_price
                        strategy_state.bars_in_trade = 0

                    # Clear entry tracking for closes
                    if is_close:
                        strategy_state.entry_time = None
                        strategy_state.entry_price = None
                        strategy_state.bars_in_trade = 0

                    self.logger.debug(
                        f"Updated virtual position for {strategy_state.strategy_id}: "
                        f"{strategy_state.tracker.get_position(strategy_state.symbol)}"
                    )

                orders_submitted += 1
                self.total_orders_submitted += 1

            except Exception as e:
                self.logger.error(f"Failed to execute order for {strategy_state.strategy_id}: {e}", exc_info=True)
                continue

        return orders_submitted

    def get_strategy_state(self, strategy_id: str) -> Optional[EnhancedStrategyState]:
        """Get strategy state by ID."""
        for strategy in self.strategies:
            if strategy.strategy_id == strategy_id:
                return strategy
        return None

    def get_all_strategies(self) -> List[EnhancedStrategyState]:
        """Get list of all strategy states."""
        return self.strategies

    def get_performance_report(self) -> Any:
        """Generate comprehensive performance report for all strategies."""
        return self.attribution.generate_report()

    def get_summary(self) -> Dict[str, Any]:
        """Get complete executor summary."""
        return {
            "strategy_count": len(self.strategies),
            "iteration_count": self.iteration_count,
            "total_orders_submitted": self.total_orders_submitted,
            "cache_stats": self.shared_data.get_cache_stats(),
            "rate_limiter_stats": self.rate_limiter.get_stats(),
            "total_pnl": self.attribution.get_total_pnl(),
            "best_strategy": self.attribution.get_best_strategy(),
            "worst_strategy": self.attribution.get_worst_strategy(),
        }

    def __repr__(self) -> str:
        """String representation of executor."""
        summary = self.get_summary()
        return (
            f"MultiStrategyExecutorEnhanced("
            f"strategies={summary['strategy_count']}, "
            f"iterations={summary['iteration_count']}, "
            f"orders={summary['total_orders_submitted']}, "
            f"total_pnl=${summary['total_pnl']:+.2f})"
        )
