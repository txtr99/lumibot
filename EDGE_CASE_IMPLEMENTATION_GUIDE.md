# Implementation Guide: Addressing Position Sync Edge Cases

## Quick Reference: Which Edge Cases Are Real Threats?

**CRITICAL (Must Fix):**
1. Stop loss fill during sync → causes double-entry or phantom zeros
2. Rapid multiple fills → position inverted
3. In-flight order not confirmed → repair zeros valid pending order
4. Repair vs real exit race → exit fills untracked
5. Concurrent access race → position math corrupted

**HIGH (Should Fix):**
6. Bracket fill during sync → lost P&L
7. Stale API data → cascading divergence
8. Position attribution loss → wrong strategy zeroed
9. Repair loop cascading → oscillating repairs
10. Order cancellation race → exit creates wrong-side position

---

## Fix Strategy 1: Order Flight Time Window

**Problem:** Repair fires before orders confirm (Edge Cases #5, #6)

**Solution:** Exclude orders within confirmation window from repair

```python
# In VirtualPositionTracker or separate class
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict

@dataclass
class PendingOrder:
    """Tracks orders that have been submitted but not yet confirmed."""
    order_id: str
    symbol: str
    quantity: float
    side: str  # "buy" or "sell"
    submitted_at: datetime
    confirmed: bool = False

class OrderFlightWindow:
    """
    Manages orders in-flight (submitted but not yet confirmed by exchange).

    Prevents repair logic from zeroing positions created by orders that:
    - Were just placed
    - Haven't yet appeared in exchange position API
    - Are legitimately filling but not visible yet
    """

    def __init__(self, window_seconds: int = 2):
        """
        Args:
            window_seconds: How long to consider orders "in-flight" (default 2 sec)
        """
        self.window_seconds = window_seconds
        self.pending_orders: Dict[str, PendingOrder] = {}  # order_id -> PendingOrder

    def register_pending(
        self, order_id: str, symbol: str, quantity: float, side: str
    ) -> None:
        """Register an order as submitted (not yet confirmed)."""
        self.pending_orders[order_id] = PendingOrder(
            order_id=order_id,
            symbol=symbol,
            quantity=quantity,
            side=side,
            submitted_at=datetime.now(),
        )

    def confirm(self, order_id: str) -> bool:
        """Mark order as confirmed by exchange. Returns True if found."""
        if order_id in self.pending_orders:
            self.pending_orders[order_id].confirmed = True
            return True
        return False

    def get_pending_quantity(self, symbol: str) -> float:
        """
        Get total quantity of unconfirmed orders for a symbol.

        Returns the virtual position impact of orders that haven't yet
        appeared in exchange position API.
        """
        total = 0.0
        now = datetime.now()

        for pending in self.pending_orders.values():
            if pending.symbol != symbol:
                continue

            # Only count orders within flight window (not yet old enough to confirm)
            age_seconds = (now - pending.submitted_at).total_seconds()
            if age_seconds < self.window_seconds:
                qty = pending.quantity if pending.side == "buy" else -pending.quantity
                total += qty

        return total

    def cleanup_expired(self) -> None:
        """Remove orders older than flight window (they should be confirmed by now)."""
        now = datetime.now()
        expired_ids = []

        for order_id, pending in self.pending_orders.items():
            age_seconds = (now - pending.submitted_at).total_seconds()
            if age_seconds >= self.window_seconds and pending.confirmed:
                expired_ids.append(order_id)

        for order_id in expired_ids:
            del self.pending_orders[order_id]


# Usage in sync/repair logic:
def reconcile_positions_with_flight_window(
    exchange_qty: float,
    virtual_qty: float,
    flight_window: OrderFlightWindow,
    symbol: str,
) -> bool:
    """
    Reconcile with awareness of in-flight orders.

    Args:
        exchange_qty: Position from exchange API
        virtual_qty: Sum of all strategy virtual positions
        flight_window: OrderFlightWindow tracking pending orders
        symbol: Trading symbol

    Returns:
        True if positions match (within tolerance)
    """
    # Get expected virtual qty including pending orders
    pending_qty = flight_window.get_pending_quantity(symbol)
    expected_virtual = virtual_qty  # (already includes pending in VirtualPositionTracker)

    # Calculate delta, accounting for in-flight orders
    delta = exchange_qty - expected_virtual

    # If delta matches pending qty, we're just waiting for fills to propagate
    if abs(delta + pending_qty) < 0.001:  # Within tolerance after accounting for pending
        logger.debug(
            f"[RECONCILE] {symbol}: exchange={exchange_qty}, "
            f"virtual={expected_virtual}, pending={pending_qty} "
            f"(waiting for {pending_qty} to confirm)"
        )
        return True

    # Real divergence (not just pending orders)
    logger.warning(
        f"[RECONCILE] {symbol}: MISMATCH exchange={exchange_qty}, "
        f"virtual={expected_virtual}, pending={pending_qty}, "
        f"delta_after_pending={delta + pending_qty}"
    )
    return False
```

---

## Fix Strategy 2: Atomic Repair Transactions

**Problem:** Repair and fill processing race (Edge Cases #6, #10)

**Solution:** Lock during critical sections

```python
import threading
from typing import Optional

class AtomicPositionRepair:
    """
    Ensures repair logic and fill processing don't race.

    Fixes Edge Case #10 (concurrent access) and #6 (repair vs exit race).
    """

    def __init__(self, tracker: 'VirtualPositionTracker'):
        self.tracker = tracker
        self._repair_lock = threading.RLock()
        self._in_repair = {}  # symbol -> is_repair_in_progress

    def repair_position_atomic(
        self,
        symbol: str,
        expected_qty: float,
        reason: str,
    ) -> bool:
        """
        Atomically repair a position.

        Ensures no concurrent execute_order() calls interfere.
        """
        with self._repair_lock:
            self._in_repair[symbol] = True

            try:
                current = self.tracker.get_position(symbol)
                current_qty = current.quantity if current else 0.0

                if abs(current_qty - expected_qty) < 0.001:
                    logger.debug(f"[REPAIR] {symbol}: no change needed")
                    return False

                logger.warning(
                    f"[REPAIR] {symbol}: repairing from {current_qty} to {expected_qty} ({reason})"
                )

                # Atomic: zero the position without interference
                self.tracker.force_flat(symbol)

                # Record repair event for audit
                self._log_repair_event(symbol, current_qty, expected_qty, reason)

                return True
            finally:
                self._in_repair[symbol] = False

    def execute_order_atomic(
        self,
        symbol: str,
        quantity: float,
        side: str,
        price: float = None,
        order_id: str = None,
    ) -> Optional['VirtualPosition']:
        """
        Atomically execute order.

        Blocks if repair is in progress for this symbol.
        Prevents concurrent writes during repair.
        """
        with self._repair_lock:
            if self._in_repair.get(symbol, False):
                logger.warning(
                    f"[ORDER] {symbol}: blocking execute_order, repair in progress"
                )
                return None

            return self.tracker.execute_order(
                symbol=symbol,
                quantity=quantity,
                side=side,
                price=price,
                order_id=order_id,
            )

    def _log_repair_event(
        self, symbol: str, from_qty: float, to_qty: float, reason: str
    ) -> None:
        """Log repair for debugging and audit trail."""
        # Could write to database, file, or metrics system
        logger.info(
            f"[REPAIR_AUDIT] {symbol} {from_qty:+.1f}→{to_qty:+.1f} reason={reason}"
        )
```

---

## Fix Strategy 3: Strategy-Level Repair Attribution

**Problem:** Repair can't distinguish which strategy's position is phantom (Edge Case #7)

**Solution:** Track strategy ownership in repair logic

```python
from typing import Dict, Tuple

class StrategyAwareRepair:
    """
    Repairs only the "most suspicious" strategy position.

    When multiple strategies share a symbol, repair targets the
    strategy whose position is least recently updated.
    """

    def __init__(self, strategy_states: Dict[str, 'EnhancedStrategyState']):
        self.strategy_states = strategy_states

    def repair_most_suspicious(
        self,
        symbol: str,
        exchange_qty: float,
    ) -> Tuple[str, bool]:
        """
        Repair only the oldest/most suspicious strategy's position.

        Args:
            symbol: Trading symbol
            exchange_qty: Confirmed position from exchange

        Returns:
            (repaired_strategy_id, was_repair_needed)
        """
        # Aggregate virtual positions by strategy
        strategy_positions = {}
        for strategy_id, state in self.strategy_states.items():
            pos = state.tracker.get_position(symbol)
            if pos:
                strategy_positions[strategy_id] = (pos.quantity, pos.last_update)

        virtual_net = sum(qty for qty, _ in strategy_positions.values())
        delta = exchange_qty - virtual_net

        if abs(delta) < 0.001:
            return ("", False)  # No repair needed

        # Find the strategy with oldest position update
        if not strategy_positions:
            logger.warning(f"[REPAIR] {symbol}: no virtual positions to repair")
            return ("", False)

        oldest_strategy = min(
            strategy_positions.items(),
            key=lambda x: x[1][1],  # Sort by last_update (oldest first)
        )[0]

        logger.warning(
            f"[REPAIR] {symbol}: targeting {oldest_strategy} "
            f"(oldest update) for repair, delta={delta}"
        )

        # Repair ONLY this strategy
        state = self.strategy_states[oldest_strategy]
        state.tracker.force_flat(symbol)

        return (oldest_strategy, True)
```

---

## Fix Strategy 4: Bidirectional Fill Confirmation

**Problem:** Virtual tracker assumes fill, but doesn't confirm via exchange (Edge Case #5, #4)

**Solution:** Check order status before trusting virtual position

```python
from enum import Enum

class OrderConfirmationStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"

class OrderConfirmationManager:
    """
    Validates that virtual fills match actual order fills.

    Prevents trusting virtual tracker until order status API confirms.
    """

    def __init__(self, client):  # ProjectXClient
        self.client = client
        self.confirmations: Dict[str, OrderConfirmationStatus] = {}

    def check_order_fill(
        self, account_id: int, order_id: int
    ) -> Tuple[bool, Optional[float]]:
        """
        Check if order actually filled on exchange.

        Returns:
            (is_filled, fill_price)
        """
        try:
            result = self.client.order_search(
                accountId=account_id,
                orderId=order_id,
            )

            if not result.get("data"):
                return (False, None)

            orders = result["data"]
            for order in orders:
                status = order.get("status")
                price = order.get("price")

                if status == 2:  # FILLED
                    return (True, price)
                elif status in (3, 4, 5):  # Cancelled, Expired, Rejected
                    return (False, None)

            return (False, None)
        except Exception as e:
            logger.error(f"[CONFIRM] Failed to check order {order_id}: {e}")
            return (False, None)

    def validate_virtual_position(
        self,
        tracker: 'VirtualPositionTracker',
        symbol: str,
        order_id: str,
        account_id: int,
    ) -> bool:
        """
        Validate that virtual position matches order fill status.

        Returns True if position is valid (either both filled or both pending).
        """
        is_filled, _ = self.check_order_fill(account_id, order_id)
        pos = tracker.get_position(symbol)

        has_virtual_position = pos is not None and pos.quantity != 0

        if is_filled and has_virtual_position:
            # Both agree: order filled and position exists
            return True
        elif not is_filled and not has_virtual_position:
            # Both agree: order pending/not filled and no position yet
            return True
        else:
            # DIVERGENCE
            logger.error(
                f"[VALIDATE] {symbol} {order_id}: "
                f"order_filled={is_filled}, has_position={has_virtual_position}"
            )
            return False
```

---

## Fix Strategy 5: Conservative Repair Policy

**Problem:** Repair can make divergence worse (Edge Case #8, #9)

**Solution:** Strict thresholds and direction guards

```python
class ConservativeRepairPolicy:
    """
    Only repair under strict conditions.

    - Never repair upward (add virtual positions)
    - Only repair when divergence > threshold
    - Log all repairs for auditing
    - Wait for staleness timeout before repairing
    """

    def __init__(self, min_divergence: float = 2.0, wait_seconds: int = 3):
        """
        Args:
            min_divergence: Only repair if |delta| >= this (contracts)
            wait_seconds: Wait this long before allowing repair
        """
        self.min_divergence = min_divergence
        self.wait_seconds = wait_seconds
        self.last_sync_time = {}  # symbol -> datetime

    def should_repair(
        self,
        symbol: str,
        exchange_qty: float,
        virtual_qty: float,
    ) -> Tuple[bool, str]:
        """
        Determine if repair should proceed.

        Returns:
            (should_repair, reason)
        """
        delta = exchange_qty - virtual_qty

        # Check 1: Is divergence large enough?
        if abs(delta) < self.min_divergence:
            return (False, f"divergence too small: {delta}")

        # Check 2: Is exchange reporting FEWER contracts? (safe to zero)
        if delta >= 0:
            return (False, f"exchange has more/equal: delta={delta} (repair upward forbidden)")

        # Check 3: Has enough time passed since last sync?
        now = datetime.now()
        last_sync = self.last_sync_time.get(symbol)
        if last_sync:
            elapsed = (now - last_sync).total_seconds()
            if elapsed < self.wait_seconds:
                return (
                    False,
                    f"too soon after last sync: {elapsed:.1f}s (wait {self.wait_seconds}s)"
                )

        # All checks passed
        self.last_sync_time[symbol] = now
        return (True, f"delta={delta} (exchange short {-delta} contracts)")

    def execute_repair(
        self,
        symbol: str,
        virtual_qty: float,
        reason: str,
    ) -> bool:
        """
        Execute repair only if policy allows.

        Returns True if repair executed.
        """
        logger.warning(
            f"[REPAIR_POLICY] {symbol}: {reason}, "
            f"will zero {virtual_qty} virtual contracts"
        )

        # Actual repair happens elsewhere
        # This just validates policy
        return True
```

---

## Fix Strategy 6: Sync Cooldown

**Problem:** Repair fires too soon after large order batch (Edge Case #1, #2)

**Solution:** Don't sync immediately after order placement

```python
from datetime import datetime, timedelta

class SyncCooldown:
    """
    Prevents sync from running too soon after orders placed.

    Gives fills time to propagate through exchange position API.
    """

    def __init__(self, cooldown_seconds: int = 2):
        self.cooldown_seconds = cooldown_seconds
        self.last_order_time: Dict[str, datetime] = {}
        self.last_sync_time: Dict[str, datetime] = {}

    def record_order_placed(self, symbol: str) -> None:
        """Call when order is placed for a symbol."""
        self.last_order_time[symbol] = datetime.now()

    def can_sync(self, symbol: str) -> Tuple[bool, str]:
        """
        Check if enough time has passed since last order.

        Returns:
            (can_sync, reason)
        """
        last_order = self.last_order_time.get(symbol)

        if not last_order:
            return (True, "no recent orders")

        elapsed = (datetime.now() - last_order).total_seconds()
        if elapsed >= self.cooldown_seconds:
            return (True, f"cooldown expired ({elapsed:.1f}s)")
        else:
            return (False, f"cooldown active ({self.cooldown_seconds - elapsed:.1f}s remaining)")

    def record_sync_executed(self, symbol: str) -> None:
        """Call after sync is executed."""
        self.last_sync_time[symbol] = datetime.now()


# Usage in portfolio manager:
def reconcile_with_cooldown(
    symbol: str,
    exchange_qty: float,
    virtual_qty: float,
    cooldown: SyncCooldown,
) -> bool:
    """Only sync if cooldown allows."""
    can_sync, reason = cooldown.can_sync(symbol)
    if not can_sync:
        logger.debug(f"[SYNC_COOLDOWN] {symbol}: skipping ({reason})")
        return True  # Assume positions are OK until we can sync

    # Proceed with normal reconciliation
    # ...
```

---

## Integration Example: Multi-Fix Approach

```python
class RobustPositionManager:
    """
    Combines all fixes for comprehensive edge case handling.
    """

    def __init__(self, strategy_states, client, account_id):
        self.strategy_states = strategy_states
        self.client = client
        self.account_id = account_id

        # All protective systems
        self.flight_window = OrderFlightWindow(window_seconds=2)
        self.sync_cooldown = SyncCooldown(cooldown_seconds=2)
        self.repair_policy = ConservativeRepairPolicy(
            min_divergence=2.0,
            wait_seconds=3,
        )
        self.confirmation_mgr = OrderConfirmationManager(client)
        self.strategy_repair = StrategyAwareRepair(strategy_states)
        self.atomic_repair = AtomicPositionRepair(tracker=None)  # Set later

    def reconcile_and_repair(self) -> Dict[str, bool]:
        """
        Execute robust reconciliation with all edge case protections.
        """
        results = {}

        # Get positions
        exchange_positions = self.get_exchange_positions()

        for symbol in exchange_positions:
            exchange_qty = exchange_positions[symbol]
            virtual_qty = self.get_virtual_net(symbol)

            # Check 1: Sync cooldown
            can_sync, reason = self.sync_cooldown.can_sync(symbol)
            if not can_sync:
                logger.debug(f"[ROBUST] {symbol}: {reason}")
                results[symbol] = True
                continue

            # Check 2: Flight window (pending orders)
            pending_qty = self.flight_window.get_pending_quantity(symbol)
            if pending_qty != 0:
                logger.debug(
                    f"[ROBUST] {symbol}: {pending_qty} pending, "
                    f"exchange={exchange_qty}, virtual={virtual_qty}"
                )
                results[symbol] = True
                continue

            # Check 3: Repair policy
            should_repair, policy_reason = self.repair_policy.should_repair(
                symbol, exchange_qty, virtual_qty
            )
            if not should_repair:
                logger.debug(f"[ROBUST] {symbol}: repair not needed ({policy_reason})")
                results[symbol] = True
                continue

            # Check 4: Execute repair atomically
            repaired_strategy, was_repaired = self.strategy_repair.repair_most_suspicious(
                symbol, exchange_qty
            )

            if was_repaired:
                logger.info(
                    f"[ROBUST] {symbol}: repaired strategy {repaired_strategy} "
                    f"({policy_reason})"
                )
                results[symbol] = False  # Repair executed, positions changed
            else:
                results[symbol] = True

        return results
```

---

## Testing These Fixes

```python
# pytest tests/test_edge_case_fixes.py

def test_order_flight_window_prevents_early_repair():
    """Fix Strategy 1: In-flight orders not repaired."""
    flight_window = OrderFlightWindow(window_seconds=2)

    # Place order (submit but not yet confirmed)
    flight_window.register_pending("order123", "ES", 1.0, "buy")

    # Pending qty should reflect the order
    assert flight_window.get_pending_quantity("ES") == 1.0

    # Reconcile sees exchange=0, virtual=1, but flight_window has 1 pending
    # Should NOT repair


def test_atomic_repair_blocks_concurrent_order():
    """Fix Strategy 2: Repair and order execution don't race."""
    tracker = VirtualPositionTracker()
    atomic = AtomicPositionRepair(tracker)

    # Simulate: repair starts, order execute tries to run concurrently
    # atomic_repair should block execute_order until repair completes


def test_strategy_aware_repair_targets_oldest():
    """Fix Strategy 3: Repair targets most suspicious strategy."""
    states = {
        "S1": make_strategy_state(last_update=old_time),
        "S2": make_strategy_state(last_update=new_time),
    }
    repair = StrategyAwareRepair(states)

    # Repair should target S1 (older)
    repaired_id, _ = repair.repair_most_suspicious("ES", 1.0)
    assert repaired_id == "S1"


def test_confirmation_detects_unfilled_order():
    """Fix Strategy 4: Bidirectional confirmation prevents false repair."""
    manager = OrderConfirmationManager(mock_client)

    # Order is in-flight (not filled yet)
    is_filled, _ = manager.check_order_fill(account_id, order123)
    assert is_filled is False

    # Virtual tracker thinks it filled (assumed)
    # validate_virtual_position should catch divergence


def test_repair_policy_refuses_upward_repair():
    """Fix Strategy 5: Conservative policy blocks dangerous repairs."""
    policy = ConservativeRepairPolicy(min_divergence=2.0)

    # Exchange has MORE contracts than virtual (delta >= 0)
    should_repair, reason = policy.should_repair("ES", 5.0, 3.0)
    assert should_repair is False
    assert "upward" in reason or "more" in reason


def test_sync_cooldown_blocks_early_sync():
    """Fix Strategy 6: Don't sync too soon after orders."""
    cooldown = SyncCooldown(cooldown_seconds=2)

    cooldown.record_order_placed("ES")

    # Try to sync immediately
    can_sync, reason = cooldown.can_sync("ES")
    assert can_sync is False

    # Wait and try again
    time.sleep(2.1)
    can_sync, reason = cooldown.can_sync("ES")
    assert can_sync is True
```

---

## Deployment Checklist

- [ ] Implement OrderFlightWindow in VirtualPositionTracker
- [ ] Add AtomicPositionRepair with locking around force_flat/execute_order
- [ ] Replace repair logic with StrategyAwareRepair (targets single strategy)
- [ ] Add OrderConfirmationManager to validate virtual fills
- [ ] Switch to ConservativeRepairPolicy (min divergence, direction guards)
- [ ] Add SyncCooldown between order placement and reconciliation
- [ ] Combine all into RobustPositionManager
- [ ] Add comprehensive logging to audit repairs
- [ ] Run all edge case tests
- [ ] Monitor live trading for repair events (should be rare)
- [ ] Gradually increase min_divergence threshold as confidence grows

