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

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from custom_portfolio.data.futures_metadata import get_multiplier, get_per_order_fee
from custom_portfolio.tools.global_rate_limiter import GlobalRateLimiter
from custom_portfolio.tools.shared_data_manager import SharedDataManager
from custom_portfolio.tools.strategy_attribution import StrategyAttribution
from custom_portfolio.tools.strategy_state import StrategyState
from lumibot.entities import Asset, Order

# Terminal color codes
_YELLOW = "\x1b[33m"
_BRIGHT_YELLOW = "\x1b[93m"
_RESET = "\x1b[0m"

# Metadata verification constants
_METADATA_STALENESS_DAYS = 60
_METADATA_VERIFICATION_FILE = Path(__file__).parent.parent.parent / ".metadata_last_verified"


class EnhancedStrategyState(StrategyState):
    """Extended state with entry tracking for time-based exits."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry_time: Optional[datetime] = None
        self.entry_price: Optional[float] = None
        self.entry_iteration: Optional[int] = None
        self.bars_in_trade: int = 0
        self.entry_side: Optional[str] = None
        self.take_profit_price: Optional[float] = None
        self.stop_loss_price: Optional[float] = None
        self.pending_tp: Optional[float] = None
        self.pending_sl: Optional[float] = None

        # ATR caching for bracket orders
        self.last_atr: Optional[float] = None
        self.last_atr_time: Optional[datetime] = None

        # Simple realized P&L tracking and trade counts (per fill)
        self.realized_pnl: float = 0.0
        self.trade_count: int = 0
        self.total_fees_paid: float = 0.0
        self.fees_since_entry: float = 0.0


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
        enable_snapshots: bool = True,
        simulate_fills: bool = True,
        max_snapshots: int = 200000,
        timestep: str = "minute",
        shared_initial_capital: float = 150000.0,
        ignore_calendar: bool = False,
        broker_strategy_name: Optional[str] = None,
        deep_portfolio_debug: bool = False,
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
            ignore_calendar: If True, skip calendar/session gating (useful for backtests)
            broker_strategy_name: Optional wrapper strategy name to tag real broker orders
            deep_portfolio_debug: Emit verbose per-iteration logs when True

        Note:
            Tick sizes are now automatically looked up per symbol from futures_metadata
        """
        self.broker = broker
        self.data_source = data_source
        self.calendar = calendar
        self.default_atr_period = default_atr_period
        self.MIN_LOOKBACK_FLOOR = 100
        self.MAX_LOOKBACK_CAP = 400
        self.timestep = timestep

        # Initialize shared resources
        self.shared_data = SharedDataManager(data_source, cache_ttl_seconds, verbose_logging=deep_portfolio_debug)
        self.rate_limiter = GlobalRateLimiter(min_order_delay_seconds)
        self.attribution = StrategyAttribution(max_snapshots=max_snapshots)
        self.shared_initial_capital = shared_initial_capital
        self.ignore_calendar = ignore_calendar
        self.broker_strategy_name = broker_strategy_name
        self.deep_portfolio_debug = deep_portfolio_debug
        self.total_initial_capital = shared_initial_capital
        self.min_bars = 90  # TEMPORARY: reduced for testing (was 300)

        # Dictionary to hold loaded strategy modules for dynamic loading
        self.strategy_modules = {}

        # Pre-compute shared lookback window (cap at 400 by requirement)
        self.max_lookback = 0

        # Create enhanced strategy states
        self.strategies: List[EnhancedStrategyState] = []
        self.strategy_count = max(1, len(strategy_configs))
        per_strategy_capital = (
            shared_initial_capital / self.strategy_count if self.strategy_count else shared_initial_capital
        )

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
            self.attribution.register_strategy(config["strategy_id"], initial_capital=per_strategy_capital)

            # Track required lookback (include common indicators)
            p = state.params
            required = max(
                int(p.get("atr_period", self.default_atr_period)) + 10,
                int(p.get("rsi_period", 0)) + 2,
                int(p.get("sma_period", 0)) + 2,
                int(p.get("lookback", 0)),
                1,
            )
            self.max_lookback = max(self.max_lookback, required)

        # Cap lookback to 400 bars as requested; ensure minimum 100 for stability
        self.max_lookback = min(self.MAX_LOOKBACK_CAP, max(self.max_lookback, self.MIN_LOOKBACK_FLOOR))

        # Snapshot toggles
        self.enable_snapshots = enable_snapshots
        self.simulate_fills = simulate_fills

        # Logger
        self.logger = logging.getLogger(__name__)
        self._log_verbose(
            f"Initialized Enhanced MultiStrategyExecutor with {len(self.strategies)} strategies"
            f"{' (calendar disabled for backtest)' if self.ignore_calendar else ''}"
        )

        # Statistics
        self.iteration_count = 0
        self.total_orders_submitted = 0

        # Check metadata staleness on startup
        self._check_metadata_staleness()

    def _check_metadata_staleness(self) -> None:
        """Check if futures metadata verification is stale and warn if needed."""
        try:
            if not _METADATA_VERIFICATION_FILE.exists():
                self._print_metadata_warning("Metadata verification file not found")
                return

            with open(_METADATA_VERIFICATION_FILE) as f:
                data = json.load(f)

            timestamp_str = data.get("timestamp")
            if not timestamp_str:
                self._print_metadata_warning("No timestamp in verification file")
                return

            last_verified = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            days_old = (datetime.now(last_verified.tzinfo) - last_verified).days

            if days_old > _METADATA_STALENESS_DAYS:
                self._print_metadata_warning(f"Metadata verification is {days_old} days old")

        except Exception as e:
            self.logger.debug(f"Could not check metadata staleness: {e}")

    def _print_metadata_warning(self, reason: str) -> None:
        """Print a prominent metadata staleness warning."""
        box_width = 64
        msg1 = f"METADATA VERIFICATION STALE ({reason})"
        msg2 = "Run: python tools/verify_topstep_metadata.py"

        print(f"\n{_BRIGHT_YELLOW}")
        print("=" * box_width)
        print(f"  WARNING: {msg1}")
        print(f"  {msg2}")
        print("=" * box_width)
        print(f"{_RESET}\n")

    def print_multiplier_warnings(self) -> None:
        """Print warning if any symbols had multiplier default to 1.0 during the session."""
        from lumibot.tools.virtual_position_tracker import get_multiplier_defaults

        defaults = get_multiplier_defaults()
        if not defaults:
            return

        box_width = 64
        print(f"\n{_BRIGHT_YELLOW}")
        print("=" * box_width)
        print("  WARNING: MULTIPLIER DEFAULTED TO 1.0 FOR THESE SYMBOLS:")
        for symbol in sorted(defaults):
            print(f"      - {symbol} (P/L may be incorrect by 10-100x)")
        print("  Run: python tools/verify_topstep_metadata.py to fix")
        print("=" * box_width)
        print(f"{_RESET}\n")

    def _wilder_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """Calculate Wilder's ATR (Average True Range)."""
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()
        return atr

    def _log_verbose(self, message: str, level: str = "info") -> None:
        """
        Emit verbose logs only when deep_portfolio_debug is enabled; otherwise send to debug.
        """
        if self.deep_portfolio_debug:
            if level.lower() == "debug":
                self.logger.debug(message)
            else:
                self.logger.info(message)
        else:
            self.logger.debug(message)

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
        try:
            from custom_portfolio.data.futures_metadata import get_tick_size
        except Exception:
            try:
                from lumibot.data.futures_metadata import get_tick_size  # type: ignore
            except Exception:
                get_tick_size = None

        tick_size = get_tick_size(symbol) if callable(get_tick_size) else 0
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
        # Accept both legacy (pt_mult/sl_mult) and bracket_config keys (profit_target_mult/stop_loss_mult)
        pt_mult = float(strategy_state.params.get("pt_mult", strategy_state.params.get("profit_target_mult", 0.0)))
        sl_mult = float(strategy_state.params.get("sl_mult", strategy_state.params.get("stop_loss_mult", 0.0)))
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

    def _check_bracket_hit(self, strategy_state: EnhancedStrategyState, market_data: pd.DataFrame):
        """
        Check whether the latest bar hits TP/SL.

        Returns:
            (hit, price, reason)
        """
        if market_data is None or len(market_data) == 0:
            return False, None, None

        pos = strategy_state.tracker.get_position(strategy_state.symbol)
        if pos is None or pos.quantity == 0:
            return False, None, None

        last_bar = market_data.iloc[-1]
        high = last_bar.get("high", last_bar.get("close"))
        low = last_bar.get("low", last_bar.get("close"))

        tp = strategy_state.take_profit_price
        sl = strategy_state.stop_loss_price
        qty = pos.quantity

        if qty > 0:
            if sl is not None and low is not None and low <= sl:
                return True, float(sl), "bracket_sl"
            if tp is not None and high is not None and high >= tp:
                return True, float(tp), "bracket_tp"
        elif qty < 0:
            if sl is not None and high is not None and high >= sl:
                return True, float(sl), "bracket_sl"
            if tp is not None and low is not None and low <= tp:
                return True, float(tp), "bracket_tp"

        return False, None, None

    def _check_time_exit(self, strategy_state: EnhancedStrategyState, market_data: pd.DataFrame) -> bool:
        """
        Check if position should be exited based on time (max bars in trade).

        Args:
            strategy_state: Strategy state object
            market_data: Market data DataFrame

        Returns:
            True if time exit was triggered, False otherwise
        """
        max_bars = (
            strategy_state.params.get("max_bars")
            or strategy_state.params.get("max_bars_in_trade")
            or strategy_state.params.get("max_bars_in_position")
        )
        # Provide a default so dummy strategies actually exit
        try:
            if max_bars is None or pd.isna(max_bars) or float(max_bars) <= 0:
                max_bars = 50
        except Exception:
            max_bars = 50

        # Check if we have a position
        qty = strategy_state.get_current_position_qty()
        if qty == 0:
            return False

        # Check if we have an entry time
        if strategy_state.entry_time is None:
            return False

        # Primary path: use the per-iteration counter (incremented in on_trading_iteration)
        if strategy_state.bars_in_trade >= int(max_bars):
            self._log_verbose(
                f"{strategy_state.strategy_id}: Time exit triggered "
                f"({strategy_state.bars_in_trade} bars >= {max_bars} max bars)"
            )
            return True

        # Fallback: derive bars_in_trade from index if counter not yet populated
        if len(market_data) > 0:
            bars_since = int((market_data.index > strategy_state.entry_time).sum())
            strategy_state.bars_in_trade = max(strategy_state.bars_in_trade, bars_since)
            if bars_since >= int(max_bars):
                self._log_verbose(
                    f"{strategy_state.strategy_id}: Time exit triggered "
                    f"({bars_since} bars >= {max_bars} max bars; index-based)"
                )
                return True

        # Fallback: iteration counter if timestamps/index fail
        if strategy_state.entry_iteration is not None:
            bars_since_iter = self.iteration_count - strategy_state.entry_iteration
            if bars_since_iter >= int(max_bars):
                self._log_verbose(
                    f"{strategy_state.strategy_id}: Time exit triggered "
                    f"({bars_since_iter} bars >= {max_bars} max bars; iteration-based)"
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
        iter_start = time.perf_counter()

        self.iteration_count += 1

        if self.shared_data.data_source is None:
            self.logger.error("No data_source configured; cannot run iteration.")
            return {"error": "data_source_missing"}

        self._log_verbose(
            f"{_YELLOW}=== Enhanced Multi-Strategy Iteration #{self.iteration_count} at {current_time} ==={_RESET}"
        )

        # Statistics for this iteration
        strategies_processed = 0
        signals_generated = 0
        time_exits_triggered = 0
        orders_to_submit = []

        # Step 1: Fetch data once for all unique symbols
        symbols = list(set(strategy.symbol for strategy in self.strategies))
        self.logger.debug(f"Fetching data for {len(symbols)} unique symbols: {symbols}")

        # Fetch with enough history for ATR calculations
        self._log_verbose(
            f"{_YELLOW}[FETCH] start symbols={symbols} length={self.max_lookback} timestep={self.timestep}{_RESET}"
        )
        fetch_start = time.perf_counter()
        self.shared_data.fetch_for_all_strategies(
            symbols=symbols,
            length=self.max_lookback,
            timestep=self.timestep,
            asset_type=Asset.AssetType.CONT_FUTURE,
        )
        self._log_verbose(
            f"{_YELLOW}[FETCH] done in {time.perf_counter() - fetch_start:.2f}s "
            f"cache_stats={self.shared_data.get_cache_stats()}{_RESET}"
        )

        # Step 2: Process each strategy independently
        for strategy_state in self.strategies:
            try:
                per_strategy_start = time.perf_counter()
                # 2a. Get cached data (no API call)
                self._log_verbose(
                    f"{_YELLOW}[DATA] get_cached_data for {strategy_state.strategy_id} "
                    f"symbol={strategy_state.symbol} len={self.max_lookback} ts={self.timestep}{_RESET}"
                )
                market_data = self.shared_data.get_cached_data(strategy_state.symbol, self.max_lookback, self.timestep)

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

                # Truncate data up to current time so signals evaluate current bar, not full month.
                # Fast-path: if current_time is at/after last bar, keep full data; otherwise slice by position.
                if hasattr(df, "index") and len(df.index) > 0:
                    try:
                        idx = df.index
                        last_idx = idx[-1]
                        if current_time < last_idx:
                            ct_ts = pd.Timestamp(current_time)
                            idx_tz = getattr(idx, "tz", None)
                            if idx_tz is not None:
                                if ct_ts.tzinfo is None:
                                    ct_ts = ct_ts.tz_localize(idx_tz)
                                elif ct_ts.tzinfo != idx_tz:
                                    ct_ts = ct_ts.tz_convert(idx_tz)
                            else:
                                if ct_ts.tzinfo is not None:
                                    ct_ts = ct_ts.tz_convert(None)
                            pos = idx.searchsorted(ct_ts, side="right")
                            if pos and pos < len(df):
                                df = df.iloc[:pos]
                    except Exception:
                        pass

                self._log_verbose(
                    f"{_YELLOW}[PROCESS] {strategy_state.strategy_id} using "
                    f"{len(df) if hasattr(df,'__len__') else 'NA'} rows{_RESET}"
                )

                # Count this strategy as processed once we have data in hand
                strategies_processed += 1

                # Track bars in trade while a position is open
                virtual_pos = strategy_state.tracker.get_position(strategy_state.symbol)
                virtual_qty = virtual_pos.quantity if virtual_pos else 0.0
                if virtual_qty != 0:
                    strategy_state.bars_in_trade += 1

                # If flat and not enough history, skip new entries (still allow exits if position exists)
                if virtual_qty == 0 and len(df) < self.min_bars:
                    continue

                # 2b. If already in position, first check for bracket hits
                if virtual_qty != 0:
                    hit, hit_price, hit_reason = self._check_bracket_hit(strategy_state, df)
                    if hit and hit_price is not None:
                        close_order = self._create_close_order(strategy_state, virtual_qty)
                        if close_order:
                            orders_to_submit.append((strategy_state, close_order, True, hit_reason, hit_price))
                            continue

                # 2c. Time-based exit check
                if virtual_qty != 0 and self._check_time_exit(strategy_state, df):
                    time_exits_triggered += 1
                    close_order = self._create_close_order(strategy_state, virtual_qty)
                    if close_order:
                        orders_to_submit.append((strategy_state, close_order, True, "time_exit", None))
                        # Clear entry tracking after time exit
                        strategy_state.entry_time = None
                        strategy_state.entry_price = None
                        strategy_state.bars_in_trade = 0
                        strategy_state.take_profit_price = None
                        strategy_state.stop_loss_price = None
                    continue

                # 2d. Check calendar/timing (only if calendar is available)
                if (not self.ignore_calendar) and (self.calendar is not None):
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
                            f"{strategy_state.strategy_id}: Cannot enter new orders (session restrictions)"
                        )
                        continue

                    # 2f. Force close if required by calendar
                    if status.must_be_flat and virtual_qty != 0:
                        self._log_verbose(
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

                # Signal diagnostics (info-level, yellow)
                try:
                    last_bar = df.iloc[-1]
                    bar_ts = getattr(last_bar, "name", current_time)
                    close_val = last_bar["close"] if "close" in df.columns else None
                    open_val = last_bar["open"] if "open" in df.columns else None
                except Exception:
                    bar_ts, close_val, open_val = current_time, None, None
                # Always emit signal diagnostics at INFO so we can see HOLD/BUY/SELL
                self._log_verbose(
                    f"{_YELLOW}[SIGNAL] {strategy_state.strategy_id} "
                    f"signal={signal} ts={bar_ts} close={close_val} open={open_val} "
                    f"bars={len(df)} virt_qty={virtual_qty}{_RESET}"
                )

                # 2i. Create order with bracket if signal generated
                if signal in ["BUY", "SELL"]:
                    signals_generated += 1
                    order = self._create_bracket_order_for_strategy(strategy_state, signal, df)
                    if order:
                        orders_to_submit.append((strategy_state, order, False, "entry", None))
                self._log_verbose(
                    f"{_YELLOW}[PROCESS] {strategy_state.strategy_id} done in "
                    f"{time.perf_counter() - per_strategy_start:.4f}s virt_qty={virtual_qty}{_RESET}"
                )

            except Exception as e:
                self.logger.error(f"Error processing strategy {strategy_state.strategy_id}: {e}", exc_info=True)
                continue

        # Step 3: Execute all orders sequentially with rate limiting
        self.logger.debug(f"{_YELLOW}[FLOW] before_execute orders_to_submit={len(orders_to_submit)}{_RESET}")
        orders_submitted = self._execute_all_orders(orders_to_submit, current_time)
        self.logger.debug(f"{_YELLOW}[FLOW] after_execute orders_submitted={orders_submitted}{_RESET}")

        # Step 4: Log iteration summary
        self.logger.debug(f"{_YELLOW}[FLOW] cache_stats_start{_RESET}")
        cache_stats = self.shared_data.get_cache_stats()
        self.logger.debug(f"{_YELLOW}[FLOW] cache_stats_end{_RESET}")

        self.logger.debug(f"{_YELLOW}[FLOW] rate_limiter_stats_start{_RESET}")
        rate_limiter_stats = self.rate_limiter.get_stats()
        self.logger.debug(f"{_YELLOW}[FLOW] rate_limiter_stats_end{_RESET}")

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

        self._log_verbose(
            f"{_YELLOW}Iteration complete: {strategies_processed} strategies processed, "
            f"{signals_generated} signals, {time_exits_triggered} time exits, "
            f"{orders_submitted} orders, cache hit rate: {cache_stats['hit_rate']:.1f}%{_RESET}"
        )
        self.logger.debug(f"{_YELLOW}[FLOW] iteration_summary_logged{_RESET}")
        self._log_verbose(
            f"{_YELLOW}[FLOW] iteration_duration={time.perf_counter() - iter_start:.4f}s "
            f"orders={orders_submitted} cache_hit_rate={cache_stats['hit_rate']:.1f}%{_RESET}"
        )

        # Record equity curve data point
        if self.enable_snapshots:
            # Calculate total realized P&L across all strategies
            total_realized_pnl = sum(s.realized_pnl for s in self.strategies)

            # Calculate total unrealized P&L across all strategies
            total_unrealized_pnl = 0.0
            for strategy_state in self.strategies:
                pos = strategy_state.tracker.get_position(strategy_state.symbol)
                if pos and pos.quantity != 0 and strategy_state.entry_price is not None:
                    # Get current price from cached data
                    try:
                        market_data = self.shared_data.get_cached_data(
                            strategy_state.symbol, self.max_lookback, self.timestep
                        )
                        if market_data is not None:
                            df = market_data.df if hasattr(market_data, "df") else market_data
                            if len(df) > 0:
                                current_price = df["close"].iloc[-1]
                                multiplier = 1.0
                                try:
                                    from custom_portfolio.data.futures_metadata import get_multiplier

                                    multiplier = get_multiplier(strategy_state.symbol)
                                except Exception:
                                    pass
                                unrealized = (current_price - strategy_state.entry_price) * pos.quantity * multiplier
                                total_unrealized_pnl += unrealized
                    except Exception as e:
                        self.logger.debug(f"Error calculating unrealized P&L for {strategy_state.strategy_id}: {e}")

            # Calculate account balance (realized only) and portfolio value (including unrealized)
            initial_capital = getattr(self, "shared_initial_capital", 0.0) or getattr(
                self, "total_initial_capital", 0.0
            )
            account_balance = initial_capital + total_realized_pnl
            portfolio_value = account_balance + total_unrealized_pnl

            self.attribution.record_equity_curve(current_time, account_balance, portfolio_value)

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
        # Prefer injected generate_signal_func (set by PortfolioManager.loader)
        sig_func = getattr(strategy_state, "generate_signal_func", None)
        if callable(sig_func):
            try:
                return sig_func(strategy_state, market_data)
            except Exception as e:
                self.logger.error(f"Signal function failed for {strategy_state.strategy_id}: {e}", exc_info=True)
                return "HOLD"

        # Fallback: HOLD if no function injected
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

            # Stash targets for simulated fill tracking
            strategy_state.pending_tp = tp_price
            strategy_state.pending_sl = sl_price

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
            order.tag = strategy_state.strategy_id
            # TEMPORARILY COMMENTED OUT - testing if this causes ProjectX order submission errors
            # if self.broker_strategy_name:
            #     order.strategy = self.broker_strategy_name

            self._log_verbose(
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

            order.tag = strategy_state.strategy_id
            # TEMPORARILY COMMENTED OUT - testing if this causes ProjectX order submission errors
            # if self.broker_strategy_name:
            #     order.strategy = self.broker_strategy_name

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

        if len(orders_to_submit) == 0:
            self._log_verbose("No orders to submit this iteration.")
            return 0
        else:
            self._log_verbose(f"Submitting {len(orders_to_submit)} orders (simulate_fills={self.simulate_fills})")

        for item in orders_to_submit:
            if len(item) == 5:
                strategy_state, order, is_close, reason, fill_override = item
            else:
                strategy_state, order, is_close, reason = item
                fill_override = None
            try:
                # Wait for rate limit
                wait_time = self.rate_limiter.wait_if_needed()

                if wait_time > 0:
                    self.logger.debug(
                        f"Rate limited: waited {wait_time:.2f}s before "
                        f"submitting order for {strategy_state.strategy_id}"
                    )

                # Capture position before we mutate it so we can detect closes/reversals
                pos_before = strategy_state.tracker.get_position(strategy_state.symbol)
                qty_before = pos_before.quantity if pos_before else 0.0

                # Submit order to broker unless simulating fills or broker missing
                mark_submitted = False
                if not self.simulate_fills and self.broker is not None:
                    self._log_verbose(
                        f"Submitting order for {strategy_state.strategy_id} ({reason}): "
                        f"{order.side} {order.quantity} {order.asset.symbol}"
                    )
                    try:
                        self.broker.submit_order(order)
                        mark_submitted = True
                    except Exception as submit_err:
                        self.logger.error(
                            f"Broker submit failed for {strategy_state.strategy_id}: {submit_err}", exc_info=True
                        )
                else:
                    # Simulated path just logs
                    self.logger.debug(
                        f"Simulated submit for {strategy_state.strategy_id} ({reason}): "
                        f"{order.side} {order.quantity} {order.asset.symbol}"
                    )
                    mark_submitted = True
                if mark_submitted:
                    self.rate_limiter.mark_order_submitted()

                    # Update virtual position immediately (assume market orders fill)
                    # Get current price from cached data (use shared lookback)
                    market_data = self.shared_data.get_cached_data(
                        strategy_state.symbol, self.max_lookback, self.timestep
                    )

                if market_data is not None:
                    if hasattr(market_data, "df"):
                        df = market_data.df
                    else:
                        df = market_data

                    if df is None:
                        self.logger.warning(
                            f"No market data frame for {strategy_state.strategy_id}; skipping fill update."
                        )
                        continue

                    if len(df) == 0:
                        self.logger.warning(
                            f"Empty market data when executing order for {strategy_state.strategy_id}; "
                            f"skipping fill update."
                        )
                        continue

                    # Pick price at or before current_time (not end-of-period) to avoid future-looking fills
                    bar_time = None
                    fill_price = None
                    try:
                        idx = df.index
                        ct = pd.Timestamp(current_time)
                        if getattr(idx, "tz", None) is not None:
                            if ct.tzinfo is None:
                                ct = ct.tz_localize(idx.tz)
                            elif ct.tzinfo != idx.tz:
                                ct = ct.tz_convert(idx.tz)
                        else:
                            if ct.tzinfo is not None:
                                ct = ct.tz_convert(None)

                        pos = idx.searchsorted(ct, side="right")
                        if pos > 0:
                            row = df.iloc[pos - 1]
                            bar_time = getattr(row, "name", ct)
                            fill_price = float(row["close"])
                    except Exception:
                        bar_time = None
                        fill_price = None

                    if fill_override is not None:
                        fill_price = float(fill_override)
                        bar_time = df.index[-1] if len(df.index) > 0 else current_time
                    if fill_price is None:
                        # Fallback: use last bar in the frame
                        fill_price = float(df["close"].iloc[-1])
                        bar_time = df.index[-1] if len(df.index) > 0 else current_time
                    current_price = fill_price

                    # Simulated virtual fill
                    strategy_state.tracker.execute_order(
                        strategy_state.symbol, order.quantity, order.side, current_price
                    )

                    # Track per-side TopstepX fee (if known)
                    fee_per_side = get_per_order_fee(strategy_state.symbol) or 0.0
                    fees_this_order = fee_per_side * abs(order.quantity)
                    if fees_this_order:
                        strategy_state.total_fees_paid += fees_this_order
                        strategy_state.fees_since_entry += fees_this_order

                    # Track entry/exit bookkeeping
                    pos_after = strategy_state.tracker.get_position(strategy_state.symbol)
                    qty_after = pos_after.quantity if pos_after else 0.0

                    # Reversal or flattening: close prior position if we had one
                    if qty_before != 0 and (qty_after == 0 or qty_before * qty_after < 0):
                        entry_price = (
                            strategy_state.entry_price if strategy_state.entry_price is not None else current_price
                        )
                        multiplier = get_multiplier(strategy_state.symbol)
                        realized = (current_price - entry_price) * qty_before * multiplier
                        net_realized = realized - strategy_state.fees_since_entry
                        strategy_state.realized_pnl += net_realized
                        self.attribution.record_trade(
                            strategy_state.strategy_id,
                            entry_price,
                            current_price,
                            qty_before,
                            timestamp=bar_time,
                            fees=strategy_state.fees_since_entry,
                            symbol=strategy_state.symbol,
                            multiplier=multiplier,
                        )
                        strategy_state.trade_history.append(
                            {
                                "timestamp": bar_time,
                                "entry_price": entry_price,
                                "exit_price": current_price,
                                "quantity": qty_before,
                                "pnl": net_realized,
                                "fees": strategy_state.fees_since_entry,
                            }
                        )
                        strategy_state.trade_count += 1
                        # Reset entry tracking after a close/reversal
                        strategy_state.entry_time = None
                        strategy_state.entry_price = None
                        strategy_state.entry_iteration = None
                        strategy_state.entry_side = None
                        strategy_state.take_profit_price = None
                        strategy_state.stop_loss_price = None
                        strategy_state.bars_in_trade = 0
                        strategy_state.fees_since_entry = 0.0

                    # New entry (including reversal opening leg)
                    if (qty_after != 0 and qty_before == 0) or (qty_before * qty_after < 0):
                        strategy_state.entry_time = bar_time
                        strategy_state.entry_price = current_price
                        strategy_state.entry_iteration = self.iteration_count
                        strategy_state.bars_in_trade = 0
                        strategy_state.entry_side = order.side
                        strategy_state.take_profit_price = getattr(strategy_state, "pending_tp", None)
                        strategy_state.stop_loss_price = getattr(strategy_state, "pending_sl", None)
                        strategy_state.pending_tp = None
                        strategy_state.pending_sl = None
                        strategy_state.fees_since_entry = fees_this_order

                    # If we simply added to an existing position, keep the original entry but refresh bars counter
                    if qty_after != 0 and qty_before == qty_after and qty_after != 0:
                        strategy_state.bars_in_trade = max(strategy_state.bars_in_trade, 0)

                    self.logger.debug(
                        f"Updated virtual position for {strategy_state.strategy_id}: "
                        f"{strategy_state.tracker.get_position(strategy_state.symbol)}"
                    )
                    orders_submitted += 1
                    self.total_orders_submitted += 1

                    # Snapshots for attribution/logging (optional)
                    if self.enable_snapshots:
                        pos_obj = strategy_state.tracker.get_position(strategy_state.symbol)
                        pos_qty = pos_obj.quantity if pos_obj else 0.0
                        entry_price = strategy_state.entry_price
                        last_price = current_price
                        unrealized = (last_price - entry_price) * pos_qty if entry_price is not None else 0.0
                        self.attribution.record_snapshot(
                            strategy_state.strategy_id,
                            {
                                "timestamp": bar_time,
                                "symbol": strategy_state.symbol,
                                "position_qty": pos_qty,
                                "notional_exposure": pos_qty * last_price,
                                "last_price": last_price,
                                "entry_price": entry_price,
                                "bars_in_trade": strategy_state.bars_in_trade,
                                "last_signal": getattr(strategy_state, "last_signal", None),
                                "unrealized_pnl": unrealized,
                                "realized_pnl": strategy_state.realized_pnl,
                                "trade_count": strategy_state.trade_count,
                                "fees_paid": strategy_state.total_fees_paid,
                                "fees_since_entry": strategy_state.fees_since_entry,
                            },
                        )
                else:
                    # No data; skip counting as submitted
                    continue

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

    def force_flatten(self, current_time: Optional[datetime] = None) -> tuple[int, list]:
        """
        Force-close all open positions using the latest cached prices.

        Args:
            current_time: Timestamp used for the close (defaults to now)

        Returns:
            Tuple of (number of close orders executed, details list).
        """
        if current_time is None:
            current_time = datetime.now()

        orders: List[tuple] = []
        forced_details: list = []
        for state in self.strategies:
            pos = state.tracker.get_position(state.symbol)
            if pos and pos.quantity != 0:
                last_price = None
                try:
                    md = self.shared_data.get_cached_data(state.symbol, self.max_lookback, self.timestep)
                    df = md.df if hasattr(md, "df") else md
                    if df is not None and len(df) > 0:
                        last_price = float(df["close"].iloc[-1])
                except Exception:
                    last_price = None
                entry_price = state.entry_price
                multiplier = get_multiplier(state.symbol)
                if last_price is not None and entry_price is not None:
                    gross = (last_price - entry_price) * pos.quantity * multiplier
                    net = gross - state.fees_since_entry
                else:
                    gross = None
                    net = None
                close_order = self._create_close_order(state, pos.quantity)
                if close_order:
                    orders.append((state, close_order, True, "force_flatten", None))
                    forced_details.append(
                        {
                            "strategy_id": state.strategy_id,
                            "symbol": state.symbol,
                            "qty": pos.quantity,
                            "entry_price": entry_price,
                            "last_price": last_price,
                            "multiplier": multiplier,
                            "gross_pnl": gross,
                            "fees_pending": state.fees_since_entry,
                            "net_pnl": net,
                        }
                    )

        if orders:
            self._log_verbose(f"Forcing flatten of {len(orders)} open positions at end of run")
            count = self._execute_all_orders(orders, current_time)
            # Print multiplier warnings at end of run
            self.print_multiplier_warnings()
            return count, forced_details

        # Print multiplier warnings even if no positions to flatten
        self.print_multiplier_warnings()
        return 0, forced_details

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
