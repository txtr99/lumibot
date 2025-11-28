# Bracket Order Repair: Vulnerable Code Patterns

This document identifies specific code patterns in the current implementation that enable the edge cases described in `BRACKET_ORDER_ORPHANING_EDGE_CASES.md`.

---

## Pattern 1: Exception Silencing in Bracket Cancellation

**Location**: `tools/bracket_order_manager.py`, lines 2204-2210

```python
try:
    cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
    if cancel_result.get("cancelled"):
        result["brackets_cancelled"].extend(cancel_result["cancelled"])
except Exception as ce:
    self.logger.warning(f"[POSITION-REPAIR] Failed to cancel brackets for {sid}: {ce}")
    # ⚠️ CRITICAL: No return/raise! Execution continues below!

# Lines 2199-2203: Position reset happens regardless of cancel success
state.tracker.reset()
state.entry_price = None
state.take_profit_price = None
state.stop_loss_price = None
```

### Problem
- Exception is caught and logged but **execution continues unconditionally**
- Position is reset even if bracket cancellation fails
- Creates inconsistent state: flat position with active brackets

### Vulnerable Pattern
```python
try:
    risky_operation()
except Exception:
    log_warning()
    # No explicit control flow decision

# This proceeds regardless of try/except outcome
unsafe_operation()
```

### Fix
```python
try:
    cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
    if cancel_result.get("cancelled"):
        result["brackets_cancelled"].extend(cancel_result["cancelled"])
except Exception as ce:
    self.logger.error(f"[POSITION-REPAIR] Cannot safely zero {sid}: bracket cancel failed: {ce}")
    result["warnings"].append({
        "strategy": sid,
        "reason": "bracket_cancel_failed",
        "error": str(ce),
        "action": "skipping_position_zero_to_maintain_consistency"
    })
    continue  # ← Skip this strategy's position zero

# Only reset if cancel succeeded or was not needed
state.tracker.reset()
```

---

## Pattern 2: No Confirmation of Cancel Success

**Location**: `tools/bracket_order_manager.py`, lines 301-320

```python
def _cancel_order(self, order_id: int, order_type: str, base_tag: str) -> bool:
    try:
        self.logger.info(f"[BRACKET-REASONING] ... [trying to cancel {order_id}] ...")
        response = self.client.api.order_cancel(self.account_id, order_id)

        if response.get("success"):
            self.logger.info(f"[BRACKET] Cancelled {order_type}({order_id})")
            return True
        else:
            error = response.get("errorCode")
            msg = response.get("errorMessage", "")
            # ⚠️ PROBLEM: Only logs, doesn't check if order is actually terminal
            self.logger.warning(f"[BRACKET] Failed to cancel {order_type}({order_id}): {error} {msg}")
            return False

    except Exception as e:
        self.logger.error(f"Error cancelling {order_type}({order_id}): {e}")
        return False
```

### Problem
- **Doesn't verify order is actually cancelled after cancel request**
- Network ambiguity: cancel might have succeeded even if response failed
- Returns `False` for any error, but position gets zeroed anyway (due to Pattern 1)
- No re-check of order status

### Vulnerable Pattern
```python
def cancel_order(id):
    response = api.cancel(id)
    if response["success"]:
        return True
    return False
    # Never checks: is the order actually in TERMINAL state?
```

### Fix
```python
def _cancel_order_with_confirmation(self, order_id: int, order_type: str, base_tag: str) -> bool:
    """Cancel order and confirm it reached terminal state."""
    try:
        response = self.client.api.order_cancel(self.account_id, order_id)

        if response.get("success"):
            self.logger.info(f"[BRACKET] Cancelled {order_type}({order_id})")
            # ← NEW: Verify order is actually terminal
            return self._verify_order_terminal(order_id, order_type, base_tag)
        else:
            # Even on API failure, check if order is terminal anyway
            self.logger.warning(f"[BRACKET] Cancel API failed for {order_type}({order_id}), checking status...")
            return self._verify_order_terminal(order_id, order_type, base_tag)

    except Exception as e:
        self.logger.error(f"Error cancelling {order_type}({order_id}): {e}")
        # On network error, assume failure and warn
        return False

def _verify_order_terminal(self, order_id: int, order_type: str, base_tag: str) -> bool:
    """Poll order status to confirm it reached TERMINAL state."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            orders = self.client.api.order_search(self.account_id, start_timestamp=...).get("orders", [])
            for order in orders:
                if order.get("id") == order_id:
                    status = order.get("status")
                    if status in self.TERMINAL_STATUSES:
                        self.logger.info(f"[BRACKET] Confirmed {order_type}({order_id}) terminal (status={status})")
                        return True
            # Order not found in search (may be old) - assume cancelled
            return True
        except Exception:
            time.sleep(0.1)

    self.logger.error(f"[BRACKET] Could not verify terminal status for {order_type}({order_id})")
    return False
```

---

## Pattern 3: No Cleanup of Orphaned Bracket Pairs

**Location**: `tools/bracket_order_manager.py`, lines 94-98, 368-390

```python
class BracketOrderManager:
    def __init__(self, ...):
        # Active bracket pairs: base_tag -> BracketPair
        self.brackets: Dict[str, BracketPair] = {}  # Line 95

    def cancel_bracket(self, base_tag: str) -> Dict[str, bool]:
        """Manually cancel a bracket."""
        result = {"sl_cancelled": False, "tp_cancelled": False}

        if base_tag not in self.brackets:
            return result

        bracket = self.brackets[base_tag]

        if bracket.sl_order_id:
            result["sl_cancelled"] = self._cancel_order(bracket.sl_order_id, "SL", base_tag)

        if bracket.tp_order_id:
            result["tp_cancelled"] = self._cancel_order(bracket.tp_order_id, "TP", base_tag)

        # ⚠️ PROBLEM: Never removes bracket from self.brackets!
        # Even if cancel succeeds, pair stays in dict forever
        return result
```

### Problem
- Only `_process_fills()` removes bracket pairs (lines 274-283)
- If cancel succeeds but no fill arrives, pair is **orphaned in memory**
- Polling continues to check orphaned orders indefinitely
- Memory leak over long sessions

### Vulnerable Pattern
```python
def cancel_bracket(tag):
    order1_cancelled = cancel_order(...)
    order2_cancelled = cancel_order(...)
    return {"order1": order1_cancelled, "order2": order2_cancelled}
    # Never removes from self.brackets dict
```

### Fix
```python
def cancel_bracket(self, base_tag: str, remove_from_tracking: bool = True) -> Dict[str, Any]:
    """Manually cancel a bracket and optionally remove from tracking."""
    result = {"sl_cancelled": False, "tp_cancelled": False, "removed": False}

    if base_tag not in self.brackets:
        return result

    bracket = self.brackets[base_tag]

    if bracket.sl_order_id:
        result["sl_cancelled"] = self._cancel_order(bracket.sl_order_id, "SL", base_tag)

    if bracket.tp_order_id:
        result["tp_cancelled"] = self._cancel_order(bracket.tp_order_id, "TP", base_tag)

    # ← NEW: Remove from tracking if both legs confirmed cancelled
    if result["sl_cancelled"] and result["tp_cancelled"] and remove_from_tracking:
        del self.brackets[base_tag]
        result["removed"] = True
        self.logger.info(f"[BRACKET] Removed {base_tag} from tracking after cancel")

    return result

def _cleanup_orphaned_brackets(self):
    """Remove bracket pairs that haven't been updated in timeout period."""
    now = datetime.now(timezone.utc)
    timeout_seconds = 300  # 5 minutes

    to_remove = []
    for base_tag, pair in self.brackets.items():
        age = (now - pair.created_at).total_seconds()
        if age > timeout_seconds and pair.active and pair.sl_terminal and pair.tp_terminal:
            to_remove.append(base_tag)
            self.logger.warning(
                f"[BRACKET] Removing stale orphaned bracket {base_tag} (age={age}s, "
                f"sl_terminal={pair.sl_terminal}, tp_terminal={pair.tp_terminal})"
            )

    for base_tag in to_remove:
        del self.brackets[base_tag]

    return len(to_remove)
```

---

## Pattern 4: No Per-Position Locking in VirtualPositionTracker

**Location**: `lumibot/tools/virtual_position_tracker.py`, lines 77-294

```python
class VirtualPositionTracker:
    def __init__(self):
        self.positions: Dict[str, VirtualPosition] = {}  # No lock!
        self.trade_history = []  # No lock!

    def reset(self):
        """Reset all virtual positions to flat."""
        self.positions.clear()  # ← Can race with execute_order()
        self.trade_history.clear()  # ← Can race with execute_order()
        self.start_time = datetime.now()

    def execute_order(self, symbol: str, quantity: float, side: str, price: float = None, order_id: str = None):
        """Execute a virtual order and update position."""
        # ... calculations ...

        self.positions[symbol] = VirtualPosition(...)  # ← Can race with reset()
        self.trade_history.append({...})  # ← Can race with reset()
```

### Problem
- No synchronization primitives
- `reset()` can clear dict while `execute_order()` is writing to it
- Concurrent modification leads to data corruption
- Especially dangerous when repair_position_desync() runs in same thread as polling

### Vulnerable Pattern
```python
class UnsafeTracker:
    def __init__(self):
        self.data = {}

    def reset(self):
        self.data.clear()  # Not thread-safe

    def update(self, key, value):
        self.data[key] = value  # Not thread-safe
```

### Fix
```python
import threading

class SafeVirtualPositionTracker:
    def __init__(self):
        self.positions: Dict[str, VirtualPosition] = {}
        self.trade_history = []
        self._lock = threading.RLock()  # ← Add lock

    def reset(self):
        """Reset all virtual positions to flat."""
        with self._lock:
            self.positions.clear()
            self.trade_history.clear()
            self.start_time = datetime.now()

    def execute_order(self, symbol: str, quantity: float, side: str, price: float = None, order_id: str = None):
        """Execute a virtual order and update position."""
        with self._lock:  # ← Protect entire operation
            # ... existing calculations ...
            self.positions[symbol] = VirtualPosition(...)
            self.trade_history.append({...})
            return self.positions[symbol]

    def get_position(self, symbol: str) -> Optional[VirtualPosition]:
        """Get current virtual position for a symbol."""
        with self._lock:
            return self.positions.get(symbol)
```

---

## Pattern 5: State Divergence Between BracketOrderManager and EnhancedStrategyState

**Location**:
- `tools/bracket_order_manager.py`, lines 90-92, 94-95
- `custom_portfolio/multi_strategy_executor_enhanced.py`, lines 66-81

```python
# BracketOrderManager state
class BracketOrderManager:
    def __init__(self, ...):
        self.strategy_states: Dict[str, Any] = strategy_states or {}  # Line 92
        self.brackets: Dict[str, BracketPair] = {}  # Line 95

# EnhancedStrategyState
class EnhancedStrategyState(StrategyState):
    def __init__(self, ...):
        self.entry_time: Optional[datetime] = None
        self.entry_price: Optional[float] = None
        self.take_profit_price: Optional[float] = None
        self.stop_loss_price: Optional[float] = None
        self.brackets_submitted: bool = False

# repair_position_desync() clears strategy state:
state.tracker.reset()
state.entry_price = None
state.take_profit_price = None
state.stop_loss_price = None
# ⚠️ But self.brackets dict is NOT cleared/cleaned!
```

### Problem
- Two separate state machines tracking same position + brackets
- Repair clears strategy state but not manager state
- Manager continues polling for filled brackets that match zeroed positions
- No way to reconcile state if they diverge

### Vulnerable Pattern
```python
class A:
    def __init__(self):
        self.state = {"position": 5}

class B:
    def __init__(self, a):
        self.a = a
        self.mirrors = {"position": 5}

# Later
a.state["position"] = 0
# b.mirrors still = {"position": 5}
# No mechanism to keep them in sync
```

### Fix
```python
class BracketOrderManager:
    def repair_position_desync(self, sync_result: Optional[Dict] = None) -> Dict[str, Any]:
        """Take corrective action when exchange and virtual positions don't match."""
        # ... existing code ...

        for sid, state, qty in strategies_with_pos:
            # ... determine what to zero ...

            # ATOMIC: Zero position and clean brackets together
            brackets_to_remove = self._get_brackets_for_strategy(sid)

            try:
                # Phase 1: Cancel all brackets
                for base_tag in brackets_to_remove:
                    self.cancel_bracket(base_tag, remove_from_tracking=True)

                # Phase 2: Only then zero position
                state.tracker.reset()
                state.entry_price = None
                state.take_profit_price = None
                state.stop_loss_price = None
                state.brackets_submitted = False

                # Phase 3: Verify brackets were removed
                remaining = self._get_brackets_for_strategy(sid)
                if remaining:
                    self.logger.error(
                        f"[POSITION-REPAIR] ⚠️ After zeroing {sid}, "
                        f"{len(remaining)} bracket(s) still remain: {remaining}"
                    )
                    result["warnings"].append({
                        "strategy": sid,
                        "reason": "orphaned_brackets_after_zero",
                        "count": len(remaining),
                        "tags": remaining
                    })

            except Exception as e:
                self.logger.error(f"[POSITION-REPAIR] Atomic zero failed for {sid}: {e}")
                result["warnings"].append({
                    "strategy": sid,
                    "reason": "atomic_operation_failed",
                    "error": str(e),
                    "action": "skipped_to_maintain_consistency"
                })

    def _get_brackets_for_strategy(self, strategy_id: str) -> List[str]:
        """Get all bracket tags for a strategy."""
        prefix = f"BRK_ENTRY_{strategy_id}_"
        return [tag for tag in self.brackets.keys() if tag.startswith(prefix)]
```

---

## Pattern 6: No Atomic Transaction Boundary for Position Zeroing

**Location**: `tools/bracket_order_manager.py`, lines 2185-2228

```python
for sid, state, qty in strategies_with_pos:
    # ... calculate what to zero ...

    # PROBLEM: Multiple operations with no atomicity
    try:
        cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
        if cancel_result.get("cancelled"):
            result["brackets_cancelled"].extend(cancel_result["cancelled"])
    except Exception as ce:
        self.logger.warning(f"[POSITION-REPAIR] Failed to cancel brackets for {sid}: {ce}")

    # This happens REGARDLESS of cancel result
    state.tracker.reset()
    state.entry_price = None
    state.take_profit_price = None
    state.stop_loss_price = None

    # And also this
    try:
        cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
        # ...
    except Exception as ce:
        self.logger.warning(...)

    # No rollback if any step fails
    result["repairs_made"].append({...})
```

### Problem
- Can't roll back if cancel fails but position is already zeroed
- No "commit" point where zeroing is confirmed safe
- Partial success leaves system in inconsistent state

### Vulnerable Pattern
```python
def update_account():
    transfer_money()  # Might fail
    update_ledger()   # Always happens
    log_transaction() # Always happens
    # No way to undo if transfer failed
```

### Fix
```python
def repair_position_desync_atomic(self, sync_result: Optional[Dict] = None) -> Dict[str, Any]:
    """Atomic repair: either succeed completely or fail completely."""
    result = {
        "repairs_made": [],
        "warnings": [],
        "brackets_cancelled": [],
    }

    # Collect all repairs needed (read-only phase)
    repairs_needed = []
    for discrepancy in sync_result.get("discrepancies", []):
        symbol = discrepancy["symbol"]
        excess = discrepancy["virtual_qty"] - discrepancy["exchange_qty"]

        strategies_to_zero = self._find_strategies_to_zero(symbol, excess)
        repairs_needed.append({
            "symbol": symbol,
            "strategies": strategies_to_zero
        })

    # Validation phase: check all preconditions
    validation_errors = []
    for repair in repairs_needed:
        for sid, state in repair["strategies"]:
            # Check: can we actually cancel these brackets?
            try:
                brackets = self._get_brackets_for_strategy(sid)
                for base_tag in brackets:
                    if not self._validate_can_cancel(base_tag):
                        validation_errors.append((sid, base_tag))
            except Exception as e:
                validation_errors.append((sid, str(e)))

    if validation_errors:
        self.logger.error(f"[POSITION-REPAIR] Validation failed, aborting entire repair: {validation_errors}")
        result["warnings"].append({
            "reason": "validation_failed",
            "errors": validation_errors,
            "action": "repair_aborted"
        })
        return result

    # Execution phase: only proceed if validation passed
    for repair in repairs_needed:
        for sid, state in repair["strategies"]:
            try:
                # Step 1: Cancel
                cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
                if not cancel_result.get("all_cancelled"):
                    raise Exception(f"Cancel incomplete: {cancel_result}")

                # Step 2: Zero (only if cancel succeeded)
                state.tracker.reset()
                state.entry_price = None

                # Step 3: Verify
                remaining = self._get_brackets_for_strategy(sid)
                if remaining:
                    raise Exception(f"Orphaned brackets remain: {remaining}")

                result["repairs_made"].append({...})

            except Exception as e:
                # Compensation: try to restore brackets?
                self.logger.error(f"[POSITION-REPAIR] Repair failed for {sid}, attempting rollback: {e}")
                result["warnings"].append({
                    "strategy": sid,
                    "reason": "repair_failed",
                    "error": str(e),
                    "rollback_attempted": True
                })

    return result
```

---

## Pattern 7: Fill Processing Without Position State Check

**Location**: `tools/bracket_order_manager.py`, line 1220 (approx)

```python
def _process_fills(self) -> Dict[str, Any]:
    """Process fills from trade history."""
    result = {"fills": [], "errors": []}

    # Get recent trades
    trades = self.client.api.trade_search(...)

    for trade in trades:
        # ... extract order info ...

        # ⚠️ PROBLEM: No check that strategy position exists/is non-flat
        if state and strategy_id in self.strategy_states:
            state = self.strategy_states[strategy_id]

            # Blindly execute fill without checking position state
            state.tracker.execute_order(
                symbol=symbol,
                quantity=...,
                side=...,
                price=...,
                order_id=...
            )
```

### Problem
- No verification that position matches expected state before fill
- Fill on zeroed position gets lost
- No check if position was zeroed by concurrent repair

### Vulnerable Pattern
```python
def apply_fill(strategy_id, fill):
    state = strategies[strategy_id]
    # No check: is state.tracker still flat?
    # No check: is this fill expected?
    state.tracker.execute_order(...)
```

### Fix
```python
def _process_fills(self) -> Dict[str, Any]:
    """Process fills from trade history."""
    result = {"fills": [], "orphaned": [], "errors": []}

    trades = self.client.api.trade_search(...)

    for trade in trades:
        strategy_id = ...
        symbol = ...
        fill_price = ...
        fill_quantity = ...

        state = self.strategy_states.get(strategy_id)
        if not state:
            result["orphaned"].append({
                "trade_id": trade.get("id"),
                "reason": "strategy_not_found"
            })
            continue

        # ← NEW: Verify position is non-flat BEFORE applying fill
        current_pos = state.tracker.get_position(symbol)
        if current_pos is None or current_pos.quantity == 0:
            # Position is flat - fill is orphaned
            self.logger.warning(
                f"[FILL] Orphaned fill for {strategy_id} {symbol}: "
                f"position is flat but received fill at {fill_price}"
            )
            result["orphaned"].append({
                "trade_id": trade.get("id"),
                "strategy": strategy_id,
                "symbol": symbol,
                "reason": "position_flat_at_fill_time",
                "fill_price": fill_price,
                "fill_quantity": fill_quantity
            })
            continue

        # ← NEW: Verify fill matches current position direction
        if (fill_side == "sell" and current_pos.quantity <= 0) or \
           (fill_side == "buy" and current_pos.quantity >= 0):
            # Fill is opposite direction to current position - sanity check failed
            self.logger.error(
                f"[FILL] ⚠️ DIRECTION MISMATCH for {strategy_id}: "
                f"position {current_pos.quantity} but fill is {fill_side}"
            )
            result["errors"].append({
                "trade_id": trade.get("id"),
                "reason": "direction_mismatch"
            })
            continue

        # Only then apply the fill
        state.tracker.execute_order(
            symbol=symbol,
            quantity=fill_quantity,
            side=fill_side,
            price=fill_price,
            order_id=...
        )

        result["fills"].append({...})
```

---

## Summary: Vulnerable Patterns and Mitigations

| Pattern | Location | Risk | Mitigation |
|---------|----------|------|-----------|
| Exception silencing | lines 2204-2210 | Position zeroed despite cancel failure | Add explicit control flow (continue/raise) |
| No cancel confirmation | lines 301-320 | Network ambiguity | Re-check order status after cancel |
| No bracket cleanup | lines 368-390 | Memory leak, polling waste | Add cleanup on successful cancel |
| No position locking | VirtualPositionTracker | Data corruption | Add threading.RLock() |
| State divergence | Manager vs Strategy state | Inconsistent views | Synchronize at atomic boundary |
| No transaction boundary | lines 2185-2228 | Partial failures | Add validation phase before execution |
| No position check at fill | _process_fills() | Orphaned fills | Verify position exists before apply |

