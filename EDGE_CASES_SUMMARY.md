# Position Sync Edge Cases: Executive Summary

## The Problem

Your multi-strategy futures system tracks positions two ways:

1. **Virtual Locally** (per-strategy): Assumes market orders fill instantly
2. **Exchange Globally** (aggregate): Queries API which has ~10-100ms latency

When virtual > exchange, repair logic zeros phantom positions. But real-world timing creates scenarios where this breaks badly.

---

## 10 Critical Edge Cases (by severity)

### CRITICAL (Fix Immediately)

**#1: In-Flight Order Repaired Before Confirmation**
- Timestamp: 10-100ms after order placement
- Problem: Sync fires before exchange confirms fill, repair zeros valid order
- Impact: Entry order invisible, double-entry attempt
- Example: Buy 1 ES → assumed filled → sync fires (too early) → zero → real fill arrives (ignored)

**#2: Stop Loss Fill During Sync Check**
- Timestamp: Exact moment SL fills, before BOM processes it
- Problem: SL exit legitimate but repair sees "missing quantity" and zeros
- Impact: Exit invisible, position inverted (short instead of flat)
- Example: SL fills sell 1 ES → sync sees exchange=2, virtual=3 → repairs by zeroing S1 → exit processor creates -1 qty

**#3: Rapid Multiple Fills (Both SL and TP)**
- Timestamp: Sub-second (5-50ms between fills)
- Problem: API allows both bracket legs to fill (no true OCO), BOM can't cancel
- Impact: Position inverted, double-exit in accounting
- Example: Price jumps, both SL (sell 1) and TP (sell 1) fill → process both as exits → qty becomes -1

**#4: Concurrent Access Race**
- Timestamp: Millisecond-level threading issue
- Problem: Repair thread zeros position while fill thread is calculating
- Impact: Position math corrupted, wrong-side position created
- Example: Repair sets qty=0, fill processor reads qty=0 then subtracts → qty becomes -1

**#5: Repair Cascades in Wrong Direction**
- Timestamp: 2-10 seconds after first repair
- Problem: Repair makes divergence worse, next sync fires in opposite direction
- Impact: Oscillating repairs, unreliable state
- Example: Repair zeros +1 virtual → fill arrives making virtual=-1 → next sync tries to "fix" upward

### HIGH (Should Fix Soon)

**#6: Bracket Fill During Sync** (10-50ms timing window)
- TP or SL fills while sync checks aggregate positions
- P&L tracking loses exit event
- Multiple strategies enter before TP/SL fills

**#7: Stale API Data** (15-100ms API lag)
- Position API returns outdated snapshot
- Repair fires on data that's seconds old
- Double-entry on next signal

**#8: Position Attribution Loss** (Multiple strategies per symbol)
- Repair can't distinguish which strategy's position is phantom
- Zeros innocent strategies instead of phantom
- All strategies on symbol get repaired, not just the problem one

**#9: Repair Loop Cascading** (2-10 seconds)
- Repair triggers opposite-direction divergence
- System becomes less reliable after repair
- P&L tracking completely lost

**#10: Order Cancellation Race** (50-200ms window)
- Repair severs entry-exit bracket link
- TP cancelled when SL already filled
- Exit creates wrong-side position

---

## Why These Happen

| Cause | Why | Impact |
|-------|-----|--------|
| **10-100ms API latency** | Brokers batch position updates, not real-time | Repair assumes stale == phantom |
| **Assumption of instant fills** | Virtual tracker doesn't wait for confirmation | Sync can fire before orders propagate |
| **No transaction boundaries** | Repair and fill processing run independently | Race conditions, ordering issues |
| **Aggregate-level repair** | Repair loses strategy identity | Can't target which position is phantom |
| **No pending order tracking** | System doesn't distinguish "in-flight" from "phantom" | Premature repair of valid orders |
| **Symbol-level aggregation** | Multiple strategies on ES share data | Ambiguous which position corresponds to which fill |

---

## Quick Diagnosis: Is Your System At Risk?

Check these in your code:

```python
# RISK 1: Repair without pending order tracking?
if virtual_qty > exchange_qty:
    force_flat(symbol)  # ← DANGEROUS, might repair in-flight orders

# RISK 2: Sync triggered immediately after order placement?
place_order()
reconcile_positions()  # ← Should wait 2-3 seconds

# RISK 3: Concurrent access without locks?
class VirtualPositionTracker:
    def force_flat(self):
        self.qty = 0  # ← Race with execute_order()

# RISK 4: Repair at symbol level for multiple strategies?
for symbol in symbols:
    if exchange[symbol] < virtual[symbol]:
        zero_all_strategies_on_symbol(symbol)  # ← WRONG, zeros all

# RISK 5: No idempotency check on repair operations?
force_flat("ES")  # Called twice in edge case scenario
force_flat("ES")  # No protection against double-repair
```

---

## Immediate Fixes (Ranked by Impact)

### Priority 1: Pending Order Window

**Code:** Add 2-second window after order placement where sync is blocked

```python
class OrderFlightWindow:
    def register_pending(self, order_id, symbol, qty, side):
        self.pending_orders[order_id] = (datetime.now(), symbol, qty)

    def get_pending_qty(self, symbol):
        now = datetime.now()
        return sum(qty for t, s, qty in self.pending_orders.values()
                   if s == symbol and (now - t).seconds < 2)

# In sync logic:
pending = flight_window.get_pending_qty("ES")
if pending > 0:
    logger.debug(f"Skipping sync, {pending} orders pending")
    return True  # Assume positions OK until pending confirmed
```

**Impact:** Fixes edge cases #1, #5
**Effort:** 2-3 hours
**Confidence:** Very High

### Priority 2: Atomic Repair with Locking

**Code:** Prevent concurrent writes during repair

```python
class AtomicPositionRepair:
    def __init__(self, tracker):
        self.tracker = tracker
        self._repair_lock = threading.RLock()

    def repair(self, symbol):
        with self._repair_lock:
            self.tracker.force_flat(symbol)  # No race now

    def execute_order_during_repair(self, symbol, qty, side):
        with self._repair_lock:
            if self._in_repair.get(symbol):
                return None  # Block order execution during repair
            return self.tracker.execute_order(symbol, qty, side)
```

**Impact:** Fixes edge cases #4, #10
**Effort:** 1-2 hours
**Confidence:** Very High

### Priority 3: Strategy-Level Repair

**Code:** Target most suspicious strategy instead of all

```python
def repair_most_suspicious(symbol, exchange_qty):
    # Get all strategies' positions on this symbol
    positions = {
        strategy_id: (qty, last_update)
        for strategy_id, qty in get_all_virtual(symbol).items()
    }

    # Find oldest position (most suspicious)
    oldest = min(positions.items(), key=lambda x: x[1][1])[0]

    logger.info(f"Repairing {oldest} (oldest on {symbol})")
    zero_strategy_position(oldest, symbol)
```

**Impact:** Fixes edge cases #7, #8
**Effort:** 1-2 hours
**Confidence:** High

### Priority 4: Conservative Repair Policy

**Code:** Never repair upward, only if divergence > threshold, with cooldown

```python
class ConservativeRepairPolicy:
    def should_repair(self, symbol, exchange, virtual):
        delta = exchange - virtual

        # Never repair upward (exchange has more)
        if delta >= 0:
            return False

        # Only if divergence > 2 contracts
        if abs(delta) < 2.0:
            return False

        # Only if 3+ seconds since last sync
        elapsed = (now() - self.last_sync[symbol]).seconds
        if elapsed < 3:
            return False

        return True
```

**Impact:** Reduces false repairs, prevents cascading
**Effort:** 1 hour
**Confidence:** High

### Priority 5: Sync Cooldown

**Code:** Don't sync within 2 seconds of large order batch

```python
class SyncCooldown:
    def record_orders_placed(self, symbols):
        for symbol in symbols:
            self.last_order[symbol] = now()

    def can_sync(self, symbol):
        elapsed = (now() - self.last_order.get(symbol, 0)).seconds
        return elapsed > 2  # Wait 2 seconds after orders

# In sync routine:
if not cooldown.can_sync(symbol):
    logger.debug(f"Sync cooldown active for {symbol}")
    return True  # Assume OK until cooldown expires
```

**Impact:** Fixes edge cases #1, #2, #6
**Effort:** 30 minutes
**Confidence:** Very High

---

## Implementation Roadmap

### Week 1: Foundation
- [ ] Add OrderFlightWindow (2h)
- [ ] Add SyncCooldown (0.5h)
- [ ] Add AtomicPositionRepair with locks (2h)
- Total: 4.5 hours

### Week 2: Intelligence
- [ ] Add StrategyAwareRepair (2h)
- [ ] Implement ConservativeRepairPolicy (1h)
- [ ] Add OrderConfirmationManager (2h)
- Total: 5 hours

### Week 3: Testing
- [ ] Write edge case tests (6h)
- [ ] Manual scenario testing (4h)
- [ ] Live trading validation (4h)
- Total: 14 hours

### Week 4: Monitoring
- [ ] Add repair event logging (1h)
- [ ] Build dashboard to track repairs (2h)
- [ ] Set up alerts for divergence (1h)
- Total: 4 hours

**Grand Total: 27.5 hours (roughly 1 week)**

---

## What to Monitor in Production

```python
# Key metrics to track
repairs_executed = 0       # Should be << 1 per hour
repair_direction = []      # Track if always downward (safe)
avg_repair_delta = 0       # Average divergence when repair happens
flight_window_exits = 0    # How often orders exit flight window intact
cascading_repairs = 0      # Repairs that trigger another repair
```

**Expected behavior after fixes:**
- Repairs: 0-1 per week (not per hour)
- Direction: Always downward (zero phantom positions)
- Delta: 1-2 contracts maximum
- Flight window exits: 95%+ successful
- Cascading: 0

If you see:
- Repairs > 2 per hour → edge case occurring, debug
- Repairs in both directions → cascading behavior, fix policy
- High delta (5+) → larger issue, check for missing fills
- Flight window exits < 90% → orders taking too long to confirm

---

## Validation Checklist

Before deploying fixes, verify:

- [ ] In-flight orders not repaired within 2s
- [ ] SL/TP fills don't cause position inversion
- [ ] Concurrent access doesn't corrupt qty math
- [ ] Only oldest strategy repaired (not all on symbol)
- [ ] Repair never increases virtual position (upward safe)
- [ ] Each repair logged with reason for audit
- [ ] Strategy ownership preserved through repair
- [ ] Idempotency prevents double-processing
- [ ] P&L tracking survives repair operation
- [ ] No cascading repairs (repair→divergence→repair)

---

## Questions for Your Team

Before implementing:

1. **How often do you see repairs firing?** (should be rare)
2. **Are orders consistently confirmed <100ms after placement?**
3. **Do you have access to order fill timestamps from exchange?** (could improve validation)
4. **How critical is per-strategy P&L attribution?** (affects repair strategy choice)
5. **What's your tolerance for blocking syncs during high-frequency entry periods?** (affects cooldown duration)

---

## Files Generated

1. **EDGE_CASES_POSITION_SYNC.md** - Detailed 10 scenarios with root causes
2. **EDGE_CASE_IMPLEMENTATION_GUIDE.md** - Code examples for all 6 fixes
3. **EDGE_CASES_VISUAL_SCENARIOS.md** - Timeline diagrams for 6 critical scenarios
4. **EDGE_CASES_SUMMARY.md** - This document

Start with EDGE_CASES_POSITION_SYNC.md to understand risks, then use EDGE_CASE_IMPLEMENTATION_GUIDE.md to code solutions.

