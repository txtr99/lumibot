# Strategy Implementation Patterns Analysis
## Deep Analysis of Existing LumiBot Strategies for Multi-Strategy Architecture

**Document Version:** 1.0
**Date:** 2025-11-18
**Analysis Scope:** test_strat_v02_working_with_virtual_positions.py and framework patterns

---

## Executive Summary

This document analyzes the implementation patterns found in LumiBot's existing strategies to extract reusable patterns for multi-strategy architecture. The analysis reveals that successful strategies follow a consistent pattern of initialization, shared resource setup, trading logic with independent state, and lifecycle management that can be directly adapted for multi-strategy scenarios.

**Key Findings:**
- Virtual position tracking provides complete strategy isolation
- Trading calendar system enables shared timing infrastructure
- Parameter-based configuration supports strategy variants
- Independent state management via `self.vars` enables clean separation
- Event-driven callbacks (on_filled_order) support async execution

---

## Pattern 1: Independent State Management

### Implementation in test_strat_v02

```python
# Lines 124-138: Strategy initialization with isolated state
def initialize(self):
    self.sleeptime = "1M"
    self.set_market("24/5")

    # Contract asset stored in self.vars for persistence
    if not hasattr(self.vars, "contract_asset"):
        symbol = self.parameters["symbol"]
        self.vars.contract_asset = Asset(symbol, asset_type=Asset.AssetType.CONT_FUTURE)

    # Virtual position tracker - independent of broker
    if not hasattr(self.vars, "virtual_tracker"):
        from lumibot.tools.virtual_position_tracker import VirtualPositionTracker
        self.vars.virtual_tracker = VirtualPositionTracker()

    # Order tracking DataFrame
    if not hasattr(self.vars, "order_history"):
        self.vars.order_history = pd.DataFrame(columns=[...])
```

**Pattern Analysis:**
- ✅ Uses `self.vars` namespace to avoid collisions
- ✅ Guards initialization with `hasattr` checks
- ✅ Each state component is independent and isolated
- ✅ No shared mutable state between strategies

**Multi-Strategy Adaptation:**
```python
class IndependentStrategy(Strategy):
    def __init__(self, strategy_id, **kwargs):
        super().__init__(**kwargs)
        self.strategy_id = strategy_id  # Unique identifier

    def initialize(self):
        # Namespace state by strategy_id to prevent collisions
        tracker_key = f"tracker_{self.strategy_id}"
        if not hasattr(self.vars, tracker_key):
            setattr(self.vars, tracker_key, VirtualPositionTracker())

        self.tracker = getattr(self.vars, tracker_key)
```

---

## Pattern 2: Shared Resource Utilization

### Trading Calendar Implementation

```python
# Lines 167-172: Shared calendar initialization
if not hasattr(self.vars, "calendar"):
    self.vars.calendar = TradingCalendar(MOUNTAIN_TZ, TOPSTEPX_PLATFORM)
    self.vars.calendar.register_sessions(TRADING_SESSIONS)
    self.vars.calendar.map_symbols(SESSION_DEFAULTS)
```

**Pattern Analysis:**
- ✅ Single calendar instance shared across iterations
- ✅ Immutable configuration (sessions, symbol mapping)
- ✅ Stateless queries via `get_status()`
- ✅ No side effects from concurrent access

**Multi-Strategy Benefits:**
```python
# Multiple strategies can safely share one calendar
class MultiStrategyExecutor:
    def __init__(self, strategies):
        # Create shared calendar ONCE
        self.shared_calendar = TradingCalendar(...)

        # All strategies reference same instance
        for strategy in strategies:
            strategy.calendar = self.shared_calendar
```

**Why This Works:**
- Calendar queries are read-only
- No mutable state modified by queries
- Each strategy gets independent `CalendarStatus` object
- Thread-safe by design (no shared mutable state)

---

## Pattern 3: Virtual Position Tracking

### Implementation Pattern

```python
# Lines 489-506: Using virtual positions instead of broker positions
def on_trading_iteration(self):
    # Get VIRTUAL position (independent of broker)
    virtual_pos = self.vars.virtual_tracker.get_position(symbol)
    virtual_qty = virtual_pos.quantity if virtual_pos else 0

    # Display position with current price
    last_price = self.get_last_price(asset)
    position_table = self.vars.virtual_tracker.format_position_table(symbol, last_price)

    # Make trading decisions based on virtual position
    if is_even_minute and virtual_qty <= 0:
        # Enter long...

    elif not is_even_minute and virtual_qty >= 0:
        # Enter short...
```

**Critical Pattern Elements:**
1. **Never use broker positions** for trading logic
2. **Always use virtual positions** for consistency
3. **Update virtual immediately** after order submission
4. **Independent P&L calculation** per strategy

**Multi-Strategy Implementation:**
```python
class Strategy1:
    def initialize(self):
        self.tracker = VirtualPositionTracker()  # Strategy 1's tracker

class Strategy2:
    def initialize(self):
        self.tracker = VirtualPositionTracker()  # Strategy 2's tracker

# Complete isolation:
# - Strategy 1 can be LONG ES
# - Strategy 2 can be SHORT ES
# - Same symbol, opposite positions, no conflicts!
```

---

## Pattern 4: Order Execution with Virtual Updates

### Execution Pattern

```python
# Lines 647-669: Order submission with virtual update
if exit_qty > 0:
    exit_order = self.create_order(asset, exit_qty, Order.OrderSide.BUY)
    exit_submitted = self.submit_order(exit_order)
    exit_id = exit_submitted.identifier

    # Update virtual position immediately (assume fills)
    self.vars.virtual_tracker.execute_order(symbol, exit_qty, "buy", last_price)

# Now enter LONG position
order = self.create_order(asset, trade_qty, Order.OrderSide.BUY)
submitted_order = self.submit_order(order)
order_id = submitted_order.identifier

# Update virtual position (assume market orders fill)
self.vars.virtual_tracker.execute_order(symbol, trade_qty, "buy", last_price)
```

**Pattern Analysis:**
- Order submission and virtual update are atomic operations
- Virtual position assumes immediate fills (valid for market orders)
- No dependency on broker confirmation for logic
- Async order tracking happens separately via `on_filled_order`

**Multi-Strategy Sequencing:**
```python
def execute_all_strategy_orders(strategies, delay_seconds=2):
    """Execute orders from all strategies with rate limiting"""
    all_orders = []

    # Step 1: Collect orders from all strategies
    for strategy in strategies:
        if strategy.has_pending_order():
            order = strategy.get_pending_order()
            all_orders.append({
                'strategy': strategy,
                'order': order,
                'price': strategy.get_last_price()
            })

            # Update virtual position immediately
            strategy.tracker.execute_order(
                order.asset.symbol,
                order.quantity,
                order.side,
                order.price
            )

    # Step 2: Execute orders sequentially with delays
    for order_info in all_orders:
        submit_order(order_info['order'])
        time.sleep(delay_seconds)  # Rate limiting
```

---

## Pattern 5: Parameter-Based Strategy Configuration

### Configuration Pattern

```python
# Lines 117-121: Strategy parameters
parameters = {
    "symbol": "MGC",
    "contracts_to_trade": 1,
    "allowed_sessions": ["24/7"],
}
```

**Multi-Strategy Variant Generation:**
```python
# Generate 10 strategy variants with different parameters
def create_strategy_variants(base_symbol, count=10):
    strategies = []

    for i in range(count):
        config = {
            "strategy_id": f"strat_{i+1}",
            "symbol": base_symbol,
            "contracts_to_trade": 1,
            "allowed_sessions": ["New_York"],
            # Vary technical parameters
            "fast_sma": 5 + i,
            "slow_sma": 15 + i*2,
            "rsi_oversold": 25 + i,
            "rsi_overbought": 75 - i,
        }

        strategy = create_strategy_from_config(config)
        strategies.append(strategy)

    return strategies
```

**Benefits:**
- Easy to create multiple similar strategies
- Parameter optimization becomes straightforward
- Configuration can be stored in JSON/YAML files
- Enables systematic strategy variations

---

## Pattern 6: Lifecycle Method Coordination

### Order Fill Callback

```python
# Lines 752-759: Async order fill notification
def on_filled_order(self, position, order, price, quantity, multiplier):
    symbol = self.parameters["symbol"]
    self.log_message(
        f"Order filled: {order.side} {quantity} {symbol} @ {price}.",
        color="green",
    )
```

**Multi-Strategy Coordination:**
```python
class MultiStrategyExecutor:
    def on_filled_order(self, position, order, price, quantity, multiplier):
        """Route order fills to originating strategy"""

        # Find which strategy submitted this order
        strategy_id = self.get_strategy_for_order(order.identifier)
        originating_strategy = self.strategies[strategy_id]

        # Call strategy's callback
        originating_strategy.on_filled_order(position, order, price, quantity, multiplier)

        # Update attribution tracking
        self.attribution.record_fill(strategy_id, order, price, quantity)
```

---

## Pattern 7: Robust Error Handling

### Order Rejection Handling

```python
# Lines 367-481: Comprehensive rejected order logging
def _log_rejected_order(self, order_details, error_response, purpose, submitted_order):
    # Extract error details
    if hasattr(submitted_order, "error") and submitted_order.error:
        error_info = submitted_order.error
    else:
        error_info = error_response or "No error details available"

    # Parse error (handle multiple formats)
    if "Failed to place order: " in error_info:
        dict_str = error_info.split("Failed to place order: ", 1)[1]
        error_dict = ast.literal_eval(dict_str)

    # Log to both console and file
    console_msg = f"""
🚨 ORDER REJECTED BY BROKER
Strategy: {strategy_name}
Purpose: {purpose}
Error: {error_message}
"""
    self.log_message(console_msg, color="red")

    # Persistent file logging
    with open(self.vars.rejected_orders_log_path, "a") as f:
        f.write(file_entry)
```

**Multi-Strategy Error Isolation:**
```python
class ResilientMultiStrategyExecutor:
    def execute_strategies(self):
        for strategy in self.strategies:
            try:
                strategy.on_trading_iteration()
            except OrderRejectionError as e:
                # Log but continue with other strategies
                self.log_rejected_order(strategy.id, e)
                continue
            except Exception as e:
                # Critical error - log and disable strategy
                self.disable_strategy(strategy.id, reason=str(e))
                continue
```

---

## Pattern 8: Data Access Patterns

### Shared vs. Strategy-Specific Data

**Shared Data (Efficient):**
```python
# Get price data ONCE for all strategies
market_data = broker.data_source.get_historical_prices(asset, 100, "1M")

# All strategies process same data
for strategy in strategies:
    strategy.process_shared_data(market_data)
```

**Strategy-Specific Calculations:**
```python
class StrategyVariant1:
    def process_shared_data(self, market_data):
        # Strategy-specific indicator calculations
        self.sma_fast = market_data['close'].rolling(10).mean()
        self.sma_slow = market_data['close'].rolling(20).mean()

class StrategyVariant2:
    def process_shared_data(self, market_data):
        # Different indicators for same data
        self.rsi = calculate_rsi(market_data['close'])
        self.macd = calculate_macd(market_data['close'])
```

---

## Pattern 9: Calendar-Based Trading Windows

### Status Check Pattern

```python
# Lines 508-518: Single call for complete status
status = self.vars.calendar.get_status(
    symbol, current_dt, virtual_qty, None,
    allowed_sessions=self.parameters.get("allowed_sessions")
)

# Extract minute info for trading logic
current_minute = status.minute
is_even_minute = status.is_even_minute

# Check platform restrictions
if not status.platform_open:
    return  # Skip iteration

# Check session restrictions
if not status.can_enter_orders and virtual_qty == 0:
    return  # No new entries
```

**Multi-Strategy Calendar Sharing:**
```python
class MultiStrategyManager:
    def __init__(self):
        # One calendar for all strategies
        self.calendar = TradingCalendar(...)

    def check_trading_windows(self, strategies, current_time):
        results = {}

        for strategy in strategies:
            status = self.calendar.get_status(
                strategy.symbol,
                current_time,
                strategy.get_virtual_position(),
                allowed_sessions=strategy.allowed_sessions
            )

            results[strategy.id] = {
                'can_trade': status.platform_open and status.can_enter_orders,
                'must_close': status.must_be_flat,
                'status': status
            }

        return results
```

---

## Pattern 10: Position Verification and Confirmation

### Wait-and-Confirm Pattern

```python
# Lines 322-365: Polling for position confirmation
def _wait_and_confirm_flat(self, asset, symbol, max_attempts=3):
    for attempt in range(1, max_attempts + 1):
        time.sleep(2)

        # Refresh position from broker
        position = self.get_position(asset)
        current_qty = position.quantity if position is not None else 0

        if current_qty == 0:
            return True  # Confirmed flat
        else:
            # Log diagnostic info
            self.log_message(
                f"Position not flat yet (qty: {current_qty}), attempt {attempt}/{max_attempts}.",
                color="yellow",
            )

    return False  # Failed to confirm
```

**Multi-Strategy Adaptation:**
This pattern should NOT be used in multi-strategy scenarios!
- Creates delays that slow down all strategies
- Virtual positions eliminate need for broker confirmation
- Better to use virtual tracker and verify async

**Recommended Approach:**
```python
# Immediate virtual update (no waiting)
strategy.tracker.execute_order(symbol, qty, side, price)

# Async verification via on_filled_order callback
def on_filled_order(self, position, order, ...):
    # Verify virtual matches broker (for audit trail)
    virtual_qty = self.tracker.get_position(symbol).quantity
    broker_qty = position.quantity

    if virtual_qty != broker_qty:
        self.log_warning(f"Position mismatch: virtual={virtual_qty}, broker={broker_qty}")
```

---

## Reusable Components for Multi-Strategy

### Component 1: StrategyState Container

```python
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class StrategyState:
    """Complete state container for one strategy instance"""
    strategy_id: str
    symbol: str
    contracts: int

    # Virtual position tracking
    virtual_tracker: VirtualPositionTracker

    # Trading parameters
    params: dict

    # State tracking
    order_history: pd.DataFrame
    trade_history: list
    last_signal: Optional[str] = None
    last_execution_time: Optional[datetime] = None

    # Performance metrics
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
```

### Component 2: SharedResourceManager

```python
class SharedResourceManager:
    """Manages resources shared across all strategies"""

    def __init__(self, broker):
        self.broker = broker
        self.calendar = None
        self.data_cache = {}
        self.last_fetch = {}

    def get_or_fetch_data(self, symbol, length, timestep):
        """Fetch data once, cache for all strategies"""
        cache_key = f"{symbol}_{length}_{timestep}"

        # Check cache freshness (TTL = 60 seconds)
        if cache_key in self.data_cache:
            last_fetch_time = self.last_fetch[cache_key]
            if (datetime.now() - last_fetch_time).seconds < 60:
                return self.data_cache[cache_key]

        # Fetch fresh data
        data = self.broker.data_source.get_historical_prices(
            Asset(symbol), length, timestep
        )

        # Update cache
        self.data_cache[cache_key] = data
        self.last_fetch[cache_key] = datetime.now()

        return data
```

### Component 3: StrategyAttribution

```python
class StrategyAttribution:
    """Track performance attribution per strategy"""

    def __init__(self):
        self.strategy_metrics = {}

    def register_strategy(self, strategy_id, initial_capital):
        self.strategy_metrics[strategy_id] = {
            'capital': initial_capital,
            'trades': [],
            'pnl': 0.0,
            'win_rate': 0.0,
            'sharpe': 0.0
        }

    def record_trade(self, strategy_id, entry_price, exit_price, quantity):
        pnl = (exit_price - entry_price) * quantity
        self.strategy_metrics[strategy_id]['trades'].append(pnl)
        self.strategy_metrics[strategy_id]['pnl'] += pnl

    def calculate_metrics(self, strategy_id):
        metrics = self.strategy_metrics[strategy_id]
        trades = metrics['trades']

        if not trades:
            return metrics

        # Calculate win rate
        wins = [t for t in trades if t > 0]
        metrics['win_rate'] = len(wins) / len(trades)

        # Calculate Sharpe ratio
        returns = pd.Series(trades)
        metrics['sharpe'] = returns.mean() / returns.std() if returns.std() > 0 else 0

        return metrics
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Shared Mutable State

**❌ Bad:**
```python
class BadMultiStrategy:
    shared_position = {'ES': 0}  # Mutable shared state!

    def strategy1_trade(self):
        self.shared_position['ES'] += 1  # Race condition!

    def strategy2_trade(self):
        self.shared_position['ES'] -= 1  # Conflicts!
```

**✅ Good:**
```python
class GoodMultiStrategy:
    def __init__(self):
        self.strategy1_tracker = VirtualPositionTracker()
        self.strategy2_tracker = VirtualPositionTracker()

    def strategy1_trade(self):
        self.strategy1_tracker.execute_order('ES', 1, 'buy', 4500)

    def strategy2_trade(self):
        self.strategy2_tracker.execute_order('ES', 1, 'sell', 4500)
```

### Anti-Pattern 2: Blocking Waits in Loop

**❌ Bad:**
```python
for strategy in strategies:
    order = strategy.submit_order()
    time.sleep(10)  # Blocks entire loop!
    verify_order_filled(order)  # Wastes time
```

**✅ Good:**
```python
# Async submission
for strategy in strategies:
    order = strategy.submit_order()
    strategy.tracker.execute_order(...)  # Immediate virtual update

# Async verification via callback
def on_filled_order(self, ...):
    # Handle fills as they arrive
```

### Anti-Pattern 3: Redundant API Calls

**❌ Bad:**
```python
for strategy in strategies:
    data = strategy.get_historical_prices('ES', 100, '1M')  # 30 identical calls!
    strategy.calculate_signals(data)
```

**✅ Good:**
```python
# Fetch once
shared_data = shared_manager.get_historical_prices('ES', 100, '1M')  # 1 call

# Distribute to all
for strategy in strategies:
    strategy.calculate_signals(shared_data)
```

---

## Recommended Multi-Strategy Pattern

### Complete Implementation Template

```python
class MultiStrategySystem:
    def __init__(self, broker, strategy_configs):
        self.broker = broker
        self.shared_resources = SharedResourceManager(broker)
        self.attribution = StrategyAttribution()

        # Create independent strategies
        self.strategies = []
        for config in strategy_configs:
            strategy = self.create_strategy(config)
            self.strategies.append(strategy)
            self.attribution.register_strategy(strategy.id, config['capital'])

    def on_trading_iteration(self):
        # 1. Fetch shared data ONCE
        symbols = list(set(s.symbol for s in self.strategies))
        for symbol in symbols:
            self.shared_resources.get_or_fetch_data(symbol, 100, '1M')

        # 2. All strategies generate signals independently
        for strategy in self.strategies:
            data = self.shared_resources.data_cache[f"{strategy.symbol}_100_1M"]
            strategy.calculate_signals(data)

        # 3. Execute orders sequentially with rate limiting
        self.execute_all_orders()

        # 4. Update attribution
        for strategy in self.strategies:
            if strategy.has_completed_trade():
                self.attribution.record_trade(...)

    def execute_all_orders(self):
        for strategy in self.strategies:
            if strategy.has_pending_order():
                order = strategy.get_pending_order()
                self.broker.submit_order(order)
                time.sleep(2)  # Rate limiting
```

---

## Summary and Recommendations

**Key Patterns to Adopt:**
1. ✅ Virtual position tracking per strategy
2. ✅ Shared immutable resources (calendar, data)
3. ✅ Independent strategy state via `self.vars` namespacing
4. ✅ Parameter-based configuration
5. ✅ Async callbacks for fills

**Patterns to Avoid:**
1. ❌ Shared mutable state
2. ❌ Blocking waits in loops
3. ❌ Redundant API calls
4. ❌ Tight coupling between strategies

**Next Implementation Steps:**
1. Create `MultiStrategyExecutor` class extending `StrategyExecutor`
2. Implement `SharedResourceManager` for data caching
3. Add `StrategyAttribution` for performance tracking
4. Build two-strategy example demonstrating patterns
5. Validate with comprehensive backtests

---

*End of Strategy Implementation Patterns Analysis*
