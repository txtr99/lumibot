# Recovery & Restart: Test Scenarios and Validation Checklist

This document provides concrete test cases to validate recovery logic under edge cases.

---

## TEST CATEGORY 1: Cold Start with Existing Positions

### Test 1.1: Exchange Has Positions, Virtual Tracker Empty

**Setup**:
```python
# Exchange state
exchange_positions = [
    {"contractId": "CON.F.US.EP.Z25", "size": 10, "type": 1}  # 10 ES long
]

# Bot state (fresh start)
virtual_tracker = VirtualPositionTracker()  # empty
order_registry = OrderRegistry()  # empty
```

**Execution**:
1. Bot starts
2. Calls `position_search_open()` → returns 10 ES
3. Calls `check_position_sync()` with empty virtual tracker

**Expected Behavior**:
- Sync result: `is_synced=False, action_needed="FLATTEN_SYMBOL"`
- Bot triggers `flatten_symbol()`
- Closes all 10 ES

**Validation**:
```python
# Check that:
assert sync_result.is_synced == False
assert sync_result.exchange_qty == 10
assert sync_result.virtual_qty == 0
assert sync_result.action_needed == "FLATTEN_SYMBOL"

# Check that FLATTEN attempted
assert flatten_called == True
assert close_order_submitted == True
assert close_order_qty == 10
assert close_order_side == "SELL"
```

**Risk to Test**: **CRITICAL** - This is the most common restart scenario.

---

### Test 1.2: Exchange Has Positions, Virtual Tracker Has Different Positions

**Setup**:
```python
# Exchange state
exchange_positions = [
    {"contractId": "CON.F.US.EP.Z25", "size": 10, "type": 1}  # 10 ES long
]

# Bot state (from previous run)
virtual_tracker.execute_order("ES", 5, "buy", 4750.0)  # 5 ES from one strategy
# (Missing 5 ES from another strategy that crashed)
```

**Execution**:
1. Bot starts with stale virtual position (5 ES)
2. Sync check: virtual=5, exchange=10
3. Mismatch detected

**Expected Behavior**:
- Bot should NOT assume partial positions are correct
- Flatten ALL 10 (not just the 5 it knows about)

**Validation**:
```python
assert sync_result.exchange_qty == 10
assert sync_result.virtual_qty == 5
assert sync_result.is_synced == False

# Check that close amount is exchange_qty, not virtual_qty
assert close_order_qty == 10  # NOT 5!
```

---

## TEST CATEGORY 2: Corrupted State Files

### Test 2.1: Virtual Tracker State File Truncated

**Setup**:
```python
# State file content (truncated):
# {"ES": {"symbol": "ES", "quantity": 5, "avg_entry_price": 4750,
# ← ENDS HERE, missing closing braces
```

**Execution**:
1. Bot loads `virtual_positions_ES.json`
2. `json.load()` raises `JSONDecodeError`
3. Exception handler should clear and start fresh

**Expected Behavior**:
- Parser catches exception, logs warning
- Tracker falls back to empty state
- Sync check: virtual=0, exchange=X → triggers flatten if X > 0

**Validation**:
```python
try:
    tracker = load_virtual_positions("corrupted_file.json")
except JSONDecodeError as e:
    logger.warning(f"Corrupted state file: {e}")
    tracker = VirtualPositionTracker()  # Fresh start

# Check that tracker is empty
assert len(tracker.positions) == 0
```

---

### Test 2.2: Order Registry Backup Fallback

**Setup**:
```python
# Primary registry file is corrupt or missing
# Backup exists from 10 minutes ago

# Primary: OrderRegistry_current.json (CORRUPT or MISSING)
# Backup1: OrderRegistry_previous.json (VALID, 10 min old)
# Backup2: OrderRegistry_archive.json (VALID, 1 hour old)
```

**Execution**:
1. Bot tries to load primary registry
2. Fails (missing or corrupt)
3. Falls back to backup1 (10 min old)

**Expected Behavior**:
- Registry loads from backup1
- Logs warning about age of backup
- Proceed with stale (but valid) registry state

**Validation**:
```python
# Try primary, if fails try backups
registry = None
for filepath in [primary, backup1, backup2]:
    try:
        registry = load_registry(filepath)
        logger.info(f"Loaded registry from {filepath}")
        break
    except Exception as e:
        logger.warning(f"Failed to load {filepath}: {e}")

assert registry is not None
assert len(registry.orders) > 0
```

---

## TEST CATEGORY 3: Orphaned Orders

### Test 3.1: Pending SL/TP Orders After Restart

**Setup**:
```python
# Day 1: Entry fills
# ES_1M_01 enters long at 4750
# TP order submitted: 4770, order_id=888
# SL order submitted: 4730, order_id=889

# Day 2: Bot crashes before saving state

# Restart Day 2
# Virtual tracker: ES = 1 long @ 4750 (loaded from file)
# Registry: EMPTY (lost during crash)
# Exchange: TP order 888 OPEN, SL order 889 OPEN
```

**Execution**:
1. Bot starts
2. Sync check: virtual=1, exchange=1 → SYNCED ✓
3. New signal: "BUY" for ES_1M_01 (trying to add more)
4. `can_open_position()` check → registry.has_pending_close() → **FALSE** (registry empty!)
5. Bot submits new entry order

**Expected Behavior** (BAD):
- Bot submits second entry order
- Now 2 TP + 2 SL orders on exchange
- Risk of double-closes or cascading fills

**Validation**:
```python
# Should catch that registry is stale
assert registry.has_pending_close("ES_1M_01") == False  # Bug!

# This leads to:
can_open = can_open_position("ES_1M_01", "ES", registry, tracker)
assert can_open == True  # WRONG! Should be False due to pending brackets

# Entry order gets submitted (WRONG)
assert len(new_orders) == 1
assert new_orders[0].purpose == OrderPurpose.ENTRY
```

**How to Fix** (pseudocode):
```python
def can_open_position_with_archaeology(strategy_id, symbol, registry, tracker, client, account_id):
    # First check local registry
    if registry.has_pending_entry(strategy_id):
        logger.debug(f"{strategy_id} has pending entry in registry")
        return False

    # If registry is empty/lost, check exchange directly
    if len(registry.orders) == 0:
        logger.warning(f"Registry is empty, checking exchange for orphaned orders")
        pending_orders = client.order_search(account_id, status=1)  # OPEN orders
        our_tags = [o["custom_tag"] for o in pending_orders if is_our_tag(o["custom_tag"])]

        for tag in our_tags:
            parsed = parse_tag(tag)
            if parsed["strategy_id"] == strategy_id and parsed["purpose"] == "ENT":
                logger.warning(f"Found orphaned entry order: {tag}")
                return False

    return True
```

---

### Test 3.2: Bracket Resubmission After Restart

**Setup**:
```python
# Day 1: Entry fills
# state.entry_time = 14:00:00
# state.entry_price = 4750.0
# state.brackets_submitted = True
# Exchange: TP order 888, SL order 889

# Day 2: Bot restarts
# state.entry_time = None (fresh instance)
# state.brackets_submitted = False (fresh instance)  ← BUG
# state.entry_price = None
# state.entry_side = None
```

**Execution**:
1. Bot detects position: ES = 1 long (from virtual tracker)
2. Checks: `if not state.brackets_submitted and position > 0:`
3. **TRUE** (because `brackets_submitted` was reset to False)
4. Bot resubmits TP + SL orders

**Expected Behavior** (BAD):
- New TP order created at 4770 (same price, different order_id)
- New SL order created at 4730 (same price, different order_id)
- Now exchange has: 2 TP orders, 2 SL orders

**Validation**:
```python
# Before restart
assert state.brackets_submitted == True
assert state.pending_tp == 4770
assert state.pending_sl == 4730

# After restart (simulated)
new_state = EnhancedStrategyState("ES_1M_01", config)
assert new_state.brackets_submitted == False  # ← RESET
assert new_state.pending_tp == None  # ← LOST

# This causes resubmission
if not new_state.brackets_submitted and position_qty > 0:
    submit_brackets()  # ← HAPPENS AGAIN
```

**How to Fix**:
```python
# Persist state to disk
class EnhancedStrategyState:
    def save_to_disk(self, filepath: str):
        state_dict = {
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_price": self.entry_price,
            "brackets_submitted": self.brackets_submitted,
            "pending_tp": self.pending_tp,
            "pending_sl": self.pending_sl,
        }
        save_atomic(filepath, state_dict)

    @classmethod
    def load_from_disk(cls, filepath: str, config):
        state_dict = load_atomic(filepath)
        instance = cls(**config)
        instance.entry_time = datetime.fromisoformat(state_dict["entry_time"]) if state_dict["entry_time"] else None
        instance.brackets_submitted = state_dict["brackets_submitted"]
        # ... restore other fields
        return instance
```

---

## TEST CATEGORY 4: Tag Collisions

### Test 4.1: Millisecond Collision Risk

**Setup**:
```python
# Mock time to return fixed millisecond
with mock.patch("time.time", return_value=1732556789.123):
    tag1 = registry.generate_unique_tag(OrderPurpose.ENTRY, "ES_1M_01")
    # Result: "ENT_ES_1M_01_1732556789123_ABC123"

    tag2 = registry.generate_unique_tag(OrderPurpose.ENTRY, "ES_1M_01")
    # Result: "ENT_ES_1M_01_1732556789123_XYZ789"
```

**Execution**:
1. Generate 1000 tags in rapid succession
2. Check for duplicates

**Expected Behavior**:
- All tags are unique (UUID prevents collision)
- No duplicates across 1000 iterations

**Validation**:
```python
tags = set()
for i in range(1000):
    tag = registry.generate_unique_tag(OrderPurpose.ENTRY, "ES_1M_01")
    assert tag not in tags, f"Collision detected: {tag}"
    tags.add(tag)

assert len(tags) == 1000
```

---

### Test 4.2: RNG Reseed on Restart

**Setup**:
```python
# Simulate weak RNG (reseeded with time)
import random

# Run 1
random.seed(int(time.time()))
uuid_run1 = uuid.uuid4()

# Immediate restart (same second)
random.seed(int(time.time()))
uuid_run2 = uuid.uuid4()

# If RNG is weak, could produce same UUIDs
```

**Execution**:
1. Simulate 10 rapid restarts within same second
2. Generate UUIDs each time
3. Check for collisions

**Expected Behavior**:
- All UUIDs are unique (Python's uuid.uuid4() is cryptographically random)
- No collisions even with weak system clock

**Validation**:
```python
uuids = set()
for restart_num in range(10):
    # Simulate restart
    uuid_val = uuid.uuid4()
    uuid_str = uuid_val.hex[:6].upper()

    assert uuid_str not in uuids, f"UUID collision on restart {restart_num}"
    uuids.add(uuid_str)
```

---

## TEST CATEGORY 5: Cascade Failures

### Test 5.1: NUCLEAR Flatten Retry Loop

**Setup**:
```python
# Sync mismatch detected
exchange_qty = 5
virtual_qty = 0

# First flatten attempt fails
attempt_1_close_submitted = True
attempt_1_close_status = "failed"  # Network error

# Retry
attempt_2_close_submitted = True
attempt_2_close_status = "failed"  # Still failing

# Retry again
attempt_3_close_submitted = True
attempt_3_close_status = "failed"  # Third time
```

**Execution**:
1. Sync mismatch triggers flatten
2. Close order fails
3. Next iteration, sync check still sees mismatch
4. Flatten triggered AGAIN
5. Creates retry loop

**Expected Behavior**:
- Bot should detect consecutive flatten retries
- Give up after N attempts (3-5)
- Escalate to manual intervention (don't keep retrying)

**Validation**:
```python
flatten_attempts = 0
max_attempts = 3

while exchange_qty > 0 and flatten_attempts < max_attempts:
    flatten_symbol(...)
    flatten_attempts += 1

    # Recheck
    exchange_qty = check_position_sync()

if flatten_attempts >= max_attempts:
    logger.critical(f"FLATTEN FAILED {max_attempts} times, escalating to manual review")
    # Send alert, don't retry
    return False
```

---

### Test 5.2: Multiple Symbols Cascade Flatten

**Setup**:
```python
# All symbols have mismatches
sync_results = {
    "ES": SyncResult(synced=False, action_needed="FLATTEN"),
    "NQ": SyncResult(synced=False, action_needed="FLATTEN"),
    "GC": SyncResult(synced=False, action_needed="FLATTEN"),
}

# Each flatten takes 2 seconds (submit order + wait)
# ES flatten fails at 1 second (network error)
```

**Execution**:
1. Bot starts flattening ES
2. ES close order submission fails
3. Meanwhile, NQ flatten is queued
4. When ES fails, bot retries immediately
5. Creates cascade of retries

**Expected Behavior**:
- Serialize flatten attempts (one at a time)
- If one fails, don't proceed to next until resolved
- Or flatten all in parallel with timeout guard

**Validation**:
```python
# Serialize approach
flatten_queue = ["ES", "NQ", "GC"]
for symbol in flatten_queue:
    result = flatten_symbol(symbol)
    if not result.success:
        logger.error(f"Failed to flatten {symbol}, stopping cascade")
        break  # Don't proceed to NQ and GC
```

---

## TEST CATEGORY 6: Session Boundary

### Test 6.1: Old Order from Previous Session

**Setup**:
```python
# Order created 5 minutes before session close (yesterday)
old_order = {
    "orderId": 1000,
    "custom_tag": "ENT_ES_1M_01_1732556700000_OLD001",
    "status": 1,  # OPEN
    "side": "SELL",
    "size": 5,
    "symbol": "ES",
    "created_at": datetime(2025, 11, 26, 15, 55, 0)  # Yesterday 15:55
}

# Today's startup (different day)
today_startup_time = datetime(2025, 11, 27, 9, 30, 0)
session_age = (today_startup_time - old_order["created_at"]).total_seconds()  # ~18 hours
```

**Execution**:
1. Bot starts today
2. Queries `order_search()` with no age filter
3. Sees old order from yesterday, status=OPEN
4. Ignores it (doesn't know it's stale)
5. Old order fills during market open
6. Position changes unexpectedly

**Expected Behavior**:
- On startup, query old orders (past 24 hours)
- Flag any that are still OPEN (should have closed)
- Either:
  - Manually review before trading
  - Auto-cancel if age > threshold
  - Log warning and proceed with caution

**Validation**:
```python
def startup_order_archaeology(client, account_id, age_threshold_hours=24):
    stale_cutoff = datetime.now() - timedelta(hours=age_threshold_hours)

    all_orders = client.order_search(account_id)
    stale_open_orders = [
        o for o in all_orders
        if o["created_at"] < stale_cutoff and o["status"] == 1  # OPEN
    ]

    if stale_open_orders:
        logger.warning(f"Found {len(stale_open_orders)} stale open orders:")
        for order in stale_open_orders:
            logger.warning(f"  {order['tag']}, age={(datetime.now() - order['created_at']).total_seconds() / 3600:.1f}h")

        # Require manual confirmation before trading
        return False

    return True
```

---

## TEST CATEGORY 7: State File Atomicity

### Test 7.1: Atomic Write During Shutdown

**Setup**:
```python
# Normal operation
state = {
    "ES": {"symbol": "ES", "quantity": 5, "avg_entry_price": 4750},
    "NQ": {"symbol": "NQ", "quantity": 3, "avg_entry_price": 19500},
}

# Shutdown signal received
# Start writing state file

# POWER LOSS during write (after 50% complete)
# File now contains: ES data + half of NQ data
```

**Execution**:
1. Bot crashes mid-write
2. State file is corrupt
3. On restart, load fails

**Expected Behavior**:
- Use atomic write pattern (write to temp, then rename)
- On read error, fall back to backup

**Validation**:
```python
def save_state_atomic(filepath: str, state: dict):
    import tempfile

    # Write to temporary file
    temp_path = filepath + ".tmp"
    with open(temp_path, "w") as f:
        json.dump(state, f)

    # Atomic rename (on same filesystem)
    os.rename(temp_path, filepath)  # ← ATOMIC

def load_state_with_fallback(filepath: str, backup_paths: list[str]):
    # Try primary
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load {filepath}: {e}")

    # Try backups
    for backup_path in backup_paths:
        try:
            with open(backup_path) as f:
                return json.load(f)
        except Exception:
            pass

    # No valid state found
    return None
```

---

## TEST CATEGORY 8: Recovery Checklist

Create a test suite that validates each scenario:

```python
class RecoveryTestSuite:
    def test_1_1_cold_start_with_exchange_positions(self):
        """Exchange has positions, virtual tracker empty"""
        pass

    def test_1_2_partial_virtual_positions(self):
        """Virtual tracker has subset of exchange positions"""
        pass

    def test_2_1_corrupted_tracker_file(self):
        """State file is truncated/malformed"""
        pass

    def test_2_2_registry_backup_fallback(self):
        """Primary registry lost, use backup"""
        pass

    def test_3_1_orphaned_brackets_after_restart(self):
        """SL/TP orders persist, registry lost"""
        pass

    def test_3_2_bracket_resubmission(self):
        """Brackets resubmitted due to flag reset"""
        pass

    def test_4_1_tag_uniqueness_1000_iterations(self):
        """Generate 1000 tags, no duplicates"""
        pass

    def test_4_2_rng_reseed_rapid_restart(self):
        """Rapid restart within same second, UUIDs still unique"""
        pass

    def test_5_1_flatten_retry_limit(self):
        """NUCLEAR flatten retries max 3 times then gives up"""
        pass

    def test_5_2_cascade_flatten_serialization(self):
        """Multiple symbol flattens serialized, one failure stops cascade"""
        pass

    def test_6_1_old_order_from_previous_session(self):
        """Orders > 24h old flagged for review"""
        pass

    def test_7_1_atomic_state_write(self):
        """State write to temp then rename, corruption prevention"""
        pass

if __name__ == "__main__":
    suite = RecoveryTestSuite()
    # Run all tests
```

---

## Summary: What Must Be Tested

| Test | Priority | Expected Outcome | Risk if Skipped |
|------|----------|------------------|-----------------|
| Cold start with exchange positions | CRITICAL | Sync detects mismatch, flattens safely | Bot closes wrong positions |
| Corrupted state file | CRITICAL | Fallback to backup or empty state | Crash on startup |
| Orphaned bracket orders | CRITICAL | Detect via archaeology, prevent resubmit | Duplicate bracket orders |
| Bracket flag reset | CRITICAL | Restore from saved state | Duplicate brackets |
| Tag collision risk | HIGH | UUID prevents collision | Exchange rejects order |
| Flatten retry limit | HIGH | Bot stops retrying after 3x fails | Infinite retry loop |
| Cascade flatten | HIGH | Stop if one fails | Partial liquidation |
| Old orders from prev session | MEDIUM | Flag for review or auto-cancel | Unexpected fills |
| Atomic writes | MEDIUM | Write to temp, atomic rename | Corruption on crash |

