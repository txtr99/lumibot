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

        # Entry-time bracket parameters (stored when signal fires, used after fill)
        self.entry_atr: Optional[float] = None  # ATR at signal time
        self.entry_pt_mult: Optional[float] = None  # Profit target multiplier
        self.entry_sl_mult: Optional[float] = None  # Stop loss multiplier
        self.brackets_submitted: bool = False  # Flag to prevent duplicate bracket submission

        # Signal visibility: list of (label, is_true) tuples for display
        # Set by get_signal_visibility() function in strategy file
        self.signal_visibility: List[tuple] = []

        # Data status: tracks if strategy has enough bars for evaluation
        # "ok" = enough data, "ERR" = insufficient bars
        self.data_status: str = "ok"
        self.data_bar_count: int = 0
        self.min_bars_required: int = 0  # Computed from indicator params

        # Simple realized P&L tracking and trade counts (per fill)
        self.realized_pnl: float = 0.0
        self.trade_count: int = 0
        self.total_fees_paid: float = 0.0
        self.fees_since_entry: float = 0.0

        # Exit type counters (since bot start)
        self.sl_count: int = 0  # Stop-loss exits
        self.tp_count: int = 0  # Take-profit exits
        self.time_exit_count: int = 0  # Time-based exits
        self.force_close_count: int = 0  # Calendar-forced exits

        # Coordination flag: Set by BracketOrderManager when it handles TP/SL exit
        # Prevents executor from double-counting P&L with less accurate bar prices
        self.exit_handled_by_bracket: bool = False

        # Contract ID for ProjectX API (e.g., "CON.F.US.EP.Z25")
        # Set during strategy loading from futures_metadata
        self.contract_id: str = ""


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
        debug_indicators: bool = False,
        bracket_manager=None,
        order_registry=None,
        live_mode: bool = False,
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
            debug_indicators: Enable per-strategy indicator debug logging when True
            order_registry: OrderRegistry instance for centralized order tracking
            live_mode: If True, always fetch fresh data from API (don't use prefetched store)

        Note:
            Tick sizes are now automatically looked up per symbol from futures_metadata
        """
        self.broker = broker
        self.data_source = data_source
        self.calendar = calendar
        self.default_atr_period = default_atr_period
        self.MIN_LOOKBACK_FLOOR = 100
        self.MAX_LOOKBACK_CAP = 800
        # Extra minutes to request beyond max_lookback to account for unfillable gaps
        # (e.g., 61-min daily maintenance window that can't be forward-filled)
        self.FETCH_BUFFER_MINUTES = 400
        self.timestep = timestep
        self.live_mode = live_mode

        # Initialize shared resources
        # In live mode, skip prefetched data store to avoid stale startup data
        self.shared_data = SharedDataManager(
            data_source, cache_ttl_seconds, verbose_logging=deep_portfolio_debug, live_mode=live_mode
        )
        self.rate_limiter = GlobalRateLimiter(min_order_delay_seconds)
        self.attribution = StrategyAttribution(max_snapshots=max_snapshots)
        self.shared_initial_capital = shared_initial_capital
        self.ignore_calendar = ignore_calendar
        self.broker_strategy_name = broker_strategy_name
        self.deep_portfolio_debug = deep_portfolio_debug
        self.debug_indicators = debug_indicators
        self.bracket_manager = bracket_manager  # For race-safe close pattern
        self.order_registry = order_registry  # For bulletproof order tracking
        self.total_initial_capital = shared_initial_capital

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
                allowed_sessions=config.get("allowed_sessions", ["24/7"]),
            )

            # Store strategy type for dynamic loading
            state.strategy_type = config.get("strategy_type", "DefaultStrategy")

            # Set debug flag for per-strategy indicator logging
            state.debug_indicators = self.debug_indicators

            self.strategies.append(state)

            # Register with attribution tracker
            self.attribution.register_strategy(config["strategy_id"], initial_capital=per_strategy_capital)

            # Calculate min_bars_required from ALL indicator params
            # This ensures each strategy gets enough data for its indicators
            p = state.params
            indicator_periods = []
            for key, value in p.items():
                # Check all params that represent indicator periods/lengths
                if any(suffix in key.lower() for suffix in ["_length", "_period", "lookback"]):
                    try:
                        indicator_periods.append(int(value))
                    except (ValueError, TypeError):
                        pass
                # Also check common param names without suffixes
                if key.lower() in [
                    "sma_fast",
                    "sma_slow",
                    "ema_fast",
                    "ema_slow",
                    "ema_medium",
                    "ema_medium_fast",
                    "rsi_fast",
                    "rsi_slow",
                ]:
                    try:
                        indicator_periods.append(int(value))
                    except (ValueError, TypeError):
                        pass

            # Add buffer: longest indicator period + 50 bars for calculations/warmup
            max_period = max(indicator_periods) if indicator_periods else 20
            state.min_bars_required = max_period + 50

            # Track max lookback across all strategies for data fetching
            required = max(
                int(p.get("atr_period", self.default_atr_period)) + 10,
                state.min_bars_required,
                1,
            )
            self.max_lookback = max(self.max_lookback, required)

        # Cap lookback to 400 bars as requested; ensure minimum 100 for stability
        self.max_lookback = min(self.MAX_LOOKBACK_CAP, max(self.max_lookback, self.MIN_LOOKBACK_FLOOR))

        # Add buffer for unfillable gaps (maintenance windows) to ensure we get enough bars
        # This is added AFTER capping because the cap is for indicator requirements,
        # but the buffer is for data availability during market closures
        self.max_lookback = self.max_lookback + self.FETCH_BUFFER_MINUTES

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

    def _log_ohlcv_debug(self, symbol: str, source_name: str, df: pd.DataFrame) -> None:
        """
        Log last 60 bars of OHLCV for debugging price discrepancies.

        Args:
            symbol: Symbol being logged (e.g., "MGC")
            source_name: Name of data source (e.g., "Strategy", "TrendSentiment")
            df: DataFrame with OHLCV columns
        """
        # Only log for MGC to debug the price discrepancy issue
        if symbol not in ("MGC", "MES", "MNQ"):
            return

        if df is None or len(df) == 0:
            self.logger.info(f"[OHLCV-DEBUG] {source_name}/{symbol}: NO DATA")
            return

        tail = df.tail(60)
        self.logger.info(f"[OHLCV-DEBUG] {source_name}/{symbol}: {len(df)} total bars (showing last {len(tail)})")
        if len(tail) > 0:
            first_idx = tail.index[0]
            last_idx = tail.index[-1]
            self.logger.info(
                f"  First: {first_idx} O={tail['open'].iloc[0]:.2f} "
                f"H={tail['high'].iloc[0]:.2f} L={tail['low'].iloc[0]:.2f} "
                f"C={tail['close'].iloc[0]:.2f}"
            )
            self.logger.info(
                f"  Last:  {last_idx} O={tail['open'].iloc[-1]:.2f} "
                f"H={tail['high'].iloc[-1]:.2f} L={tail['low'].iloc[-1]:.2f} "
                f"C={tail['close'].iloc[-1]:.2f}"
            )

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
        # If BracketOrderManager already handled this exit with actual fill prices,
        # skip bar-based detection to avoid double-counting P&L
        if strategy_state.exit_handled_by_bracket:
            strategy_state.exit_handled_by_bracket = False  # Clear for next trade
            return False, None, None

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

        # In live mode with real broker brackets, let BOM handle fills via trade_search.
        # But add fallback: if brackets were submitted long ago and price clearly crossed,
        # the bracket orders may have failed/cancelled - force close as safety net.
        if strategy_state.brackets_submitted:
            brackets_age = time.time() - getattr(strategy_state, "brackets_submitted_at", 0)
            FALLBACK_SECONDS = 60  # After 60s, if price crossed TP/SL, assume brackets failed

            if brackets_age < FALLBACK_SECONDS:
                # Still within grace period - trust BOM to handle fills
                return False, None, None

            # Fallback check: price clearly crossed TP/SL but no fill received
            price_crossed_sl = False
            price_crossed_tp = False
            if qty > 0:
                price_crossed_sl = sl is not None and low is not None and low <= sl
                price_crossed_tp = tp is not None and high is not None and high >= tp
            elif qty < 0:
                price_crossed_sl = sl is not None and high is not None and high >= sl
                price_crossed_tp = tp is not None and low is not None and low <= tp

            if price_crossed_sl or price_crossed_tp:
                reason = "bracket_sl" if price_crossed_sl else "bracket_tp"
                price = sl if price_crossed_sl else tp
                self.logger.warning(
                    f"[BRACKET-FALLBACK] {strategy_state.strategy_id}: Brackets submitted {brackets_age:.0f}s ago "
                    f"but price crossed {reason.upper()} with no fill. Forcing close as safety net."
                )
                return True, float(price), reason

            # Price hasn't crossed yet - keep waiting for BOM
            return False, None, None

        # Backtest mode or no brackets submitted - use bar-based detection
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

        # Invalidate cache at start of each iteration to ensure fresh data
        # Cache still serves its purpose: sharing data across strategies WITHIN this iteration
        self.shared_data.invalidate_cache()

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

        # Log OHLCV debug info once per unique symbol (for price discrepancy debugging)
        logged_symbols = set()
        for sym in symbols:
            if sym not in logged_symbols:
                logged_symbols.add(sym)
                try:
                    md = self.shared_data.get_cached_data(sym, self.max_lookback, self.timestep)
                    if md is not None:
                        sym_df = md.df if hasattr(md, "df") else md
                        self._log_ohlcv_debug(sym, "Strategy", sym_df)
                except Exception as log_err:
                    self.logger.debug(f"[OHLCV-DEBUG] Failed to log {sym}: {log_err}")

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

                # Track data status for visibility (using per-strategy minimum)
                strategy_state.data_bar_count = len(df)
                min_required = strategy_state.min_bars_required
                if len(df) < min_required:
                    strategy_state.data_status = "ERR"
                    # HARD STOP: Insufficient bars is a critical data issue
                    error_msg = (
                        f"\n{'='*70}\n"
                        f"HARD STOP: INSUFFICIENT BARS FOR STRATEGY\n"
                        f"{'='*70}\n"
                        f"Strategy:     {strategy_state.strategy_id}\n"
                        f"Symbol:       {strategy_state.symbol}\n"
                        f"Bars received: {len(df)}\n"
                        f"Bars required: {min_required}\n"
                        f"Deficit:      {min_required - len(df)} bars\n"
                        f"Current time: {current_time}\n"
                        f"Data range:   {df.index[0]} to {df.index[-1]}\n"
                        f"{'='*70}\n"
                        f"This indicates a data fetch or lookback configuration issue.\n"
                        f"Check: max_lookback setting, data source availability, cache integrity.\n"
                        f"{'='*70}\n"
                    )
                    self.logger.error(error_msg)
                    raise RuntimeError(error_msg)
                else:
                    strategy_state.data_status = "ok"

                # Update signal visibility for ALL strategies (even those in positions)
                # This must run before any early exits so display always shows current indicators
                vis_func = getattr(strategy_state, "get_signal_visibility_func", None)
                if callable(vis_func) and len(df) >= min_required:
                    try:
                        vis_start = time.perf_counter()
                        vis_result = vis_func(strategy_state, df)
                        vis_elapsed = time.perf_counter() - vis_start
                        if vis_elapsed > 5.0:
                            self.logger.warning(
                                f"[TIMING-ALERT] {strategy_state.strategy_id} vis_func took {vis_elapsed:.2f}s (>5s)! "
                                f"iter={self.iteration_count} ts={current_time}"
                            )
                        # Check for NaN values in visibility results
                        has_nan = False
                        if vis_result:
                            for _, val in vis_result:
                                if val is None or (isinstance(val, float) and pd.isna(val)):
                                    has_nan = True
                                    break
                        if has_nan:
                            strategy_state.data_status = "NaN"
                            self.logger.warning(f"Signal visibility has NaN for {strategy_state.strategy_id}")
                        strategy_state.signal_visibility = vis_result or []
                    except Exception as e:
                        self.logger.warning(f"Signal visibility failed for {strategy_state.strategy_id}: {e}")
                        strategy_state.data_status = "ERR"
                        strategy_state.signal_visibility = []

                # If flat and not enough history, skip new entries (still allow exits if position exists)
                if virtual_qty == 0 and len(df) < min_required:
                    continue

                # 2b. If already in position, first check for bracket hits
                if virtual_qty != 0:
                    hit, hit_price, hit_reason = self._check_bracket_hit(strategy_state, df)
                    if hit and hit_price is not None:
                        self.logger.info(
                            f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: I detected a bracket hit! "
                            f"The price reached {hit_price} which triggered our {hit_reason}. "
                            f"We're currently holding {virtual_qty} contracts, so I need to close this position now. "
                            f"Creating a market order to exit."
                        )
                        close_order = self._create_close_order(strategy_state, virtual_qty)
                        if close_order:
                            orders_to_submit.append((strategy_state, close_order, True, hit_reason, hit_price))
                            continue

                # 2c. Time-based exit check
                if virtual_qty != 0 and self._check_time_exit(strategy_state, df):
                    time_exits_triggered += 1
                    self.logger.info(
                        f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Time to exit! "
                        f"We've been in this trade for {strategy_state.bars_in_trade} bars, which exceeds our max. "
                        f"Still holding {virtual_qty} contracts. I'm forcing a close now to avoid overexposure."
                    )
                    close_order = self._create_close_order(strategy_state, virtual_qty)
                    if close_order:
                        orders_to_submit.append((strategy_state, close_order, True, "time_exit", None))
                        # NOTE: Don't clear entry_price here! It's needed for P&L calculation
                        # when the order is processed. Entry tracking is cleared in
                        # _execute_all_orders after P&L is calculated (lines 1466-1474).
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
                        self.logger.debug(
                            f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Skipping - platform is closed. "
                            f"Reason: {status.platform_reason}. I can't do anything while the exchange is down."
                        )
                        continue

                    # 2e. Skip if can't enter new positions
                    if not status.can_enter_orders and virtual_qty == 0:
                        self.logger.debug(
                            f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Skipping signal check. "
                            f"We're flat and outside our allowed session ({status.session_reason}). "
                            f"No point generating signals when I can't act on them."
                        )
                        continue

                    # 2f. Force close if required by calendar
                    if status.must_be_flat and virtual_qty != 0:
                        self.logger.info(
                            f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Urgent! Must close position NOW. "
                            f"Reason: {status.close_reason}. We're holding {virtual_qty} contracts but the calendar "
                            f"says we need to be flat. Submitting close order immediately."
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
                    self.logger.info(
                        f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Got a {signal} signal! "
                        f"Current price is {close_val}, I have {len(df)} bars of data. "
                        f"Strategy conditions are met so I'm creating an entry order with TP/SL brackets."
                    )
                    order = self._create_bracket_order_for_strategy(strategy_state, signal, df)
                    if order:
                        orders_to_submit.append((strategy_state, order, False, "entry", None))
                per_strategy_elapsed = time.perf_counter() - per_strategy_start
                if per_strategy_elapsed > 5.0:
                    self.logger.warning(
                        f"[TIMING-ALERT] {strategy_state.strategy_id} TOTAL took {per_strategy_elapsed:.2f}s! "
                        f"iter={self.iteration_count} ts={current_time}"
                    )
                self._log_verbose(
                    f"{_YELLOW}[PROCESS] {strategy_state.strategy_id} done in "
                    f"{per_strategy_elapsed:.4f}s virt_qty={virtual_qty}{_RESET}"
                )

            except Exception as e:
                self.logger.error(f"Error processing strategy {strategy_state.strategy_id}: {e}", exc_info=True)
                continue

        # Step 3: Execute all orders sequentially with rate limiting
        self.logger.debug(f"{_YELLOW}[FLOW] before_execute orders_to_submit={len(orders_to_submit)}{_RESET}")
        orders_submitted, rejected_errors = self._execute_all_orders(orders_to_submit, current_time)
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
            "rejected_errors": rejected_errors,  # List of rejection error messages for market closed detection
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
                    # Get current price from cached data filtered to current_time (prevents lookahead)
                    try:
                        df = self.shared_data.get_data_at_time(
                            strategy_state.symbol, current_time, self.max_lookback, self.timestep
                        )
                        if df is not None and len(df) > 0:
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
                sig_start = time.perf_counter()
                signal = sig_func(strategy_state, market_data)
                sig_elapsed = time.perf_counter() - sig_start
                if sig_elapsed > 5.0:
                    self.logger.warning(
                        f"[TIMING-ALERT] {strategy_state.strategy_id} sig_func took {sig_elapsed:.2f}s (>5s)! "
                        f"iter={self.iteration_count}"
                    )
                # Check for NaN or invalid signal
                if signal is None or (isinstance(signal, float) and pd.isna(signal)):
                    self.logger.warning(f"Signal is NaN/None for {strategy_state.strategy_id}")
                    strategy_state.data_status = "NaN"
                    return "HOLD"
                return signal
            except Exception as e:
                self.logger.error(f"Signal function failed for {strategy_state.strategy_id}: {e}", exc_info=True)
                strategy_state.data_status = "ERR"
                return "HOLD"

        # Fallback: HOLD if no function injected
        return "HOLD"

    def _create_bracket_order_for_strategy(
        self, strategy_state: EnhancedStrategyState, signal: str, market_data: pd.DataFrame
    ) -> Optional[Order]:
        """
        Create an ENTRY-ONLY market order for a strategy.

        Brackets (TP/SL) are NOT submitted here - they will be submitted by
        bracket_order_manager AFTER the entry fill is confirmed, using the
        actual fill price + stored ATR parameters.

        Args:
            strategy_state: Strategy state object
            signal: Signal ('BUY' or 'SELL')
            market_data: Market data DataFrame

        Returns:
            Order object (entry only, no brackets) or None
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

            # Calculate and STORE ATR parameters for later bracket submission
            # (Brackets will be calculated from actual fill price, not stale bar data)
            atr_period = int(strategy_state.params.get("atr_period", self.default_atr_period))
            pt_mult = float(strategy_state.params.get("pt_mult", strategy_state.params.get("profit_target_mult", 0.0)))
            sl_mult = float(strategy_state.params.get("sl_mult", strategy_state.params.get("stop_loss_mult", 0.0)))

            # Calculate ATR now and store for later
            if len(market_data) >= atr_period + 1:
                atr_data_length = min(atr_period + 1, len(market_data))
                high_series = market_data["high"].tail(atr_data_length)
                low_series = market_data["low"].tail(atr_data_length)
                close_series = market_data["close"].tail(atr_data_length)
                atr = self._wilder_atr(high_series, low_series, close_series, atr_period)
                atr_now = float(atr.iloc[-1]) if not atr.isna().iloc[-1] else None
            else:
                atr_now = None

            # Store ATR and multipliers for bracket_order_manager to use after fill
            strategy_state.entry_atr = atr_now
            strategy_state.entry_pt_mult = pt_mult
            strategy_state.entry_sl_mult = sl_mult
            strategy_state.brackets_submitted = False  # Reset flag for new entry

            atr_str = f"{atr_now:.2f}" if atr_now is not None else "None"
            self.logger.info(
                f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Storing ATR={atr_str} for later bracket "
                f"calculation. pt_mult={pt_mult}, sl_mult={sl_mult}. Brackets will be submitted AFTER "
                f"entry fill is confirmed with actual fill price."
            )

            # For backtest/virtual tracking, estimate bracket levels (won't be used for live orders)
            if atr_now is not None:
                baseline = float(close_series.iloc[-1])
                if side == "buy":
                    est_tp = baseline + atr_now * pt_mult if pt_mult > 0 else None
                    est_sl = baseline - atr_now * sl_mult if sl_mult > 0 else None
                else:
                    est_tp = baseline - atr_now * pt_mult if pt_mult > 0 else None
                    est_sl = baseline + atr_now * sl_mult if sl_mult > 0 else None
                strategy_state.pending_tp = self._round_to_tick(est_tp, strategy_state.symbol)
                strategy_state.pending_sl = self._round_to_tick(est_sl, strategy_state.symbol)
            else:
                strategy_state.pending_tp = None
                strategy_state.pending_sl = None

            # Create ENTRY-ONLY order (no bracket class - brackets submitted after fill)
            order = Order(
                strategy_state.strategy_id,
                asset,
                strategy_state.contracts,
                side,
                order_type=Order.OrderType.MARKET,
                # NO order_class=Order.OrderClass.BRACKET
                # NO secondary_limit_price or secondary_stop_price
            )

            # Generate unique tag using registry (timestamp + UUID for true uniqueness)
            if self.order_registry:
                from tools.order_registry import OrderPurpose

                order.tag = self.order_registry.generate_unique_tag(OrderPurpose.ENTRY, strategy_state.strategy_id)
            else:
                # Fallback to old format if no registry (backward compatibility)
                import uuid

                order.tag = f"{strategy_state.strategy_id}-{uuid.uuid4().hex[:8].upper()}"

            self._log_verbose(
                f"Created ENTRY-ONLY order for {strategy_state.strategy_id}: "
                f"{side} {strategy_state.contracts} {asset.symbol}, tag={order.tag}. "
                f"Brackets pending fill confirmation."
            )

            return order

        except Exception as e:
            self.logger.error(f"Failed to create entry order for {strategy_state.strategy_id}: {e}")
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

            # Generate unique tag using registry (timestamp + UUID for true uniqueness)
            if self.order_registry:
                from tools.order_registry import OrderPurpose

                order.tag = self.order_registry.generate_unique_tag(OrderPurpose.CLOSE, strategy_state.strategy_id)
            else:
                # Fallback to old format if no registry (backward compatibility)
                import uuid

                order.tag = f"{strategy_state.strategy_id}-{uuid.uuid4().hex[:8].upper()}"

            return order

        except Exception as e:
            self.logger.error(f"Failed to create close order for {strategy_state.strategy_id}: {e}")
            return None

    def _execute_all_orders(self, orders_to_submit: List[tuple], current_time: datetime) -> tuple[int, list[str]]:
        """
        Execute all orders sequentially with global rate limiting.

        Args:
            orders_to_submit: List of (strategy_state, order, is_close, reason) tuples
            current_time: Current timestamp

        Returns:
            Tuple of (orders_submitted_count, list_of_rejection_error_messages)
        """
        orders_submitted = 0
        rejected_errors: list[str] = []

        if len(orders_to_submit) == 0:
            self._log_verbose("No orders to submit this iteration.")
            return 0, []
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
                    # ============================================================
                    # RACE-SAFE CLOSE: Cancel brackets BEFORE closing position
                    # ============================================================
                    # If this is a close order and we have a bracket_manager,
                    # cancel any open TP/SL orders first to prevent orphans
                    if is_close and self.bracket_manager is not None:
                        try:
                            cancel_result = self.bracket_manager.cancel_brackets_for_strategy(
                                strategy_state.strategy_id,
                                wait_seconds=0.5,
                            )
                            if cancel_result.get("cancelled"):
                                self._log_verbose(
                                    f"[RACE-SAFE] Cancelled {len(cancel_result['cancelled'])} "
                                    f"brackets before closing {strategy_state.strategy_id}"
                                )
                            if cancel_result.get("errors"):
                                self.logger.warning(
                                    f"[RACE-SAFE] Errors cancelling brackets for "
                                    f"{strategy_state.strategy_id}: {cancel_result['errors']}"
                                )
                        except Exception as cancel_err:
                            # Log but don't block the close - better to close with potential
                            # orphan than to leave position open
                            self.logger.warning(
                                f"[RACE-SAFE] Failed to cancel brackets for "
                                f"{strategy_state.strategy_id}: {cancel_err}"
                            )
                    # ============================================================

                    self._log_verbose(
                        f"Submitting order for {strategy_state.strategy_id} ({reason}): "
                        f"{order.side} {order.quantity} {order.asset.symbol}, tag={order.tag}"
                    )
                    try:
                        self.broker.submit_order(order)
                        # CRITICAL: Check if order was actually accepted (not rejected due to timeout/error)
                        # The broker may return without exception but set order.status = "rejected"
                        if getattr(order, "status", None) == "rejected":
                            error_msg = getattr(order, "error", "Unknown rejection reason")
                            # Track rejection errors for market closed detection
                            rejected_errors.append(error_msg)
                            # Print to console for visibility (rejections should NEVER be silent)
                            print(
                                f"\033[91m❌ ORDER REJECTED: {strategy_state.strategy_id} "
                                f"{order.side} {order.quantity} {order.asset.symbol}: {error_msg}\033[0m",
                                flush=True,
                            )
                            self.logger.error(f"Order rejected for {strategy_state.strategy_id}: {error_msg}")
                            # DO NOT update virtual position - order never actually hit the exchange
                            mark_submitted = False
                        else:
                            mark_submitted = True
                            # Register order ID with bracket manager so we track its fill
                            if self.bracket_manager is not None and hasattr(order, "id") and order.id:
                                self.bracket_manager.register_our_order(order.id)
                                # Mark in-flight window to prevent position sync race conditions
                                self.bracket_manager.mark_order_submitted()
                            # Register with order registry for bulletproof tracking
                            if self.order_registry and hasattr(order, "id") and order.id:
                                from tools.order_registry import OrderIntent, OrderPurpose

                                purpose = OrderPurpose.CLOSE if is_close else OrderPurpose.ENTRY
                                intent = OrderIntent(
                                    strategy_id=strategy_state.strategy_id,
                                    symbol=strategy_state.symbol,
                                    side=order.side.upper(),
                                    qty=int(order.quantity),
                                    purpose=purpose,
                                    is_close=is_close,
                                )
                                self.order_registry.register_submission(order.tag, order.id, intent)
                    except Exception as submit_err:
                        # Track exception errors for market closed detection
                        rejected_errors.append(str(submit_err))
                        # Print to console for visibility (errors should NEVER be silent)
                        print(
                            f"\033[91m❌ ORDER FAILED: {strategy_state.strategy_id} "
                            f"{order.side} {order.quantity} {order.asset.symbol}: {submit_err}\033[0m",
                            flush=True,
                        )
                        self.logger.error(
                            f"Broker submit failed for {strategy_state.strategy_id}: {submit_err}", exc_info=True
                        )
                else:
                    # Dry-run / simulated path - log clearly that order is NOT being sent
                    self.logger.info(
                        f"[DRY-RUN] Simulated {order.side} {order.quantity} {order.asset.symbol} "
                        f"for {strategy_state.strategy_id} ({reason}) - NOT submitted to exchange"
                    )
                    mark_submitted = True
                if mark_submitted:
                    self.rate_limiter.mark_order_submitted()

                    # Update virtual position immediately (assume market orders fill)
                    # Get current price from cached data filtered to current_time (prevents lookahead)
                    df = self.shared_data.get_data_at_time(
                        strategy_state.symbol, current_time, self.max_lookback, self.timestep
                    )

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

                    # df is already filtered to current_time, so iloc[-1] is safe
                    if True:  # Preserve indentation for following code
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
                            # Fallback: use last bar in filtered frame (safe - no lookahead)
                            fill_price = float(df["close"].iloc[-1])
                            bar_time = df.index[-1] if len(df.index) > 0 else current_time
                        current_price = fill_price

                        # Simulated virtual fill (use order.tag for idempotency)
                        order_id = getattr(order, "tag", None)
                        self.logger.info(
                            f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: I just submitted a {order.side} "
                            f"order for {order.quantity} contracts. Since it's a market order, I'm assuming it "
                            f"fills immediately at {current_price}. Updating my virtual position now. (tag={order_id})"
                        )
                        strategy_state.tracker.execute_order(
                            strategy_state.symbol, order.quantity, order.side, current_price, order_id=order_id
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
                            # Check if BracketOrderManager already handled P&L with actual fill prices
                            if not strategy_state.exit_handled_by_bracket:
                                # Record P&L using bar close (only path for time/force exits)
                                entry_price = (
                                    strategy_state.entry_price
                                    if strategy_state.entry_price is not None
                                    else current_price
                                )
                                multiplier = get_multiplier(strategy_state.symbol)
                                realized = (current_price - entry_price) * qty_before * multiplier
                                net_realized = realized - strategy_state.fees_since_entry
                                strategy_state.realized_pnl += net_realized
                                self.logger.info(
                                    f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Trade complete! "
                                    f"Entry={entry_price}, exit={current_price}, qty={qty_before}. "
                                    f"Net P&L: ${net_realized:.2f}. Resetting entry tracking."
                                )
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
                                # Increment exit type counter
                                if reason == "bracket_sl":
                                    strategy_state.sl_count += 1
                                elif reason == "bracket_tp":
                                    strategy_state.tp_count += 1
                                elif reason == "time_exit":
                                    strategy_state.time_exit_count += 1
                                elif reason == "force_close":
                                    strategy_state.force_close_count += 1
                            else:
                                # BOM already recorded P&L with actual fills - skip to avoid double-counting
                                self.logger.info(
                                    f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: Exit handled by "
                                    f"BracketOrderManager with actual fill prices. Skipping bar-based P&L."
                                )
                            # Reset entry tracking after a close/reversal (always do this)
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
                            self.logger.info(
                                f"[EXECUTOR-REASONING] {strategy_state.strategy_id}: New position opened! "
                                f"I went {order.side} with {qty_after} contracts at {current_price}. "
                                f"Targets: TP={strategy_state.take_profit_price}, SL={strategy_state.stop_loss_price}. "
                                f"Now watching for bracket hits or time exit."
                            )

                        # If we simply added to existing position, keep original entry but refresh bars
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
                                    "notional_exposure": pos_qty,  # Contract count (simplified from dollar value)
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

        return orders_submitted, rejected_errors

    def get_strategy_state(self, strategy_id: str) -> Optional[EnhancedStrategyState]:
        """Get strategy state by ID."""
        for strategy in self.strategies:
            if strategy.strategy_id == strategy_id:
                return strategy
        return None

    def get_all_strategies(self) -> List[EnhancedStrategyState]:
        """Get list of all strategy states."""
        return self.strategies

    @property
    def strategy_states(self) -> Dict[str, "EnhancedStrategyState"]:
        """Get strategy states as dict keyed by strategy_id.

        Used by BracketOrderManager for fill processing and bracket recreation.
        """
        return {s.strategy_id: s for s in self.strategies}

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

    def get_live_status_table(self) -> str:
        """
        Generate a live status table for all strategies with color coding.

        Columns:
        - symbol, strategy_id, qty (exposure), side, entry_price, current_price
        - unrealized_pnl, ticks_to_sl, ticks_to_tp, sl_tp_exist, pnl_at_tp, pnl_at_sl
        - time_held, max_bars, bars_in_trade, last_signal

        Color coding:
        - Long positions: dark green background
        - Short positions: dark purple background
        - Positive P&L: bold green
        - Negative P&L: red
        """
        from custom_portfolio.data.futures_metadata import get_multiplier, get_tick_size

        # ANSI color codes
        RESET = "\033[0m"
        RED = "\033[91m"
        YELLOW = "\033[93m"  # NaN/warning indicator
        BOLD_GREEN = "\033[1;92m"
        DIM = "\033[2m"
        CYAN = "\033[96m"  # Signal visibility TRUE
        GRAY = "\033[90m"  # Signal visibility FALSE
        # Background colors for positions
        BG_DARK_GREEN = "\033[48;5;22m"  # Dark green for long
        BG_DARK_PURPLE = "\033[48;5;54m"  # Dark purple for short

        rows = []
        for state in sorted(self.strategies, key=lambda s: (s.symbol, s.strategy_id)):
            pos = state.tracker.get_position(state.symbol)
            qty = pos.quantity if pos else 0

            # Get current price from cache
            current_price = None
            try:
                md = self.shared_data.get_cached_data(state.symbol, self.max_lookback, self.timestep)
                df = md.df if hasattr(md, "df") else md
                if df is not None and len(df) > 0:
                    current_price = float(df["close"].iloc[-1])
            except Exception:
                pass

            # Calculate values
            multiplier = get_multiplier(state.symbol)
            tick_size = get_tick_size(state.symbol)

            # Side and colors
            if qty > 0:
                side = "LONG"
                row_bg = BG_DARK_GREEN
            elif qty < 0:
                side = "SHORT"
                row_bg = BG_DARK_PURPLE
            else:
                side = "-"
                row_bg = ""

            # Entry price
            entry_price = state.entry_price if state.entry_price else 0

            # Unrealized P&L
            unrealized_pnl = 0.0
            if qty != 0 and entry_price and current_price:
                unrealized_pnl = (current_price - entry_price) * qty * multiplier

            # Format P&L with color
            if unrealized_pnl > 0:
                pnl_str = f"{BOLD_GREEN}${unrealized_pnl:+.2f}{RESET}"
            elif unrealized_pnl < 0:
                pnl_str = f"{RED}${unrealized_pnl:+.2f}{RESET}"
            else:
                pnl_str = f"${unrealized_pnl:.2f}"

            # SL/TP prices and ticks
            sl_price = state.stop_loss_price
            tp_price = state.take_profit_price
            sl_tp_exist = "Y" if (sl_price or tp_price) else "N"

            ticks_to_sl = "-"
            ticks_to_tp = "-"
            pnl_at_sl = "-"
            pnl_at_tp = "-"

            if current_price and tick_size > 0:
                if sl_price:
                    ticks_to_sl_val = abs(current_price - sl_price) / tick_size
                    ticks_to_sl = f"{ticks_to_sl_val:.0f}"
                    # P&L at SL
                    if qty != 0:
                        pnl_at_sl_val = (sl_price - entry_price) * qty * multiplier if entry_price else 0
                        pnl_at_sl = (
                            f"{RED}${pnl_at_sl_val:.0f}{RESET}" if pnl_at_sl_val < 0 else f"${pnl_at_sl_val:.0f}"
                        )

                if tp_price:
                    ticks_to_tp_val = abs(tp_price - current_price) / tick_size
                    ticks_to_tp = f"{ticks_to_tp_val:.0f}"
                    # P&L at TP
                    if qty != 0:
                        pnl_at_tp_val = (tp_price - entry_price) * qty * multiplier if entry_price else 0
                        pnl_at_tp = (
                            f"{BOLD_GREEN}${pnl_at_tp_val:.0f}{RESET}" if pnl_at_tp_val > 0 else f"${pnl_at_tp_val:.0f}"
                        )

            # Time held
            time_held = "-"
            if state.entry_time and qty != 0:
                # Use pandas timestamp for timezone compatibility
                now = pd.Timestamp.now(tz=state.entry_time.tzinfo if hasattr(state.entry_time, "tzinfo") else None)
                delta = now - state.entry_time
                minutes = int(delta.total_seconds() / 60)
                if minutes >= 60:
                    time_held = f"{minutes // 60}h{minutes % 60}m"
                else:
                    time_held = f"{minutes}m"

            # Max bars from params (check multiple locations)
            max_bars = (
                state.params.get("max_bars")
                or state.params.get("max_bars_in_trade")
                or state.params.get("max_bars_in_position")
            )
            # Also check if it was passed in time_exit config
            if not max_bars and hasattr(state, "time_exit_config"):
                max_bars = state.time_exit_config.get("max_bars")
            max_bars_str = str(int(max_bars)) if max_bars else "-"

            # Bars in trade
            bars_in = state.bars_in_trade if qty != 0 else 0

            # Last signal
            last_signal = getattr(state, "last_signal", None) or "-"

            # Data status column - shows actual/required bars with color coding
            data_status = getattr(state, "data_status", "ok")
            bar_count = getattr(state, "data_bar_count", 0)
            min_required = getattr(state, "min_bars_required", 0)
            if data_status == "ok":
                # Green: sufficient data
                data_col = f"{CYAN}{bar_count}/{min_required}{RESET}"
            elif data_status == "NaN":
                # Yellow: NaN values detected in calculations
                data_col = f"{YELLOW}NaN{RESET}"
            else:
                # Red: insufficient bars or calculation error
                data_col = f"{RED}{bar_count}/{min_required}{RESET}"

            # Signal visibility columns (up to 5)
            # Format: colored label based on True/False
            vis_cols = ["", "", "", "", ""]  # 5 placeholder columns
            signal_vis = getattr(state, "signal_visibility", []) or []
            for i, (label, is_true) in enumerate(signal_vis[:5]):
                color = CYAN if is_true else GRAY
                vis_cols[i] = f"{color}{label}{RESET}"

            # Trading window countdowns from calendar
            t_win = "-"
            t_flat = "-"
            if self.calendar is not None:
                from datetime import datetime
                from datetime import timezone as tz

                try:
                    allowed_sessions = getattr(state, "allowed_sessions", None) or ["24/7"]
                    cal_status = self.calendar.get_status(
                        state.symbol,
                        datetime.now(tz.utc),
                        position_qty=int(qty),
                        allowed_sessions=allowed_sessions,
                    )

                    # tWin: countdown until order entry is blocked (stop_new_orders)
                    if cal_status.can_enter_orders:
                        # Currently can trade - show countdown to stop_new_orders
                        if cal_status.stop_orders_countdown_seconds is not None:
                            mins = cal_status.stop_orders_countdown_seconds // 60
                            if mins > 30:
                                t_win = f"{BOLD_GREEN}{mins}m{RESET}"
                            else:
                                t_win = f"{YELLOW}{mins}m{RESET}"
                        else:
                            t_win = f"{BOLD_GREEN}open{RESET}"
                    else:
                        # Can't trade - show countdown to next opening
                        if cal_status.platform_countdown_seconds is not None:
                            mins = cal_status.platform_countdown_seconds // 60
                            t_win = f"{RED}+{mins}m{RESET}"
                        else:
                            t_win = f"{RED}closed{RESET}"

                    # tFlat: countdown until must be flat (force_flat)
                    if cal_status.close_countdown_seconds is not None:
                        flat_mins = cal_status.close_countdown_seconds // 60
                        if flat_mins <= 15:
                            t_flat = f"{RED}{flat_mins}m{RESET}"
                        elif flat_mins <= 60:
                            t_flat = f"{YELLOW}{flat_mins}m{RESET}"
                        else:
                            t_flat = f"{flat_mins}m"
                    elif cal_status.must_be_flat:
                        t_flat = f"{RED}NOW{RESET}"
                except Exception:
                    pass  # Keep defaults if calendar lookup fails

            # Build row
            row = {
                "symbol": state.symbol,
                "strategy": state.strategy_id[-6:],  # Last 6 chars for brevity
                "qty": f"{qty:+.0f}" if qty != 0 else "-",
                "side": side,
                "entry": f"{entry_price:.2f}" if entry_price else "-",
                "price": f"{current_price:.2f}" if current_price else "-",
                "pnl": pnl_str if qty != 0 else "-",
                "→SL": ticks_to_sl,
                "→TP": ticks_to_tp,
                "SL/TP": sl_tp_exist,
                "@SL": pnl_at_sl,
                "@TP": pnl_at_tp,
                "held": time_held,
                "bars": f"{bars_in}/{max_bars_str}",
                "signal": last_signal[:4] if last_signal != "-" else "-",
                "#tr": str(state.trade_count) if state.trade_count > 0 else "-",
                "rPnL": (
                    f"{BOLD_GREEN}${state.realized_pnl:+.0f}{RESET}"
                    if state.realized_pnl > 0
                    else (f"{RED}${state.realized_pnl:+.0f}{RESET}" if state.realized_pnl < 0 else "-")
                ),
                "exits": (
                    f"{state.sl_count}/{state.tp_count}/{state.time_exit_count}" if state.trade_count > 0 else "-"
                ),
                "tWin": t_win,
                "tFlat": t_flat,
                "data": data_col,
                "v1": vis_cols[0],
                "v2": vis_cols[1],
                "v3": vis_cols[2],
                "v4": vis_cols[3],
                "v5": vis_cols[4],
                "_bg": row_bg,
            }
            rows.append(row)

        if not rows:
            return "No strategies loaded"

        # Build table
        headers = [
            "symbol",
            "strategy",
            "qty",
            "side",
            "entry",
            "price",
            "pnl",
            "→SL",
            "→TP",
            "SL/TP",
            "@SL",
            "@TP",
            "held",
            "bars",
            "signal",
            "#tr",
            "rPnL",
            "exits",
            "tWin",
            "tFlat",
            "data",
            "v1",
            "v2",
            "v3",
            "v4",
            "v5",
        ]
        col_widths = {h: max(len(h), max(len(str(r.get(h, ""))) for r in rows)) for h in headers}

        # Header line
        header_line = "  ".join(f"{h:>{col_widths[h]}}" for h in headers)
        separator = "-" * len(header_line)

        lines = [f"{DIM}{header_line}{RESET}", separator]

        for row in rows:
            bg = row.get("_bg", "")
            line_parts = []
            for h in headers:
                val = str(row.get(h, "-"))
                # Strip ANSI for width calculation
                import re

                clean_val = re.sub(r"\033\[[0-9;]*m", "", val)
                padding = col_widths[h] - len(clean_val)
                line_parts.append(" " * padding + val)
            line = "  ".join(line_parts)
            if bg:
                lines.append(f"{bg}{line}{RESET}")
            else:
                lines.append(line)

        return "\n".join(lines)

    def plot_instrument_charts(self, bars: int = 60, height: int = 12) -> None:
        """
        Plot side-by-side mini-charts for each unique instrument in terminal.

        Uses plotext to render compact sparkline-style charts showing recent
        price action for each traded symbol (ES, NQ, GC, etc.).

        Args:
            bars: Number of bars to show (default: 60 = 1 hour at 1-minute)
            height: Height of each chart in terminal rows (default: 12)
        """
        try:
            import plotext as plt
        except ImportError:
            print("(plotext not installed - run: pip install plotext)")
            return

        # Get unique symbols and their data
        symbols = sorted(set(s.symbol for s in self.strategies))
        if not symbols:
            return

        # Collect data for each symbol
        symbol_data = {}
        for symbol in symbols:
            try:
                md = self.shared_data.get_cached_data(symbol, self.max_lookback, self.timestep)
                df = md.df if hasattr(md, "df") else md
                if df is not None and len(df) > 0 and "close" in df.columns:
                    closes = df["close"].tail(bars).values.tolist()
                    if len(closes) >= 10:  # Need minimum data
                        symbol_data[symbol] = closes
            except Exception:
                pass

        if not symbol_data:
            print("(no chart data available)")
            return

        num_charts = len(symbol_data)
        if num_charts == 0:
            return

        # Setup plotext for side-by-side charts
        plt.clear_figure()
        plt.clear_data()

        # Calculate width per chart (terminal width divided by number of charts)
        try:
            term_width = plt.tw()
            chart_width = max(20, (term_width - 4) // num_charts)
        except Exception:
            chart_width = 40

        # Configure subplots: 1 row, N columns (side by side)
        plt.subplots(1, num_charts)

        # Dark theme for readability
        try:
            plt.canvas_color("black")
            plt.axes_color("black")
            plt.ticks_color("white")
        except Exception:
            pass

        # Plot each symbol
        for idx, (symbol, closes) in enumerate(symbol_data.items(), start=1):
            plt.subplot(1, idx)

            # Set size for this subplot
            try:
                plt.plotsize(chart_width, height)
            except Exception:
                pass

            plt.title(symbol)

            # Y-axis limits with padding
            try:
                ymin, ymax = min(closes), max(closes)
                span = ymax - ymin if ymax != ymin else max(abs(ymax), 1.0)
                pad = span * 0.05
                plt.ylim(ymin - pad, ymax + pad)
            except Exception:
                pass

            # Determine color based on trend
            if len(closes) > 1:
                if closes[-1] > closes[0]:
                    color = "green"
                elif closes[-1] < closes[0]:
                    color = "red"
                else:
                    color = "cyan"
            else:
                color = "cyan"

            try:
                plt.plot(closes, color=color)
            except Exception:
                plt.plot(closes)

            # Minimal labels
            plt.xlabel("")
            plt.ylabel("")

        # Render all charts at once
        try:
            plt.show()
        except Exception as e:
            print(f"(chart render failed: {e})")

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
            count, _rejected = self._execute_all_orders(orders, current_time)
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
