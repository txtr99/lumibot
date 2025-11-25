# Hybrid Bracket Order System: Broker + Client Safety Net

## Executive Summary

This design combines broker-native bracket orders (fast, resilient to disconnection) with client-side monitoring (safety net for broker failures). The client monitors but **only acts if broker fails**, preventing duplicate closures.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    HYBRID BRACKET SYSTEM                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐         ┌──────────────┐                     │
│  │   PRIMARY:   │         │   BACKUP:    │                     │
│  │   Broker     │────────▶│   Client     │                     │
│  │   Brackets   │  Status │   Monitor    │                     │
│  └──────────────┘  Feed   └──────────────┘                     │
│         │                         │                             │
│         │ (Fast execution)        │ (Safety net only)          │
│         │                         │                             │
│         ▼                         ▼                             │
│  ┌──────────────┐         ┌──────────────┐                     │
│  │   Broker     │         │   Client     │                     │
│  │   TP/SL      │         │   TP/SL      │                     │
│  │   Execution  │         │   Backup     │                     │
│  └──────────────┘         └──────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Core Principles

### 1. **Broker is Primary, Client is Safety Net**
- Broker brackets execute immediately on server-side (lowest latency)
- Client only monitors and acts if broker **provably failed**
- Avoid redundant closure at all costs

### 2. **Three-State Reconciliation Model**
```python
class BracketState(Enum):
    BROKER_ACTIVE = "broker_active"      # Broker handling it
    BROKER_FAILED = "broker_failed"      # Broker failed, client takes over
    CLOSED = "closed"                     # Position closed (any source)
```

### 3. **Detection via Absence, Not Presence**
- Don't trigger on price touch → wait for confirmation
- Don't trigger on network blips → use staleness timeout
- Don't trigger on partial data → wait for complete picture

---

## Design Components

### Component 1: Bracket State Tracker

```python
@dataclass
class HybridBracketState:
    """Tracks the complete lifecycle of a hybrid bracket."""

    # Identity
    parent_order_id: str
    position_asset: Asset
    position_side: str  # "long" or "short"
    position_qty: int
    entry_price: float

    # Bracket levels
    tp_price: Optional[float]
    sl_price: Optional[float]

    # Broker component
    broker_tp_order_id: Optional[str] = None
    broker_sl_order_id: Optional[str] = None
    broker_last_seen: Optional[datetime] = None  # Last confirmed alive
    broker_state: str = "active"  # active, failed, triggered, unknown

    # Client component
    client_monitoring: bool = True
    client_enabled: bool = False  # Only True if broker fails
    client_last_check: Optional[datetime] = None

    # Status tracking
    state: str = "BROKER_ACTIVE"  # BROKER_ACTIVE, BROKER_FAILED, CLOSED
    closed_by: Optional[str] = None  # "broker_tp", "broker_sl", "client_tp", "client_sl", "manual"
    closed_at: Optional[datetime] = None

    # Staleness detection
    STALENESS_TIMEOUT: ClassVar[timedelta] = timedelta(seconds=30)
    BROKER_FAILURE_THRESHOLD: ClassVar[timedelta] = timedelta(seconds=60)


class HybridBracketManager:
    """Manages hybrid bracket lifecycle with broker-primary, client-backup logic."""

    def __init__(self, broker, data_source):
        self.broker = broker
        self.data_source = data_source
        self.brackets: Dict[str, HybridBracketState] = {}
        self._lock = RLock()

        # Reconciliation state
        self._position_cache: Dict[str, Position] = {}
        self._order_status_cache: Dict[str, dict] = {}
        self._last_reconciliation: Optional[datetime] = None
```

---

### Component 2: Broker Status Detection

**Goal:** Detect when broker bracket has **provably** triggered or failed

```python
class BrokerBracketDetector:
    """Detects broker bracket status from order updates and position changes."""

    def detect_broker_status(self, bracket: HybridBracketState) -> str:
        """
        Returns: "active", "triggered", "failed", "unknown"

        Detection Logic:
        1. If position closed → "triggered" (broker succeeded)
        2. If orders canceled + position still open → "failed"
        3. If orders status stale > 60s + position open → "failed"
        4. If orders active and fresh → "active"
        5. Otherwise → "unknown" (keep monitoring)
        """

        # Step 1: Check position state
        position = self._get_current_position(bracket.position_asset)
        position_open = position and position.quantity != 0

        # Step 2: Query broker order status
        tp_status = self._get_order_status(bracket.broker_tp_order_id) if bracket.broker_tp_order_id else None
        sl_status = self._get_order_status(bracket.broker_sl_order_id) if bracket.broker_sl_order_id else None

        now = self.data_source.get_datetime()

        # CASE 1: Position closed → Broker succeeded
        if not position_open:
            if tp_status == "filled":
                return "triggered_tp"
            elif sl_status == "filled":
                return "triggered_sl"
            else:
                return "triggered_unknown"

        # CASE 2: Explicit order failure signals
        if (tp_status in ["canceled", "rejected", "error"] and
            sl_status in ["canceled", "rejected", "error"]):
            return "failed"

        # CASE 3: Staleness timeout (no updates, position still open)
        if bracket.broker_last_seen and (now - bracket.broker_last_seen) > bracket.BROKER_FAILURE_THRESHOLD:
            # Verify orders still exist and aren't just stale cache
            fresh_tp = self._fetch_fresh_order_status(bracket.broker_tp_order_id)
            fresh_sl = self._fetch_fresh_order_status(bracket.broker_sl_order_id)

            if fresh_tp in ["canceled", "rejected", None] and fresh_sl in ["canceled", "rejected", None]:
                return "failed"

        # CASE 4: Orders active and recently updated
        if (tp_status in ["open", "new"] or sl_status in ["open", "new"]):
            if bracket.broker_last_seen and (now - bracket.broker_last_seen) < bracket.STALENESS_TIMEOUT:
                return "active"

        # CASE 5: Ambiguous state
        return "unknown"

    def _get_order_status(self, order_id: str) -> Optional[str]:
        """Get cached order status (fast path)."""
        if not order_id:
            return None

        # Check stream cache first
        if order_id in self._order_status_cache:
            cached = self._order_status_cache[order_id]
            if (datetime.now() - cached['timestamp']) < timedelta(seconds=10):
                return cached['status']

        return None

    def _fetch_fresh_order_status(self, order_id: str) -> Optional[str]:
        """Fetch live order status from broker API (slow path, use sparingly)."""
        if not order_id:
            return None

        try:
            order = self.broker._pull_broker_order(order_id)
            if order:
                status = order.status
                self._order_status_cache[order_id] = {
                    'status': status,
                    'timestamp': datetime.now()
                }
                return status
        except Exception as e:
            logger.warning(f"Failed to fetch fresh order status for {order_id}: {e}")

        return None

    def _get_current_position(self, asset: Asset) -> Optional[Position]:
        """Get current position for asset."""
        try:
            return self.broker._pull_position(strategy=None, asset=asset)
        except Exception:
            return None
```

---

### Component 3: Duplicate Prevention Logic

**Goal:** Ensure client never closes a position broker is about to close

```python
class DuplicatePreventionGuard:
    """Prevents client from duplicating broker's closure."""

    def should_client_execute(self, bracket: HybridBracketState,
                             trigger_reason: str) -> Tuple[bool, str]:
        """
        Determines if client should execute TP/SL.

        Returns: (should_execute: bool, reason: str)
        """

        # RULE 1: Never execute if broker status is "active"
        broker_status = self.detector.detect_broker_status(bracket)
        if broker_status == "active":
            return False, "broker_active"

        # RULE 2: Never execute if broker recently triggered
        if broker_status.startswith("triggered"):
            return False, f"broker_already_closed_{broker_status}"

        # RULE 3: Only execute if broker provably failed
        if broker_status != "failed":
            return False, f"broker_status_ambiguous_{broker_status}"

        # RULE 4: Check position still exists
        position = self.detector._get_current_position(bracket.position_asset)
        if not position or position.quantity == 0:
            return False, "position_already_closed"

        # RULE 5: Grace period after last broker update (avoid race)
        now = self.data_source.get_datetime()
        if bracket.broker_last_seen and (now - bracket.broker_last_seen) < timedelta(seconds=15):
            return False, "grace_period_active"

        # RULE 6: Double-check order status one final time before executing
        final_tp_status = self.detector._fetch_fresh_order_status(bracket.broker_tp_order_id)
        final_sl_status = self.detector._fetch_fresh_order_status(bracket.broker_sl_order_id)

        if final_tp_status in ["open", "new"] or final_sl_status in ["open", "new"]:
            return False, "broker_orders_still_active"

        # ALL CHECKS PASSED: Client can safely execute
        return True, "broker_failed_client_takeover"
```

---

### Component 4: Position Reconciliation

**Goal:** Maintain accurate view of position state across broker and client

```python
class PositionReconciler:
    """Reconciles position state between broker truth and local tracking."""

    def reconcile_bracket_position(self, bracket: HybridBracketState) -> dict:
        """
        Reconciles bracket position state.

        Returns: {
            'position_exists': bool,
            'quantity': int,
            'broker_orders_exist': bool,
            'discrepancies': List[str]
        }
        """

        reconciliation = {
            'position_exists': False,
            'quantity': 0,
            'broker_orders_exist': False,
            'discrepancies': []
        }

        # 1. Fetch ground truth from broker
        position = self.detector._get_current_position(bracket.position_asset)

        if position and position.quantity != 0:
            reconciliation['position_exists'] = True
            reconciliation['quantity'] = position.quantity

            # Check if quantity matches expected
            if abs(position.quantity) != abs(bracket.position_qty):
                reconciliation['discrepancies'].append(
                    f"qty_mismatch: expected={bracket.position_qty}, actual={position.quantity}"
                )

        # 2. Check broker orders still exist
        tp_exists = False
        sl_exists = False

        if bracket.broker_tp_order_id:
            tp_status = self.detector._fetch_fresh_order_status(bracket.broker_tp_order_id)
            tp_exists = tp_status in ["open", "new", "pending"]

        if bracket.broker_sl_order_id:
            sl_status = self.detector._fetch_fresh_order_status(bracket.broker_sl_order_id)
            sl_exists = sl_status in ["open", "new", "pending"]

        reconciliation['broker_orders_exist'] = tp_exists or sl_exists

        # 3. Check for state inconsistencies
        if reconciliation['position_exists'] and not reconciliation['broker_orders_exist']:
            # Position open but no bracket orders → need client backup
            reconciliation['discrepancies'].append("position_unprotected")

        if not reconciliation['position_exists'] and reconciliation['broker_orders_exist']:
            # Orders exist but no position → orphaned orders
            reconciliation['discrepancies'].append("orphaned_orders")

        return reconciliation

    def periodic_reconciliation(self):
        """Periodic full reconciliation across all brackets."""

        logger.info("Starting periodic bracket reconciliation")

        for bracket_id, bracket in self.brackets.items():
            try:
                recon = self.reconcile_bracket_position(bracket)

                # Handle discrepancies
                if "position_unprotected" in recon['discrepancies']:
                    logger.warning(f"Bracket {bracket_id}: Position unprotected, enabling client backup")
                    bracket.client_enabled = True
                    bracket.state = "BROKER_FAILED"

                if "orphaned_orders" in recon['discrepancies']:
                    logger.warning(f"Bracket {bracket_id}: Orphaned orders detected, cleaning up")
                    self._cleanup_orphaned_orders(bracket)

                # Update last reconciliation time
                bracket.client_last_check = self.data_source.get_datetime()

            except Exception as e:
                logger.error(f"Reconciliation failed for bracket {bracket_id}: {e}")

        self._last_reconciliation = self.data_source.get_datetime()

    def _cleanup_orphaned_orders(self, bracket: HybridBracketState):
        """Cancel orphaned bracket orders that have no position."""
        if bracket.broker_tp_order_id:
            try:
                self.broker.cancel_order_by_id(bracket.broker_tp_order_id)
            except Exception:
                pass

        if bracket.broker_sl_order_id:
            try:
                self.broker.cancel_order_by_id(bracket.broker_sl_order_id)
            except Exception:
                pass
```

---

### Component 5: Client Backup Execution

**Goal:** Client closes position only when broker provably failed

```python
class ClientBackupExecutor:
    """Executes TP/SL on client side when broker fails."""

    def monitor_and_execute(self, bracket: HybridBracketState):
        """
        Main monitoring loop: check if client should take over.

        Called periodically by strategy on_trading_iteration.
        """

        # Skip if bracket already closed
        if bracket.state == "CLOSED":
            return

        # Step 1: Check broker status
        broker_status = self.detector.detect_broker_status(bracket)

        # Step 2: Update bracket state
        if broker_status == "active":
            bracket.broker_last_seen = self.data_source.get_datetime()
            bracket.state = "BROKER_ACTIVE"
            return

        if broker_status.startswith("triggered"):
            bracket.state = "CLOSED"
            bracket.closed_by = broker_status
            bracket.closed_at = self.data_source.get_datetime()
            logger.info(f"Bracket {bracket.parent_order_id} closed by broker: {broker_status}")
            return

        if broker_status == "failed":
            bracket.state = "BROKER_FAILED"
            bracket.client_enabled = True

        # Step 3: If broker failed, check if client should act
        if bracket.client_enabled:
            self._check_and_execute_client_backup(bracket)

    def _check_and_execute_client_backup(self, bracket: HybridBracketState):
        """Execute client-side TP/SL if conditions met."""

        # Get current market price
        current_price = self._get_current_price(bracket.position_asset)
        if not current_price:
            logger.warning(f"Cannot get current price for {bracket.position_asset}")
            return

        # Check TP trigger
        tp_triggered = False
        sl_triggered = False

        if bracket.tp_price:
            if bracket.position_side == "long" and current_price >= bracket.tp_price:
                tp_triggered = True
            elif bracket.position_side == "short" and current_price <= bracket.tp_price:
                tp_triggered = True

        # Check SL trigger
        if bracket.sl_price:
            if bracket.position_side == "long" and current_price <= bracket.sl_price:
                sl_triggered = True
            elif bracket.position_side == "short" and current_price >= bracket.sl_price:
                sl_triggered = True

        # Decide which to execute (SL takes priority)
        trigger_reason = None
        if sl_triggered:
            trigger_reason = "client_sl"
        elif tp_triggered:
            trigger_reason = "client_tp"

        if not trigger_reason:
            return  # No trigger yet

        # Final duplicate prevention check
        should_execute, reason = self.guard.should_client_execute(bracket, trigger_reason)

        if not should_execute:
            logger.info(f"Client execution blocked for {bracket.parent_order_id}: {reason}")
            return

        # EXECUTE CLIENT CLOSURE
        logger.warning(f"🚨 CLIENT BACKUP EXECUTING: {trigger_reason} for {bracket.parent_order_id}")

        try:
            close_order = self._create_close_order(bracket, trigger_reason)
            self.broker.submit_order(close_order)

            # Mark as closed
            bracket.state = "CLOSED"
            bracket.closed_by = trigger_reason
            bracket.closed_at = self.data_source.get_datetime()

            logger.info(f"✅ Client backup successfully closed position: {bracket.parent_order_id}")

        except Exception as e:
            logger.error(f"❌ Client backup execution failed for {bracket.parent_order_id}: {e}")
            # Don't mark as closed on failure, will retry next iteration

    def _create_close_order(self, bracket: HybridBracketState, reason: str) -> Order:
        """Create market order to close position."""

        # Reverse the side
        close_side = "sell" if bracket.position_side == "long" else "buy"

        order = Order(
            asset=bracket.position_asset,
            quantity=abs(bracket.position_qty),
            side=close_side,
            order_type="market",
            tag=f"CLIENT_BACKUP_{reason.upper()}_{bracket.parent_order_id}"
        )

        return order

    def _get_current_price(self, asset: Asset) -> Optional[float]:
        """Get current market price for asset."""
        try:
            return self.data_source.get_last_price(asset)
        except Exception:
            return None
```

---

### Component 6: Stream Event Handlers

**Goal:** Update bracket state from real-time broker events

```python
class BracketStreamHandler:
    """Handles real-time updates for hybrid brackets."""

    def on_order_update(self, order_update: dict):
        """
        Called when broker streams order update.

        Updates bracket tracking and staleness timestamps.
        """

        order_id = str(order_update.get('id'))
        status = order_update.get('status')

        # Check if this is a bracket order
        bracket = self._find_bracket_by_order_id(order_id)
        if not bracket:
            return  # Not a bracket order

        # Update broker last seen timestamp
        bracket.broker_last_seen = datetime.now()

        # Update order status cache
        self._order_status_cache[order_id] = {
            'status': status,
            'timestamp': datetime.now()
        }

        # Handle status transitions
        if status in ["filled", "fill"]:
            # Bracket order filled → position closed by broker
            if order_id == bracket.broker_tp_order_id:
                bracket.state = "CLOSED"
                bracket.closed_by = "broker_tp"
                bracket.closed_at = datetime.now()
                logger.info(f"Bracket {bracket.parent_order_id} TP filled by broker")

            elif order_id == bracket.broker_sl_order_id:
                bracket.state = "CLOSED"
                bracket.closed_by = "broker_sl"
                bracket.closed_at = datetime.now()
                logger.info(f"Bracket {bracket.parent_order_id} SL filled by broker")

        elif status in ["canceled", "cancelled", "rejected", "error"]:
            # Bracket order failed → may need client backup
            logger.warning(f"Bracket order {order_id} failed: {status}")

            # Check if ALL bracket orders failed
            broker_status = self.detector.detect_broker_status(bracket)
            if broker_status == "failed":
                logger.warning(f"All bracket orders failed, enabling client backup")
                bracket.state = "BROKER_FAILED"
                bracket.client_enabled = True

    def on_position_update(self, position_update: dict):
        """
        Called when broker streams position update.

        Detects position closure to infer bracket execution.
        """

        asset_symbol = position_update.get('symbol')
        quantity = position_update.get('quantity', 0)

        # Find brackets for this asset
        for bracket in self.brackets.values():
            if bracket.position_asset.symbol == asset_symbol:

                # Position closed → broker succeeded
                if quantity == 0 and bracket.state != "CLOSED":
                    logger.info(f"Position closed for {asset_symbol}, marking bracket as closed")
                    bracket.state = "CLOSED"
                    bracket.closed_by = "broker_unknown"
                    bracket.closed_at = datetime.now()
                    bracket.broker_last_seen = datetime.now()

    def _find_bracket_by_order_id(self, order_id: str) -> Optional[HybridBracketState]:
        """Find bracket that contains this order ID."""
        for bracket in self.brackets.values():
            if order_id in [bracket.broker_tp_order_id, bracket.broker_sl_order_id]:
                return bracket
        return None
```

---

## Integration with Existing ProjectX Broker

### Modification Points

```python
# lumibot/brokers/projectx.py

class ProjectX(Broker):

    def __init__(self, config, data_source, **kwargs):
        super().__init__(...)

        # Add hybrid bracket manager
        self.hybrid_bracket_manager = HybridBracketManager(
            broker=self,
            data_source=data_source
        )

    def _submit_order(self, order: Order) -> Order:
        """Override to support hybrid brackets."""

        # Check if this is a bracket order
        if order.order_class == Order.OrderClass.BRACKET:

            # Submit parent order with broker brackets
            submitted = self._submit_bracket_to_broker(order)

            # Register in hybrid manager for monitoring
            if submitted and submitted.id:
                bracket_state = HybridBracketState(
                    parent_order_id=submitted.id,
                    position_asset=order.asset,
                    position_side="long" if order.side == "buy" else "short",
                    position_qty=order.quantity,
                    entry_price=order.limit_price or 0,
                    tp_price=getattr(order, 'secondary_limit_price', None),
                    sl_price=getattr(order, 'secondary_stop_price', None),
                    broker_tp_order_id=submitted._synthetic_bracket.get('children', {}).get('tp'),
                    broker_sl_order_id=submitted._synthetic_bracket.get('children', {}).get('sl'),
                    broker_last_seen=datetime.now()
                )

                self.hybrid_bracket_manager.register_bracket(bracket_state)

            return submitted

        else:
            # Regular order
            return super()._submit_order(order)

    def _handle_order_update(self, data):
        """Override to update hybrid bracket tracking."""

        # Call original handler
        super()._handle_order_update(data)

        # Update hybrid bracket state
        self.hybrid_bracket_manager.stream_handler.on_order_update(data)

    def _handle_position_update(self, data):
        """Override to detect bracket closures."""

        # Call original handler
        super()._handle_position_update(data)

        # Update hybrid bracket state
        self.hybrid_bracket_manager.stream_handler.on_position_update(data)
```

### Strategy Integration

```python
# custom_portfolio/strategies/portfolio_manager.py

class PortfolioManager(Strategy):

    def on_trading_iteration(self):
        """Override to add bracket monitoring."""

        # Regular strategy logic
        # ...

        # Monitor hybrid brackets (client backup)
        if hasattr(self.broker, 'hybrid_bracket_manager'):
            self.broker.hybrid_bracket_manager.monitor_all_brackets()

        # Periodic reconciliation (every 10 iterations)
        if self.iteration_count % 10 == 0:
            if hasattr(self.broker, 'hybrid_bracket_manager'):
                self.broker.hybrid_bracket_manager.reconcile_all_brackets()
```

---

## Timeout and Staleness Handling

### Timeout Configuration

```python
# Configurable timeouts
TIMEOUTS = {
    'staleness_warning': timedelta(seconds=30),   # Warn if no updates
    'broker_failure': timedelta(seconds=60),      # Declare broker failed
    'grace_period': timedelta(seconds=15),        # Wait after last update
    'reconciliation_interval': timedelta(minutes=1)  # Full recon frequency
}
```

### Staleness Detection Flow

```
Time → 0s      30s [WARN]      60s [FAIL]      75s [EXECUTE]
       │        │               │               │
       ├────────┼───────────────┼───────────────┤
       │        │               │               │
  Order placed  No updates     Broker failed   Client executes
                logged         Client enabled  (after grace period)
```

---

## Pseudocode: Main Monitor Loop

```python
def monitor_all_brackets(self):
    """Main monitoring loop called each strategy iteration."""

    for bracket_id, bracket in list(self.brackets.items()):

        # Skip closed brackets
        if bracket.state == "CLOSED":
            continue

        # 1. Check broker status
        broker_status = self.detector.detect_broker_status(bracket)

        # 2. Update state machine
        if broker_status == "active":
            bracket.state = "BROKER_ACTIVE"
            bracket.broker_last_seen = now()
            continue

        elif broker_status.startswith("triggered"):
            bracket.state = "CLOSED"
            bracket.closed_by = broker_status
            self._remove_bracket(bracket_id)
            continue

        elif broker_status == "failed":
            bracket.state = "BROKER_FAILED"
            bracket.client_enabled = True
            logger.warning(f"Broker bracket failed for {bracket_id}, client taking over")

        elif broker_status == "unknown":
            # Ambiguous, keep monitoring
            pass

        # 3. If client enabled, check if should execute
        if bracket.client_enabled:
            self.executor.monitor_and_execute(bracket)
```

---

## Duplicate Prevention Guarantees

### Multi-Layer Safety Checks

```
Layer 1: Broker Status Check
         ↓
Layer 2: Position Reconciliation
         ↓
Layer 3: Grace Period Wait
         ↓
Layer 4: Final Order Status Refresh
         ↓
Layer 5: Atomic Execution Check
         ↓
      EXECUTE
```

### Race Condition Handling

**Scenario 1: Broker fills during client check**
```
T0: Client checks → broker status "failed"
T1: Client enters grace period
T2: Broker order fills (stream delayed)
T3: Client performs final check → sees filled order
T4: Client ABORTS execution ✓
```

**Scenario 2: Simultaneous broker + client fill**
```
T0: Client submits close order
T1: Broker order fills simultaneously
T2: Both orders attempt to close position
T3: Second order rejected (no position) ✓
```

**Scenario 3: Network partition**
```
T0: Network partition, no broker updates for 60s
T1: Client declares broker failed
T2: Client performs fresh API query → broker orders still active
T3: Client ABORTS execution ✓
```

---

## Testing Strategy

### Unit Tests

```python
def test_broker_active_prevents_client_execution():
    """Client should never execute while broker is active."""
    bracket = create_test_bracket()
    bracket.state = "BROKER_ACTIVE"
    bracket.broker_last_seen = datetime.now()

    should_execute, reason = guard.should_client_execute(bracket, "client_tp")
    assert not should_execute
    assert reason == "broker_active"


def test_broker_failure_enables_client():
    """Client should take over when broker fails."""
    bracket = create_test_bracket()
    bracket.broker_tp_order_id = "123"
    bracket.broker_sl_order_id = "456"

    # Mock broker orders as canceled
    mock_order_status("123", "canceled")
    mock_order_status("456", "canceled")

    status = detector.detect_broker_status(bracket)
    assert status == "failed"


def test_position_closed_prevents_client_execution():
    """Client should not execute if position already closed."""
    bracket = create_test_bracket()
    mock_position(bracket.position_asset, quantity=0)

    should_execute, reason = guard.should_client_execute(bracket, "client_tp")
    assert not should_execute
    assert "position_already_closed" in reason


def test_grace_period_prevents_premature_execution():
    """Client should wait grace period after last broker update."""
    bracket = create_test_bracket()
    bracket.broker_last_seen = datetime.now() - timedelta(seconds=10)

    should_execute, reason = guard.should_client_execute(bracket, "client_sl")
    assert not should_execute
    assert "grace_period" in reason
```

### Integration Tests

```python
def test_hybrid_bracket_broker_success_path():
    """Test normal path: broker handles everything."""
    # Submit bracket order
    order = create_bracket_order(tp=5050, sl=4950)
    submitted = broker.submit_order(order)

    # Verify hybrid bracket registered
    assert submitted.id in broker.hybrid_bracket_manager.brackets

    # Simulate broker TP fill
    simulate_broker_fill(submitted._synthetic_bracket['children']['tp'])

    # Verify client never executed
    assert bracket.closed_by.startswith("broker_")
    assert not bracket.client_enabled


def test_hybrid_bracket_broker_failure_client_takeover():
    """Test failure path: broker fails, client takes over."""
    # Submit bracket order
    order = create_bracket_order(tp=5050, sl=4950)
    submitted = broker.submit_order(order)

    # Simulate broker orders getting canceled
    simulate_order_cancel(submitted._synthetic_bracket['children']['tp'])
    simulate_order_cancel(submitted._synthetic_bracket['children']['sl'])

    # Wait for failure detection
    sleep(60)

    # Trigger client check
    broker.hybrid_bracket_manager.monitor_all_brackets()

    # Verify broker failure detected
    bracket = broker.hybrid_bracket_manager.brackets[submitted.id]
    assert bracket.state == "BROKER_FAILED"
    assert bracket.client_enabled

    # Simulate price hitting SL
    mock_price(order.asset, 4950)

    # Trigger client execution
    broker.hybrid_bracket_manager.monitor_all_brackets()

    # Verify client executed
    assert bracket.state == "CLOSED"
    assert bracket.closed_by == "client_sl"
```

---

## Configuration and Feature Flags

```python
HYBRID_BRACKET_CONFIG = {
    'enabled': True,

    # Timeouts
    'staleness_warning_seconds': 30,
    'broker_failure_timeout_seconds': 60,
    'grace_period_seconds': 15,
    'reconciliation_interval_seconds': 60,

    # Execution
    'client_backup_enabled': True,
    'require_fresh_status_check': True,
    'max_execution_retries': 3,

    # Monitoring
    'log_staleness_warnings': True,
    'log_state_transitions': True,
    'enable_reconciliation': True,

    # Safety
    'duplicate_prevention_enabled': True,
    'position_verification_required': True,
}
```

---

## Monitoring and Observability

### Metrics to Track

```python
HYBRID_BRACKET_METRICS = {
    'total_brackets': Counter('hybrid_brackets_total'),
    'broker_success': Counter('hybrid_brackets_broker_success'),
    'broker_failures': Counter('hybrid_brackets_broker_failures'),
    'client_executions': Counter('hybrid_brackets_client_executions'),
    'duplicate_preventions': Counter('hybrid_brackets_duplicate_prevented'),
    'staleness_warnings': Counter('hybrid_brackets_staleness_warnings'),
    'reconciliation_errors': Counter('hybrid_brackets_reconciliation_errors'),

    'broker_latency': Histogram('hybrid_brackets_broker_latency_ms'),
    'client_latency': Histogram('hybrid_brackets_client_latency_ms'),
    'time_to_failure_detection': Histogram('hybrid_brackets_failure_detection_ms'),
}
```

### Logging

```python
# State transitions
logger.info(f"[HYBRID] Bracket {id} state: {old_state} → {new_state}")

# Broker status
logger.debug(f"[HYBRID] Broker status for {id}: {status}")

# Client takeover
logger.warning(f"[HYBRID] 🚨 Client backup executing for {id}: {reason}")

# Duplicate prevention
logger.info(f"[HYBRID] ✋ Client execution blocked: {reason}")

# Reconciliation
logger.info(f"[HYBRID] Reconciliation complete: {stats}")
```

---

## Advantages of Hybrid Design

1. **Best of Both Worlds**
   - Broker: Fast execution, works when client disconnects
   - Client: Safety net for broker failures

2. **Eliminates Duplicate Risk**
   - Multi-layer verification before client acts
   - Grace periods and fresh status checks
   - Position reconciliation

3. **Resilient to Network Issues**
   - Broker handles most cases server-side
   - Client only acts on provable failure
   - Staleness detection with timeouts

4. **Production-Ready**
   - Comprehensive testing strategy
   - Metrics and observability
   - Feature flags for gradual rollout

5. **Backwards Compatible**
   - Existing broker logic unchanged
   - Hybrid manager is additive layer
   - Can be disabled via config

---

## Summary

This hybrid design provides **broker-primary execution with client-side safety net**, ensuring positions are protected even if broker brackets fail, while **eliminating the risk of duplicate closures** through multi-layer verification and staleness detection.

Key innovations:
- **Absence-based detection** (don't act on price touch, act on confirmed failure)
- **Grace periods** to avoid racing with broker
- **Fresh status verification** before client executes
- **Position reconciliation** to catch discrepancies
- **Comprehensive logging and metrics** for observability

The system is designed to be **paranoid about duplicates** and **conservative about client takeover**, only acting when broker has **provably failed** and **sufficient time has passed** to ensure no race conditions.
