# Edge Cases: Position Sync/Repair Logic in Multi-Strategy Futures Trading

## System Context

**Architecture:**
- Multiple independent strategies (ES_1M_01 through ES_1M_10, NQ_1M_01 through NQ_1M_10, GC_1M_01 through GC_1M_10)
- Each strategy has a **local virtual position tracker** (independent, assumes market orders fill immediately)
- Virtual positions **net together** into a single exchange position per symbol
- **Sync/repair logic**: When exchange position shows fewer contracts than virtual net, the system zeros phantom virtual positions

**Critical Components:**
- `VirtualPositionTracker.execute_order()` - Tracks fills locally
- `VirtualPositionTracker.force_flat()` - Zeros position without recording trade
- `BracketOrderManager.poll_and_cleanup()` - Detects TP/SL fills via polling
- `portfolio_manager.reconcile_positions()` - Compares exchange vs virtual net

**Key Assumption (FRAGILE):**
- Market orders fill immediately in virtual tracker (queued before exchange confirms)
- Exchange position API is eventually consistent but may lag

---

## Edge Cases: Order Fill Timing

### 1. **Bracket Fill During Sync Check**

**Scenario:**
```
T0:   Sync check initiated
      Virtual net: ES +3 (from 3 strategies: S1:+1, S2:+1, S3:+1)
      Exchange reports: +2 (one fill not yet reflected)

T1:   While checking...
      TP order fills on exchange (S1 sells 1 ES)
      Exchange now has: +1 (S1:flat, S2:+1, S3:+1)

T2:   Sync completes
      Repair logic sees: exchange=+2, virtual=+3
      Delta=-1 (virtual overstated by 1)

T3:   Repair zeros S1's position (assumes phantom)
      But S1 was legitimately flat! (its TP filled)
```

**What Goes Wrong:**
- S1's P&L calculation breaks (position shows 0 but should record the exit fill)
- If S1 re-enters immediately, virtual tracker has stale historical data
- Trade history loses the legitimate exit event
- P&L attribution assigns loss/profit to wrong strategy

**Why It Happens:**
- Sync check and API are both reading stale data (same millisecond snapshot)
- TP fill hasn't propagated to position API yet
- Repair assumes "missing quantity = phantom position" without checking if it's a real fill in flight

---

### 2. **Stop Loss Fill During Sync (Opposite Direction)**

**Scenario:**
```
T0:   Virtual tracker has: S1:+2 ES (long)
      SL order at 6400 (opposite side = sell 2)

T1:   Price drops to 6399
      SL fills on exchange (2 contracts sold)
      Exchange now shows: 0 (for S1)
      Virtual tracker still shows: +2 (assumed fill hasn't happened yet)

T2:   Sync detects:
      Exchange: 0
      Virtual: +2 (S1) + 1 (S2) = +3
      Delta: -3 contracts missing

T3:   Repair fires:
      "We show +3 virtually but exchange shows 0, zero all virtual positions"
      Zeros S1:+2 AND S2:+1

T4:   S2's position was NEVER on exchange (only virtual)
      Now S1's exit fill is untracked
      S2 has phantom zero qty position
```

**What Goes Wrong:**
- S1: Legitimate exit order (SL fill) is invisible to position tracker
- S1: Re-entry signal fires but tracker thinks position is flat (may double-enter)
- S2: Legitimate position becomes a "repaired" phantom (quantity=0 forced)
- Total: Both strategies lose position history + P&L tracking breaks

**Why It Happens:**
- Repair logic is **aggregate-level** (total exchange vs total virtual)
- Cannot distinguish which virtual position corresponds to which exchange order
- SL fill = position reduction (legitimate), but repair sees it as "missing qty"

---

### 3. **Rapid Multiple Fills (Sub-Second)**

**Scenario:**
```
T0:   BracketOrderManager polls bracket state
      SL order: status=OPEN (1), TP order: status=OPEN (1)

T0+50ms: Market moves fast, both SL AND TP fill in same tick
         Exchange state: both orders now have status=FILLED (2)
         But two separate trades occurred (only one should have!)

T1:   Next poll reads:
      SL: status=FILLED, TP: status=FILLED
      Manager thinks: "one must have filled, cancel the other"
      Tries to cancel TP...but it's already FILLED (errorCode 5)

T2:   Virtual tracker processes TWO exit fills
      First: SL fill (sell 1)
      Second: TP fill (sell 1 again?! Double exit)

T3:   Position becomes -1 ES (short when it should be flat)
      Next iteration: Repair sees virtual=-1, exchange=0
      Zeros the short position (legitimate loss not captured)
```

**What Goes Wrong:**
- BracketOrderManager's poll granularity misses simultaneous fills
- Virtual tracker processes multiple exit fills for same position
- Position goes to wrong side (short instead of flat)
- P&L calculation is catastrophically wrong

**Why It Happens:**
- Polling inherent latency (can't capture atomic market state)
- No atomic transaction semantics in API
- Virtual tracker doesn't validate that exits are <= entry quantity

---

### 4. **Stale Position API Data (Millisecond Old)**

**Scenario:**
```
T0:   Order fills on exchange
      Actual exchange state: ES +1 (from entry order)
      Position API response time: 15ms (broker's internal lag)

T1:   Sync check calls position_search_open()
      Exchange API returns: stale snapshot from T0-15ms
      API response shows: ES 0 (position not yet recorded)

T2:   Sync sees: exchange=0, virtual=+1
      Repair zeros virtual position

T3:   10ms later, position API catches up
      Exchange would show: +1
      But virtual is already zeroed

T4:   Next trade fires (signal confirms)
      Virtual thinks position is flat
      Places new entry order
      Exchange receives: another buy (now has +2)
      Virtual thinks: +1
      DIVERGENCE: exchange=+2, virtual=+1
```

**What Goes Wrong:**
- Repair act on stale API data (all APIs have lag!)
- Legitimate entry position gets zeroed before it's confirmed
- Double-entry on next signal
- Position divergence grows exponentially

**Why It Happens:**
- APIs are eventually consistent, not instantly consistent
- Sync timing doesn't account for API internal latencies
- No deduplication between repair and legitimate entry

---

### 5. **"In-Flight" Order Not Yet Confirmed**

**Scenario:**
```
T0:   Strategy places market order: buy 1 ES
      Order submitted to API
      Virtual tracker immediately updates: +1 ES
      (assumes market order fills instantly)

T0+5ms: Order acknowledged by API: order_id=999
        But fill status not yet available

T1:   Sync check (before fill propagates)
      Exchange position API: still shows 0
      (fill is queued, not yet in position snapshot)

T2:   Sync logic sees:
      Virtual: +1 (from assumed-filled entry)
      Exchange: 0 (fill not yet visible)
      Delta: -1 (positions diverge)

T3:   Repair zeros virtual position
      But order IS actually filling! Just not visible yet

T4:   Fill arrives at exchange
      Exchange now shows: +1
      Virtual shows: 0 (was repaired)
      PHANTOM SHORT: now virtual is -1 when zeroing
      OR positions tracked separately as phantom and real
```

**What Goes Wrong:**
- Repair acts before order is confirmed filled
- Virtual position is zeroed but order is legitimately filling
- Creates a true desync (not phantom)
- Next position update creates divergence

**Why It Happens:**
- Virtual tracker assumes instant fill (very aggressive assumption)
- Sync logic doesn't distinguish "in-flight" from "completed"
- No handshake between order placement and position confirmation

---

### 6. **Race: Repair Zeroing vs Real Exit Arriving**

**Scenario:**
```
Timeline (interleaved):
T0:   SL order fills on exchange (legitimate exit)
      Market: fill processed, position reduced
      Order status: FILLED

T0+2ms: Sync check starts
         Reads aggregate: virtual=+1 (stale, hasn't processed exit yet)
         Reads exchange position: already shows 0 (fill visible)

T0+4ms: Repair logic fires
         "Exchange is 0 but virtual is +1, phantom detected"
         Calls force_flat() on virtual tracker
         Sets: quantity=0, total_cost=0, last_update=now()

T0+6ms: BracketOrderManager.poll_and_cleanup() runs
         Detects SL fill in order status
         Tries to process fill: execute_order("sell", 1)
         But virtual position is already 0!
         Order processing FAILS or updates position backwards

T0+8ms: Virtual position is corrupted
         Entry-time lost
         Average price lost
         P&L calculation invalid
```

**What Goes Wrong:**
- Repair happens BETWEEN SL detecting fill and order processing
- Order processor finds position=0, can't process exit properly
- If idempotency check sees order_id already processed: double-counts
- If idempotency check skips: exit fill is invisible
- P&L becomes arbitrary (no entry price to calculate against)

**Why It Happens:**
- Repair and exit fill processing are unsynchronized
- Two separate systems (repair logic, bracket manager) operate independently
- No ordering guarantee between force_flat() and execute_order()

---

### 7. **Position on "Wrong" Strategy After Aggregation**

**Scenario:**
```
Three strategies on ES:
- ES_1M_01: virtual=+1, symbol=ES
- ES_1M_02: virtual=+1, symbol=ES
- ES_1M_03: virtual=+1, symbol=ES
- Total virtual net: +3 ES

Exchange reports: +2 ES (one fill hasn't propagated yet)
Repair detects: virtual=+3, exchange=+2, delta=-1

Repair decision: "One position is phantom"
But WHICH ONE?

Option A: Zero ES_1M_01
  Result: ES_1M_01 loses legitimate position

Option B: Zero ES_1M_03
  Result: ES_1M_03 loses legitimate position

Currently: Repair zeros ALL (+3 total) if delta > tolerance
  Result: All three lose positions

Then later, actual orders fill:
  - ES_1M_02's order confirms fills
  - Exchange now shows: +2 (from 2 of the strategies)
  - Virtual shows: +1 (ES_1M_02 recovered, others still zeroed)
  - STILL DIVERGED: now virtual=1, exchange=2
```

**What Goes Wrong:**
- Repair can't distinguish which virtual position corresponds to exchange position
- Repair may zero legitimate positions from innocent strategies
- Creates worse divergence than it fixes
- Innocent strategies lose trading history

**Why It Happens:**
- Repair works at symbol-level aggregation (loses strategy identity)
- No mapping between individual strategy positions and exchange order fills
- Multiple strategies sharing symbol = ambiguous ownership of fills

---

### 8. **Repair Loop: Repair Triggers Cascading Repairs**

**Scenario:**
```
T0:   Initial repair fires (legitimate scenario from edge case #4)
      Zeros S1's position: was +1, now 0
      Sets: virtual_qty=0

T1:   Real fill arrives: S1's order_id is processed
      Tries execute_order("buy", 1, order_id=S1_entry)
      Idempotency check: have we seen this order_id before?

      If YES (already processed): skips update, returns existing position
      If NO: updates virtual to +1

T2:   Another sync check happens
      Virtual: 0 (because execute_order was skipped or force_flat won)
      Exchange: +1 (fill arrived)
      Delta: +1 (opposite direction than before!)

      Repair: "Virtual is UNDER-reporting, this shouldn't happen"
      New repair logic: ??? (depends on repair implementation)

T3:   If repair tries to fix upward:
      Artificially increments virtual to +1
      Later, duplicate entry order lands
      Virtual: +2 (one real, one phantom)
      Exchange: +1
      Back to divergence
```

**What Goes Wrong:**
- Repair logic doesn't account for future fill arrival
- Can create oscillating divergences
- Repair may trigger in opposite direction (adding instead of removing)
- System becomes unreliable; repair can make it worse

**Why It Happens:**
- Repair doesn't have "context" about orders in-flight
- Idempotency checks in execute_order() may defer updates
- Multiple repair strategies (zero vs add) can conflict

---

### 9. **Order Cancellation Race During Sync**

**Scenario:**
```
T0:   SL order is registered with BracketOrderManager
      manager.register_bracket("ES_LONG_1", sl_order_id=101, tp_order_id=102)

T1:   Sync check runs
      Sees divergence (hypothetically from edge case #4)
      Calls repair to zero virtual position

T2:   Repair zeros S1's virtual position
      force_flat(): quantity=0

T3:   BracketOrderManager.poll_and_cleanup() runs
      Polls order 101 (SL): status=FILLED
      Polls order 102 (TP): status=OPEN
      Detects SL filled, tries to cancel TP

      API call: order_cancel(102)
      Success: TP cancelled

T4:   Fill processor runs
      Processes SL fill: execute_order("sell", 1, order_id=101)
      Virtual position: quantity is 0 (was repaired)
      Can't process negative exit on flat position

      Execution fails OR:
      Creates short position: quantity=-1 (opposite of intent)
```

**What Goes Wrong:**
- Repair severs link between entry position and exit orders
- Order cancellation succeeds but virtual position is already zeroed
- Exit fill creates wrong-side position
- TP order cancelled uselessly (SL already filled anyway, but processor doesn't know)

**Why It Happens:**
- Repair doesn't coordinate with bracket manager
- Bracket manager doesn't check if entry position still exists
- No transaction boundary around entry + exit

---

### 10. **Concurrent Access: Repair Zeroing While Exit Fills Processing**

**Scenario:**
```
Thread/Coroutine A (Sync + Repair):
  T0:   reconcile_positions() running
  T1:   Reads virtual tracker state: +1 ES
  T2:   Reads exchange position: 0 ES
  T3:   Decides: zero virtual position
  T4:   Calls force_flat(symbol="ES")
        [ACQUIRE LOCK - write to tracker.positions["ES"]]
        Sets: quantity=0, last_update=now()
        [RELEASE LOCK]

Thread/Coroutine B (BracketOrderManager):
  T0:   poll_and_cleanup() running
  T1:   Detects SL order filled
  T2:   Calls tracker.execute_order("sell", 1, order_id=SL_ID)
        [ACQUIRE LOCK - write to tracker.positions["ES"]]
        Entry qty: 1
        New qty: 1 - 1 = 0
        [RELEASE LOCK]

Interleaving (RACE):
  A-T4:  force_flat() starts: quantity=0
  B-T2:  execute_order() starts reading: quantity=1
  A-T4:  force_flat() completes: quantity=0
  B-T2:  execute_order() does math: 0 - 1 = -1 (SHORT!)

Result: Position is -1 (short) instead of 0 (flat)
        Then next repair sees: virtual=-1, exchange=0
        Zeros negative position
        Creates corrupted state
```

**What Goes Wrong:**
- Race condition between repair and fill processing
- Position math corrupted by concurrent write
- Creates wrong-side position
- Subsequent repair on corrupted state

**Why It Happens:**
- VirtualPositionTracker.force_flat() and execute_order() share mutable state
- No atomicity guarantee across position read → repair → execute
- Multiple systems write to same positions dict

---

## Summary Table: Impact & Severity

| Edge Case | Trigger | Impact | Severity |
|-----------|---------|--------|----------|
| Bracket fill during sync | TP/SL fills while sync checks | Lost P&L, lost trade history | HIGH |
| Stop loss during sync | SL executes while syncing | Double-entry, phantom zero | CRITICAL |
| Rapid multiple fills | Sub-second fills | Position inverted (short instead of flat) | CRITICAL |
| Stale API data | Broker lag in position API | Double-entry, cascading divergence | HIGH |
| In-flight order not confirmed | Assumption of instant fill | Premature repair of valid order | CRITICAL |
| Repair vs real exit race | Repair between detection and processing | Exit fills untracked, wrong-side position | CRITICAL |
| Position attribution loss | Multiple strategies on same symbol | Wrong strategy zeroed, repair overzealous | HIGH |
| Repair loop cascading | Opposite-direction divergence after repair | Oscillating repairs, unreliable state | HIGH |
| Order cancellation race | Repair severs entry-exit link | TP cancelled when SL fills, exit wrong-side | HIGH |
| Concurrent access race | Threads write simultaneously | Position math corrupted, wrong-side position | CRITICAL |

---

## Recommended Solutions (Conceptual)

### A. **Idempotency Window with Pending Orders**
- Track orders in "pending" state for 1-2 seconds after placement
- Exclude pending orders from sync checks
- Repair only on confirmed discrepancies (exchange filled, not pending)

### B. **Strategy-Level Repair (Not Aggregate)**
- Track which strategy owns which virtual position
- When divergence detected, repair only the oldest or most suspicious position
- Log repaired strategy ID for audit

### C. **Atomic Transaction Boundaries**
- Wrap entry + bracket placement in atomic unit
- Wrap repair + position update in atomic unit
- No concurrent writes during critical sections

### D. **Bidirectional Fill Confirmation**
- Don't trust virtual tracker alone
- Poll actual order fills and confirm against virtual
- Only when both agree on count, proceed

### E. **Conservative Repair Policy**
- Repair only when divergence > some threshold (2+ contracts)
- Never repair in direction of growth (add virtual)
- Log ALL repairs with context for debugging

### F. **Sync Cooldown**
- Don't sync within X seconds of large order batch
- Wait for fills to propagate through API
- Accept temporary divergences as normal

---

## Test Scenarios to Implement

1. **Timing Injection**: Place SL/TP fills at exact sync moments
2. **API Latency Simulation**: Delay position API responses by 50-100ms
3. **Concurrent Order Simulation**: Fire multiple fills simultaneously
4. **Stale Data Injection**: Return old snapshots from position API
5. **In-Flight Order Test**: Sync before order confirmation arrives
6. **Race Condition Test**: Run sync + fill processing in parallel threads
7. **Position Attribution Test**: Multiple strategies on same symbol with divergence
8. **Cascading Repair Test**: Let repair trigger another sync cycle

