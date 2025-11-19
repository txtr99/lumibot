# Multi-Strategy Architecture Design Proposals
## Scalable Design for Running 30+ Independent Trading Strategies

**Document Version:** 1.0
**Date:** 2025-11-18
**Target:** 30 strategies across 3 markets (10 per file)

---

## Executive Summary

This document presents three architectural approaches for implementing multi-strategy trading in LumiBot, ranging from minimal modification to the current framework (Simple Extension) to a comprehensive enterprise-grade solution (Advanced Multi-Strategy Framework). All designs prioritize:

- **Strategy Independence:** Complete isolation of position tracking and decision-making
- **Resource Efficiency:** Shared data sources, calendars, and API connections
- **Rate Limit Compliance:** 2-second delays between orders, optimized API usage
- **Backtest Accuracy:** Realistic execution simulation for all strategies

**Recommended Approach:** **Design 2 (Enhanced Multi-Strategy Executor)** provides the best balance of implementation complexity, performance, and maintainability for the stated requirements.

---

## Design 1: Simple Extension (Minimal Changes)

### Architecture Overview

**Philosophy:** Extend existing `StrategyExecutor` with minimal framework changes.

```
┌──────────────────────────────────────────────────────┐
│         MultiStrategyWrapper                         │
│  - Manages list of Strategy instances               │
│  - Coordinates execution across strategies           │
│  - Delegates to individual StrategyExecutors         │
└──────────────────────────────────────────────────────┘
                       ↓
    ┌─────────────────┬─────────────────┬─────────────────┐
    ↓                 ↓                 ↓                 ↓
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│Strategy1│     │Strategy2│  ...│Strategy9│     │Strategy10│
│Executor │     │Executor │     │Executor │     │Executor  │
└─────────┘     └─────────┘     └─────────┘     └─────────┘
    ↓                 ↓                 ↓                 ↓
  ┌────────────────────────────────────────────────────────┐
  │          Shared Broker & Data Source                   │
  └────────────────────────────────────────────────────────┘
```

### Implementation

```python
from typing import List
from lumibot.strategies import Strategy
from lumibot.traders import Trader

class MultiStrategyWrapper:
    """
    Wrapper that coordinates multiple independent strategies.
    Each strategy has its own executor but shares broker/data.
    """

    def __init__(self, strategies: List[Strategy], shared_broker):
        self.strategies = strategies
        self.broker = shared_broker

        # Each strategy gets same broker (shared connection)
        for strategy in self.strategies:
            strategy.broker = self.broker

    def run_all(self):
        """Execute all strategies in coordinated fashion"""
        trader = Trader()

        # Add all strategies to single trader instance
        for strategy in self.strategies:
            trader.add_strategy(strategy)

        # Trader manages execution coordination
        trader.run_all()

# Usage
broker = create_broker()

strategies = [
    Strategy1(parameters={"symbol": "ES", "fast_sma": 10, "slow_sma": 20}),
    Strategy2(parameters={"symbol": "ES", "fast_sma": 5, "slow_sma": 15}),
    Strategy3(parameters={"symbol": "ES", "fast_sma": 15, "slow_sma": 30}),
    # ... 7 more strategies
]

wrapper = MultiStrategyWrapper(strategies, broker)
wrapper.run_all()
```

### Advantages
- ✅ Minimal code changes to framework
- ✅ Each strategy fully independent
- ✅ Backward compatible with existing strategies
- ✅ Easy to understand and debug

### Disadvantages
- ❌ No data sharing optimization (redundant API calls)
- ❌ No centralized rate limiting across strategies
- ❌ Higher memory footprint (N executors)
- ❌ Limited coordination between strategies

### When to Use
- Quick proof-of-concept
- Small number of strategies (<5)
- When strategy isolation is paramount
- When development time is limited

### Estimated Performance

**30 Strategies:**
- Memory: ~100 MB (30 executors + overhead)
- API calls: ~1800/min (30 strategies × 60 calls)
- CPU: Moderate (30 independent event loops)

**Verdict:** ⚠️ Not recommended for 30+ strategies due to inefficiency

---

## Design 2: Enhanced Multi-Strategy Executor (Recommended)

### Architecture Overview

**Philosophy:** Single executor manages multiple strategies with shared infrastructure.

```
┌──────────────────────────────────────────────────────────────┐
│              MultiStrategyExecutor                           │
│  ┌────────────────────────────────────────────────────────┐  │
│  │         Shared Resource Layer                          │  │
│  │  - SharedDataManager (caches historical prices)       │  │
│  │  - TradingCalendar (session/platform timing)          │  │
│  │  - GlobalRateLimiter (2-sec order delays)             │  │
│  │  - StrategyAttribution (performance tracking)         │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │         Strategy Execution Loop                        │  │
│  │  1. Fetch data once for all symbols                   │  │
│  │  2. Distribute data to all strategies                 │  │
│  │  3. Collect signals from all strategies               │  │
│  │  4. Execute orders sequentially (rate limited)        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────┬──────┬──────┬─────────┬──────┬──────────────────┐  │
│  │Strat1│Strat2│Strat3│ ...    │Strat9│Strat10           │  │
│  │Track1│Track2│Track3│        │Track9│Track10           │  │
│  └──────┴──────┴──────┴─────────┴──────┴──────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                              ↓
                    ┌──────────────────────┐
                    │  Broker & Data Source│
                    └──────────────────────┘
```

### Implementation

```python
from typing import List, Dict
from datetime import datetime
import time
import pandas as pd

class SharedDataManager:
    """Caches market data for reuse across all strategies"""

    def __init__(self, data_source):
        self.data_source = data_source
        self.cache = {}
        self.last_fetch = {}

    def fetch_for_all_strategies(self, symbols: List[str], length: int, timestep: str):
        """Fetch data once for all unique symbols"""
        for symbol in set(symbols):  # Deduplicate
            cache_key = f"{symbol}_{length}_{timestep}"

            # Check cache freshness (60-second TTL)
            if cache_key in self.cache:
                age = (datetime.now() - self.last_fetch[cache_key]).seconds
                if age < 60:
                    continue  # Cache still fresh

            # Fetch from API
            data = self.data_source.get_historical_prices(
                Asset(symbol), length, timestep
            )

            self.cache[cache_key] = data
            self.last_fetch[cache_key] = datetime.now()

    def get_cached_data(self, symbol: str, length: int, timestep: str):
        """Retrieve cached data for strategy"""
        cache_key = f"{symbol}_{length}_{timestep}"
        return self.cache.get(cache_key)


class GlobalRateLimiter:
    """Enforces minimum delay between orders across all strategies"""

    def __init__(self, min_delay_seconds: float = 2.0):
        self.min_delay = min_delay_seconds
        self.last_order_time = 0.0

    def wait_if_needed(self):
        """Block until rate limit allows next order"""
        current_time = time.time()
        elapsed = current_time - self.last_order_time

        if elapsed < self.min_delay:
            sleep_time = self.min_delay - elapsed
            time.sleep(sleep_time)

        self.last_order_time = time.time()


class StrategyState:
    """Independent state container for each strategy"""

    def __init__(self, strategy_id, symbol, params):
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.params = params

        # Virtual position tracking
        from lumibot.tools.virtual_position_tracker import VirtualPositionTracker
        self.tracker = VirtualPositionTracker()

        # Pending orders
        self.pending_orders = []

        # Performance tracking
        self.total_pnl = 0.0
        self.trade_count = 0


class MultiStrategyExecutor:
    """
    Enhanced executor that runs multiple strategies efficiently.
    Shares data, calendar, and rate limiting across all strategies.
    """

    def __init__(self, broker, strategy_configs: List[Dict]):
        self.broker = broker
        self.strategy_configs = strategy_configs

        # Shared resources
        self.data_manager = SharedDataManager(broker.data_source)
        self.rate_limiter = GlobalRateLimiter(min_delay_seconds=2.0)

        # Trading calendar (shared across all strategies)
        from lumibot.tools.trading_calendar import TradingCalendar
        self.calendar = TradingCalendar(timezone, platform_config)
        self.calendar.register_sessions(sessions)
        self.calendar.map_symbols(symbol_defaults)

        # Create strategy states
        self.strategies = []
        for config in strategy_configs:
            state = StrategyState(
                strategy_id=config['id'],
                symbol=config['symbol'],
                params=config['params']
            )
            self.strategies.append(state)

    def on_trading_iteration(self):
        """Main execution loop - runs all strategies efficiently"""

        # 1. Fetch data ONCE for all symbols
        symbols = [s.symbol for s in self.strategies]
        self.data_manager.fetch_for_all_strategies(symbols, length=100, timestep="1M")

        # 2. Process each strategy independently
        for strategy_state in self.strategies:
            # Get cached data (no API call)
            market_data = self.data_manager.get_cached_data(
                strategy_state.symbol, 100, "1M"
            )

            # Check calendar/timing
            current_time = datetime.now()
            status = self.calendar.get_status(
                strategy_state.symbol,
                current_time,
                strategy_state.tracker.get_position(strategy_state.symbol).quantity
            )

            # Skip if not allowed to trade
            if not status.platform_open or not status.can_enter_orders:
                continue

            # Generate signal
            signal = self.generate_signal(strategy_state, market_data)

            # Create order if needed
            if signal != 'HOLD':
                order = self.create_order_for_strategy(strategy_state, signal)
                strategy_state.pending_orders.append(order)

        # 3. Execute all orders sequentially with rate limiting
        self.execute_all_pending_orders()

    def generate_signal(self, strategy_state, market_data):
        """Strategy-specific signal generation"""
        # Example: SMA crossover
        fast_sma = market_data['close'].rolling(strategy_state.params['fast_sma']).mean()
        slow_sma = market_data['close'].rolling(strategy_state.params['slow_sma']).mean()

        if fast_sma.iloc[-1] > slow_sma.iloc[-1]:
            return 'BUY'
        elif fast_sma.iloc[-1] < slow_sma.iloc[-1]:
            return 'SELL'
        else:
            return 'HOLD'

    def create_order_for_strategy(self, strategy_state, signal):
        """Create order object for strategy"""
        asset = Asset(strategy_state.symbol, asset_type=Asset.AssetType.CONT_FUTURE)

        if signal == 'BUY':
            return Order(strategy_state.strategy_id, asset, 1, "buy")
        elif signal == 'SELL':
            return Order(strategy_state.strategy_id, asset, 1, "sell")

    def execute_all_pending_orders(self):
        """Execute orders from all strategies with rate limiting"""
        for strategy_state in self.strategies:
            for order in strategy_state.pending_orders:
                # Wait for rate limit
                self.rate_limiter.wait_if_needed()

                # Submit order
                self.broker.submit_order(order)

                # Update virtual position immediately
                current_price = self.data_manager.get_cached_data(
                    strategy_state.symbol, 1, "1M"
                )['close'].iloc[-1]

                strategy_state.tracker.execute_order(
                    strategy_state.symbol,
                    order.quantity,
                    order.side,
                    current_price
                )

            # Clear pending orders
            strategy_state.pending_orders.clear()
```

### Advantages
- ✅ **Highly efficient:** Single data fetch for all strategies
- ✅ **Global rate limiting:** Prevents API violations
- ✅ **Scalable:** Handles 30+ strategies easily
- ✅ **Independent positions:** Each strategy tracks own P&L
- ✅ **Shared calendar:** Consistent timing across strategies
- ✅ **Moderate complexity:** Reasonable implementation effort

### Disadvantages
- ⚠️ Requires framework modifications
- ⚠️ More complex than simple extension
- ⚠️ Testing requires multi-strategy scenarios

### When to Use
- ✅ **10-50 strategies** in production
- ✅ When API efficiency is critical
- ✅ When strategies trade same/similar symbols
- ✅ When rate limiting must be centralized

### Estimated Performance

**30 Strategies:**
- Memory: ~25 MB (single executor + 30 state objects)
- API calls: ~60/min (1 data fetch + 30 orders)
- CPU: Low-moderate (single event loop)

**Verdict:** ⭐⭐⭐⭐⭐ **RECOMMENDED** for 30+ strategies

---

## Design 3: Advanced Multi-Strategy Framework (Enterprise-Grade)

### Architecture Overview

**Philosophy:** Complete multi-strategy trading platform with advanced features.

```
┌─────────────────────────────────────────────────────────────────┐
│            Multi-Strategy Trading Platform                      │
├─────────────────────────────────────────────────────────────────┤
│  Strategy Management Layer                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  StrategyRegistry: Dynamic add/remove strategies        │   │
│  │  StrategyLifecycle: Start/stop/pause individual strats  │   │
│  │  StrategyHealth: Monitor performance, auto-disable bad  │   │
│  └──────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│  Portfolio Construction Layer                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  SignalAggregator: Combine signals from all strategies  │   │
│  │  PositionSizer: Allocate capital based on performance   │   │
│  │  RiskManager: Enforce portfolio-level limits            │   │
│  └──────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│  Execution Layer                                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  SmartOrderRouter: Optimize order execution             │   │
│  │  OrderBatcher: Combine multiple strategy orders         │   │
│  │  SlippageEstimator: Predict execution costs             │   │
│  └──────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│  Shared Infrastructure (same as Design 2)                       │
│  - SharedDataManager, TradingCalendar, GlobalRateLimiter       │
├─────────────────────────────────────────────────────────────────┤
│  Monitoring & Analytics                                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  PerformanceAttribution: Track P&L per strategy         │   │
│  │  RealTimeDashboard: Live metrics and alerts             │   │
│  │  HistoricalAnalytics: Backtest results database         │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

**1. Strategy Registry**
```python
class StrategyRegistry:
    """Dynamic strategy management"""

    def __init__(self):
        self.strategies = {}
        self.active_strategies = set()
        self.paused_strategies = set()

    def register(self, strategy_id, strategy_config):
        """Add new strategy at runtime"""
        strategy_state = StrategyState(strategy_id, **strategy_config)
        self.strategies[strategy_id] = strategy_state
        self.active_strategies.add(strategy_id)

    def pause(self, strategy_id):
        """Temporarily stop strategy execution"""
        self.active_strategies.remove(strategy_id)
        self.paused_strategies.add(strategy_id)

    def resume(self, strategy_id):
        """Resume paused strategy"""
        self.paused_strategies.remove(strategy_id)
        self.active_strategies.add(strategy_id)

    def get_active_strategies(self):
        """Return only currently active strategies"""
        return [self.strategies[sid] for sid in self.active_strategies]
```

**2. Signal Aggregator**
```python
class SignalAggregator:
    """Combine signals from multiple strategies"""

    def aggregate_signals(self, strategy_signals: Dict[str, str]):
        """
        Aggregate signals across strategies trading same symbol.

        Example:
            strategy_signals = {
                'strat1': 'BUY',
                'strat2': 'BUY',
                'strat3': 'SELL',
                'strat4': 'HOLD'
            }

        Returns:
            {
                'consensus': 'BUY',  # 2 BUY vs 1 SELL
                'strength': 0.67,    # 2/3 agree
                'breakdown': {'BUY': 2, 'SELL': 1, 'HOLD': 1}
            }
        """
        signal_counts = {}
        for signal in strategy_signals.values():
            signal_counts[signal] = signal_counts.get(signal, 0) + 1

        # Consensus is most common signal
        consensus = max(signal_counts, key=signal_counts.get)

        # Strength is percentage agreement
        total = len(strategy_signals)
        strength = signal_counts[consensus] / total if total > 0 else 0

        return {
            'consensus': consensus,
            'strength': strength,
            'breakdown': signal_counts
        }
```

**3. Risk Manager**
```python
class PortfolioRiskManager:
    """Enforce portfolio-level risk limits"""

    def __init__(self, max_total_exposure, max_per_symbol):
        self.max_total_exposure = max_total_exposure
        self.max_per_symbol = max_per_symbol

    def validate_order(self, new_order, current_positions):
        """Check if order would violate risk limits"""

        # Calculate new total exposure
        total_exposure = sum(abs(p.quantity) for p in current_positions.values())
        total_exposure += abs(new_order.quantity)

        if total_exposure > self.max_total_exposure:
            return False, "Exceeds max total exposure"

        # Calculate new symbol exposure
        symbol_exposure = current_positions.get(new_order.symbol, 0)
        symbol_exposure += new_order.quantity

        if abs(symbol_exposure) > self.max_per_symbol:
            return False, f"Exceeds max exposure for {new_order.symbol}"

        return True, "Order approved"
```

**4. Performance Attribution**
```python
class PerformanceAttribution:
    """Track detailed performance metrics per strategy"""

    def __init__(self):
        self.strategy_metrics = {}

    def record_trade(self, strategy_id, entry_price, exit_price, quantity, timestamp):
        """Record completed trade"""
        if strategy_id not in self.strategy_metrics:
            self.strategy_metrics[strategy_id] = {
                'trades': [],
                'total_pnl': 0.0,
                'win_count': 0,
                'loss_count': 0
            }

        pnl = (exit_price - entry_price) * quantity
        self.strategy_metrics[strategy_id]['trades'].append({
            'pnl': pnl,
            'timestamp': timestamp
        })
        self.strategy_metrics[strategy_id]['total_pnl'] += pnl

        if pnl > 0:
            self.strategy_metrics[strategy_id]['win_count'] += 1
        else:
            self.strategy_metrics[strategy_id]['loss_count'] += 1

    def generate_report(self):
        """Generate performance attribution report"""
        report = pd.DataFrame()

        for strategy_id, metrics in self.strategy_metrics.items():
            trades = pd.DataFrame(metrics['trades'])

            row = {
                'strategy_id': strategy_id,
                'total_pnl': metrics['total_pnl'],
                'trade_count': len(trades),
                'win_rate': metrics['win_count'] / len(trades) if len(trades) > 0 else 0,
                'avg_pnl': trades['pnl'].mean() if len(trades) > 0 else 0,
                'sharpe': trades['pnl'].mean() / trades['pnl'].std() if len(trades) > 0 and trades['pnl'].std() > 0 else 0
            }

            report = pd.concat([report, pd.DataFrame([row])], ignore_index=True)

        return report
```

### Advantages
- ✅ **Enterprise-grade features:** Dynamic strategy management, risk controls
- ✅ **Advanced portfolio construction:** Signal aggregation, position sizing
- ✅ **Comprehensive monitoring:** Real-time attribution, performance tracking
- ✅ **Production-ready:** Suitable for institutional deployment
- ✅ **Extensible:** Easy to add new features and strategies

### Disadvantages
- ❌ **High complexity:** Significant implementation effort
- ❌ **Longer development time:** 3-6 months vs. 1-2 weeks
- ❌ **More testing required:** Complex integration scenarios
- ❌ **Potential over-engineering:** May exceed current requirements

### When to Use
- Large-scale deployment (50+ strategies)
- Institutional/professional trading operations
- When dynamic strategy management is needed
- When portfolio-level risk controls are required
- Multi-asset, multi-market scenarios

### Estimated Performance

**30 Strategies:**
- Memory: ~50 MB (additional monitoring/analytics)
- API calls: ~60/min (same as Design 2)
- CPU: Moderate (additional analytics processing)

**Verdict:** ⭐⭐⭐⭐ Excellent for enterprise, may be overkill for current needs

---

## Feature Comparison Matrix

| Feature | Design 1 (Simple) | Design 2 (Enhanced) | Design 3 (Advanced) |
|---------|-------------------|---------------------|---------------------|
| **Implementation Complexity** | Low | Medium | High |
| **Data Sharing** | ❌ No | ✅ Yes | ✅ Yes |
| **Global Rate Limiting** | ❌ No | ✅ Yes | ✅ Yes |
| **Memory Efficiency** | ❌ Low | ✅ High | ⚠️ Medium |
| **Independent Positions** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Strategy Attribution** | ❌ No | ⚠️ Basic | ✅ Advanced |
| **Dynamic Strategy Management** | ❌ No | ❌ No | ✅ Yes |
| **Portfolio-Level Risk** | ❌ No | ❌ No | ✅ Yes |
| **Signal Aggregation** | ❌ No | ❌ No | ✅ Yes |
| **Real-Time Monitoring** | ⚠️ Basic | ⚠️ Medium | ✅ Advanced |
| **Development Time** | 1 week | 2-3 weeks | 2-3 months |
| **Maintenance Burden** | Low | Medium | High |
| **Suitable for 30 Strategies** | ❌ No | ✅ Yes | ✅ Yes |

---

## Recommended Implementation Path

### Phase 1: Foundation (Week 1-2)
**Implement Design 2 Core Components**

1. `SharedDataManager`: Cache market data
2. `GlobalRateLimiter`: Enforce 2-second delays
3. `StrategyState`: Independent position tracking
4. `MultiStrategyExecutor`: Main coordination loop

**Deliverable:** Working system with 2 strategies

### Phase 2: Enhancement (Week 3-4)
**Add Monitoring and Attribution**

1. `StrategyAttribution`: Track per-strategy P&L
2. Enhanced logging per strategy
3. Backtesting validation framework

**Deliverable:** 10-strategy file with performance metrics

### Phase 3: Scaling (Week 5-6)
**Deploy Full 30-Strategy System**

1. 3 files × 10 strategies each
2. Comprehensive testing
3. Production deployment

**Deliverable:** Complete 30-strategy system

### Phase 4: Optimization (Ongoing)
**Optional Enhancements from Design 3**

1. Dynamic strategy enable/disable
2. Signal aggregation
3. Portfolio risk limits

**Deliverable:** Enterprise-grade features as needed

---

## Technical Specifications

### Data Flow Optimization

**Before (Unoptimized):**
```
Iteration N:
  Strategy 1: fetch ES data (API call 1)
  Strategy 2: fetch ES data (API call 2)  ← Redundant!
  Strategy 3: fetch ES data (API call 3)  ← Redundant!
  ...
  Strategy 30: fetch ES data (API call 30) ← Redundant!

Total: 30 API calls for identical data
```

**After (Optimized with Design 2):**
```
Iteration N:
  SharedDataManager: fetch ES data (API call 1)
  ↓ cached data distributed to all strategies
  Strategy 1: use cached ES data (0 API calls)
  Strategy 2: use cached ES data (0 API calls)
  ...
  Strategy 30: use cached ES data (0 API calls)

Total: 1 API call for all strategies
Savings: 96.7% reduction in data API calls
```

### Rate Limiting Coordination

**Sequential Order Execution:**
```python
def execute_all_orders(strategies, rate_limiter):
    """
    Execute orders from all strategies with global rate limiting.

    Example timing for 30 strategies with 2-second delays:
    - 00:00: Strategy 1 order submitted
    - 00:02: Strategy 2 order submitted
    - 00:04: Strategy 3 order submitted
    - ...
    - 00:58: Strategy 30 order submitted

    Total time: 60 seconds for all 30 orders
    """
    for strategy in strategies:
        if strategy.has_pending_order():
            # Global rate limiter ensures 2-sec minimum between orders
            rate_limiter.wait_if_needed()

            order = strategy.get_pending_order()
            broker.submit_order(order)

            # Update virtual position immediately
            strategy.tracker.execute_order(...)
```

### Memory Optimization

**State Object Size Estimation:**
```python
# Per-strategy memory footprint
strategy_state = {
    'strategy_id': 64 bytes (string),
    'symbol': 32 bytes (string),
    'params': 512 bytes (dict),
    'tracker': 2048 bytes (VirtualPositionTracker),
    'pending_orders': 256 bytes (list),
    'metrics': 512 bytes (dict)
}

# Total per strategy: ~3.5 KB
# 30 strategies: ~105 KB
# Shared data cache: ~2 MB (single DataFrame)
# Total system: ~2.1 MB

# Compare to Design 1:
# 30 executors × 3 MB each = ~90 MB
# Savings: 97.7% reduction in memory
```

---

## Backtest Integration

### Multi-Strategy Backtest Flow

```python
class MultiStrategyBacktester:
    def backtest(self, strategies, start_date, end_date, symbols):
        """
        Backtest multiple strategies with realistic execution.

        Key features:
        - Shared data timestamps (no look-ahead bias)
        - Sequential order execution (realistic slippage)
        - Independent P&L tracking per strategy
        - Portfolio-level metrics
        """

        # Load historical data for all symbols
        data = self.load_data(symbols, start_date, end_date)

        # Initialize all strategy states
        for strategy in strategies:
            strategy.initialize()

        # Iterate bar-by-bar
        for timestamp, bar_data in data.iterrows():
            # All strategies see same timestamp
            for strategy in strategies:
                strategy.on_bar(bar_data)

            # Collect signals
            signals = [s.get_signal() for s in strategies]

            # Execute orders with realistic delays
            self.execute_orders_with_slippage(signals, bar_data)

        # Generate results
        return self.create_multi_strategy_report(strategies)
```

---

## Implementation Recommendation

**For the stated requirement (30 strategies, 3 markets):**

### Choose Design 2 (Enhanced Multi-Strategy Executor)

**Rationale:**
1. ✅ Handles 30 strategies efficiently
2. ✅ Minimizes API calls (critical for rate limits)
3. ✅ Reasonable implementation complexity (2-3 weeks)
4. ✅ Maintains strategy independence completely
5. ✅ Provides necessary performance attribution
6. ✅ Scales beyond 30 strategies if needed

**Implementation Priority:**
1. **Week 1:** Core infrastructure (SharedDataManager, GlobalRateLimiter)
2. **Week 2:** Multi-strategy executor, 2-strategy example
3. **Week 3:** Attribution, logging, 10-strategy file
4. **Week 4:** Testing, optimization, 30-strategy deployment

**Success Metrics:**
- ✅ API calls reduced by >90% vs. unoptimized
- ✅ All 30 strategies execute independently
- ✅ 2-second rate limit enforced globally
- ✅ Backtest results match live trading
- ✅ Per-strategy P&L tracked accurately

---

## Next Steps

1. ✅ Review architecture proposals (this document)
2. → Implement Design 2 core components
3. → Create two-strategy working example
4. → Validate with comprehensive backtests
5. → Scale to 10-strategy file
6. → Deploy full 30-strategy system

---

*End of Multi-Strategy Design Proposals*
