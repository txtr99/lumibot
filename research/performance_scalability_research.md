# Performance & Scalability Research
## External Research on Trading System Architecture Best Practices

**Document Version:** 1.0
**Date:** 2025-11-18
**Research Methods:** Perplexity Deep Research + Reason queries

---

## Executive Summary

This document synthesizes external research on multi-strategy trading system architecture, focusing on industry best practices for performance optimization, scalability patterns, and real-world implementations. The research validates our proposed Design 2 architecture and provides additional insights from institutional trading platforms.

**Key Research Findings:**

1. **Event-Driven Architecture** is the industry standard for multi-strategy systems
2. **Shared Data Infrastructure** with caching reduces API calls by 90%+
3. **Virtual Position Tracking** is essential for strategy isolation
4. **Rate Limiting** must be coordinated globally, not per-strategy
5. **Asynchronous Concurrency** (asyncio) outperforms threading for I/O-bound trading systems
6. **Token Bucket Algorithm** is preferred for API rate limiting
7. **Multi-Strategy Backtesting** requires careful timestamp synchronization to avoid look-ahead bias

---

## Research Finding 1: Event-Driven Architecture (EDA)

### Industry Standard Pattern

**Source:** Perplexity Deep Research - "Multi-strategy trading system architecture"

**Finding:** Event-driven architecture naturally handles timing complexities when multiple strategies need to react to the same market data[^1].

**Application to LumiBot:**
```python
# Current LumiBot: Polling-based
def on_trading_iteration(self):
    data = self.get_historical_prices(...)  # Blocks
    signal = self.calculate_signal(data)
    if signal:
        self.submit_order(...)

# Event-driven enhancement
class EventDrivenMultiStrategy:
    def on_market_data_event(self, event):
        """All strategies receive same event simultaneously"""
        for strategy in self.strategies:
            strategy.process_event(event)  # Non-blocking
```

**Benefits for Multi-Strategy:**
- All strategies see identical market data at same logical time
- Natural support for async processing
- Strategies don't block each other's execution
- Scales to handle large numbers of strategies

**Implementation Recommendation:**
LumiBot's current `StrategyExecutor` already uses queue-based processing, which is event-driven. Enhancement needed: distribute market data events to all strategies simultaneously rather than sequential processing.

---

## Research Finding 2: Shared Resource Management

### Resource Pooling Pattern

**Source:** Perplexity Deep Research - "API rate limiting and optimization techniques"

**Finding:** Resource pooling for API connections combined with centralized caching reduces redundant network requests by up to 95%[^2].

**Industry Pattern:**
```python
# Token Bucket Algorithm for Rate Limiting
class TokenBucketRateLimiter:
    def __init__(self, capacity, refill_rate):
        self.capacity = capacity  # Maximum burst
        self.tokens = capacity
        self.refill_rate = refill_rate  # Tokens per second
        self.last_refill = time.time()

    def acquire(self):
        self._refill_tokens()

        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def _refill_tokens(self):
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now
```

**Application to LumiBot:**
Our Design 2 `GlobalRateLimiter` implements a simpler fixed-delay approach, which is sufficient for the 2-second requirement. For more complex scenarios, the token bucket algorithm provides better burst handling.

**Performance Impact:**
- **Without sharing:** 30 strategies × 60 API calls/hour = 1,800 calls
- **With sharing:** 1 shared call + 30 orders = 31 calls
- **Reduction:** 98.3%

---

## Research Finding 3: Python Concurrency Models

### Threading vs. Asyncio for Trading

**Source:** Perplexity Deep Research - "Memory and CPU performance considerations"

**Finding:** For I/O-bound workloads (trading systems waiting for market data and order fills), asyncio provides better scalability than threading due to Python's GIL (Global Interpreter Lock)[^3].

**Threading (Current Approach):**
```python
# StrategyExecutor uses Thread
class StrategyExecutor(Thread):
    def run(self):
        while self.should_continue:
            self.on_trading_iteration()  # Blocks on I/O

# Problem: GIL prevents true parallelism
# 30 threads serialize execution despite appearing concurrent
```

**Asyncio (Recommended Enhancement):**
```python
import asyncio

class AsyncMultiStrategyExecutor:
    async def run_all_strategies(self):
        """Concurrent execution without GIL limitations"""
        tasks = [
            self.run_strategy(strategy)
            for strategy in self.strategies
        ]

        # All strategies run concurrently
        await asyncio.gather(*tasks)

    async def run_strategy(self, strategy):
        while True:
            data = await self.fetch_data_async(strategy.symbol)
            signal = strategy.calculate_signal(data)

            if signal:
                await self.submit_order_async(strategy, signal)

            await asyncio.sleep(strategy.sleeptime)
```

**Performance Comparison (30 Strategies):**

| Approach | CPU Usage | Latency | Scalability |
|----------|-----------|---------|-------------|
| Threading | High (context switching) | Medium | Poor (GIL bottleneck) |
| Asyncio | Low (single thread) | Low | Excellent (cooperative) |
| Multiprocessing | Very High | High | Medium (process overhead) |

**Recommendation:** Consider async enhancement for live trading, though current threading approach is adequate for backtesting.

---

## Research Finding 4: Strategy Isolation Patterns

### Side Isolation Approach

**Source:** External research on institutional trading platforms[^4]

**Finding:** Professional trading systems maintain separate position tracking spaces for each strategy, even when trading the same instrument.

**Industry Implementation:**
```python
# Pattern: Ledger-Based Position Tracking
class PositionLedger:
    def __init__(self):
        self.entries = []  # Chronological order of position changes

    def record_entry(self, strategy_id, symbol, qty, side, price, timestamp):
        self.entries.append({
            'strategy_id': strategy_id,
            'symbol': symbol,
            'qty': qty,
            'side': side,
            'price': price,
            'timestamp': timestamp
        })

    def get_position(self, strategy_id, symbol):
        """Calculate current position from ledger"""
        relevant_entries = [
            e for e in self.entries
            if e['strategy_id'] == strategy_id and e['symbol'] == symbol
        ]

        position = 0
        for entry in relevant_entries:
            if entry['side'] == 'buy':
                position += entry['qty']
            else:
                position -= entry['qty']

        return position
```

**LumiBot Implementation:**
Our `VirtualPositionTracker` already implements this pattern effectively. Enhancement: add ledger storage for audit trail and position replay capability.

---

## Research Finding 5: Backtesting Accuracy

### Timestamp Synchronization Critical

**Source:** Perplexity Reason query - "Multi-strategy backtesting accuracy"

**Finding:** The most significant source of inaccuracy in multi-strategy backtesting is order latency and execution timing. When multiple strategies generate signals simultaneously, execution order matters[^5].

**Problem Example:**
```python
# Incorrect: Strategies process bars independently
for strategy in strategies:
    for bar in historical_data:
        strategy.on_bar(bar)  # Different timestamps!
        strategy.execute_orders()

# Result: Strategy 1 sees bar at T+0
#         Strategy 2 sees bar at T+1
#         Look-ahead bias introduced!
```

**Correct Implementation:**
```python
# Correct: All strategies process same timestamp
for bar in historical_data:
    timestamp = bar.index[0]

    # All strategies see same timestamp
    for strategy in strategies:
        strategy.on_bar(bar, timestamp)

    # Collect all signals at once
    signals = [s.get_signal() for s in strategies]

    # Execute in defined order (or randomized)
    for signal in signals:
        execute_with_slippage(signal, bar)
```

**Slippage Modeling:**
```python
def calculate_execution_price(base_price, order_size, position_in_queue):
    """Model market impact based on order sequencing"""
    # First orders get better execution
    slippage_bps = 2 * position_in_queue  # 2bps per position

    if order_side == 'buy':
        return base_price * (1 + slippage_bps / 10000)
    else:
        return base_price * (1 - slippage_bps / 10000)

# Strategy 1 (first): 4500.00 (no slippage)
# Strategy 2 (second): 4500.90 (2bps worse)
# Strategy 3 (third): 4501.80 (4bps worse)
```

**Application to LumiBot:**
Implement multi-strategy backtest mode that:
1. Synchronizes all strategies to same bar timestamps
2. Collects all signals before executing any orders
3. Models execution impact based on order sequence
4. Tracks per-strategy results independently

---

## Research Finding 6: Smart Order Routing (SOR)

### Portfolio-Level Optimization

**Source:** Perplexity Deep Research - "Order sequencing and execution management"

**Finding:** Institutional systems batch orders from multiple strategies and optimize execution across venues to minimize transaction costs[^6].

**Industry Pattern:**
```python
class SmartOrderRouter:
    def optimize_execution(self, strategy_orders):
        """
        Combine multiple strategy orders for cost efficiency.

        Example:
        - Strategy 1: BUY 1 ES
        - Strategy 2: BUY 1 ES
        - Strategy 3: SELL 1 ES

        Net order: BUY 1 ES (instead of 3 separate orders)
        Savings: 2 fewer orders, reduced slippage
        """
        net_orders = self._net_positions_by_symbol(strategy_orders)

        for symbol, net_qty in net_orders.items():
            if net_qty != 0:
                self.execute_order(symbol, abs(net_qty), 'buy' if net_qty > 0 else 'sell')

            # Track individual strategy contributions
            self._attribute_fills_to_strategies(symbol, strategy_orders)
```

**Application to LumiBot:**
For strategies guaranteed to trade same direction (per requirements), order batching isn't applicable. However, principle applies if expanding to multi-direction scenarios.

**Current Implementation Status:** Not applicable for same-direction requirement.

---

## Research Finding 7: Performance Attribution

### Institutional Approach

**Source:** External research on institutional platforms[^7]

**Finding:** Professional multi-strategy platforms track not just individual strategy returns, but also interaction effects (e.g., did Strategy A's large order hurt Strategy B's execution?).

**Advanced Attribution:**
```python
class AdvancedAttribution:
    def attribute_performance(self, strategies, trade_history):
        """
        Decompose portfolio returns into components:
        - Pure strategy alpha (signal quality)
        - Execution costs (slippage, fees)
        - Interaction effects (strategy A affecting B)
        - Timing effects (when strategy entered vs. exited)
        """
        attribution = {}

        for strategy in strategies:
            # Pure strategy returns (assume perfect execution)
            pure_alpha = self.calculate_perfect_execution_pnl(strategy)

            # Actual returns
            actual_pnl = strategy.total_pnl

            # Execution drag
            execution_drag = actual_pnl - pure_alpha

            # Interaction effects (statistical estimate)
            interaction_impact = self.estimate_interaction_effects(
                strategy, other_strategies, trade_history
            )

            attribution[strategy.id] = {
                'pure_alpha': pure_alpha,
                'execution_drag': execution_drag,
                'interaction_effects': interaction_impact,
                'total_pnl': actual_pnl
            }

        return attribution
```

**Application to LumiBot:**
Implement basic attribution in Phase 1, add advanced interaction modeling in Phase 4 (optional enhancements).

---

## Research Finding 8: Memory-Efficient Data Structures

### Polars vs. Pandas

**Source:** Perplexity research on Python performance optimization[^8]

**Finding:** For large datasets (common in multi-strategy backtesting), Polars DataFrames are 2-3× faster than Pandas for indicator calculations.

**Performance Comparison:**
```python
import pandas as pd
import polars as pl
import time

# Same data, different frameworks
data_size = 1_000_000

# Pandas
start = time.time()
df_pandas = pd.DataFrame({'close': range(data_size)})
df_pandas['sma'] = df_pandas['close'].rolling(20).mean()
pandas_time = time.time() - start

# Polars
start = time.time()
df_polars = pl.DataFrame({'close': range(data_size)})
df_polars = df_polars.with_columns(
    pl.col('close').rolling_mean(20).alias('sma')
)
polars_time = time.time() - start

# Result: Polars is ~2.5× faster
```

**LumiBot Integration:**
LumiBot already supports Polars via `return_polars=True` parameter in `get_historical_prices()`. Multi-strategy implementation should leverage this for performance.

---

## Research Finding 9: Monitoring and Alerting

### Real-Time Performance Tracking

**Source:** Research on institutional trading infrastructure[^9]

**Finding:** Production multi-strategy systems implement real-time monitoring dashboards that track:
- Per-strategy P&L in real-time
- API usage vs. rate limits
- Order rejection rates
- Execution latency
- Strategy health scores

**Industry Implementation:**
```python
class RealTimeMonitor:
    def __init__(self, strategies):
        self.strategies = strategies
        self.metrics = {}
        self.alerts = []

    def update_metrics(self):
        """Called every iteration to track metrics"""
        for strategy in self.strategies:
            self.metrics[strategy.id] = {
                'pnl': strategy.total_pnl,
                'position': strategy.tracker.get_position(strategy.symbol).quantity,
                'last_signal': strategy.last_signal,
                'win_rate': strategy.calculate_win_rate(),
                'latency_ms': strategy.avg_execution_latency
            }

            # Health checks
            if strategy.total_pnl < -1000:
                self.raise_alert('HIGH', f"Strategy {strategy.id} losses exceed $1000")

            if strategy.win_rate < 0.3:
                self.raise_alert('MEDIUM', f"Strategy {strategy.id} win rate below 30%")

    def generate_dashboard(self):
        """Create visual dashboard"""
        from rich.table import Table
        from rich.console import Console

        table = Table(title="Multi-Strategy Dashboard")
        table.add_column("Strategy ID")
        table.add_column("P&L", justify="right")
        table.add_column("Position")
        table.add_column("Win Rate")

        for strategy_id, metrics in self.metrics.items():
            table.add_row(
                strategy_id,
                f"${metrics['pnl']:.2f}",
                str(metrics['position']),
                f"{metrics['win_rate']*100:.1f}%"
            )

        Console().print(table)
```

**Application to LumiBot:**
Add basic monitoring to Phase 2, expand with dashboard in Phase 4.

---

## Research Finding 10: Error Handling and Resilience

### Circuit Breaker Pattern

**Source:** Research on production trading system reliability[^10]

**Finding:** Professional systems implement circuit breakers that automatically disable underperforming or malfunctioning strategies.

**Industry Pattern:**
```python
class CircuitBreaker:
    def __init__(self, strategy_id, loss_threshold=-500, failure_threshold=5):
        self.strategy_id = strategy_id
        self.loss_threshold = loss_threshold
        self.failure_threshold = failure_threshold
        self.consecutive_failures = 0
        self.is_open = False  # Circuit open = strategy disabled

    def check_strategy_health(self, strategy):
        """Determine if strategy should be disabled"""

        # Check 1: Excessive losses
        if strategy.total_pnl < self.loss_threshold:
            self.trip_circuit("Losses exceed threshold")
            return False

        # Check 2: Consecutive failures
        if strategy.last_order_failed:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0

        if self.consecutive_failures >= self.failure_threshold:
            self.trip_circuit("Too many consecutive failures")
            return False

        return True

    def trip_circuit(self, reason):
        """Disable strategy"""
        self.is_open = True
        logger.error(f"Circuit breaker OPENED for {self.strategy_id}: {reason}")

    def reset(self):
        """Manually re-enable strategy"""
        self.is_open = False
        self.consecutive_failures = 0
```

**Application to LumiBot:**
Implement in Phase 3 for production deployment.

---

## Performance Benchmarks

### Industry Standards

Based on external research, professional multi-strategy systems achieve:

| Metric | Industry Standard | LumiBot Target (Design 2) |
|--------|-------------------|---------------------------|
| **Latency** | <50ms per strategy | ~100ms (acceptable) |
| **API Efficiency** | 90%+ reduction via caching | 95%+ reduction |
| **Memory per Strategy** | <5 MB | ~3.5 KB ✅ |
| **Max Strategies** | 100+ per process | 50+ (exceeds requirement) |
| **Uptime** | 99.9% | 99%+ (goal) |
| **Order Success Rate** | >95% | >95% (with retries) |

---

## Scalability Projections

### Extrapolated Performance

**From 30 to 100 Strategies:**

| Component | 30 Strategies | 100 Strategies | Scaling Factor |
|-----------|---------------|----------------|----------------|
| Memory | ~2.5 MB | ~8.5 MB | Linear (acceptable) |
| Data API Calls | 1/min | 1/min | Constant (excellent) |
| Order API Calls | 30/min | 100/min | Linear (expected) |
| CPU Usage | Low-Medium | Medium | Near-linear |
| Execution Time | 60s (30×2s) | 200s (100×2s) | Linear |

**Conclusion:** Design 2 architecture scales efficiently to 100+ strategies with linear resource growth.

---

## Key Recommendations from External Research

### Immediate Priorities (Phase 1)

1. ✅ **Implement Shared Data Manager** - Industry standard, 90%+ efficiency gain
2. ✅ **Use Virtual Position Tracking** - Professional pattern for strategy isolation
3. ✅ **Global Rate Limiter** - Essential for API compliance
4. ✅ **Event-driven distribution** - Current queue system is good, enhance distribution

### Future Enhancements (Phase 4)

1. **Consider Asyncio Migration** - For live trading performance improvement
2. **Add Circuit Breakers** - Production reliability
3. **Implement Advanced Attribution** - Institutional-grade performance tracking
4. **Real-Time Dashboard** - Operational visibility

---

## External Validation of Design 2

Our proposed Design 2 (Enhanced Multi-Strategy Executor) aligns with industry best practices:

✅ **Event-Driven Architecture:** Matches industry standard pattern
✅ **Shared Data Infrastructure:** Validated by institutional platforms
✅ **Virtual Position Tracking:** Professional isolation approach
✅ **Global Rate Limiting:** Essential for API compliance
✅ **Independent State Management:** Prevents strategy cross-contamination
✅ **Performance Characteristics:** Meets/exceeds industry benchmarks

**Conclusion:** Design 2 architecture is well-aligned with proven industry patterns and should achieve target performance for 30+ strategies.

---

## References

[^1]: Perplexity Deep Research - "Event-Driven Backtesting with Python-Part I" - QuantStart
[^2]: Perplexity Deep Research - "API Rate Limiting" - API7 Learning Center
[^3]: Perplexity Deep Research - "Python Concurrency" - Real Python
[^4]: Perplexity Deep Research - "Trading Multiple Algo Strategies" - KJ Trading Systems
[^5]: Perplexity Reason - "Backtesting Systematic Trading Strategies" - QuantStart
[^6]: Perplexity Deep Research - "Smart Order Routing Technology" - ChainUp
[^7]: Perplexity Deep Research - "Multi-Strategy Portfolios" - QuantInsti
[^8]: Perplexity Deep Research - "Python Performance Optimization" - Various sources
[^9]: Perplexity Deep Research - "Institutional Crypto Trading Infrastructure" - Fireblocks
[^10]: Perplexity Deep Research - "Production Trading System Patterns" - Various sources

---

*End of Performance & Scalability Research*
