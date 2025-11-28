# Visual Scenarios: Order Fill Timing Edge Cases

## Legend

```
T0, T1, T2, ...     = Timeline points
S1, S2, S3, ...     = Strategy instances (ES_1M_01, ES_1M_02, etc.)
VT                  = VirtualPositionTracker
API                 = Exchange Position API
BOM                 = BracketOrderManager
Sync                = Reconciliation logic
```

---

## Scenario 1: Stop Loss Fills During Sync Check

**Critical Issue:** SL exit fills at exact moment sync compares aggregate positions

```
SETUP:
  S1 virtual: +1 ES (long)
  S2 virtual: +1 ES (long)
  S3 virtual: +1 ES (long)
  Net virtual: +3 ES
  S1's SL order: sell 1 ES @ 6400

TIMELINE:

  T0:00s  |  S1's price hits 6400
  T0:00s  |  Exchange processes S1's SL fill
  T0:00s  |  Exchange now has: +2 ES (only S2 and S3)
          |  (S1 no longer in position API response)
          |
  T0:10ms |  [SYNC CHECK STARTS]
          |  sync.reconcile_positions() called
          |
  T0:15ms |  Reads exchange position API
          |  Exchange API response: {ES: 2}  [from T0:10ms snapshot]
          |  (SL fill just happened, still propagating through API)
          |
  T0:20ms |  Reads virtual tracker aggregate
          |  Virtual net: +3 ES (S1:+1, S2:+1, S3:+1)
          |  (Virtual tracker hasn't processed SL fill yet!)
          |
  T0:25ms |  [REPAIR LOGIC FIRES]
          |  Delta = exchange(2) - virtual(3) = -1
          |  "Virtual shows +1 more than exchange"
          |  "One position is phantom, zero it"
          |
  T0:30ms |  force_flat("ES") on S1?
          |  Problem: S1's position WASN'T phantom!
          |  It was legitimately exited via SL fill
          |
  T0:35ms |  [BOM PROCESSES SL FILL]
          |  BOM.poll_and_cleanup() detects SL order filled
          |  Calls: VT.execute_order("sell", 1, order_id=SL_001)
          |  Expected: S1 qty: +1 → 0 (flat)
          |  Actual: S1 qty: 0 → -1 (SHORT!)
          |  [CORRUPTED: position inverted]
          |
  T0:40ms |  Next sync check
          |  Exchange: +2 ES (from S2, S3)
          |  Virtual: -1 + 1 + 1 = +1 ES
          |  Delta: +1 (opposite direction from before!)
          |  [DIVERGED: wrong direction]
          |
  T0:45ms |  Repair tries to "fix" upward
          |  Or decides it's a real position (confusion!)

ROOT CAUSE:
  ✗ Repair fires before SL fill processing
  ✗ Repair doesn't coordinate with BOM
  ✗ SL fill creates valid position reduction, not phantom
  ✗ Sync/repair have stale aggregate view

CONSEQUENCE:
  ✗ S1's exit invisible to accounting
  ✗ S1 position inverted (short instead of flat)
  ✗ Next entry signal fires, sees "flat" (it's short!)
  ✗ Double-entry attempt (buy when short = cover + long entry)
  ✗ P&L calculation wrong (no entry price for inverted position)
  ✗ Cascading divergence: each repair attempt worsens state
```

---

## Scenario 2: Rapid Multiple Fills (Sub-Millisecond)

**Critical Issue:** TP and SL fill simultaneously, confusing bracket manager

```
SETUP:
  S1 virtual: +1 ES
  Entry price: 5000
  SL order: sell 1 @ 4990 (downside protection)
  TP order: sell 1 @ 5010 (profit target)
  Both orders registered with BOM

TIMELINE:

  T0:00s  |  Price action at 5000
  T0:00s  |  Price jumps to 5020 in one tick
          |  Both SL (4990) AND TP (5010) are triggered!
          |
  T0:05ms |  Exchange fills BOTH orders
          |  SL order fills first: sell 1 @ 4990
          |  TP order fills next: sell 1 @ 5010
          |  Both status: FILLED (2)
          |  (This shouldn't happen in OCO, but API doesn't enforce OCO!)
          |
  T0:10ms |  [BOM.poll_and_cleanup() runs]
          |  Polls SL order: status=FILLED
          |  Polls TP order: status=FILLED
          |  Decision: "One filled, cancel the other"
          |  (But it doesn't know WHICH one filled first)
          |
  T0:15ms |  Tries to cancel TP order
          |  API call: order_cancel(tp_order_id=102)
          |  Exchange response: errorCode=5 "Order not found"
          |  (TP already filled, can't cancel it!)
          |
  T0:20ms |  [VT.execute_order() processes fills]
          |  First call: execute_order("sell", 1, order_id=SL_101)
          |  S1 qty: +1 → 0 (flat, correct)
          |
  T0:25ms |  Second call: execute_order("sell", 1, order_id=TP_102)
          |  S1 qty: 0 → -1 (SHORT!)
          |  [CORRUPTED: double-exit creates inverted position]
          |
  T0:30ms |  Virtual tracker now has:
          |  S1: -1 ES (short when should be flat)
          |  Order fills: [SL: sell 1, TP: sell 1]
          |  (Accounting sees 2 exits, only 1 entry)
          |
  T0:35ms |  Next sync/repair check
          |  Exchange: 0 ES (both fills settled)
          |  Virtual: -1 ES (corrupted state)
          |  Delta: +1 ES
          |  Repair: "Virtual is SHORT but exchange is flat"
          |
  T0:40ms |  Repair zeros S1's position
          |  Sets qty: -1 → 0
          |  Lost the corrupted state, but also lost exit P&L

ROOT CAUSE:
  ✗ Exchange API doesn't enforce OCO (allows both fills)
  ✗ BOM's poll granularity misses simultaneous fills
  ✗ BOM can't determine which fill happened first
  ✗ VT processes both exit fills against same entry
  ✗ No validation in VT: can't sell more than position size

CONSEQUENCE:
  ✗ Position inverted (short when should be flat)
  ✗ Double-exit in order history
  ✗ P&L calculation: sold 2, but only owned 1
  ✗ Trade accounting nonsensical
  ✗ Repair operation not diagnostic (zeros error state)
  ✗ No visibility into what actually happened
```

---

## Scenario 3: In-Flight Order Not Yet Confirmed

**Critical Issue:** Repair fires before exchange confirms order fill

```
SETUP:
  S1 virtual: FLAT (no position)
  Entry signal fires: buy 1 ES

TIMELINE:

  T0:00s  |  [ENTRY ORDER PLACEMENT]
          |  S1 places market order: buy 1 ES
          |  Order submitted to API: order_id=123
          |
  T0:02ms |  [VT.execute_order() called immediately]
          |  Assumption: market order fills instantly
          |  VT.execute_order("buy", 1, order_id=123)
          |  S1 qty: 0 → +1
          |
  T0:05ms |  [API ACKNOWLEDGES ORDER]
          |  Exchange API returns: {order_id: 123, status: 1 (OPEN)}
          |  (Order acknowledged but not yet in fill queue)
          |
  T0:10ms |  [SYNC CHECK TRIGGERED (too early!)]
          |  sync.reconcile_positions() called
          |  Reads exchange position API
          |  Query: position_search_open()
          |
  T0:15ms |  Exchange position API response
          |  Returns: ES position list
          |  NO S1 position yet (fill hasn't propagated to position API)
          |  Exchange qty: 0
          |  Virtual qty: +1 (assumed fill)
          |
  T0:20ms |  [MISMATCH DETECTED]
          |  Delta: exchange(0) - virtual(+1) = -1
          |  "Virtual has +1 that exchange doesn't show"
          |  Repair logic: "Must be phantom, zero it"
          |
  T0:25ms |  [REPAIR EXECUTES]
          |  VT.force_flat("ES")
          |  S1.qty: +1 → 0
          |  (Zeros the position we just entered!)
          |
  T0:30ms |  [ORDER ACTUALLY FILLS]
          |  Exchange match engine processes order #123
          |  Order fills at 5001
          |  Position created on exchange: +1 ES @ 5001
          |
  T0:35ms |  [BOM PROCESSES FILL]
          |  Order status poll: order_id=123, status=FILLED
          |  Calls: VT.execute_order("buy", 1, order_id=123, price=5001)
          |
  T0:40ms |  Idempotency check:
          |  order_id=123 already processed? YES (from T0:02)
          |  Returns existing position without update
          |  S1 qty: stays 0 (doesn't re-apply fill)
          |
  T0:45ms |  Virtual tracker state:
          |  S1: 0 qty (repaired, fill ignored)
          |  Exchange: +1 ES (from order 123)
          |  P&L tracking: broken (no entry price for position)
          |
  T0:50ms |  Next iteration: signal fires again
          |  S1 thinks position is flat (it's not!)
          |  Places another buy order
          |  Trying to buy +1 when already +1
          |  Result: tries to create position with +2 total

ROOT CAUSE:
  ✗ Sync triggered too early (before fills propagate)
  ✗ Virtual tracker assumes instant fill (aggressive assumption)
  ✗ No "pending order" window to prevent early sync
  ✗ Repair can't distinguish pending from phantom
  ✗ Idempotency check prevents recovery

CONSEQUENCE:
  ✗ Entry fill is invisible (zeroed before processing)
  ✗ Virtual position becomes flat when exchange has +1
  ✗ Re-entry attempt (double-entry)
  ✗ P&L tracking lost (no entry price)
  ✗ Position accumulates (+2 when should be +1)
  ✗ Subsequent exit orders close wrong qty or fail
```

---

## Scenario 4: Concurrent Repair vs Fill Processing

**Critical Issue:** Two threads write to same position simultaneously

```
SETUP:
  S1 virtual: +1 ES
  SL order: sell 1 ES
  Exchange position: +1 ES (synced)

TIMELINE:

  Thread A (Main/Sync Loop):           Thread B (BOM/Fill Processing):
  ─────────────────────────────────────────────────────────────────

  T0:00s
  reconcile_positions() starts

  T0:05ms
  Reads exchange API:
  {ES: 1}

  T0:10ms
  Reads virtual net:
  S1.qty = +1

  T0:15ms
  No divergence, no repair needed
                                       ← T0:15ms: SL order fills
                                       BOM.poll_and_cleanup() runs
                                       Detects SL order: status=FILLED

                                       ← T0:18ms: Calls VT.execute_order()
                                       Tries: S1.qty = +1 - 1 = 0

  T0:20ms
  Wait... let me recheck positions!
  (Unexpected divergence detected elsewhere)
  force_flat("ES") called
  Acquires lock on positions["ES"]
  Sets: S1.qty = 0
                                       ← T0:22ms: execute_order continues
                                       Reads: S1.qty (reads 0 just set)
                                       Calculates: new_qty = 0 - 1 = -1
                                       Writes: S1.qty = -1

  T0:25ms
  Releases lock

  T0:30ms
  S1 qty is now -1 (SHORT!)
  But no entry on exchange
  [CORRUPTED STATE]

ROOT CAUSE:
  ✗ No synchronization between repair and fill processing
  ✗ Both threads write to same position object
  ✗ force_flat() doesn't check if fill processing is happening
  ✗ execute_order() doesn't check if repair is happening
  ✗ Race window: read(qty) → write(qty) not atomic

CONSEQUENCE:
  ✗ Position inverted (short instead of flat)
  ✗ Next sync sees: exchange=0, virtual=-1
  ✗ Repair tries to fix (zeros the -1)
  ✗ But the -1 is real (corrupted, but real)
  ✗ Masking the root cause, not fixing it
  ✗ Undetected data corruption
```

---

## Scenario 5: Position Attribution Loss

**Critical Issue:** Multiple strategies on same symbol, repair can't distinguish

```
SETUP:
  ES_1M_01 virtual: +1 ES
  ES_1M_02 virtual: +1 ES
  ES_1M_03 virtual: +1 ES
  Exchange: +2 ES (only 2 visible, 1 fill in propagation)

TIMELINE:

  T0:00s  |  [SYNC CHECK]
          |  Aggregate virtual: +1 + 1 + 1 = +3 ES
          |  Exchange: +2 ES
          |  Delta: -1 ES (virtual overstates by 1)
          |
  T0:05ms |  [REPAIR DECISION]
          |  "One position is phantom"
          |  Options:
          |    A) Zero ES_1M_01
          |    B) Zero ES_1M_02
          |    C) Zero ES_1M_03
          |  Current repair logic: ???
          |
  T0:10ms |  [REPAIR (Current Implementation)]
          |  Likely: Zero all three (force_flat("ES"))
          |  OR: Zero the first one in strategy order
          |  OR: Random/undefined behavior
          |
  T0:15ms |  If ALL three zeroed:
          |  ES_1M_01.qty: +1 → 0 (innocent!)
          |  ES_1M_02.qty: +1 → 0 (innocent!)
          |  ES_1M_03.qty: +1 → 0 (innocent!)
          |
  T0:20ms |  [ACTUAL FILLS ARRIVE]
          |  ES_1M_01's order fills
          |  ES_1M_02's order fills
          |  Exchange: +2 ES
          |
  T0:25ms |  [FILL PROCESSING]
          |  ES_1M_01.execute_order("buy", 1)
          |  Idempotency: already processed? (depends on implementation)
          |
          |  If YES: qty stays 0 (fill ignored)
          |  If NO: qty goes 0 → +1 (recovered)
          |
  T0:30ms |  If fill processing SKIPS (idempotency):
          |  Virtual: ES_1M_01:0, ES_1M_02:0, ES_1M_03:0
          |  Exchange: +2 ES
          |  [WORSE DIVERGENCE THAN BEFORE]
          |
  T0:35ms |  If fill processing RECOVERS:
          |  Virtual: ES_1M_01:+1, ES_1M_02:+1, ES_1M_03:0
          |  Exchange: +2 ES
          |  [STILL DIVERGED: now ES_1M_03 is phantom]
          |
  T0:40ms |  Meanwhile:
          |  ES_1M_03's order DID fill (delayed propagation)
          |  But virtual was zeroed, so fill is invisible
          |  ES_1M_03 thinks position is flat
          |  Next signal fires: buys again
          |  Exchange receives: another buy (now +3)
          |  [FURTHER DIVERGENCE]

ROOT CAUSE:
  ✗ Repair works at symbol level (loses strategy identity)
  ✗ Can't map virtual position → exchange order
  ✗ Multiple strategies on symbol = ambiguous ownership
  ✗ Repair can zero innocent strategies

CONSEQUENCE:
  ✗ Wrong strategy zeroed (or all zeroed)
  ✗ Legitimate positions destroyed
  ✗ Fills from innocent strategies become invisible
  ✗ Cascading re-entries from zeroed strategies
  ✗ Divergence grows instead of shrinking
  ✗ Repair makes situation worse, not better
```

---

## Scenario 6: Order Cancellation Race

**Critical Issue:** Repair severs entry-exit link, bracket manager can't operate

```
SETUP:
  S1 virtual: +1 ES
  SL order: sell 1 @ 4990 (registered with BOM)
  TP order: sell 1 @ 5010 (registered with BOM)

TIMELINE:

  T0:00s  |  [NORMAL OPERATION]
          |  BOM monitors both SL and TP
          |  Price moves, SL gets close to trigger
          |
  T0:50ms |  [UNEXPECTED SYNC CHECK]
          |  No divergence detected, no repair needed
          |  (Everything looks OK)
          |
  T0:52ms |  Price hits SL: 4990
          |  Exchange fills SL order
          |  Order status: FILLED (2)
          |
  T0:55ms |  [SYNC CHECK RUNS AGAIN]
          |  Exchange shows: 0 ES (SL filled, position closed)
          |  Virtual shows: +1 ES (fill not yet processed)
          |  Delta: -1
          |
          |  [REPAIR FIRES (WRONG TIMING!)]
          |  Calls: force_flat("ES")
          |  S1.qty: +1 → 0
          |
  T0:60ms |  [BOM.poll_and_cleanup() runs]
          |  Polls SL: status=FILLED ✓
          |  Polls TP: status=OPEN (1)
          |  Decision: SL filled, cancel TP
          |
  T0:65ms |  API call: order_cancel(tp_order_id=102)
          |  Exchange: "TP order cancelled" ✓
          |
  T0:70ms |  [FILL PROCESSING]
          |  Process SL fill:
          |  execute_order("sell", 1, order_id=101)
          |
          |  Current state: S1.qty = 0 (from repair)
          |  Math: 0 - 1 = -1 (INVERTED!)
          |
  T0:75ms |  Virtual: S1.qty = -1 (SHORT!)
          |  Exchange: 0 ES (flat)
          |  [CORRUPTED: virtual is inverted]
          |
  T0:80ms |  Next action:
          |  TP was cancelled, but position is already closed
          |  SL processed, but position is now short (wrong side!)
          |
          |  Next signal fires:
          |  S1 is short, new signal wants to go long
          |  Places buy order (cover short + new entry)
          |  Wrong qty or unwanted side reversal

ROOT CAUSE:
  ✗ Repair severs link between position and brackets
  ✗ BOM cancels TP assuming entry still exists
  ✗ Cancelled TP is wasted (SL already filled anyway)
  ✗ Exit fill processing finds zeroed position
  ✗ Exit creates inverted position

CONSEQUENCE:
  ✗ TP cancelled unnecessarily
  ✗ Exit fill inverts position (short instead of flat)
  ✗ Wrong-side unwind trade needed
  ✗ Extra trades, extra fees
  ✗ Unreliable bracket behavior
  ✗ SL fill didn't need TP cancel (already mutually exclusive)
```

---

## Summary: Why These Edge Cases Happen

| Edge Case | Root Cause | Timing Issue |
|-----------|-----------|--------------|
| SL during sync | Repair fires before fill processing | 10ms-200ms lag |
| Rapid double fills | Poll granularity missing atomic state | 5ms-50ms |
| In-flight order | Sync before order propagates | 10ms-100ms API lag |
| Concurrent writes | No synchronization primitives | Lock-free → race |
| Attribution loss | Symbol-level aggregation | Lost strategy identity |
| Cancellation race | Repair severs entry-exit link | Timing-dependent |

**Common Pattern:**
- System assumes "instant" but APIs have real latency (10-100ms)
- Multiple systems (repair, fill processing, bracket manager) operate independently
- No coordination between systems
- Repair based on snapshots, not consistent transactions
- P&L tracking assumes entry-exit pairing, but repairs can break it

