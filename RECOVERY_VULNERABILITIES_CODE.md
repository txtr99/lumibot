# Recovery Logic Vulnerabilities: Code-Level Analysis

This document maps specific code vulnerabilities that could be triggered by restart/recovery edge cases.

---

## VULNERABILITY 1: VirtualPositionTracker - No Persistence Layer

**File**: `/lumibot/tools/virtual_position_tracker.py`

**Issue**: The `VirtualPositionTracker` class has NO built-in persistence. It's entirely in-memory.

```python
class VirtualPositionTracker:
    def __init__(self):
        """Initialize the virtual position tracker."""
        self.positions: Dict[str, VirtualPosition] = {}  # ← IN MEMORY ONLY
        self.trade_history = []  # ← IN MEMORY ONLY
        self.start_time = datetime.now()
        self._processed_order_ids: Set[str] = set()  # ← IN MEMORY ONLY
```

**What Happens on Restart**:
1. New instance created → all dicts are empty
2. No deserialization from disk
3. All previous positions lost

**Current State**: Position tracker must be serialized/deserialized **at the application layer** (e.g., in `run_portfolio.py`), but there's no standard pattern.

**Risk**: If `run_portfolio.py` crashes before saving state, recovery fails completely.

---

## VULNERABILITY 2: OrderRegistry - Lost Order Context

**File**: `/tools/order_registry.py`

**Issue**: OrderRegistry is also in-memory only. On restart, all order records are lost.

```python
class OrderRegistry:
    def __init__(self, tracker: Any = None):
        self.orders: Dict[str, OrderRecord] = {}  # ← IN MEMORY, LOST ON RESTART
        self.order_id_to_tag: Dict[int, str] = {}  # ← SAME
```

**Cascade Effect**:
1. Bot crashes
2. Exchange still has pending TP/SL orders
3. Registry empty on restart
4. `has_pending_close()` returns False (registry empty)
5. Bot thinks it's safe to enter new position
6. **Result**: Duplicate brackets or double-entry

**Code Path Vulnerable**:
```python
def has_pending_close(self, strategy_id: str) -> bool:
    """Check if strategy has a pending close order (TP, SL, or CLOSE)."""
    close_purposes = {OrderPurpose.TAKE_PROFIT, OrderPurpose.STOP_LOSS, OrderPurpose.CLOSE}
    pending_statuses = {OrderStatus.PENDING, OrderStatus.SUBMITTED}

    for record in self.orders.values():  # ← EMPTY AFTER RESTART
        if (record.strategy_id == strategy_id
            and record.status in pending_statuses
            and record.purpose in close_purposes):
            return True
    return False  # ← ALWAYS FALSE ON RESTART (incorrect!)
```

---

## VULNERABILITY 3: Position Sync Check - Assumes Flat is Safe

**File**: `/tools/order_registry.py`, function `check_position_sync()`

**Issue**: The sync check only verifies exchange qty == virtual qty. But doesn't check for orphaned orders.

```python
def check_position_sync(symbol: str, exchange_positions: List[Dict], tracker: Any) -> SyncResult:
    """Compare aggregated virtual positions vs exchange position."""

    # Only checks POSITION quantities
    exchange_qty = 0
    for pos in exchange_positions:
        contract_id = pos.get("contractId", "")
        pos_symbol = extract_symbol_from_contract(contract_id)
        if pos_symbol == symbol:
            exchange_qty = pos.get("size", 0) * (1 if pos.get("type") == 1 else -1)

    virtual_qty = 0
    if tracker:
        for pos in tracker.get_all_positions():
            if pos.get("symbol") == symbol:
                virtual_qty += pos.get("quantity", 0)

    # ONLY CHECKS POSITION SIZE
    if abs(exchange_qty - virtual_qty) < 1e-9:
        return SyncResult(is_synced=True, ...)  # ✓ SYNCED

    # DOESN'T CHECK:
    # - Are there pending SL/TP orders?
    # - Are there entry orders waiting to fill?
    # - Are there orders from previous session?
```

**The Gap**: Two systems in "sync" on position qty but completely out of sync on order state.

---

## VULNERABILITY 4: NUCLEAR Flatten - Assumes Cancellations Succeed

**File**: `/tools/order_registry.py`, function `flatten_symbol()`

```python
async def flatten_symbol(symbol: str, client: Any, account_id: str,
                        registry: OrderRegistry, tracker: Any) -> bool:
    logger.warning(f"[NUCLEAR] Flattening all exposure for {symbol}")

    try:
        # 1. Cancel ALL pending orders for symbol
        pending = registry.get_pending_orders_for_symbol(symbol)
        for record in pending:
            try:
                logger.info(f"[NUCLEAR] Cancelling order {record.tag} (id={record.order_id})")
                result = client.order_cancel(account_id, record.order_id)
                if result.get("success"):
                    registry.register_cancellation(record.order_id)  # ← MARKS AS CANCELLED
                    # BUG: What if order already filled or doesn't exist?
            except Exception as e:
                logger.error(f"[NUCLEAR] Failed to cancel {record.tag}: {e}")

        # 2. Get current exchange position
        positions = client.position_search_open(account_id)
        # ...

        # 3. Close exchange position if any
        if abs(exchange_qty) > 0:
            # ... submit close order ...

        # 4. Mark ALL virtual positions for symbol as closed
        if tracker:
            closed_count = 0
            for pos in tracker.get_all_positions():
                if pos.get("symbol") == symbol:
                    strategy_id = pos.get("strategy_id")
                    if strategy_id:
                        tracker.close_position(strategy_id)  # ← MARKS FLAT BEFORE CLOSE CONFIRMS
                        closed_count += 1
            logger.info(f"[NUCLEAR] Closed {closed_count} virtual positions for {symbol}")
```

**Vulnerabilities**:
1. **Line "MARKS AS CANCELLED"**: If order doesn't exist (errorCode 5), bot marks it cancelled anyway
2. **Line "MARKS FLAT BEFORE CLOSE CONFIRMS"**: Tracker marked flat BEFORE close order confirms filled
3. **Race Condition**: Between marking virtual flat and close order submitting/filling

**What Could Happen**:
```
Timeline:
09:00:00 - Sync mismatch detected, flatten_symbol() called
09:00:01 - Cancel pending TP order (succeeds)
09:00:02 - Submit close order for 5 ES @ market
09:00:03 - Mark virtual position as flat (TRACKER.close_position())
09:00:04 - Close order still PENDING on exchange
09:00:05 - New signal fires, tries to open position
09:00:06 - registry.has_pending_close() = False (registry lost on restart!)
09:00:07 - Bot submits BUY 5 ES → position becomes 5 long
09:00:08 - Previous close order fills (SELL 5) → exchange = 0
09:00:09 - Bot thinks: position = 5, Exchange: position = 0
09:00:10 - SYNC CHECK: 5 != 0 → FLATTEN_SYMBOL AGAIN (infinite loop)
```

---

## VULNERABILITY 5: MultiStrategyExecutorEnhanced - No State Recovery

**File**: `/custom_portfolio/multi_strategy_executor_enhanced.py`

**Issue**: Strategy states are created fresh on each run. No persistence layer for `EnhancedStrategyState`.

```python
class EnhancedStrategyState(StrategyState):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entry_time: Optional[datetime] = None
        self.entry_price: Optional[float] = None
        self.entry_iteration: Optional[int] = None
        self.bars_in_trade: int = 0
        self.entry_side: Optional[str] = None
        self.take_profit_price: Optional[float] = None
        self.stop_loss_price: Optional[float] = None
        self.pending_tp: Optional[float] = None
        self.pending_sl: Optional[float] = None
        self.last_atr: Optional[float] = None
        self.brackets_submitted: bool = False  # ← ALWAYS False on restart!
```

**Problem with `brackets_submitted`**:
- **Day 1**: Entry fills at 4750, ATR=20
  - Bracket TP submitted at 4770 (order_id=888)
  - Bracket SL submitted at 4730 (order_id=889)
  - `brackets_submitted = True`

- **Day 2 (Bot restarts)**:
  - Virtual tracker: ES = 1 long @ 4750
  - Strategy state: `brackets_submitted = False` (new instance)
  - Old TP/SL orders still live on exchange

**What Happens**:
```python
# In on_trading_iteration() for ES_1M_01:
if state.brackets_submitted == False and has_position:
    logger.info("Submitting brackets...")
    # Resubmit TP at 4770 (NEW ORDER, same price)
    # Resubmit SL at 4730 (NEW ORDER, same price)
    # Result: TWO TP orders, TWO SL orders on exchange
```

**Code Path**:
```python
if signal == "HOLD" and has_position:
    # Check time-based exit
    if bars_in_trade > max_bars:
        # Close position
    elif not state.brackets_submitted:  # ← ALWAYS TRUE on restart!
        # Resubmit brackets (WRONG!)
```

---

## VULNERABILITY 6: Custom Tag Generation - Millisecond Collision Risk

**File**: `/tools/order_registry.py`, function `generate_unique_tag()`

```python
def generate_unique_tag(self, purpose: OrderPurpose, strategy_id: str) -> str:
    """Generate a truly unique tag with timestamp + UUID."""
    unix_ms = int(time.time() * 1000)  # ← MILLISECOND PRECISION
    uuid_suffix = uuid.uuid4().hex[:6].upper()
    purpose_str = purpose.value if isinstance(purpose, OrderPurpose) else purpose
    return f"{purpose_str}_{strategy_id}_{unix_ms}_{uuid_suffix}"
```

**Vulnerability**: Two rapid calls within same millisecond could collide:
```python
# Call 1 at 1732556789123
tag1 = registry.generate_unique_tag(OrderPurpose.ENTRY, "ES_1M_01")
# Result: "ENT_ES_1M_01_1732556789123_ABC123"

# Call 2 at 1732556789123 (same millisecond)
tag2 = registry.generate_unique_tag(OrderPurpose.ENTRY, "ES_1M_01")
# Result: "ENT_ES_1M_01_1732556789123_XYZ789"
# UUID differs, so NO COLLISION
```

**BUT**: If UUID generation is seeded with time, restart at same millisecond:
```python
# Previous run crashed:
# Generated: "ENT_ES_1M_01_1732556789123_ABC123" → filled

# Restart happens immediately:
# System time: still 1732556789123 (millisecond level)
# RNG might reseed with time
# Generated: "ENT_ES_1M_01_1732556789123_ABC123" ← COLLISION!
```

**Python Risk**: `uuid.uuid4()` is cryptographically random, BUT if `/dev/urandom` isn't properly seeded on restart, it could repeat.

---

## VULNERABILITY 7: Position Repair Logic - Assumes Exchange is Ground Truth

**File**: `/custom_portfolio/strategies/run_portfolio.py`, method `repair_position_desync()`

**Issue**: When mismatch detected, bot assumes exchange position is correct and virtual is wrong.

```python
# Pseudocode from run_portfolio.py
sync_result = bracket_manager.check_position_sync()
if not sync_result.get("synced"):
    logger.error(f"Position mismatch: {sync_result}")
    # Assume exchange is correct
    repair_result = bracket_manager.repair_position_desync(sync_result)
```

**The Hidden Assumption**:
> "Exchange position is always authoritative, virtual tracker is fallible"

**But What If**:
1. Exchange API is returning **stale positions** from 5 minutes ago (caching issue)?
2. Exchange has a **bug** in `position_search_open()`?
3. Virtual tracker is correct, but exchange hasn't updated?

**Scenario: Exchange Reports Stale Position**
```
Real situation: Position = 5 ES long
Exchange API cached result: Position = 10 ES long (5 min stale)
Virtual tracker: Position = 5 ES long (fresh)

Bot reads sync check:
- Exchange qty: 10 (STALE)
- Virtual qty: 5 (FRESH)
- MISMATCH!

Bot decides: Exchange is right, flatten 10 ES
RESULT: Closes 5 ES that didn't need to be closed
```

---

## VULNERABILITY 8: Cascade Close Failure - No Idempotency

**File**: `/tools/order_registry.py`, function `flatten_symbol()`

**Issue**: If close order fails but virtual tracker is already marked flat, subsequent retries will fail.

```python
# Step 1: Mark virtual flat
if tracker:
    tracker.close_position(strategy_id)  # Position now 0

# Step 2: Get exchange position
positions = client.position_search_open(account_id)
exchange_qty = ...  # Still 5 ES (hasn't changed)

# Step 3: Submit close order
if abs(exchange_qty) > 0:
    # close order submission FAILS
    result = client.order_place(...)
    if not result.get("success"):
        logger.error("Failed to close")
        # But virtual is already marked flat!
        # Next iteration will see:
        # - Virtual: 0 ES
        # - Exchange: 5 ES
        # - MISMATCH AGAIN (infinite loop)
```

---

## VULNERABILITY 9: Bracket Manager - Lost Fill History

**File**: `/tools/bracket_order_manager.py`

**Issue**: BracketOrderManager also has no persistence.

```python
class BracketOrderManager:
    def __init__(self, client, account_id: int,
                 strategy_states: Optional[Dict[str, Any]] = None, ...):
        self.brackets: Dict[str, BracketPair] = {}  # ← IN MEMORY
        self._order_to_bracket: Dict[int, str] = {}  # ← IN MEMORY
```

**On Restart**:
1. Manager initialized with empty `self.brackets`
2. Exchange still has old bracket orders
3. Manager has no knowledge of them
4. When querying `order_search()`, finds old brackets but:
   - Doesn't know which were just filled
   - Doesn't know which correspond to which position
   - Can't trigger the "cancel other leg" logic

**Result**: Orphaned orders persist and could cause cascading fills.

---

## VULNERABILITY 10: Session Boundary - Orders Carry Over Undetected

**File**: `/custom_portfolio/strategies/run_portfolio.py`

**Issue**: No cleanup of orders from previous trading session.

```python
# On startup, bot does NOT query old orders:
# - No check for orders placed yesterday that are still pending
# - No check for orders older than 24 hours
# - No archival of historical fills

# If order from yesterday (5 min before session close) still pending:
# - Order: SELL 5 ES @ limit 4750
# - Status: OPEN (never filled)
# - Created: 2025-11-26 15:55:00

# Today at 09:30 when markets open:
# - Bot starts fresh, virtual = 0
# - Order fills: sells 5 ES at 4745
# - Position becomes: SHORT 5 ES
# - Bot has no knowledge of this fill
# - Next iteration: virtual=0, exchange=-5 → MISMATCH
```

---

## SUMMARY: Vulnerabilities by Severity

### CRITICAL
1. **VirtualPositionTracker** - No persistence, lost on restart
2. **OrderRegistry** - No persistence, lost on restart
3. **Cascade Flatten** - Potential infinite loop on failure
4. **Bracket Resubmission** - `brackets_submitted` flag reset on restart

### HIGH
5. **NUCLEAR Flatten** - Race condition between marking flat and confirming close
6. **BracketOrderManager** - No persistence, orphaned orders undetected
7. **Session Boundary** - Old orders from previous session not cleaned up
8. **Exchange as Ground Truth** - Assumes exchange is always authoritative

### MEDIUM
9. **Tag Collision** - Millisecond + weak RNG could cause duplicates
10. **Sync Check** - Only checks position qty, not order state

---

## Recommended Fixes (Priority Order)

### Phase 1: Prevent Infinite Loops
```python
# Add retry counter to NUCLEAR flatten
class NuclearFlattenAttempt:
    max_retries: int = 3
    attempts: int = 0

    def can_retry(self) -> bool:
        return self.attempts < self.max_retries

    def increment(self):
        self.attempts += 1

# Only flatten if not already attempted 3 times
```

### Phase 2: Persistence Layer
```python
# Add JSON serialization to VirtualPositionTracker
def save_state(self, filepath: str):
    state = {pos.symbol: asdict(pos) for pos in self.positions.values()}
    save_atomic(filepath, state)

def load_state(self, filepath: str):
    state = load_atomic(filepath)
    for symbol, data in state.items():
        self.positions[symbol] = VirtualPosition(**data)
```

### Phase 3: Order Archaeology
```python
# On startup, query all orders from past 24 hours
old_orders = client.order_search(
    created_after=datetime.now() - timedelta(hours=24)
)

# Warn about anything still pending
for order in old_orders:
    if order.status == "OPEN":
        logger.warning(f"STALE ORDER: {order.tag}, created {order.created_at}")
        # Require manual approval before trading
```

### Phase 4: Idempotent Close Orders
```python
# Store close order ID in virtual position, prevent resubmit
class VirtualPosition:
    pending_close_order_id: Optional[int] = None

    def set_pending_close(self, order_id: int):
        self.pending_close_order_id = order_id

    def has_pending_close(self) -> bool:
        return self.pending_close_order_id is not None
```

