# Edge Cases: Position Sync/Repair Logic - Recovery & Restart Scenarios

**Focus**: What happens when the bot restarts with existing exchange positions, corrupted state files, or stale order registries?

---

## SCENARIO 1: Bot Restart with Live Exchange Positions (Cold Start Problem)

### The Situation
- Bot crashes/restarts unexpectedly
- Exchange still has 10 ES contracts long (from Strategy_ES_1M_03)
- Virtual position tracker file is **missing or deleted**
- Order registry has no record of the fills

### What Could Go Wrong

#### 1.1 Virtual Position Tracker is Empty
**Problem**: Bot initializes with zero virtual positions, but exchange shows 10 ES long.
- Bot sees: `virtual_qty = 0`
- Exchange shows: `exchange_qty = 10`
- **Result**: Sync check triggers `MISMATCH DETECTED` → `FLATTEN_SYMBOL`
- **Impact**: Bot force-closes the 10 ES position immediately, locking in any unrealized loss

**Example**:
```
[PositionSync] MISMATCH: Exchange shows 10 but virtual tracker shows 0 for ES.
Difference: 10
[NUCLEAR] Flattening all exposure for ES
[NUCLEAR] Closing exchange position: SELL 10 ES, tag=NUCLEAR_ES_1732556789123_ABC123
```

**Hidden Issue**: The closed position might have had 2.5 points of profit! But the bot doesn't know and liquidates.

---

#### 1.2 Virtual Position Tracker File Exists but is Corrupted
**Problem**: State file exists but JSON is malformed or partially written.
- File path: `./lumibot_state/virtual_positions_ES.json`
- File content: `{"ES": {"quantity": 5, "avg_entry_price"` ← **TRUNCATED**

**What Happens**:
1. Loader tries `json.load()` → `JSONDecodeError`
2. Exception handling: `except Exception: positions.clear()`
3. Bot thinks positions are flat, but exchange has 10 ES
4. **Result**: Same as 1.1 → FLATTEN_SYMBOL triggers

**Code Path** (vulnerable):
```python
try:
    with open(state_file) as f:
        data = json.load(f)  # ← FAILS if file is corrupt
        # Load positions...
except Exception:
    logger.warning(f"Failed to load {state_file}, starting fresh")
    # positions remain empty → virtual_qty = 0
```

**Why This Matters**: If the state file was auto-saved during a crash mid-write, it's unrecoverable without a backup.

---

#### 1.3 Partial Recovery: Tracker File Exists But Missing Old Position Entries
**Problem**: Virtual tracker has some positions but not all.
- Tracker shows: ES = 5 long (Strategy_ES_1M_01)
- Exchange shows: ES = 10 long (strategies 01, 02, 03 combined)
- Missing: 5 ES from strategy_03 (log was flushed before save)

**What Happens**:
```
Exchange qty: 10
Virtual qty: 5 (only 01 + 02)
Mismatch: 5 contracts
→ FLATTEN_SYMBOL triggered
```

**The Trap**: Strategy_ES_1M_03's position is orphaned, and bot force-flattens everything, erasing the orphaned 5 ES without any record.

---

## SCENARIO 2: Order Registry is Stale or Missing

### The Situation
- Virtual position tracker says: ES = 5 long
- Order registry is **completely empty** (file deleted or lost)
- No records of which orders filled, which are pending

### What Could Go Wrong

#### 2.1 Bracket Orders Are Orphaned
**Problem**: Stop loss and take profit orders exist on exchange but registry has no record.
- Exchange has: SL order (id=999) OPEN, TP order (id=1000) OPEN
- Registry shows: NO ORDERS
- Virtual position: 5 ES long

**What Happens**:
1. Bot doesn't know about SL/TP orders
2. Sync check passes (5 ES = 5 ES)
3. **But**: New signal comes in, bot tries to add 2 more ES
4. Bot submits 2 ES BUY → position becomes 7 ES
5. **Meanwhile**: SL order fills at some point, closing 5 ES
6. **Result**: Bot thinks it has 7 ES, exchange has 2 ES
7. **Next sync check**: 7 != 2 → FLATTEN_SYMBOL

**The Insidious Part**: The SL order was supposed to auto-close the position. By losing the registry, bot didn't track it and created a mismatch later.

---

#### 2.2 Pending Entry Orders Don't Get Retried
**Problem**: Order registry is empty, but exchange has a PENDING entry order (status=1).
- Exchange has: Entry order (id=2000) OPEN, custom_tag="ENT_ES_1M_01_1732556700000_XYZ"
- Registry shows: NOTHING
- Strategy signal: "BUY" (trying to enter again)

**What Happens**:
1. `can_open_position()` check: registry.has_pending_entry() → False (registry is empty)
2. Bot doesn't know pending order exists
3. Bot submits a SECOND entry order for same strategy
4. **Result**: 2 entry orders on exchange, position could fill twice as big
5. **When both fill**: Position = 2x intended size → margin issues

**Code Path** (assumes registry is source of truth):
```python
if registry.has_pending_entry(strategy_id):
    # Skip submitting new entry
else:
    # PROCEED - but this is WRONG if registry lost state!
    submit_entry_order()
```

---

## SCENARIO 3: Mixed State - Tracker OK, Registry Corrupt

### The Situation
- Virtual position tracker is intact: ES = 8 long (across strategies)
- Order registry corrupted or lost
- Exchange shows: ES = 8 long, with 2 PENDING SL/TP orders

### What Could Go Wrong

#### 3.1 Bracket Orders Get Duplicated on Retry
**Problem**: Bot doesn't know which brackets already exist, submits them again.
- Exchange already has: TP order at 4780 (id=888)
- Tracker shows: entry_price=4760, pending_tp_price=4780
- Registry: MISSING

**What Happens**:
1. On restart, strategy state has `pending_tp_price=4780`
2. Bot checks: "Do we have a pending TP?"
3. Registry check fails (empty) → assumes NO pending TP
4. Bot resubmits TP order at 4780
5. **Now Exchange has**: 2 TP orders at 4780
6. **When price hits 4780**: BOTH TP orders could fill (race condition)

**The Catastrophic Outcome**:
- Exchange takes first fill at 4780, position closes 4 ES
- Second fill attempts to close 4 more ES, but only 4 left
- Partial fill for 4 ES → position = 0 (correct by accident)
- But P&L is recorded twice in some systems

---

#### 3.2 Bracket Tag Collision
**Problem**: Bot regenerates bracket order tags that collide with existing orders.
- Old bracket: `TP_ES_1M_01_1732556700000_ABC123` → filled
- On restart, generate new tag: `TP_ES_1M_01_1732556700000_ABC123` ← **SAME TAG!**

**Why This Happens**: If tag generation is based only on timestamp (and not UUID or nonce).

**What Happens**:
1. CustomTag collision on exchange API
2. Exchange rejects: "Duplicate custom tag"
3. Or exchange silently ignores the new order

---

## SCENARIO 4: Exchange Positions from Previous Session (Didn't Close Yesterday)

### The Situation
- Yesterday: Bot crashed, didn't flatten positions
- Positions carried over: 5 ES long, 3 GC short
- Today: Bot restarts with fresh virtual tracker

### What Could Go Wrong

#### 4.1 Wrong Symbol State
**Problem**: Virtual tracker initializes all symbols to FLAT. But exchange has legacy positions in symbols that current strategy set doesn't trade.
- Exchange position: 2 YM long (from archived strategy)
- Current strategies: ES, NQ, GC only (no YM)
- Virtual tracker: no YM entry

**What Happens**:
1. Sync check sees: exchange_qty=2, virtual_qty=0 (YM not tracked)
2. **Result**: FLATTEN_SYMBOL for YM
3. **Impact**: Closes orphaned 2 YM unexpectedly

**Why It Matters**: If YM was in profit, or had pending orders, bot just nukes it without warning.

---

#### 4.2 Position Size Mismatch from Leverage Change
**Problem**: Yesterday bot used 2x leverage on ES (20 contracts). Today it changed to 1x (10 contracts).
- Exchange still shows: 20 ES
- Virtual tracker expects: 10 ES
- Mismatch: 20 != 10 → FLATTEN_SYMBOL

**What Happens**:
1. Bot force-closes all 20 ES
2. Realizes the leverage was intentionally increased yesterday
3. **Result**: 10 contracts worth of loss locked in unnecessarily

---

## SCENARIO 5: Order Registry Timestamp/UUID Collision Risk

### The Situation
- Bot restart happens within same millisecond as previous submission
- UUID generation isn't truly random (bad seed, predictable RNG)

### What Could Go Wrong

#### 5.1 Tag Format: `ENT_ES_1M_01_1732556789123_ABC123`
**Problem**: If UUID generation uses time-based seeding, restarting at same millisecond could produce same UUID.

**Example**:
- Order 1 (crashed run): `ENT_ES_1M_01_1732556789123_ABC123` → filled
- Order 2 (restart): `ENT_ES_1M_01_1732556789123_ABC123` ← **COLLISION!**

**What Happens**:
1. Exchange sees duplicate tag
2. Either rejects new order, or updates existing record
3. Registry gets confused about which fill is which

**Code Path** (vulnerable if UUID source is weak):
```python
uuid_suffix = uuid.uuid4().hex[:6].upper()  # ← If reseeded with time, could repeat
```

---

## SCENARIO 6: Cascading Flattens (Nuclear Option Gone Wrong)

### The Situation
- Position mismatch detected for ES
- FLATTEN_SYMBOL triggered
- But the close order fails or hangs

### What Could Go Wrong

#### 6.1 Close Order Submission Fails, But Virtual Position Already Marked Flat
**Problem**: Nuclear flatten has race condition between cancelling brackets and closing position.
```python
# Step 1: Cancel pending brackets (succeeds)
registry.register_cancellation(order_id)

# Step 2: Get exchange position (succeeds, shows 5 ES)
exchange_qty = 5

# Step 3: Submit close order (FAILS - network error, invalid price)
result = client.order_place(...)
if not result.get("success"):
    logger.error("Failed to close")
    # But we already marked brackets as cancelled!

# Step 4: Mark virtual positions closed (RUNS ANYWAY)
tracker.close_position(strategy_id)  # ← EXECUTED EVEN THOUGH CLOSE FAILED
```

**The Trap**: Virtual tracker says position is closed, but exchange still has 5 ES. Next iteration:
- Virtual: 0 ES
- Exchange: 5 ES
- **Result**: FLATTEN_SYMBOL triggers AGAIN (recursive flatten loop)

---

#### 6.2 Multiple Symbols Getting Flattened Simultaneously
**Problem**: Portfolio has sync mismatches on ES, NQ, and GC at the same time. Bot attempts to flatten all three.
- ES: submitting SELL 10
- NQ: submitting SELL 5
- GC: submitting SELL 3

**What Happens**:
1. Orders submitted rapidly (0.15s apart)
2. If one fails, others might still execute
3. **Result**: Partial flattens create new mismatches
4. **Cascading**: Next sync check finds ES flat but NQ still open
5. **Outcome**: Uncontrolled liquidation of portfolio

---

## SCENARIO 7: Virtual Position File Partially Corrupted (Strategy-Specific)

### The Situation
- State file contains multiple strategies: ES_1M_01, ES_1M_02, ES_1M_03
- File corrupted after ES_1M_02 entry

### Example Corrupted File
```json
{
  "ES_1M_01": {"symbol": "ES", "quantity": 2, "avg_entry_price": 4750.25},
  "ES_1M_02": {"symbol": "ES", "quantity": 3, "avg_entry_price": 4755.00},
  "ES_1M_03": {"symbol": "ES", "quantity":
  // ← FILE ENDS HERE, TRUNCATED
```

**What Happens**:
1. JSON parse fails on entire file
2. **All** positions lost (not just ES_1M_03)
3. Bot thinks all strategies are flat
4. Exchange has 8 ES (2+3+3)
5. **Result**: FLATTEN_SYMBOL → closes all 8 ES

**The Tragedy**: ES_1M_01 and ES_1M_02 had valid data, but lost due to cascade failure.

---

## SCENARIO 8: Time-Based Order Abandonment

### The Situation
- Order was submitted at 14:55 (5 minutes before session close)
- Order still PENDING at 15:00 when session closes
- Bot crashes, doesn't get to force-close

### Next Day Startup
- Order still exists on exchange from yesterday
- Virtual tracker is fresh (no position record)
- Order was for "ES SELL 5 @ limit 4750"

**What Happens**:
1. Sync check: virtual=0, exchange=0 (position already flat)
2. Sync passes ✓
3. **But**: Old limit order still exists and could fill during today's open
4. If it fills: Position goes SHORT 5 ES
5. Bot has no knowledge of the fill
6. **Next sync check**: virtual=0, exchange=-5 → MISMATCH → FLATTEN

**The Problem**: Orphaned orders from previous session aren't cleaned up during startup.

---

## SCENARIO 9: Strategy Configuration Changes Between Restarts

### The Situation
- Yesterday: Portfolio ran 10 ES strategies
- Today: Configuration changed to 5 ES strategies
- Exchange still has positions from all 10 strategies

### What Happens
1. Bot loads new config (5 strategies)
2. Virtual tracker: 5 strategies initialized
3. Sync check aggregates virtual for ES: 25 contracts (5 strategies × 5 each)
4. Exchange shows: 50 contracts (legacy 10 strategies × 5 each)
5. **Result**: MISMATCH detected (25 != 50)
6. **Outcome**: FLATTEN_SYMBOL → closes ALL 50 ES

**The Insidious Part**: Bot didn't know about the 5 archived strategies still holding positions.

---

## SCENARIO 10: Inconsistent State Between Tracker and Registry

### The Situation
- Virtual tracker file has been manually edited (e.g., for recovery)
- Order registry saved separately
- They disagree about current position

### Example
- **Tracker says**: ES = 0 (manually set to flat)
- **Registry shows**: 1 entry order PENDING for ES
- **Exchange shows**: ES = 5 (position got filled, but registry wasn't updated)

**What Happens**:
1. Sync check: virtual=0, exchange=5 → MISMATCH
2. Bot flattens 5 ES (sells)
3. **But**: Registry still thinks entry order is pending
4. When close order fills, registry tries to match it to the orphaned entry order
5. **Result**: Incorrect P&L attribution, position state becomes nonsensical

---

## SUMMARY: Critical Edge Cases Table

| Scenario | Problem | Trigger | Risk Level |
|----------|---------|---------|-----------|
| **1.1** | Virtual tracker empty, exchange has positions | Sync mismatch | CRITICAL |
| **1.2** | Corrupted state file | JSON parse failure | HIGH |
| **1.3** | Partial state recovery | Missing entries | HIGH |
| **2.1** | Orphaned bracket orders | Lost registry | CRITICAL |
| **2.2** | Duplicate entry orders | Missing registry checks | HIGH |
| **3.1** | Duplicate bracket resubmission | Registry loss + tracker intact | HIGH |
| **3.2** | Tag collisions | Weak UUID generation | MEDIUM |
| **4.1** | Legacy symbol positions | Stale exchange state | MEDIUM |
| **4.2** | Leverage mismatch | Config changes | HIGH |
| **5.1** | Tag collision on restart | Weak RNG | MEDIUM |
| **6.1** | Close order failure → infinite loop | Race condition | CRITICAL |
| **6.2** | Cascading flattens | Multiple mismatches | CRITICAL |
| **7** | Cascade corruption | Partial file truncation | HIGH |
| **8** | Orphaned orders from previous session | Session boundary | MEDIUM |
| **9** | Archived strategies still holding | Config drift | HIGH |
| **10** | Tracker/registry inconsistency | Manual edits | MEDIUM |

---

## Recommendations for Hardening Recovery Logic

### 1. State File Redundancy
```python
# Keep 3 backups: current, previous, archive
save_state(positions, "virtual_positions_current.json")
rotate_backup("virtual_positions_current.json",
               "virtual_positions_previous.json",
               "virtual_positions_archive.json")
```

### 2. Atomic Writes
```python
# Write to temp file, then atomic rename
with open(state_file + ".tmp", "w") as f:
    json.dump(positions, f)
os.rename(state_file + ".tmp", state_file)  # atomic
```

### 3. Checksum Validation
```python
# Include checksum in state file
state = {
    "positions": {...},
    "checksum": hashlib.sha256(json.dumps(...)).hexdigest()
}
```

### 4. Cold Start Detection
```python
# If both tracker and registry are empty, check exchange
# If exchange has positions and local state is empty, require manual intervention
if virtual_qty == 0 and registry_empty and exchange_qty > 0:
    logger.critical(f"COLD START ANOMALY: Exchange has {exchange_qty} {symbol} "
                    f"but local state is empty. REFUSING TO PROCEED.")
    # Require manual review before auto-flattening
```

### 5. Atomic Order Submission
```python
# Don't mark position as closed until close order CONFIRMED filled
# Not just submitted
if close_order.status == FILLED:
    tracker.force_flat(symbol)
else:
    logger.warning(f"Close order pending, not marking virtual position flat yet")
```

### 6. Orphaned Order Cleanup
```python
# On startup, scan all pending orders
# If they're older than session boundary, flag for review
for order in exchange.pending_orders():
    age = datetime.now() - order.created_at
    if age > timedelta(hours=24):
        logger.warning(f"ORPHANED ORDER: {order.tag}, age={age}")
        # Ask human before cancelling
```

### 7. State Reconciliation Mode
```python
# Optional "reconcile" mode that:
# 1. Reads exchange positions
# 2. Reads pending orders
# 3. Validates against tracker/registry
# 4. Reports discrepancies
# 5. Doesn't trade until discrepancies resolved
```

### 8. Tag Collision Detection
```python
# Query exchange for ALL orders with our tag pattern
# Deduplicate in-flight tags before generating new ones
active_tags = set(order.tag for order in exchange.order_search())
new_tag = generate_unique_tag(...)
while new_tag in active_tags:
    new_tag = generate_unique_tag(...)  # retry with new UUID
```

---

## Testing Strategy

Create test cases for:
1. **Corrupted state file recovery** → ensure fallback behavior
2. **Empty registry with live positions** → verify correct sync check
3. **Partial state loss** → test individual strategy recovery
4. **Cascade flatten scenarios** → verify no infinite loops
5. **Orphaned order cleanup** → test session boundary detection
6. **Tag collision prevention** → verify RNG stability

