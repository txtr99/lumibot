# Position Sync/Repair Edge Cases - Futures Multi-Strategy System

## Current System Overview

**Virtual Position Tracking:**
- Each strategy tracks positions independently via `VirtualPositionTracker`
- Assumes all market orders fill immediately
- Orders marked as processed via `_processed_order_ids` (idempotency)
- Order fills detected and processed after order status confirms `FILLED`

**Repair Logic (`repair_position_desync`):**
- Compares exchange position qty with net of all strategy virtual positions
- If exchange < virtual: zeros phantom positions (safest approach)
- If exchange > virtual: warns only (too risky to auto-add)
- Clears processed fills to allow re-sync on next poll

---

## 1. LIMIT ORDERS - NEVER FILLED

### Scenario 1.1: Limit Order Sits Unfilled, Gets Overridden
**Setup:**
- Strategy A submits BUY limit order for 5 ES @ 5500.00
- Order ID 111 → VirtualPositionTracker marked as processed
- Order status = OPEN (not filled)
- 30 seconds later, market drops, strategy now wants to BUY 5 more ES @ 5499.00
- Before old order fills, new order 112 is placed

**What Goes Wrong:**
- Virtual tracker already counts old order (111) as if it filled at 5500
- When limit order 111 FINALLY fills hours later during low volatility:
  - `_process_fills` will try to process fill for 111
  - But if `brackets_submitted` flag is true, won't recreate (line 782)
  - NEW position from order 112 is already tracked
  - Bracket manager might recreate old SL/TP orders from order 111's stale price
  - **RESULT**: Wrong P&L, doubled orders, orphaned brackets

**Root Cause:**
- `execute_order` marks order as processed BEFORE confirmation of fill
- No distinction between "market order assumed filled" vs "limit order waiting"
- Idempotency check prevents double-counting but blocks legitimate re-entry

---

### Scenario 1.2: Limit Order Cancelled by User, Position Never Synced
**Setup:**
- Limit order 111 placed, marked processed in VirtualPositionTracker
- User manually cancels 111 on exchange
- Virtual tracker still thinks position exists
- `repair_position_desync` runs: exchange=0, virtual=+5

**What Goes Wrong:**
- Repair logic zeroes phantom position (correct behavior)
- BUT: If order 111 later shows up as filled in `trade_search` response:
  - `_process_fills` tries to update virtual position (already zeroed)
  - Creates conflicting state: position is "flat" but fill says it should be +5
  - Next signal might fail because state is inconsistent
  - **RESULT**: Position sync corrupted, false "all good" signal from next poll

**Root Cause:**
- `_processed_fills` tracks processed fills, but doesn't validate against current virtual positions
- No bidirectional consistency check
- Repair happens independently of fill processing

---

## 2. STOP ORDERS - WORKING BUT UNTRIGGERED

### Scenario 2.1: Stop Order Gets Triggered During Data Gap
**Setup:**
- Strategy A: LONG 5 ES @ 5500, stop loss order at 5450
- Stop order ID 222 is placed and working (status=OPEN)
- Data feed glitches, 1-minute bar is missing/delayed
- During gap, market fills stop order
- Data resumes, `poll_and_cleanup()` detects filled stop

**What Goes Wrong:**
- Virtual position hasn't been reduced (stop was external order)
- Exchange shows 0, virtual shows +5
- `repair_position_desync` triggers:
  - Zeros the phantom +5 position
  - Clears `_processed_fills` for strategy
  - But the ACTUAL stop fill is still in order history!
- Next poll cycle:
  - `_process_fills` reprocesses the same stop fill (it's in `trade_search`)
  - But `_processed_fills` was cleared → adds it again
  - Position tracking becomes +5, -5, +5... thrashing
  - **RESULT**: Entry_price/P&L calculations oscillate

**Root Cause:**
- Stop orders don't update virtual position (external broker action)
- Clearing `_processed_fills` creates reprocessing vulnerability
- No transaction log of what repair cleared vs what fills need reprocessing

---

### Scenario 2.2: Orphaned Stop Loss After TP Fills
**Setup:**
- Bracket orders placed: TP at 5510, SL at 5490
- Market hits TP at 5510, fills it (order 333)
- SL remains open (order 222)
- `bracket_order_manager.poll_and_cleanup()` attempts to cancel orphan
- API call to cancel SL order 222 fails with `errorCode: 5` (order doesn't exist)

**What Goes Wrong:**
- Did order 222 fill? Or was it already cancelled?
- Manager logs "order doesn't exist" but doesn't confirm state
- If 222 WAS filled:
  - Position is net flat (TP closed, SL opened+closed)
  - But virtual tracker shows +5 still (only processed TP fill)
  - Repair detects phantom +5, zeros it
  - Loss of attribution for which order actually closed the trade
  - **RESULT**: P&L gap, wrong exit tracking

**Root Cause:**
- API errorCode 5 is ambiguous (could be already filled or never created)
- No order confirmation after cancel attempt
- Dual exit tracking (TP and SL) requires atomic coordination

---

## 3. MARKET ORDERS DURING VOLATILITY - SLIPPAGE

### Scenario 3.1: Market Order Partially Fills Multiple Times
**Setup:**
- Strategy submits BUY 10 ES market order (order 444)
- Tight spread, but order is large
- Fills in TWO tranches:
  - Trade 1: 6 ES @ 5500.00
  - Trade 2: 4 ES @ 5500.05
- Both trades have same order_id (444) but different trade_ids

**What Goes Wrong:**
- `_process_fills` looks for order_id 444 in filled orders
- Finds order 444 status=FILLED (after both tranches complete)
- Gets fill_price from `trade_prices` lookup:
  - `trade_prices = {t.get("orderId"): t.get("price") for t in trades if t.get("orderId")}`
  - This is a DICT: {444: 5500.00} (overwrites with last trade!)
  - Uses 5500.00 as entry price, not the weighted average
- Virtual position created @ 5500.00 (wrong blended price)
- If strategy uses `state.entry_price` for bracket calculations:
  - TP and SL calculated from wrong baseline
  - TP too close, SL too far (or vice versa)
  - **RESULT**: Bracket prices invalid, unfillable orders

**Root Cause:**
- Fill price lookup assumes 1 trade per order
- ProjectX can split executions into multiple trades
- No aggregation of partial fills

---

### Scenario 3.2: Market Order Slippage During Volatility Spike
**Setup:**
- Order estimate price: 5500.00
- Actual fill price: 5502.50 (gap up during execution)
- Order 444 shows filled @ 5502.50
- Virtual position created with estimated 5500.00 (stale)
- Bracket orders calculated from 5500.00

**Wait, checking code...** Line 768 in `bracket_order_manager.py`:
```python
state.entry_price = fill_price  # Updates with ACTUAL fill price
```

So this is handled correctly in fill processing. BUT:

- Entry-time issue: Strategy signals at bar close, calculates entry price and bracket prices
- Order submitted with estimated price
- Virtual position tracker records entry with estimated price (before fill)
- If strategy creates brackets BEFORE fill confirmation:
  - Brackets use estimated 5500.00
  - Actual fill @ 5502.50
  - Brackets now ±2.50 away from real entry
  - **RESULT**: Bracket invalidation

**Root Cause:**
- Timing gap between order submission and fill confirmation
- Line 778-779: `_submit_brackets_after_fill` is the fix, but only works if called
- If `brackets_submitted=True` already, won't recalculate with real price

---

## 4. PARTIAL FILLS & MULTI-FILL ORDERS

### Scenario 4.1: Order Partially Fills, Then Cancelled
**Setup:**
- BUY 10 ES order (id 555)
- Fills 7 ES @ 5500
- User cancels order before remaining 3 ES fill
- Order status = CANCELLED (order 555)
- Trade shows 7 contracts

**Current System Behavior:**
1. `_process_fills` looks for STATUS_FILLED orders
2. Order 555 has status=CANCELLED (not STATUS_FILLED=2)
3. **Never processes the trade**
4. Virtual position: NEVER UPDATED
5. Exchange position: +7
6. Virtual position: 0 (never recorded)
7. `repair_position_desync` detects exchange=+7, virtual=0
8. Logs warning, strategy has untracked position

**What Goes Wrong:**
- Partial fills with subsequent cancellation are invisible to `_process_fills`
- Logic only triggers on `STATUS_FILLED` orders, not mixed fills+cancellations
- Untracked position warning is correct, but leaves position stranded
- **RESULT**: Accurate repair detection but no mechanism to recover

**Root Cause:**
- `_process_fills` only looks at order status, ignores trade history for cancelled orders
- Should scan `trades` for all trades (regardless of final order status)

---

### Scenario 4.2: Multiple Partial Fills Stagger Over Several Polls
**Setup:**
- BUY 10 ES order (id 666)
- Poll #1: 3 fills of 2 contracts each = 6 total
- Poll #2: 2 fills of 2 contracts = 4 total
- Poll #3: Fill complete (shows STATUS_FILLED)
- Each poll runs `_process_fills`, each detects new trades

**Current System Behavior:**
1. `_processed_trades` set prevents reprocessing same trade_id
2. Each new trade gets added to virtual position when detected
3. But virtual position recorded ONLY when order status = FILLED

**Specific Issue - Timing of entry_price:**
- Strategy expects entry_price set AFTER order fills
- If partial fills occur:
  - Poll 1: 6 contracts filled, sets entry_price = first trade price (2 contracts worth)
  - Poll 2: 4 more contracts, updates entry_price to weighted avg? (depends on code path)
  - **Line 149-148 in virtual_position_tracker.py:**
    ```python
    if abs(new_qty) > abs(old_qty):
        # Adding to position - calculate weighted average
        if price:
            new_cost = abs(signed_qty * price)
            pos.total_cost += new_cost
            if abs(new_qty) > POSITION_EPSILON:
                pos.avg_entry_price = pos.total_cost / abs(new_qty)
    ```

This actually DOES handle partial fills correctly! But:

**What Goes Wrong:**
- Bracket submission (`_submit_brackets_after_fill`) is keyed on `brackets_submitted` flag
- Flag is set to TRUE after first fill confirmation
- **Subsequent partial fills won't trigger new bracket submission**
- If ATR changes between partial fills, brackets become stale
- **RESULT**: Brackets locked to first-partial-fill ATR, not final position average price

**Root Cause:**
- `brackets_submitted` is binary (true/false), doesn't account for position growth
- Should be keyed to position size, not just "any bracket submitted"

---

## 5. GTD/GTC ORDERS SPANNING SESSIONS

### Scenario 5.1: GTC Order Carries Overnight, Fills During Different Session
**Setup:**
- Friday @ 15:55 CT: Strategy places SELL 5 ES @ 5510 (GTC limit)
- Order 777 submitted, marked processed
- Weekend: no market
- Monday open: Market gaps down, never touches 5510
- Order remains open (GTC)
- Wednesday: Market rallies, order fills @ 5510.50 (slightly different)

**What Goes Wrong:**
- Virtual position marked as "processed" Friday (line 116 in virtual_position_tracker.py)
- But order never actually executed Friday
- System assumes fill happened Friday
- Monday-Wednesday: Virtual tracker shows -5 ES (from Friday submission)
- Exchange shows 0 (no fill yet)
- Repair happens, zeros phantom -5
- Wednesday fill finally arrives, but:
  - `_processed_fills` might already include 777 (if cleared during repair)
  - Or `_processed_order_ids` prevents reprocessing
  - **RESULT**: Orphaned fill, position never updated

**Root Cause:**
- `execute_order` marks order processed at submission, not at fill
- GTC orders violate "immediate fill" assumption
- Session spanning breaks idempotency model

---

### Scenario 5.2: Session Close Gaps Out, Order Never Fills
**Setup:**
- Friday @ 15:55 CT: SELL 5 ES @ 5490 (GTC, below current 5505)
- Order 888 placed, market closes
- Monday: Gaps DOWN to 5450
- Order never fills (trigger price never touched)
- Order remains open indefinitely

**What Goes Wrong:**
- VirtualPositionTracker thinks position is SOLD 5 (Friday)
- Repair doesn't detect this (exchange shows 0, but virtual shows 0 too - position closed?)
- Actually: virtual shows 0 because we're tracking it as a reduction?
- **Ambiguity: Does submitted-but-not-filled limit order count in virtual position?**

**Current Code Behavior (line 96-134 in virtual_position_tracker.py):**
```python
def execute_order(...):
    # Validates quantity > 0 (always positive)
    # Converts to signed quantity
    # Updates position immediately
```

**Clarification: Order is not yet in the `positions` dict?**
Yes! `execute_order` is called AFTER fill confirmation, not at submission.

So actually, the code is safer than scenario suggests:
- Unconfirmed limit orders are NOT added to virtual position
- Repair won't detect them
- **BUT**: How does strategy know limit order is working?

**Actual Problem:**
- Strategy needs to track pending orders separately
- Current system only tracks confirmed positions
- Pending limit orders are invisible to repair logic
- If limit order is very stale (weeks old), it might be forgotten
- No mechanism to detect and cancel zombie orders

---

## 6. EDGE CASES IN REPAIR LOGIC ITSELF

### Scenario 6.1: Direction Conflict - All Positions Wrong Way
**Setup:**
- Exchange position: +5 ES (broker somehow has long)
- Strategy A: FLAT
- Strategy B: -3 ES (short)
- Strategy C: -2 ES (short)
- Net virtual: -5 ES (opposite direction!)

**Repair Logic (line 2118):**
```python
reduce_longs = excess > 0  # excess = -5 - (+5) = -10, so reduce_longs = False (SHORT)
```
Tries to zero SHORT positions to match LONG exchange position.
- Zeros Strategy B (-3) and C (-2) = -5 total
- Result: exchange=+5, virtual=0
- **RESULT**: Technically correct but asymmetric (zeroed shorts, kept nothing long)

**What Happens Next:**
- System is now in a fragile state
- All long exposure deleted, short exposure remains
- Next trade might be LONG, creating new +5
- Then repair runs again, oscillates

**Root Cause:**
- Repair is designed for "phantom positions in same direction"
- Direction conflicts indicate fundamental tracker corruption
- Should probably trigger manual intervention flag

---

### Scenario 6.2: Repair Clears Processed Fills But Fills Are Still Pending
**Setup:**
- Exchange has +3 ES (from filled trade)
- Virtual has +5 ES (from another strategy)
- Repair detects excess, zeros a position
- During repair (line 2212-2217):
  ```python
  cleared_count = self._clear_processed_fills_for_strategy(sid)
  ```
- This clears `_processed_fills` set for the strategy

**What Goes Wrong:**
- If fill 999 was just processed 0.1 seconds ago and added to virtual position
- Now we clear it from `_processed_fills`
- Next poll cycle, fill 999 reappears in trade list (trades are returned every cycle)
- Gets reprocessed, adds to position again
- **RESULT**: Position thrashing, P&L miscalculation

**Specific Problematic Sequence:**
1. Poll #1: Fill 999 detected, added to virtual position, marked in `_processed_fills`
2. Poll #2: Repair runs, clears `_processed_fills`
3. Poll #3: Fill 999 still in trade history (always returned), gets reprocessed
4. Position now includes fill 999 twice

**Root Cause:**
- Clearing `_processed_fills` is too blunt
- Should only clear fills AFTER position repair, not before
- Or: Keep fill in `_processed_fills` even after zeroing position

---

### Scenario 6.3: Repair Succeeds Partially, Remaining Excess Never Resolves
**Setup:**
- Exchange: +5 ES
- Virtual: Strategy A (+3), Strategy B (+8) = +11 total
- Repair needs to zero 6 contracts of long positions
- Strategy A: only 3 contracts
- Strategy B: 8 contracts
- Repair zeros A (3), still needs 3 from B
- Line 2196-2197:
  ```python
  if zero_qty >= abs(qty) - 0.001:
      # Zero out completely
      state.tracker.reset()
  ```
- Zeros all 8 from B
- Total zeroed: 3 + 8 = 11 (too much!)
- Remaining to zero: 0

**Wait, re-reading line 2185-2193:**
```python
remaining_to_zero = abs(excess)  # = 6
for sid, state, qty in strategies_with_pos:
    if remaining_to_zero <= 0.001:
        break
    zero_qty = min(abs(qty), remaining_to_zero)  # min(8, 6) = 6
    remaining_to_zero -= zero_qty  # 6 - 6 = 0
```

Actually no, this is handled correctly. **Logic is fine here.**

---

### Scenario 6.4: Repair Clears All Strategy Positions, But New Order Gets Placed Same Iteration
**Setup:**
- Repair detects mismatch, zeros strategy A's position
- Calls `state.tracker.reset()` (clears all positions)
- Calls `state.entry_price = None`, `state.take_profit_price = None`, etc.
- Same iteration, strategy signals a new entry
- New order is placed with entry_price still None
- Bracket calculation fails (dividing by None?)

**Check Code (line 69-71 in multi_strategy_executor_enhanced.py EnhancedStrategyState):**
```python
self.take_profit_price: Optional[float] = None
self.stop_loss_price: Optional[float] = None
```

And later in `_submit_brackets_after_fill` (line 779):
```python
self._submit_brackets_after_fill(state, fill_price, side, qty)
```

This uses `fill_price` from the actual trade, so it should be OK.

**BUT**: If strategy tries to access entry_price immediately after repair, in same cycle:
- entry_price = None
- Strategy tries to use it for calculations
- Possible ZeroDivisionError or silent failure

**Root Cause:**
- Repair clears state mid-cycle
- No mechanism to prevent signal processing on same iteration as repair
- State is left in inconsistent state

---

## 7. ORDER REGISTRY & IDEMPOTENCY EDGE CASES

### Scenario 7.1: Order Registry Grows Unbounded
**Check Code (line 1870-1874 in bracket_order_manager.py):**
```python
if len(self._processed_fills) > 1000:
    sorted_ids = sorted(self._processed_fills)
    self._processed_fills = set(sorted_ids[-500:])
```

Good, there's cleanup. But:

**What Goes Wrong:**
- Keeps only last 500 processed fills
- If system runs for weeks, will lose history
- Old fill 111 from week 1 gets removed
- If same order_id somehow reappears (unlikely but possible in test/reset scenario):
  - Not in `_processed_fills` anymore
  - Gets reprocessed as new fill
  - Phantom position created

**Root Cause:**
- Cleanup is age-agnostic (just keeps most recent)
- Should be time-based (e.g., keep last 1 hour)

---

### Scenario 7.2: Order ID Collision After System Restart
**Setup:**
- Live trading, order 999 filled
- System crash, restart
- Restart loads previous strategy states BUT:
- `_processed_fills` set is lost (memory, not persisted)
- API returns historical trades including order 999
- Next `_process_fills` cycle sees order 999 again

**What Goes Wrong:**
- Order 999 reprocessed as new fill
- Virtual position updated again
- Position tracking corrupted
- **RESULT**: P&L calculation wrong

**Root Cause:**
- No persistence of `_processed_fills` across restarts
- Could use OrderRegistry (line 88) but currently optional
- No automatic recovery mechanism

---

## Summary: High-Risk Edge Cases

| Scenario | Severity | Impact | Likely? |
|----------|----------|--------|---------|
| 1.1 Limit order overridden | HIGH | Wrong P&L, orphaned brackets | Medium |
| 1.2 Limit cancelled + refilled | HIGH | Position oscillation, fill reprocessing | Medium |
| 2.1 Stop triggered during data gap | HIGH | Position thrashing on repair | Low |
| 2.2 Orphaned bracket ambiguity | MEDIUM | P&L gap, exit tracking loss | Low |
| 3.1 Partial fills overwrite in dict | CRITICAL | Invalid bracket prices | High |
| 3.2 Market slippage with early brackets | MEDIUM | Bracket invalidation | High |
| 4.1 Partial fill then cancel | MEDIUM | Untracked position persists | Medium |
| 4.2 Brackets locked to first partial | HIGH | Stale ATR for growing positions | High |
| 5.1 GTC order fills in different session | MEDIUM | Orphaned fill, position desync | Low |
| 5.2 Zombie pending orders | LOW | Forgotten orders, state limbo | Low |
| 6.1 Direction conflict repair | MEDIUM | Oscillating repairs, fragile state | Medium |
| 6.2 Repair clears fills mid-reprocess | CRITICAL | Position thrashing, recount | High |
| 6.3 Repair undershoots excess | MEDIUM | Orphaned remaining excess | Low |
| 6.4 State cleared mid-cycle | MEDIUM | None references, silent failures | Medium |
| 7.1 Registry unbounded growth | LOW | Memory leak over weeks | Low |
| 7.2 No persistence across restart | CRITICAL | P&L corruption on crash recovery | High |

---

## Recommendations for Hardening

1. **Distinguish order types**: Market vs Limit vs Stop - different assumptions
2. **Bidirectional validation**: Check fills against virtual positions, not just repair
3. **Aggregate partial fills**: Build weighted average price from all trades per order
4. **Atomic position updates**: Lock state during repair/fill processing
5. **Persist critical state**: Serialize `_processed_fills` to disk
6. **Order status tracking**: Track order lifecycle (Submitted → Filled/Cancelled)
7. **Session-aware GTC handling**: Special logic for orders spanning sessions
8. **Bracket recreation safety**: Key brackets to position size, not binary flag
9. **Repair idempotence**: Log what was cleared, prevent reprocessing of cleared fills
10. **Test suite**: Edge case tests for all scenarios above
