# Bracket Order Orphaning Edge Cases in Position Sync/Repair Logic

## Executive Summary

The `repair_position_desync()` method in `BracketOrderManager` zeros out phantom virtual positions when the exchange shows fewer contracts than the virtual net. When a virtual position is zeroed, its active bracket orders (SL/TP) are cancelled, but multiple race conditions and state inconsistencies can cause:

1. **Orphaned bracket orders** that remain active after position is zeroed
2. **Ghost fills** (fills on cancelled orders re-creating positions)
3. **Bracket manager state divergence** from virtual positions
4. **Double-fill processing** after position repair

---

## Scenario 1: TP/SL Fill Race During Position Zero

### Situation
1. Virtual position: +2 ES @ $5800 (with active SL @ $5750, TP @ $5850)
2. Exchange shows: 0 ES (positions externally closed)
3. `repair_position_desync()` called → zeroes virtual position
4. **CONCURRENT**: TP order fills at $5850 on exchange

### What Goes Wrong

```
Timeline:
T0: check_position_sync() → discovers virtual=+2, exchange=0 (excess=+2)
T1: repair_position_desync() starts zeroing strategy's position
T2: state.tracker.reset() → clears virtual position locally
T3: cancel_brackets_for_strategy(sid) called → sends cancel request for TP
T4: [RACE] TP order fills on exchange (TP already matched before cancel)
T5: cancel request arrives → returns error_code: 5 (order doesn't exist/already filled)
T6: _cancel_order() logs failure but doesn't detect the fill
T7: Virtual position is FLAT, but real account just +$5000 P&L from TP fill
```

### Root Cause
- **No synchronization between position zeroing and bracket cancellation**
- **Cancel request can fail if order already filled** but error is logged, not acted upon
- **Order state not re-checked after cancel attempt**

### Consequences
- Account has a real fill that virtual tracker doesn't know about
- Next check_position_sync() sees exchange position but no virtual correlate
- May trigger another repair cycle, creating data inconsistency loop

---

## Scenario 2: SL Fills While Cancelling TP

### Situation
1. Virtual position: -5 MES @ 4950 (with active SL @ 4980, TP @ 4920)
2. Price spike → both SL and TP are "triggered" simultaneously
3. Exchange attempts to fill both, but only one can match
4. `repair_position_desync()` called while this is happening

### What Goes Wrong

```
Timeline:
T0: Price spikes to 4985 → SL triggered, queued for match
T1: Price touches 4915 → TP triggered, queued for match
T2: repair_position_desync() called (async from monitoring loop)
T3: exchange has partially filled one leg (say TP @ 4915)
T4: virtual position still shows -5 MES (not yet synced)
T5: repair attempts to cancel SL, but SL is also being matched
T6: SL fills @ 4980 just as cancel arrives
T7: Two fills on same position → position becomes +0, but broker sees it differently

Conflicting Fill Order:
- Exchange order execution: SL fills first (4980), then TP (4915) fails
- Repair logic sees: both orders in TERMINAL status
- Bracket manager state: TP_FILLED=True, SL_CANCELLED (attempted)
- Actual position: -5 MES → 0 MES from SL fill only
```

### Root Cause
- **No coordination between bracket manager and exchange matching engine**
- **Two orders can reach terminal state in unpredictable sequence**
- **Virtual position state captured at T2 but exchange continues processing at T6**

### Consequences
- Bracket manager thinks TP filled, but actually SL filled
- P&L calculation wrong (uses TP price instead of SL price)
- Next iteration may try to close position based on wrong fill price
- Trade journal shows incorrect exit price

---

## Scenario 3: Bracket Cancellation Fails, Position Zeroed Anyway

### Situation
1. Virtual position: +3 ES @ 5800 (SL @ 5750, TP @ 5850)
2. Position sync detects excess
3. `repair_position_desync()` calls `cancel_brackets_for_strategy(sid)`
4. **Network error or API timeout** → cancel fails with exception

### Code Path (lines 2204-2210)

```python
try:
    cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
    if cancel_result.get("cancelled"):
        result["brackets_cancelled"].extend(cancel_result["cancelled"])
except Exception as ce:
    self.logger.warning(f"[POSITION-REPAIR] Failed to cancel brackets for {sid}: {ce}")
    # ← CRITICAL: No re-raise! Execution CONTINUES!

# Position gets zeroed even though cancel failed:
state.tracker.reset()  # Clear all positions for this strategy
state.entry_price = None
state.take_profit_price = None
state.stop_loss_price = None
```

### What Goes Wrong

```
Timeline:
T0: cancel_brackets_for_strategy() throws ConnectionError
T1: Exception caught, logged (line 2210)
T2: Execution continues (line 2199) → tracker.reset()
T3: Virtual position = FLAT (0 contracts)
T4: Bracket orders REMAIN ACTIVE on exchange
T5: Minutes later, TP fills
T6: broker receives fill notification
T7: Virtual tracker is FLAT → fill has nowhere to go
T8: Next position_sync: exchange shows fill, virtual shows FLAT
T9: Can't match the fill to any strategy
```

### Root Cause
- **Exception handling swallows the error without preventing position reset**
- **No transactional semantics**: bracket cancel and position zero should be atomic
- **Silent failure**: log warning but continue anyway

### Consequences
- Orphaned bracket remains active while virtual position is flat
- Fill on orphaned bracket creates untracked exchange position
- Data consistency breaks (exchange position with no virtual counterpart)
- May cause false "untracked position" alerts in next repair

---

## Scenario 4: Strategy-Level Bracket State vs Manager-Level Bracket State Divergence

### Situation

The `EnhancedStrategyState` has its own bracket tracking:
```python
self.brackets_submitted: bool = False
self.take_profit_price: Optional[float] = None
self.stop_loss_price: Optional[float] = None
self.pending_tp: Optional[float] = None
self.pending_sl: Optional[float] = None
```

The `BracketOrderManager` has separate tracking:
```python
self.brackets: Dict[str, BracketPair] = {}  # Line 95
```

When `repair_position_desync()` zeros a position:

```python
# Lines 2199-2202: Only clears strategy state, NOT bracket manager state
state.tracker.reset()
state.entry_price = None
state.take_profit_price = None
state.stop_loss_price = None
```

### What Goes Wrong

```
Bracket Manager State:
  brackets["ES_LONG_001"] = BracketPair(
    sl_order_id=12345,
    tp_order_id=12346,
    active=True
  )

Strategy State After Zero:
  entry_price = None
  take_profit_price = None
  stop_loss_price = None
  brackets_submitted = False  ← Still FALSE if never reset

Next Poll Cycle (poll_and_cleanup):
  - BracketOrderManager still polls status of order 12345, 12346
  - If one fills: _process_fills() tries to update state
  - But state.tracker is FLAT → position update fails silently
  - Bracket pair remains in brackets dict as "active"
  - No mechanism to clean it up (only _process_fills clears pairs)
```

### Root Cause
- **Two separate state machines: BracketOrderManager and EnhancedStrategyState**
- **repair_position_desync() only touches strategy state, not manager state**
- **No cleanup of bracket manager's brackets dict after position zero**

### Consequences
- Bracket manager continues polling orders that are orphaned
- Can cause repeated processing attempts
- Stale entries in `self.brackets` dict grow unbounded
- Memory leak over long-running sessions

---

## Scenario 5: Cancel Request Sent, Response Lost, Position Zeroed, Order Still Active

### Situation

In `_cancel_order()` (line 337+):
```python
response = self.client.api.order_cancel(self.account_id, order_id)
```

Network conditions:
1. Cancel request sent to API
2. API successfully cancels the order
3. **Response packet lost on return**
4. Client times out waiting for response
5. Exception raised in _cancel_order()

### What Goes Wrong

```
Timeline:
T0: Position sync detects excess, calls repair_position_desync()
T1: cancel_brackets_for_strategy() → _cancel_order(12345)
T2: API receives cancel request
T3: API successfully cancels order 12345 on exchange
T4: API sends response "success": True
T5: [NETWORK LOSS] Response packet lost
T6: _cancel_order() times out waiting for response
T7: Raises RequestException
T8: caught at line 2209 → logs warning
T9: Position gets zeroed anyway
T10: But order 12345 is ACTUALLY CANCELLED on exchange (order state = CANCELLED)
T11: Next poll sees order status = CANCELLED, tries to mark bracket as cancelled
T12: But position is already FLAT, so no position update happens
T13: Bracket pair never gets cleaned from self.brackets dict
```

### Root Cause
- **Network ambiguity: can't distinguish "cancel succeeded but response lost" from "cancel failed"**
- **One-way error handling: assume failure if no response, but might have succeeded**
- **Position zeroed before confirming cancel actually happened**

### Consequences
- Bracket order status is correct on exchange (cancelled)
- But local state thinks cancel failed
- May retry cancel on next poll with wrong order ID
- Orphaned bracket pair stays in manager indefinitely

---

## Scenario 6: Partial Position Zero with Mixed Bracket States

### Situation

Multiple strategies on same symbol, only some have brackets:

```
Strategy 1: +1.5 ES @ 5800, SL @ 5750, TP @ 5850 (brackets active)
Strategy 2: +0.5 ES @ 5805, NO brackets (market order entry only)
Strategy 3: +1.0 ES @ 5799, SL @ 5748, TP @ 5851 (brackets active)

Virtual net: +3.0 ES
Exchange: 0 ES (all closed externally)
Excess to zero: 3.0
```

Repair algorithm (line 2182-2193):
1. Sorts strategies by abs(quantity): [Strat2(0.5), Strat3(1.0), Strat1(1.5)]
2. Zeros Strat2 completely (no brackets) ✓
3. Zeros Strat3 completely (cancel brackets, reset position) ✓
4. Zeros Strat1 completely (cancel brackets, reset position) ✓

But what if Strat3's bracket cancellation fails?

```python
# Line 2206-2210
try:
    cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
    if cancel_result.get("cancelled"):
        result["brackets_cancelled"].extend(cancel_result["cancelled"])
except Exception as ce:
    self.logger.warning(f"[POSITION-REPAIR] Failed to cancel brackets for {sid}: {ce}")
    # Still continues to reset!

state.tracker.reset()  # Strat3 position zeroed even though cancel failed!
```

### What Goes Wrong

```
After repair:
  Strat2: virtual = FLAT, no brackets ✓
  Strat3: virtual = FLAT, brackets NOT cancelled (cancel failed) ✗
  Strat1: virtual = FLAT, brackets cancelled ✓

Bracket Manager State:
  Still tracking Strat3's SL(5748) and TP(5851)

Exchange State:
  Strat3 position = FLAT
  Strat3 SL/TP orders = ACTIVE (cancel failed)

Next Poll:
  BracketOrderManager polls Strat3's SL/TP status
  Finds them still ACTIVE
  Tries to match fill to state.position
  But state.position is FLAT
  Position update fails → logs "cannot find position for fill"
  Bracket pair stays in memory
```

### Root Cause
- **Assumption that cancel always succeeds (or fails without consequence)**
- **Partial repair: some positions zeroed, some brackets orphaned**
- **No compensation logic if cancel fails after partial zero**

### Consequences
- Inconsistent state: position flat but brackets active
- Next repair cycle may try to zero non-existent positions
- Bracket polling becomes noise (finding fills with nowhere to apply them)

---

## Scenario 7: TP Order Fills at Exact Moment Position is Reset

### Situation

Precise timing collision in the virtual position tracker:

```python
# Line 2199: state.tracker.reset()

# Inside reset() in virtual_position_tracker.py (line 265):
def reset(self):
    self.positions.clear()
    self.trade_history.clear()
    self.start_time = datetime.now()
```

Simultaneously:
- `poll_and_cleanup()` running in another thread
- Calls `_process_fills()` which tries to update position
- Calls `self.execute_order()` on tracker

### What Goes Wrong

```
Thread 1 (repair_position_desync):
T0: Acquires lock on state object?  [No lock!]
T1: state.tracker.reset()
T2: self.positions.clear()
T3: self.trade_history.clear()

Thread 2 (poll_and_cleanup):
T0.5: Detects TP fill in order search
T0.75: Calls _process_fills() for strategy
T1.5: state.tracker.execute_order()
T1.75: self.positions[symbol] = new VirtualPosition(...)  [Writes]
T2.5: Tries to access self.trade_history  [Was cleared at T3!]
T2.75: IndexError or StaleData

Result:
- Position object created in cleared positions dict
- Trade history entry created, then dict cleared
- Both operations half-complete
- Tracker in inconsistent state
```

### Root Cause
- **No synchronization primitives (locks) in VirtualPositionTracker**
- **No atomic transaction boundaries**
- **State mutation during concurrent access**

### Consequences
- VirtualPositionTracker state corruption
- Position lost or duplicated
- Next sync sees inconsistent virtual positions
- May cascade into more repairs

---

## Scenario 8: Cancel Bracket, Then Immediately Get Fill Notification

### Situation

ProjectX streaming updates (WebSocket via SignalR):

```python
# From projectx_helpers.py line 208-213:
def _handle_order_update(self, *args):
    data = args[0] if args else None
    if self.on_order_update:
        self.on_order_update(data)
```

Timeline:
1. `repair_position_desync()` calls `cancel_brackets_for_strategy()`
2. API returns: cancel successful, order status = CANCELLED
3. But streaming handler also gets update event just before

### What Goes Wrong

```
Main Thread (repair_position_desync):
T0: cancel_brackets_for_strategy(strategy_id)
T1: API returns {"success": True}
T2: state.tracker.reset() → position FLAT
T3: return from repair

Streaming Thread (ProjectXStreaming):
T0.5: on_order_update() fires with fill event for TP order
T0.7: Fill event includes: order_id=12346, status=FILLED, price=5850
T1.5: Handler processes fill
T2.5: Tries to credit position for the fill
T3.5: state.tracker.position is FLAT
T4: Position update fails / ignored
T4.5: Fill recorded but position not updated

Result:
- Streaming handler saw TP fill but position is flat
- No mechanism to reconcile the fill
- Trade recorded as "orphaned fill"
```

### Root Cause
- **Streaming updates and REST polling operate independently**
- **No coordination between cancel request and streaming notifications**
- **Fill can arrive after position is zeroed**

### Consequences
- Streaming fill not applied to virtual position
- Manual reconciliation needed
- P&L tracking becomes unreliable

---

## Scenario 9: Bracket Pair Cleanup Never Happens After Position Zero

### Situation

In `BracketOrderManager.brackets`:

```python
self.brackets: Dict[str, BracketPair] = {}  # Line 95
```

When position is zeroed:
- `state.tracker.reset()` clears virtual position
- `cancel_brackets_for_strategy()` sends cancel requests
- But **nothing removes the BracketPair from self.brackets dict**

Only `_process_fills()` removes pairs (lines 274-283):
```python
# Only removed if fill is processed
if parent_pair:
    del self.brackets[parent_pair.base_tag]
```

### What Goes Wrong

```
Scenario 1: Cancel succeeds but fill arrives via streaming before cleanup poll
  - Bracket pair in dict
  - Cancel confirms success
  - Streaming fill arrives
  - _process_fills() runs, removes from dict ✓

Scenario 2: Cancel fails, position zeroed, bracket pair orphaned
  - Bracket pair in dict
  - Cancel fails (exception)
  - Position zeroed anyway
  - No fill arrives (order cancelled on exchange despite our failure)
  - Bracket pair STAYS in dict forever ✗
  - Memory leak: brackets dict grows unbounded
  - Polling wastes CPU checking orphaned orders

Scenario 3: Cancel request succeeds but response lost
  - Same as Scenario 2
  - Order actually cancelled on exchange
  - Our code thinks cancel failed
  - Bracket pair never cleaned up
```

### Root Cause
- **Single cleanup path: _process_fills()**
- **No explicit cleanup for orphaned/failed cancellations**
- **No timeout-based cleanup**
- **No GC for stale bracket pairs**

### Consequences
- Memory leak over trading session
- Polling CPU wasted on dead orders
- brackets dict becomes source of truth conflicts

---

## Scenario 10: Fill Processing Race with Multiple Strategies

### Situation

Multiple strategies have the same symbol (e.g., ES). One strategy's repair triggers position zero while another strategy's bracket fill is being processed.

```
Strategy A: +2 ES (with brackets) → position being zeroed
Strategy B: -1 ES (with brackets) → TP just filled
Shared symbol: ES
Virtual net: +1 ES
Exchange: -1 ES (only B's position)
```

Timeline:
```
T0: check_position_sync() finds: virtual=+1, exchange=-1, excess=+2
T1: repair_position_desync() starts
T2: Finds strategies_with_pos = [Strategy A (+2)]
T3: Begins zeroing Strategy A
T4: [CONCURRENT] poll_cycle() detects Strategy B's TP fill
T5: _process_fills() called for Strategy B
T6: execute_order(symbol="ES", side="sell", qty=1)  [TP fill]
T7: Strategy A's reset() in progress
T8: One thread clears positions["ES"], other thread writes to it
T9: Data corruption in VirtualPositionTracker
```

### Root Cause
- **No per-symbol or per-position locking**
- **Concurrent modification of shared tracker**
- **Repair and polling loop not coordinated**

### Consequences
- Position data corruption
- One strategy's repair impacts another's tracking
- Cascading failures

---

## Summary Table of Risks

| Scenario | Trigger | Failure Mode | Severity | Detection |
|----------|---------|--------------|----------|-----------|
| 1 | TP/SL fill during zero | Orphaned order, real fill not tracked | **CRITICAL** | Next sync detects position |
| 2 | SL fills while cancelling TP | Both orders fill, wrong P&L | **CRITICAL** | Trade journal mismatch |
| 3 | Cancel fails, position zeroed | Orphaned bracket, untracked position | **HIGH** | Next sync, or bracket fires |
| 4 | Strategy state vs manager state | Stale bracket pairs in memory | **MEDIUM** | Memory monitoring, polling noise |
| 5 | Cancel response lost | Bracket actually cancelled, code thinks failed | **MEDIUM** | Order status mismatch on next poll |
| 6 | Partial zero with mixed brackets | Some brackets orphaned | **HIGH** | Incomplete repair, orphaned fills |
| 7 | Concurrent reset during fill | Tracker state corruption | **CRITICAL** | Arithmetic errors, position loss |
| 8 | Streaming fill after cancel | Fill not applied to zeroed position | **MEDIUM** | Streaming fill unmatched |
| 9 | Bracket pair never cleaned | Memory leak, polling waste | **LOW** | Long-running session bloat |
| 10 | Multi-strategy concurrent access | Data corruption in tracker | **CRITICAL** | Cascading failures |

---

## Recommended Mitigations

### Immediate (High Priority)

1. **Add transactional semantics** to position zeroing:
   - Verify all brackets cancelled before proceeding with zero
   - On cancel failure, back off and retry or escalate to manual intervention
   - Don't swallow cancel exceptions

2. **Add cleanup for orphaned brackets**:
   - Timeout mechanism: remove bracket pairs older than 5 minutes if never processed
   - Explicit cleanup in repair_position_desync() after all zeros
   - Track bracket pair creation time

3. **Re-check order status after cancel attempt**:
   - _cancel_order() should poll order status after cancel to confirm terminal state
   - If cancel fails but order is terminal anyway, treat as success

### Medium Priority

4. **Synchronize repair_position_desync() with poll_cycle()**:
   - Don't run both simultaneously
   - Add mutual exclusion or queue repairs

5. **Track bracket state in strategy state too**:
   - When zeroing strategy position, also mark brackets as "cancelled_by_repair"
   - Prevents reuse of stale bracket metadata

6. **Add bracket pair verification before zeroing**:
   - Check if bracket pair exists in manager before proceeding
   - Ensure no orphaned pairs remain

### Long-term (Design)

7. **Consider "position guardian" pattern**:
   - Single source of truth for position + brackets (atomic unit)
   - Prevents state divergence between VirtualPositionTracker and BracketOrderManager

8. **Add event log for position changes**:
   - Audit trail of who zeroed position and why
   - Enables post-mortem analysis of discrepancies

9. **Implement bracket lifecycle management**:
   - Clear states: ACTIVE → PENDING_CANCEL → CANCELLED_CONFIRMED → DELETED
   - Or: ACTIVE → FILL_PROCESSED → DELETED
   - Current code mixes these concepts

---

## Testing Strategy

Create tests for:

1. **Synchronization races**:
   - Fill arriving while position reset in progress
   - Cancel response arriving after position zeroed
   - Streaming update interleaving with REST polling

2. **Failure modes**:
   - Network timeouts during cancel
   - Partial bracket cancellation (SL cancel fails, TP succeeds)
   - API returning errors for already-filled orders

3. **State consistency**:
   - Verify bracket manager state matches strategy state after repair
   - Verify no stale bracket pairs in dict after cleanup
   - Verify all positions match exchange after repair

