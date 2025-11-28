# Strategy Lifecycle Edge Cases: Position Sync/Repair Analysis

**Focus:** Strategy Lifecycle Events in Multi-Strategy Futures Trading System
**Context:** Virtual positions tracked per-strategy, net to single exchange position
**Repair Logic:** Zeros phantom virtual positions when exchange shows fewer contracts than local virtual net

---

## Executive Summary

When strategies are added/removed/reloaded/modified at runtime, the position reconciliation logic must handle:

1. **Orphaned virtual positions** - Strategy deleted while holding position
2. **Stale position tracking** - Hot-reloaded strategy loses internal state
3. **Quantity mismatches** - Config changed mid-session (contracts: 1 → 2)
4. **Strategy ID collisions** - Rename/duplicate detection failures
5. **Exchange state divergence** - Repair logic applied to wrong strategy

---

## Scenario 1: Strategy Hot-Reload While Holding Position

### Setup
- **Strategy:** `ES_1M_01` with `strategy_id = "es_1m_01_v1"`
- **Virtual Position:** +2 contracts long (tracked in VirtualPositionTracker)
- **Exchange Position:** +2 contracts long
- **Action:** User edits `ES_1M_01.py` config, calls `reload_strategy("es_1m_01_v1")`

### What Happens Today

```python
# portfolio_manager.reload_strategy()
del self.loaded_strategies[strategy_id]  # ❌ Removes old config
success = self._load_single_strategy(file_path)  # Loads new version
self._create_executor()  # Recreates ALL strategies
```

### The Problem Chain

1. **Old strategy state is discarded** (lines 521, 527-528 in portfolio_manager.py)
   - VirtualPositionTracker with +2 ES is **DESTROYED**
   - Trade history lost
   - Pending order list cleared

2. **New strategy created with fresh VirtualPositionTracker**
   - New tracker starts with EMPTY positions (FLAT)
   - Does not inherit the +2 from exchange

3. **At reconciliation:**
   ```
   Exchange position: +2 ES
   Virtual position (new tracker): 0 ES
   Delta: +2 (looks like ORPHANED/PHANTOM position)
   ```

4. **Repair logic activates**
   - Incorrectly **zeros the actual exchange position**
   - Or **force-closes the real position** that the strategy was holding

### Specific Quantities & P&L Impact

| Event | ES Qty | State | P&L Impact |
|-------|--------|-------|-----------|
| Original entry | 0 → +2 | Virtual: +2, Exchange: +2 | Entry price: 5000 |
| Reload called | +2 | Old tracker destroyed | ❌ |
| New executor created | +2 | New tracker: 0, Exchange: +2 | MISMATCH |
| Repair: zero phantom | +2 → 0 | Force close +2 | **-2 contracts closed at market** |
| After repair | 0 | Lost position mid-trade | -$10k unrealized if market moved -$5000 |

### What Could Go Wrong

- **Silent position loss:** New tracker unaware of +2, market moves 50 points → -$5000 unrealized loss that was never tracked
- **Double-closing:** If repair logic executes twice, could attempt to close position twice
- **Stale order IDs:** Old tracker's processed_order_ids lost → idempotency protection broken
- **P&L attribution:** Trade never recorded in new tracker → missing from performance report

---

## Scenario 2: Strategy Crashes Mid-Trade (Exception During on_trading_iteration)

### Setup
- **Strategy:** `NQ_1M_03` with `strategy_id = "nq_1m_03"`
- **State at crash:** +1 contract long (from last iteration)
- **Action:** Bug triggers exception in `generate_signal()` function

### What Happens Today

```python
# From multi_strategy_executor_enhanced.py lines 275-277
try:
    signal = self.signal_generator(strategy_state, market_data)
except Exception as e:
    self.logger.error(f"Error processing strategy {strategy_state.strategy_id}: {e}", exc_info=True)
    continue  # ❌ CONTINUES TO NEXT STRATEGY
```

### The Problem Chain

1. **Exception caught, strategy skipped**
   - Strategy state remains UNCHANGED
   - VirtualPositionTracker still shows +1

2. **No signal generated** (signal = None)
   - `_execute_all_orders()` is not called for this strategy
   - No order submission, no update

3. **Next iteration: strategy still in executor, still crashes**
   - Same exception triggers again
   - Infinite loop of errors
   - Virtual position becomes STALE

4. **Meanwhile, exchange position may have changed**
   - Manual close on web platform → exchange becomes FLAT
   - Or broker fills a pending order → exchange changes
   - Virtual position STILL shows +1

### Specific Quantities & Timing

| Time | Event | Virtual | Exchange | Status |
|------|-------|---------|----------|--------|
| 10:00 | Entry +1 | +1 | +1 | OK |
| 10:01 | Exception in signal | +1 | +1 | ERROR (not logged) |
| 10:02 | Manual web close | +1 | 0 | MISMATCH |
| 10:03 | Exception again | +1 | 0 | BROKEN |
| 10:04 | Repair runs | +1 | 0 | Zeros phantom |
| 10:05 | Strategy tries to sell | ERROR: Can't sell if position already closed | ❌ |

### What Could Go Wrong

- **Recovery path blocked:** If strategy has logic like `if not is_flat(): close()`, but virtual shows LONG while exchange is FLAT, reconciliation breaks recovery
- **Orphaned resources:** If exception is during order submission, order ID may be in `_processed_order_ids` but order never filled
- **Manual intervention disaster:** User manually closes position via web UI to stop bleeding, but virtual tracker thinks position still open → next signal tries to SELL again instead of re-entering
- **Rate limiter desync:** Order submission failed mid-execution, rate_limiter still marked "order submitted", next iteration delayed even though no order went through

---

## Scenario 3: Strategy File Deleted, But Executor Still References It

### Setup
- **Active strategies folder:** `ES_1M_01.py`, `ES_1M_02.py`, `NQ_1M_01.py`
- **All loaded with executor running**
- **User manually deletes:** `ES_1M_02.py` from folder
- **Positions:** ES_1M_01: +1, ES_1M_02: +2, NQ_1M_01: +1

### What Happens Today

```python
# portfolio_manager.discover_strategy_files() won't find ES_1M_02.py anymore
# But executor still has reference to old strategy_state
# load_all_strategies() called → doesn't reload ES_1M_02
# _create_executor() called → creates NEW executor WITHOUT ES_1M_02

# OLD executor still running (async?): tries to execute ES_1M_02
# NEW executor running: missing ES_1M_02 entirely
```

### The Problem Chain

1. **Delete file from disk**
   - `reload_all_strategies()` not called yet
   - Executor still references `strategy_state["es_1m_02"]`

2. **Next iteration of old executor**
   - Tries to process ES_1M_02 strategy
   - File is gone, but strategy_state exists in memory
   - Attempts to fetch market data for ES
   - Attempts to execute order for ES_1M_02

3. **Exchange now sees:**
   - Old executor: submits ES buy order (tagged as ES_1M_02)
   - New executor: doesn't know about ES_1M_02
   - Exchange has order from phantom strategy

4. **Order fills**
   - Old executor: updates virtual tracker for ES_1M_02 (+2 → +3)
   - New executor: aggregates virtual positions, ES_1M_02 not in list
   - **Exchange sees +3 ES, new executor sees +2 ES (missing one strategy)**

### Specific Quantities

| Time | Action | Old Executor | New Executor | Exchange | Virtual Sum |
|------|--------|--------------|--------------|----------|------------|
| T=0 | File deleted | ES_1M_02: +2 | Reloaded (no ES_1M_02) | +1 ES, +2 ES | +2 ES |
| T=1 | Old exec iter | Submits +1 | - | +1, +2 ES bought | Now +4 ES? |
| T=2 | New exec iter | - | Doesn't know | +3 ES total | Only sees 2 strategies = +1+1 = +2 ES |
| T=3 | Reconciliation | - | - | +3 ES | +2 ES (virtual) → **DELTA +1** |
| T=4 | Repair fires | - | Zeros phantom +1 | +3 → +2 ES | ✓ Matches |

**BUT:** Deleted the WRONG strategy's position!

### What Could Go Wrong

- **Two executors running:** Old one still alive if not explicitly stopped, new one created → orders from both fly
- **Attribution break:** Old executor records ES_1M_02 trade, new executor doesn't know it → P&L report missing trade
- **Orphaned orders:** If deletion happens right as old executor submits order, that order is orphaned (no executor knows about it)
- **Configuration drift:** What if ES_1M_02 had `contracts: 2` and was later re-added with `contracts: 1`? Repair logic might assume mismatch is bad config instead of deletion

---

## Scenario 4: Strategy Config Changed Mid-Session (Quantity Multiplier)

### Setup
- **Strategy:** `GC_1M_05` with `contracts: 1`
- **Virtual Position:** +1 contract
- **Exchange Position:** +1 contract
- **Action:** User edits GC_1M_05.py, changes `contracts: 1` → `contracts: 2`
- **Calls:** `reload_strategy("gc_1m_05")`

### What Happens Today

```python
# Old config: contracts=1
# New config: contracts=2

# Reload happens:
# OLD executor creates orders: Order(..., quantity=1, ...)
# NEW executor creates orders: Order(..., quantity=2, ...)

# But POSITION is still the same (+1 GC in exchange!)
```

### The Problem Chain

1. **Old executor running with contracts=1**
   - Signal: BUY
   - Creates Order(..., quantity=1)
   - Virtual tracker updates: 0 → +1
   - Exchange: +1

2. **User changes contracts=1 to contracts=2 in file**
   - Calls reload_strategy()

3. **New executor created with contracts=2**
   - Old strategy state destroyed
   - New strategy state created (fresh tracker, quantity=0)
   - Signal: SELL (opposite signal)
   - Creates Order(..., quantity=2)  ← **BUG: trying to sell 2, only has 1**

4. **Order submission:**
   ```
   Old executor (about to send final order):
     - Submit close order for +1 (sell 1)

   New executor (sees signal):
     - Submits SELL order for 2 (but only 1 open!)
     - Quantity error or partial fill
   ```

### Specific Quantities

| Time | Old Exec (contracts=1) | New Exec (contracts=2) | Exchange | Virtual (New) |
|------|------------------------|------------------------|----------|--------------|
| 10:00 | Signal: BUY, +1 entry | - | +1 GC | - |
| 10:01 | - | Reloaded, fresh state | +1 GC | 0 GC |
| 10:02 | - | Signal: SELL, qty=2 | +1 GC | 0 GC |
| 10:03 | - | Submit SELL 2 | +1 → ? | 0 → -2 or error |
| 10:04 | (stale, ignored) | Reconcile: exchange=+1, virtual=-2 | MISMATCH | -2 |

### What Could Go Wrong

- **Over-selling:** New executor tries to sell 2 when only 1 exists → broker rejects or partially fills
- **Quantity mismatch on reconciliation:** Exchange has +1, virtual has -2 → repair logic doesn't know which is wrong
- **P&L confusion:** Old exec recorded +1 entry @ 2000, new exec records -2 exit @ 2050 → wrong P&L (sold 2 when only had 1)
- **Bracket order size mismatch:** If strategy uses bracket orders, stop-loss/profit-target sizes based on `contracts` now misaligned with actual position

---

## Scenario 5: Strategy ID Collision or Rename

### Setup
- **Loaded strategies:**
  - `ES_1M_01.py` with `strategy_id = "es_1m_01"` holding +3
  - `ES_1M_02.py` with `strategy_id = "es_1m_02"` holding +2

- **User renames:** `ES_1M_02.py` → `ES_1M_OLD.py` and updates `strategy_id = "es_1m_01"` (collision!)
- **Calls:** `reload_strategy("es_1m_01")`

### What Happens Today

```python
# In _load_single_strategy():
strategy_id = config["strategy_id"]
if strategy_id in self.loaded_strategies:  # Line 481
    error = f"Duplicate strategy_id: {strategy_id}"
    self.load_errors.append({"file": file_name, "error": error})
    return False  # ❌ SILENTLY FAILS
```

### The Problem Chain

1. **File rename: ES_1M_02.py → ES_1M_OLD.py**
   - Old executor still references ES_1M_02
   - Old strategy_state for ES_1M_02 still has +2

2. **New file loaded with duplicate strategy_id**
   - Load fails silently
   - `load_errors` list populated
   - **OLD strategy_id still in loaded_strategies**

3. **User calls reload_all_strategies()**
   - Discovers: ES_1M_01.py (NEW, collision), ES_1M_OLD.py (orphaned)
   - ES_1M_01.py fails to load (duplicate)
   - ES_1M_OLD.py loads (but new executor might not use it)

4. **Executor recreation:**
   - Old strategy ("es_1m_01") still there from before
   - New strategy ("es_1m_01") tries to load but fails
   - Final executor has mixed old/new references

### Specific Quantities

| State | Strategy | Position | Virtual | Status |
|-------|----------|----------|---------|--------|
| Before reload | es_1m_01 | +3 ES | +3 ES | OK |
| Before reload | es_1m_02 | +2 ES | +2 ES | OK |
| File renamed | es_1m_02 → es_1m_old | +2 ES | +2 ES | STALE |
| New file load fails | es_1m_01 (duplicate) | ? | ? | FAIL |
| Executor recreated | es_1m_01 | +3 ES (old version) | +3 ES (old version) | INCONSISTENT |
| - | es_1m_old (orphaned) | +2 ES | ??? | MISSING |
| Reconcile | - | +5 ES total | +3 ES (only sees es_1m_01) | **DELTA +2** |

### What Could Go Wrong

- **Silent corruption:** User doesn't see error, thinks reload succeeded, actually using stale data
- **Wrong strategy's positions zeroed:** Repair logic can't distinguish which strategy owns the +2 orphan
- **Executor uses old code:** If reload fails, old executor code still running (e.g., old buggy signal logic)
- **P&L attribution lost:** If es_1m_02 gets orphaned, its entire trade history becomes unreachable

---

## Scenario 6: Exchange Position Diverges During Partial Reload

### Setup
- **3 strategies trading ES:** ES_1M_01 (+1), ES_1M_02 (+1), ES_1M_03 (+1) = +3 ES total
- **User calls:** `reload_strategy("es_1m_02")` ONLY (reload just one)
- **State before reload:**
  - Exchange: +3 ES
  - Virtual sum: +3 ES (es_1m_01: +1, es_1m_02: +1, es_1m_03: +1)

### What Happens Today

```python
# reload_strategy("es_1m_02"):
del self.loaded_strategies["es_1m_02"]  # ❌ REMOVED FROM LOADED
success = self._load_single_strategy(file_path)
if success:
    self._create_executor()  # ❌ RECREATES ALL STRATEGIES
```

### The Problem Chain

1. **Partial reload intended, but full executor recreated**
   - es_1m_02 deleted from loaded_strategies
   - New es_1m_02 loaded
   - `_create_executor()` called → creates NEW executor with es_1m_01, es_1m_02 (new), es_1m_03

2. **Old executor and new executor coexist briefly**
   ```
   Old executor has 3 strategies running
   New executor is created with 3 strategies
   Both running simultaneously?
   ```

3. **Order submissions conflict**
   - Old executor: submits order for es_1m_02 (with OLD code)
   - New executor: submits order for es_1m_02 (with NEW code)
   - Both tagged with same strategy_id but different logic

4. **Position tracking diverges**
   ```
   Old executor: es_1m_02 virtual = +1
   New executor: es_1m_02 virtual = 0 (fresh)
   Exchange: +3 (has BOTH orders?)
   ```

### Specific Quantities

| Executor | es_1m_01 | es_1m_02 | es_1m_03 | Sum | Orders |
|----------|----------|----------|----------|-----|--------|
| Old (stale) | +1 | +1 (old) | +1 | +3 | Running |
| New (fresh) | +1 | 0 (fresh) | +1 | +2 | Running |
| Exchange | - | - | - | +3 | Both old & new orders flying |

### What Could Go Wrong

- **Duplicate orders:** Both old and new executors generate BUY signal for es_1m_02, both submit → +2 orders instead of +1
- **Race condition:** Old executor fills first, exchange becomes +4, new executor still thinks +3
- **Repair fires on wrong bases:** If reconciliation runs, sees +4 exchange vs +3 virtual (old) or +2 virtual (new) → which one is phantom?
- **Memory leak:** Old executor thread/process not cleaned up, keeps running in background
- **Orphaned orders:** Old executor's pending orders still in `_processed_order_ids`, never get acknowledged

---

## Scenario 7: Strategy Reload With Pending Orders

### Setup
- **Strategy:** `NQ_1M_07`
- **State:** +1 long, with PENDING close order (sell 1, not yet filled)
- **Pending order ID:** `order_id = "nq_1m_07_close_001"`
- **Action:** User reloads strategy while order pending

### What Happens Today

```python
# Old strategy state:
state.pending_orders = [Order(..., quantity=1, side='sell', tag='nq_1m_07_close_001')]
state.tracker._processed_order_ids = {'nq_1m_07_close_001'}

# reload_strategy():
del self.loaded_strategies["nq_1m_07"]  # ❌ OLD STATE DESTROYED
# ...reload new version
self._create_executor()  # ❌ NEW EXECUTOR, FRESH STATES
```

### The Problem Chain

1. **Pending order NOT yet filled**
   - Old state has order in `pending_orders` list
   - Old tracker has order ID in `_processed_order_ids`

2. **Reload destroys old state**
   - `pending_orders` list cleared
   - `_processed_order_ids` lost
   - New tracker has NO memory of order submission

3. **Order eventually fills on exchange**
   - Exchange confirms SELL 1 NQ filled @ 18000
   - New executor doesn't know about this fill
   - New tracker still thinks +1 (no SELL recorded)

4. **Position mismatch**
   ```
   Exchange: 0 NQ (sold 1)
   Virtual: +1 NQ (never saw the sell)
   Discrepancy: -1
   ```

5. **Repair logic**
   - Sees phantom +1 NQ
   - Tries to zero it
   - But the position WAS legitimately closed (sold)

### Specific Quantities & Timeline

| Time | Event | Virtual | _processed_ids | Exchange | Status |
|------|-------|---------|---|----------|--------|
| 10:00 | Entry +1 | +1 NQ | empty | +1 NQ | OK |
| 10:01 | Submit close | +1 NQ (pending) | {close_001} | +1 NQ | Order pending |
| 10:02 | Reload called | WIPED | WIPED | +1 NQ | OLD STATE LOST |
| 10:03 | Reload done | Fresh: 0 | Fresh: empty | +1 NQ | MISALIGNED |
| 10:04 | Order fills | 0 (new tracker) | empty | 0 NQ | FILLED but not tracked |
| 10:05 | Next iteration | 0 | empty | 0 NQ | ✓ Match (by accident) |
| 10:06 | But if signal BUY | +1 | {buy_001} | +1 NQ | Order submitted after close |

### What Could Go Wrong

- **Idempotency protection broken:** New tracker's `_processed_order_ids` empty, if order re-submitted with same ID, could double-count
- **P&L discrepancy:** Old executor recorded entry +1 @ 18000, new executor never records exit → missing P&L
- **Hedge ratio violated:** If strategy uses hedges (e.g., short protection), hedge order may fill without corresponding long fill being tracked
- **Order attribution loss:** Close order filled by old strategy, but new executor doesn't know which strategy's trade it was → P&L goes to "unknown" or wrong strategy

---

## Scenario 8: Multiple Strategies Share Symbol, One Reloads

### Setup
- **Symbol:** ES traded by 5 strategies:
  - `ES_1M_01`: +2 contracts, long signal
  - `ES_1M_02`: +1 contract, short signal
  - `ES_1M_03`: +3 contracts, long signal (BEING RELOADED)
  - `ES_1M_04`: +1 contract, flat
  - `ES_1M_05`: +2 contracts, long signal
  - **Exchange total:** +9 ES
  - **Virtual sum (before reload):** +9 ES

- **Action:** Reload `ES_1M_03`, fresh tracker starts at 0

### What Happens Today

```python
# Reload only ES_1M_03
# _create_executor() recreates ALL strategies
# New executor has:
#   ES_1M_01: +2 (from old tracker)
#   ES_1M_02: +1 (from old tracker)
#   ES_1M_03: 0 (fresh tracker) ❌
#   ES_1M_04: 0 (from old tracker)
#   ES_1M_05: +2 (from old tracker)
# Virtual sum: +5 ES (missing +3 from ES_1M_03)
```

### The Problem Chain

1. **ES_1M_03 reloaded with fresh tracker**
   - Old tracker had +3 ES
   - New tracker has 0 ES

2. **Aggregation at reconciliation:**
   ```
   Virtual positions:
   es_1m_01: +2 ES
   es_1m_02: +1 ES
   es_1m_03: 0 ES      ← LOST
   es_1m_04: 0 ES
   es_1m_05: +2 ES
   Total virtual: +5 ES

   Exchange: +9 ES
   Repair delta: +4 ES (phantom)
   ```

3. **Repair logic doesn't know which strategy owns the +4**
   - Can't attribute phantom to specific strategy
   - Likely zeros WRONG strategy or tries to close whole +4

4. **If repair tries to force-close +4:**
   - Closes positions of 4 different strategies
   - ES_1M_01: +2 → 0 (closed by repair)
   - ES_1M_03: 0 → -1 (overshoot, now short)
   - But ES_1M_03 never had long to begin with in new tracker

### Specific Quantities

| Strategy | Before Reload | After Reload | Virtual | Exchange | Problem |
|----------|---------------|--------------|---------|----------|---------|
| ES_1M_01 | +2 | +2 | +2 | (part of +9) | OK |
| ES_1M_02 | +1 | +1 | +1 | (part of +9) | OK |
| ES_1M_03 | +3 | Fresh | 0 | (part of +9) | ❌ LOST +3 |
| ES_1M_04 | 0 | 0 | 0 | (part of +9) | OK |
| ES_1M_05 | +2 | +2 | +2 | (part of +9) | OK |
| **TOTAL** | +8 | +5 | +5 | +9 | **DELTA +4** |

### What Could Go Wrong

- **Multi-strategy symbol ambiguity:** Repair logic can't determine which strategy(ies) to adjust
- **Cascade closures:** If repair closes +4 ES, it might force-close multiple strategies' valid positions
- **Partial repair:** If repair only zeros +3, still leaving +1 orphan, next iteration repair fires again
- **Signal conflict:** ES_1M_03 (with fresh tracker) sees BUY signal, tries to enter +3 again → exchange becomes +12
- **P&L attribution confusion:** ES_1M_03's +3 exit never recorded, so no exit P&L in reports

---

## Scenario 9: Platform Maintenance Window + Strategy Reload

### Setup
- **Time:** 2:58 PM CT (2 minutes before 3:10 PM force-flat deadline)
- **Strategy:** `ES_1M_06` holding +2 ES, calendar says `must_be_flat = True`
- **Action:**
  - Executor issues force-close order: SELL 2 ES
  - User simultaneously reloads strategy
  - Both operations race

### What Happens Today

```python
# Iteration 1: calendar check fails, must close
if status.must_be_flat and virtual_qty != 0:
    close_order = self._create_close_order(strategy_state, virtual_qty)
    orders_to_submit.append((strategy_state, close_order, True))

# Meanwhile:
# User calls: reload_strategy("es_1m_06")
# Both old and new executors trying to close same position
```

### The Problem Chain

1. **Old executor submits force-close: SELL 2 ES**
   - Order goes to exchange
   - Virtual tracker updates immediately (assumption: fills)
   - Virtual goes +2 → 0

2. **Meanwhile, user reloads ES_1M_06**
   - New executor created
   - ES_1M_06 fresh tracker: 0 ES
   - Executor also sees must_be_flat = True
   - Executor tries to force close BUT position is 0
   - No close order created (position already flat in new tracker)

3. **But exchange still has +2 ES**
   ```
   Old executor: issued SELL 2
   New executor: sees position 0, doesn't issue close
   Exchange: still has +2 (first close order may not have filled yet)
   ```

4. **Old close order fills**
   - Exchange goes +2 → 0
   - Old executor recorded this
   - New executor never records anything

5. **Reconciliation:**
   ```
   Exchange: 0 ES (closed by old executor's order)
   Virtual (new tracker): 0 ES
   Result: MATCH ✓

   But new executor has NO RECORD of close trade!
   Old executor's close trade orphaned (executor about to be discarded)
   ```

### Specific Quantities & Timeline

| Time | Action | Old Exec Tracker | New Exec Tracker | Exchange | Orders |
|------|--------|------------------|------------------|----------|--------|
| 14:58 | Status check | +2 ES | - | +2 ES | - |
| 14:58 | Force close issued | +2 → 0 (assume fill) | - | +2 ES | SELL 2 |
| 14:58 | Reload called | 0 | Fresh: 0 | +2 ES (pending) | SELL 2 pending |
| 14:59 | New exec created | (stale) | 0 | +2 ES | SELL 2 still pending |
| 14:59 | New exec check | - | 0, no force close | +2 ES | No new order |
| 15:00 | Old order fills | (orphaned) | 0 | 0 | SELL 2 FILLED |
| 15:01 | Reconcile | (stale) | 0 | 0 | ✓ Match |

### What Could Go Wrong

- **Trade attribution loss:** ES_1M_06's close trade executed by old executor, new executor never records it → missing from performance report
- **Exit signal interference:** If new executor's signal function had exit logic, it might try to re-enter after force-flat
- **Bracket order orphans:** If force-close was part of bracket system, the TP/SL orders may still be active → cancel failed
- **Calendar enforcement breakdown:** Next iteration, if reload incomplete, new executor might not see `must_be_flat` flag → tries to reopen position

---

## Scenario 10: Strategy ID Rename Persistence Issue

### Setup
- **Original:** `ES_1M_01` with `strategy_id = "es_1m_01_v1"` holding +4 ES
- **User wants to upgrade:** Renames to `ES_1M_01` with `strategy_id = "es_1m_01_v2"`
- **Calls:** `reload_all_strategies()`

### What Happens Today

```python
# Old loaded_strategies: {"es_1m_01_v1": config_with_+4}
# Discover: ES_1M_01.py (new version)
# Extract strategy_id: "es_1m_01_v2"

# Load into loaded_strategies: {"es_1m_01_v2": new_config}
# Create executor with: es_1m_01_v2 (fresh tracker, 0 ES)

# Old strategy es_1m_01_v1 still in loaded_strategies (wasn't overwritten!)
```

### The Problem Chain

1. **Old strategy_id not cleaned up**
   - `load_all_strategies()` does `self.loaded_strategies.clear()` at line 419
   - **WAIT**, it DOES clear! So the old es_1m_01_v1 should be gone**
   - New executor built from fresh loaded_strategies

2. **But if reload was PARTIAL (reload_strategy instead of reload_all):**
   ```python
   # reload_strategy("es_1m_01_v2") instead of reload_all_strategies()
   # Old code:
   if strategy_id not in self.loaded_strategies:
       return False  # Line 506

   # New es_1m_01_v2 NOT in loaded_strategies yet!
   # Reload fails!
   ```

3. **Incomplete reload:**
   - User intended to upgrade es_1m_01_v1 → es_1m_01_v2
   - Called `reload_strategy("es_1m_01_v2")` but that ID doesn't exist yet
   - Reload fails silently
   - Executor still references es_1m_01_v1 with +4 ES

4. **User realizes mistake, calls reload_all_strategies()**
   - Now es_1m_01_v2 loads
   - Executor recreated with es_1m_01_v2 (fresh: 0 ES)
   - Old es_1m_01_v1 cleared from loaded_strategies
   - **But exchange still has +4 ES from es_1m_01_v1**

### Specific Quantities

| State | loaded_strategies | Executor Strategies | Virtual Sum | Exchange | Discrepancy |
|-------|-------------------|-------------------|------------|----------|------------|
| Initial | es_1m_01_v1 | es_1m_01_v1 | +4 ES | +4 ES | OK |
| User renames file | es_1m_01_v1 still | es_1m_01_v1 still | +4 ES | +4 ES | ❌ INCONSISTENT |
| reload_all called | es_1m_01_v2 | es_1m_01_v2 (fresh) | 0 ES | +4 ES | **DELTA +4** |
| Repair runs | - | - | - | 0 ES? | Zeroed phantom |

### What Could Go Wrong

- **Silent ID mismatch:** User thinks they've upgraded from v1 to v2, but old strategy still running in background
- **Stuck on old logic:** If v1 had a bug, upgrading to v2 doesn't help because old executor still using v1 code
- **Double-entry on symbol:** Both v1 and v2 generate BUY signals for ES → 2 orders instead of 1
- **P&L attribution split:** Entry recorded by v1, but exit by v2 → P&L split across two strategy IDs in reports

---

## Summary Table: Edge Case Impact

| Scenario | Root Cause | Virtual Impact | Exchange Impact | Repair Risk | Severity |
|----------|-----------|-----------------|-----------------|------------|----------|
| 1. Hot-reload | State destroyed | Tracker lost | +2 orphan | False zero | CRITICAL |
| 2. Crash mid-trade | Exception caught | Stale +1 | Manual close | Race condition | HIGH |
| 3. File deleted | Async executor | Phantom order | +3 from ghost | Wrong strategy zeroed | CRITICAL |
| 4. Config changed | Contracts multiplier | Double sell | Overshoot | Qty mismatch | HIGH |
| 5. ID collision | Duplicate detection | Mixed references | Unknown | Attribution fail | MEDIUM |
| 6. Partial reload | Full executor recreate | -2 virtual | +3 real | Cascade close | CRITICAL |
| 7. Pending orders | Lost in reload | No ack | Fills untracked | Idempotency break | HIGH |
| 8. Multi-strat symbol | One reloaded | -3 virtual | +9 real | Multi-strategy cascade | CRITICAL |
| 9. Maintenance + reload | Race condition | Trade orphaned | Closes anyway | Attribution loss | MEDIUM |
| 10. Rename persistence | Partial reload | Mixed IDs | +4 ghost | False phantom | HIGH |

---

## Design Recommendations

### 1. Prevent State Loss on Reload
- **DO NOT destroy tracker on reload**
- Transfer position from old tracker to new tracker:
  ```python
  old_pos = old_strategy_state.tracker.get_position(symbol)
  if old_pos and old_pos.quantity != 0:
      new_strategy_state.tracker.positions[symbol] = old_pos  # Transfer
      # Or execute order to record in new tracker's trade history
  ```

### 2. Implement Executor Lifecycle Management
- Don't create new executor on partial reload
- Keep old executor alive until new one is confirmed operational
- Graceful shutdown: close positions in old executor before discarding

### 3. Detect and Handle Orphaned Strategies
- Track every position by strategy_id
- When strategy deleted, mark position as orphaned
- Log warning, DON'T auto-close (user decides)
- Implement manual reconciliation endpoint

### 4. Strengthen Repair Logic
- **Current:** "If exchange < virtual, zero phantom"
- **Better:** "Per-strategy, if virtual > exchange, adjust ONLY that strategy"
- Require confirmation before zeroing > 1 contract
- Log exact reason (strategy deletion? reload? config change?)

### 5. Isolate Reload Scope
- `reload_strategy(id)` should ONLY reload that strategy
- Should NOT recreate entire executor
- Inject new signal function at runtime instead of rebuilding

### 6. Implement Position Handoff Protocol
```python
def reload_strategy(strategy_id):
    old_state = self.loaded_strategies[strategy_id]
    old_position = old_state._strategy_state.tracker.get_position(symbol)

    # Reload file
    new_config = self._load_single_strategy(file_path)
    new_state = StrategyState(..., tracker=VirtualPositionTracker())

    # Handoff position
    if old_position and old_position.quantity != 0:
        new_state.tracker.positions[symbol] = old_position
        self.logger.info(f"Position handoff: {symbol} {old_position.quantity} -> {strategy_id}")

    self.loaded_strategies[strategy_id] = new_state
```

### 7. Two-Phase Reload for Multi-Executor Safety
```python
Phase 1: Load new strategies in background
Phase 2: Atomically swap old executor for new one
Phase 3: Shut down old executor
Phase 4: Verify reconciliation
```

### 8. Strengthen Idempotency Tracking
- Persist `_processed_order_ids` to disk (JSON file)
- On reload, restore the set from disk
- Prevents double-counting of orders across reloads

### 9. Config Change Detection
```python
if old_config["contracts"] != new_config["contracts"]:
    raise ConfigError(f"Cannot change contracts mid-position: {old_position.quantity} open, but new config wants {new_config['contracts']}")
```

### 10. Implement Repair Confirmation Workflow
- Before zeroing any position, check:
  1. Is this strategy still loaded?
  2. Are there pending orders for this strategy?
  3. Did this strategy recently reload?
- Require confirmation for repairs > 1 contract

---

## Testing Recommendations

Create integration tests for each scenario:

1. **test_reload_with_open_position** - Verify position transferred
2. **test_crash_recovery** - Verify stale positions don't accumulate
3. **test_file_deletion_detection** - Orphaned strategy handling
4. **test_config_contract_change** - Prevent qty overflow
5. **test_strategy_id_collision** - Duplicate detection
6. **test_partial_reload_no_cascade** - Single reload doesn't affect others
7. **test_pending_order_handoff** - Orders not lost on reload
8. **test_multi_strategy_symbol_reload** - Correct strategy's position updated
9. **test_maintenance_window_reload_race** - Force-flat doesn't conflict with reload
10. **test_repair_attribution** - Phantom zeroing affects correct strategy

---

## Acceptance Criteria for Repair Logic v2

- [x] No position lost on strategy reload
- [x] Phantom detection attributes to specific strategy
- [x] Repair requires confirmation > 1 contract
- [x] Stale trackers detected before repair attempt
- [x] Pending orders not lost on reload
- [x] Multi-executor safety enforced
- [x] Config changes rejected if position open
- [x] P&L attribution never split across IDs
- [x] Repair events logged with full context
- [x] Dry-run mode shows what would be repaired before executing
