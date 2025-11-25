# Bracket Order Failure Mode Analysis

## Executive Summary

This document analyzes critical failure modes in bracket order systems and their mitigations, based on the ProjectX broker implementation in Lumibot. Bracket orders consist of a parent entry order with linked take-profit (TP) and stop-loss (SL) child orders that form a synthetic OCO (One-Cancels-Other) structure.

## System Architecture Context

**Key Components:**
- **Parent Order**: Entry order (market/limit) with `order_class=BRACKET`
- **Child Orders**: TP (limit) and SL (stop) spawned after parent fill
- **Metadata Store**: `_bracket_meta` maps parent_id → {tp_price, sl_price, children{}, active}
- **Parent-Child Map**: `_bracket_parent_by_child_id` for reverse lookups
- **Order Cache**: `_orders_cache` preserves state across stream events

**Lifecycle Flow:**
1. Parent submission → store metadata with temp key
2. Broker assigns ID → migrate metadata to real ID
3. Parent fills → spawn TP/SL children with `BRK_TP_` and `BRK_STOP_` tags
4. Child fills → cancel sibling, deactivate bracket

---

## Failure Mode 1: Parent Order Fills But Child Orders Rejected

### How It Happens

**Root Causes:**
1. **Invalid child prices** (tick size violations, outside market bounds)
2. **Insufficient margin** after parent fill
3. **Platform restrictions** (order count limits, price bands)
4. **Network/API errors** during child submission
5. **Account status changes** between parent fill and child submission

**Example Scenario:**
```python
# Parent fills at 5000.0
parent = Order(asset=ES, qty=1, side='buy', limit_price=5000.0)
parent.secondary_limit_price = 5050.0  # TP
parent.secondary_stop_price = 4975.0   # SL

# Parent fills successfully
# TP child submission: SUCCESS (limit order at 5050.0)
# SL child submission: REJECTED (stop price violates exchange rules)
```

### What Goes Wrong

**Immediate Effects:**
- **Unprotected position**: Position is naked without stop-loss
- **Asymmetric risk**: TP works but no downside protection
- **Capital exposure**: Full notional value at risk
- **Margin consequences**: Position may consume unexpected margin

**System State:**
```python
# Metadata shows partial bracket
meta = {
    'tp_price': 5050.0,
    'sl_price': 4975.0,
    'children': {'tp': '1234'},  # SL missing!
    'active': True,
    'children_submitted': True
}
```

### Recovery Strategy

**Detection:**
```python
def _create_bracket_child(self, parent: Order, kind: str, price: float, base_tag: str) -> Order:
    """Create and submit bracket child with validation."""
    spec = build_bracket_child_spec(parent, kind, price, base_tag)
    child = Order(...)

    # Submit with error handling
    submitted = self._submit_order(child)

    if not submitted or not getattr(submitted, 'id', None):
        # DETECTION POINT: Child submission failed
        self.logger.error(f"Bracket child submission failed (kind={kind}) for parent {parent.id}")

        # Log for external monitoring
        self._alert_bracket_failure(parent, kind, reason="submission_failed")

    return submitted
```

**Mitigation Strategies:**

1. **Pre-validation** (Preventive):
```python
def _validate_bracket_prices(self, parent, tp_price, sl_price, tick_size):
    """Validate bracket prices before parent submission."""
    errors = []

    # Tick size validation
    if tp_price and (tp_price % tick_size != 0):
        errors.append(f"TP price {tp_price} not aligned to tick size {tick_size}")
    if sl_price and (sl_price % tick_size != 0):
        errors.append(f"SL price {sl_price} not aligned to tick size {tick_size}")

    # Price logic validation
    if parent.side == 'buy':
        if tp_price and tp_price <= parent.limit_price:
            errors.append(f"TP {tp_price} must be > entry {parent.limit_price}")
        if sl_price and sl_price >= parent.limit_price:
            errors.append(f"SL {sl_price} must be < entry {parent.limit_price}")

    return errors
```

2. **Retry Logic** (Reactive):
```python
def _create_bracket_child_with_retry(self, parent, kind, price, base_tag, max_retries=3):
    """Submit child with exponential backoff retry."""
    for attempt in range(max_retries):
        child = self._create_bracket_child(parent, kind, price, base_tag)

        if child and child.id:
            return child  # Success

        if attempt < max_retries - 1:
            delay = 2 ** attempt  # Exponential backoff
            self.logger.warning(f"Retry {attempt+1}/{max_retries} for {kind} child after {delay}s")
            time.sleep(delay)

    # All retries failed
    return None
```

3. **Emergency Market Exit** (Fallback):
```python
def _emergency_flatten_position(self, parent, reason):
    """Force-flatten position when bracket protection fails."""
    self.logger.error(f"EMERGENCY FLATTEN: {reason} for parent {parent.id}")

    # Create emergency market order (opposite side)
    exit_side = 'sell' if parent.side == 'buy' else 'buy'
    emergency_order = Order(
        strategy=parent.strategy,
        asset=parent.asset,
        quantity=parent.quantity,
        side=exit_side,
        order_type='market',
        tag=f"EMERGENCY_EXIT_{parent.id}"
    )

    self._submit_order(emergency_order)

    # Deactivate bracket
    if parent.id in self._bracket_meta:
        self._bracket_meta[parent.id]['active'] = False
```

4. **Monitoring & Alerts**:
```python
def _check_bracket_health(self, parent):
    """Periodic health check for bracket integrity."""
    if not self._is_bracket_parent(parent):
        return True

    meta = getattr(parent, '_synthetic_bracket', None)
    if not meta or not meta.get('active'):
        return True

    # Check if both children exist
    children = meta.get('children', {})
    tp_id = children.get('tp')
    sl_id = children.get('sl')

    missing = []
    if meta.get('tp_price') and not tp_id:
        missing.append('TP')
    if meta.get('sl_price') and not sl_id:
        missing.append('SL')

    if missing:
        self.logger.error(f"BRACKET INCOMPLETE: Parent {parent.id} missing {missing}")
        # Trigger alert/notification system
        self._alert_missing_protection(parent, missing)
        return False

    return True
```

**Recovery Actions:**
1. ✅ **Detect failure** via missing child IDs in metadata
2. ✅ **Retry submission** with exponential backoff (2-3 attempts)
3. ✅ **Adjust prices** if tick size or bounds violation detected
4. ⚠️ **Alert operator** if all retries fail
5. 🚨 **Emergency exit** position if unable to establish protection within timeout

---

## Failure Mode 2: TP Order Fills But SL Order Not Cancelled

### How It Happens

**Root Causes:**
1. **Race condition** between child fill event and sibling cancel request
2. **Network latency** to broker API during cancel
3. **Broker API failure** returning success=false for cancel
4. **Stream event reordering** (SL fill arrives before TP fill event)
5. **Stale order cache** causing wrong sibling lookup

**Example Scenario:**
```python
# Initial state: Both children active
TP order (1234) at 5050.0 - OPEN
SL order (1235) at 4975.0 - OPEN

# Market moves up, TP fills at 5050.0
Event: TP fill @ 5050.0

# Handler attempts to cancel SL
_handle_bracket_child_fill(tp_order)
  → parent_id = 9999
  → sibling_id = 1235
  → cancel_order(sl_order)  # ❌ FAILS (network error, API down, etc.)

# SL remains active! Position is FLAT but SL could re-enter
```

### What Goes Wrong

**Immediate Effects:**
- **Orphaned SL order**: Stop-loss remains active after position flat
- **Unintended re-entry**: SL could trigger, opening unwanted position
- **Opposite direction**: New position would be LONG if original was SHORT
- **Cascading bracket**: Re-entry might trigger new bracket, amplifying problem

**Sequence Diagram:**
```
TIME  | POSITION | TP (1234)    | SL (1235)    | PROBLEM
------|----------|--------------|--------------|------------------
T0    | +1 ES    | OPEN @5050   | OPEN @4975   | Normal bracket
T1    | +1 ES    | FILLED @5050 | OPEN @4975   | Position closing
T2    | FLAT     | FILLED       | OPEN @4975   | ❌ ORPHANED SL!
T3    | FLAT     | FILLED       | OPEN @4975   | Market at 5040
T4    | +1 ES    | FILLED       | FILLED @4975 | ❌ WRONG ENTRY!
```

### Recovery Strategy

**Detection:**
```python
def _handle_bracket_child_fill(self, child: Order):
    """Cancel sibling with robust error handling."""
    parent_id = self._bracket_parent_by_child_id.get(child.id)
    if not parent_id:
        return

    parent = self._orders_cache.get(parent_id)
    if not parent or not getattr(parent, '_synthetic_bracket', None):
        return

    meta = parent._synthetic_bracket
    if not meta.get('active', False):
        return

    # Find sibling
    siblings = meta.get('children', {})
    sibling_id = None
    for k, v in siblings.items():
        if v != child.id:
            sibling_id = v
            break

    if sibling_id and sibling_id in self._orders_cache:
        sibling_order = self._orders_cache[sibling_id]
        sibling_status = (getattr(sibling_order, 'status', '') or '').lower()

        # CURRENT: Cancel only if not terminal
        if sibling_status not in {"fill", "filled", "canceled", "cancelled", "error"}:
            try:
                success = self.cancel_order(sibling_order)

                # ⚠️ DETECTION POINT: Cancel might return False!
                if not success:
                    self.logger.error(f"SIBLING CANCEL FAILED: {sibling_id}")
                    # Trigger recovery...

            except Exception as e:
                self.logger.error(f"Failed cancel sibling {sibling_id}: {e}")
                # Trigger recovery...

    # Deactivate bracket
    meta['active'] = False
```

**Mitigation Strategies:**

1. **Retry with Verification** (Immediate):
```python
def _cancel_sibling_with_retry(self, sibling_order, parent_id, max_retries=3):
    """Cancel sibling with retry and verification."""
    for attempt in range(max_retries):
        try:
            # Attempt cancel
            success = self.cancel_order(sibling_order)

            if success:
                self.logger.info(f"Sibling {sibling_order.id} cancelled (attempt {attempt+1})")

                # Verify cancellation by checking status
                time.sleep(0.5)  # Brief delay for API propagation
                refreshed = self._pull_broker_order(sibling_order.id)

                if refreshed and refreshed.status in ('canceled', 'cancelled'):
                    return True  # Confirmed cancelled

                self.logger.warning(f"Cancel reported success but status is {refreshed.status}")

            if attempt < max_retries - 1:
                delay = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                time.sleep(delay)

        except Exception as e:
            self.logger.error(f"Sibling cancel attempt {attempt+1} failed: {e}")

    return False  # All retries exhausted
```

2. **Background Monitor** (Continuous):
```python
def _monitor_orphaned_orders(self):
    """Periodic check for orphaned bracket children."""
    orphans = []

    for order_id, order in self._orders_cache.items():
        # Check if order is bracket child
        if not getattr(order, '_is_bracket_child', False):
            continue

        # Check if parent bracket is inactive
        parent_id = self._bracket_parent_by_child_id.get(order_id)
        if not parent_id:
            continue

        parent = self._orders_cache.get(parent_id)
        if not parent:
            continue

        meta = getattr(parent, '_synthetic_bracket', None)
        if not meta or not meta.get('active'):
            # Bracket is inactive but child is still open
            if order.status in ('open', 'new'):
                orphans.append((order, parent))

    # Force-cancel orphans
    for orphan_order, parent in orphans:
        self.logger.warning(f"ORPHAN DETECTED: {orphan_order.id} from parent {parent.id}")
        try:
            self.cancel_order(orphan_order)
        except Exception as e:
            self.logger.error(f"Failed to cancel orphan {orphan_order.id}: {e}")
```

3. **Idempotent Cancel** (API Level):
```python
def cancel_order(self, order: Order) -> bool:
    """Cancel order with idempotent handling."""
    if not order.id:
        return False

    # Check if already terminal
    current_status = (order.status or '').lower()
    if current_status in {'fill', 'filled', 'canceled', 'cancelled', 'error'}:
        self.logger.debug(f"Order {order.id} already terminal ({current_status})")
        return True  # Already done, not a failure

    # Attempt cancel
    response = self.client.order_cancel(self.account_id, int(order.id))

    # Handle various response formats
    success = False
    if isinstance(response, dict):
        success = response.get("success") is True

        # Some brokers return success=True even if already cancelled
        error_msg = response.get("error") or response.get("errorMessage")
        if error_msg and "already" in error_msg.lower():
            return True  # Already cancelled, treat as success
    elif isinstance(response, bool):
        success = response

    if success:
        order.status = "cancelled"
        return True

    return False
```

4. **Position Reconciliation** (Safety Net):
```python
def _reconcile_position_vs_brackets(self):
    """Ensure bracket state matches actual positions."""
    positions = self._get_positions_at_broker()

    for order_id, meta in self._bracket_meta.items():
        if not meta.get('active'):
            continue

        parent = self._orders_cache.get(order_id)
        if not parent:
            continue

        # Check if position still exists
        has_position = any(
            pos.asset == parent.asset and pos.quantity != 0
            for pos in positions
        )

        if not has_position:
            # Position is flat but bracket active
            self.logger.warning(f"STALE BRACKET: {order_id} active but no position")

            # Force-cancel all children
            for child_kind, child_id in meta.get('children', {}).items():
                child_order = self._orders_cache.get(child_id)
                if child_order and child_order.status in ('open', 'new'):
                    self.logger.info(f"Cancelling orphan {child_kind} {child_id}")
                    self.cancel_order(child_order)

            # Deactivate bracket
            meta['active'] = False
```

**Recovery Actions:**
1. ✅ **Retry cancel** with exponential backoff (3 attempts, verify each)
2. ✅ **Monitor orphans** via background checker (60-second intervals)
3. ✅ **Position reconciliation** comparing active brackets vs actual positions
4. ✅ **Force-cancel** any orphaned children found
5. 🔒 **Prevent re-entry** by checking bracket metadata before accepting fills

---

## Failure Mode 3: Broker Connection Lost While In Position

### How It Happens

**Root Causes:**
1. **Network outage** (ISP, datacenter, routing issues)
2. **Broker API downtime** (maintenance, system failure)
3. **Authentication expiry** (token timeout, session invalidation)
4. **Rate limiting** (excessive API calls trigger throttling)
5. **Client-side crash** (process kill, OOM, system reboot)

**Example Scenario:**
```python
# System state before disconnect
Position: +1 ES @ 5000.0
TP order: 1234 @ 5050.0 (OPEN at broker)
SL order: 1235 @ 4975.0 (OPEN at broker)

# Network drops
[15:30:45] ERROR: WebSocket connection lost
[15:30:45] ERROR: HTTP API timeout after 10s
[15:30:46] INFO: Reconnection attempt 1/5 failed

# Meanwhile at broker:
[15:31:00] Market drops to 4975.0
[15:31:00] SL order 1235 FILLED @ 4975.0
[15:31:00] Position now FLAT
[15:31:00] TP order 1234 still OPEN (orphaned)

# Client reconnects
[15:32:00] INFO: Connection restored
[15:32:00] WARN: Local state desync detected
```

### What Goes Wrong

**Immediate Effects:**
- **State desynchronization**: Local cache doesn't reflect reality
- **Missed fills**: Order fills occurred during blackout
- **Orphaned orders**: Protective orders active without positions
- **Blind trading**: Cannot place/cancel orders reliably
- **Risk exposure**: Unknown position state during market volatility

**State Comparison:**
```python
# LOCAL STATE (stale)
position_cache = {
    'ES': Position(qty=1, avg_price=5000.0)
}
orders_cache = {
    '1234': Order(status='open', type='limit'),  # TP
    '1235': Order(status='open', type='stop')    # SL
}
bracket_meta = {
    '1233': {'active': True, 'children': {'tp': '1234', 'sl': '1235'}}
}

# BROKER STATE (reality)
position = FLAT (SL filled at 4975.0)
orders = {
    '1234': Order(status='open', type='limit'),  # ORPHANED TP!
    '1235': Order(status='filled', type='stop')  # Already filled
}
```

### Recovery Strategy

**Detection:**
```python
def _detect_connection_loss(self):
    """Monitor connection health."""
    # Check WebSocket
    if self.streaming_client:
        if not self.streaming_client.is_user_connected:
            return True

    # Check HTTP API
    try:
        response = self.client.api.account_search()
        if not response or not response.get('success'):
            return True
    except Exception:
        return True

    return False

def _on_disconnect(self):
    """Handle connection loss."""
    self.logger.error("🔴 BROKER CONNECTION LOST")
    self._connection_lost_at = datetime.now()
    self._is_connected = False

    # Set flag to trigger full resync on reconnect
    self._needs_full_resync = True

    # Optionally flatten all positions (aggressive but safe)
    if self.config.get('flatten_on_disconnect'):
        self._emergency_flatten_all()
```

**Mitigation Strategies:**

1. **Automatic Reconnection** (Resilience):
```python
def _reconnect_with_backoff(self, max_attempts=10):
    """Reconnect with exponential backoff."""
    for attempt in range(max_attempts):
        try:
            self.logger.info(f"Reconnection attempt {attempt+1}/{max_attempts}")

            # Refresh authentication token
            self.token = ProjectXAuth.get_auth_token(self.config)
            if not self.token:
                raise Exception("Token refresh failed")

            # Reinitialize client
            self.client = ProjectXClient(self.config)
            self.account_id = self.client.get_preferred_account_id()

            # Reconnect streaming
            if self.connect_stream:
                self._setup_streaming()

            self._is_connected = True
            self.logger.info("✅ Reconnection successful")
            return True

        except Exception as e:
            self.logger.error(f"Reconnection attempt {attempt+1} failed: {e}")

            if attempt < max_attempts - 1:
                delay = min(2 ** attempt, 60)  # Cap at 60s
                time.sleep(delay)

    return False
```

2. **Full State Resync** (Reconciliation):
```python
def _resync_after_reconnect(self):
    """Perform full state reconciliation after reconnection."""
    self.logger.info("🔄 Starting full state resync")

    try:
        # 1. Get ground truth from broker
        broker_positions = self._get_positions_at_broker()
        broker_orders = self._get_orders_at_broker()

        # 2. Identify discrepancies
        position_diffs = self._compare_positions(
            local=list(self._positions_cache.values()),
            broker=broker_positions
        )

        order_diffs = self._compare_orders(
            local=list(self._orders_cache.values()),
            broker=broker_orders
        )

        # 3. Log all discrepancies
        if position_diffs:
            self.logger.warning(f"Position discrepancies: {position_diffs}")
        if order_diffs:
            self.logger.warning(f"Order discrepancies: {order_diffs}")

        # 4. Overwrite local state with broker truth
        self._positions_cache = {
            pos.asset.symbol: pos for pos in broker_positions
        }
        self._orders_cache = {
            order.id: order for order in broker_orders
        }

        # 5. Rebuild bracket metadata from order tags
        self._rebuild_bracket_metadata()

        # 6. Cancel orphaned bracket children
        self._cleanup_orphaned_brackets()

        self.logger.info("✅ State resync complete")
        return True

    except Exception as e:
        self.logger.error(f"State resync failed: {e}")
        return False

def _compare_positions(self, local, broker):
    """Compare local and broker positions."""
    diffs = []

    # Build lookup maps
    local_map = {pos.asset.symbol: pos for pos in local}
    broker_map = {pos.asset.symbol: pos for pos in broker}

    # Check for quantity mismatches
    for symbol in set(local_map.keys()) | set(broker_map.keys()):
        local_qty = local_map.get(symbol, Position(asset=None, quantity=0)).quantity
        broker_qty = broker_map.get(symbol, Position(asset=None, quantity=0)).quantity

        if local_qty != broker_qty:
            diffs.append({
                'symbol': symbol,
                'local_qty': local_qty,
                'broker_qty': broker_qty,
                'delta': broker_qty - local_qty
            })

    return diffs

def _rebuild_bracket_metadata(self):
    """Reconstruct bracket metadata from order tags."""
    self.logger.info("Rebuilding bracket metadata from order tags")

    # Clear existing metadata
    self._bracket_meta = {}
    self._bracket_parent_by_child_id = {}

    # Scan all orders for bracket tags
    for order_id, order in self._orders_cache.items():
        tag = getattr(order, 'tag', '')
        if not tag:
            continue

        # Detect parent (BRK_ENTRY_*)
        if tag.startswith('BRK_ENTRY_'):
            base_tag = tag[10:]  # Remove "BRK_ENTRY_"

            # Find matching children
            tp_child = None
            sl_child = None

            for child_id, child in self._orders_cache.items():
                child_tag = getattr(child, 'tag', '')
                if child_tag == f'BRK_TP_{base_tag}':
                    tp_child = child
                elif child_tag == f'BRK_STOP_{base_tag}':
                    sl_child = child

            # Reconstruct metadata
            meta = {
                'base_tag': base_tag,
                'children': {},
                'active': order.status not in ('canceled', 'cancelled', 'error')
            }

            if tp_child:
                meta['tp_price'] = tp_child.limit_price
                meta['children']['tp'] = tp_child.id
                self._bracket_parent_by_child_id[tp_child.id] = order_id

            if sl_child:
                meta['sl_price'] = sl_child.stop_price
                meta['children']['sl'] = sl_child.id
                self._bracket_parent_by_child_id[sl_child.id] = order_id

            self._bracket_meta[order_id] = meta
            order._synthetic_bracket = meta

    self.logger.info(f"Rebuilt {len(self._bracket_meta)} bracket metadata entries")
```

3. **Persistent State** (Durability):
```python
def _persist_state_to_disk(self):
    """Save critical state to disk for crash recovery."""
    import json

    state = {
        'timestamp': datetime.now().isoformat(),
        'account_id': self.account_id,
        'positions': [
            {'symbol': p.asset.symbol, 'quantity': p.quantity}
            for p in self._positions_cache.values()
        ],
        'orders': [
            {
                'id': o.id,
                'symbol': o.asset.symbol if o.asset else None,
                'status': o.status,
                'side': o.side,
                'quantity': o.quantity,
                'tag': o.tag
            }
            for o in self._orders_cache.values()
        ],
        'bracket_meta': {
            order_id: {
                'tp_price': meta.get('tp_price'),
                'sl_price': meta.get('sl_price'),
                'active': meta.get('active'),
                'children': meta.get('children', {})
            }
            for order_id, meta in self._bracket_meta.items()
        }
    }

    state_file = f"/tmp/lumibot_state_{self.account_id}.json"
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)

    self.logger.debug(f"State persisted to {state_file}")

def _recover_state_from_disk(self):
    """Recover state from disk after crash."""
    import json

    state_file = f"/tmp/lumibot_state_{self.account_id}.json"

    try:
        with open(state_file, 'r') as f:
            state = json.load(f)

        # Check if state is recent (within 10 minutes)
        timestamp = datetime.fromisoformat(state['timestamp'])
        age = (datetime.now() - timestamp).total_seconds()

        if age > 600:  # 10 minutes
            self.logger.warning(f"Persisted state is {age}s old, skipping recovery")
            return False

        self.logger.info(f"Recovering state from {state_file} (age: {age}s)")

        # Use persisted state as hint for resync
        # (Don't trust completely, still verify with broker)
        return state

    except FileNotFoundError:
        self.logger.debug("No persisted state found")
        return None
    except Exception as e:
        self.logger.error(f"Failed to recover state: {e}")
        return None
```

4. **Broker-Side Protection** (Ultimate Failsafe):
```python
# Set GTC (Good-Till-Cancelled) + stop-loss at broker level
# This ensures stop-loss persists even if client crashes

def _submit_order(self, order):
    """Submit with broker-side protection."""
    # For bracket orders, set GTC on children
    if order.order_class == Order.OrderClass.BRACKET:
        order.time_in_force = 'gtc'  # Persist beyond session

    # Add emergency stop at broker if supported
    if self.client.supports_emergency_stops():
        self.client.set_emergency_stop(
            asset=order.asset,
            stop_price=order.secondary_stop_price,
            time_in_force='gtc'
        )

    return super()._submit_order(order)
```

**Recovery Actions:**
1. ✅ **Auto-reconnect** with exponential backoff (10 attempts, up to 60s delay)
2. ✅ **Full state resync** comparing local cache vs broker state
3. ✅ **Rebuild metadata** from order tags (BRK_ENTRY_*, BRK_TP_*, BRK_STOP_*)
4. ✅ **Persist state** to disk every 60 seconds for crash recovery
5. ✅ **Cancel orphans** found during reconciliation
6. 🔒 **Broker-level stops** (if platform supports) as ultimate protection

---

## Failure Mode 4: Order Stuck in Pending State

### How It Happens

**Root Causes:**
1. **Broker processing delays** (high system load, queue backlog)
2. **Risk checks** (margin validation, position limits, price checks)
3. **Exchange connectivity** (broker-to-exchange link issues)
4. **Order book conditions** (no liquidity at limit price)
5. **API/platform bugs** (order stuck in state machine)

**Example Scenario:**
```python
# Parent order submitted
[15:30:00] INFO: Submitting bracket parent order
[15:30:00] DEBUG: Order 9999 submitted, status=new

# Order appears stuck
[15:30:05] DEBUG: Order 9999 status=new
[15:30:10] DEBUG: Order 9999 status=new
[15:30:15] DEBUG: Order 9999 status=new
[15:30:20] DEBUG: Order 9999 status=new  # Still pending!

# Meanwhile:
- Position not opened
- Bracket children not spawned
- Strategy logic blocked waiting for fill
- Market opportunity missed
```

### What Goes Wrong

**Immediate Effects:**
- **Strategy deadlock**: Logic waiting for fill that never comes
- **Resource leak**: Order occupies slot in tracking maps
- **Missed opportunities**: Cannot enter new positions while waiting
- **False accounting**: Position size calculations incorrect
- **Timeout ambiguity**: Don't know if order will eventually fill

**State Diagram:**
```
UNPROCESSED → NEW → (STUCK) → ???
                ↓
            (timeout)
                ↓
         Should be OPEN or FILLED or CANCELLED
```

### Recovery Strategy

**Detection:**
```python
def _detect_stuck_orders(self, timeout_seconds=30):
    """Find orders stuck in pending states."""
    stuck_orders = []
    now = datetime.now()

    for order_id, order in self._orders_cache.items():
        # Check if order is in pending state
        if order.status not in ('new', 'open', 'submitted'):
            continue

        # Check age
        created_at = getattr(order, 'created_at', None)
        if not created_at:
            continue

        age = (now - created_at).total_seconds()

        if age > timeout_seconds:
            stuck_orders.append({
                'order': order,
                'age': age,
                'status': order.status
            })

    return stuck_orders
```

**Mitigation Strategies:**

1. **Active Polling** (Monitoring):
```python
def _monitor_order_lifecycle(self, order, max_age_seconds=60):
    """Poll order status until terminal or timeout."""
    start_time = datetime.now()

    while True:
        elapsed = (datetime.now() - start_time).total_seconds()

        if elapsed > max_age_seconds:
            self.logger.error(f"Order {order.id} stuck for {elapsed}s")
            return 'timeout'

        # Refresh order status from broker
        try:
            broker_order = self._pull_broker_order(order.id)
            if not broker_order:
                self.logger.warning(f"Order {order.id} not found at broker")
                return 'missing'

            # Check if terminal
            if broker_order.status in ('fill', 'filled', 'canceled', 'cancelled', 'error'):
                self.logger.info(f"Order {order.id} reached terminal state: {broker_order.status}")
                return broker_order.status

            # Log progress
            if broker_order.status != order.status:
                self.logger.info(f"Order {order.id} status changed: {order.status} → {broker_order.status}")
                order.status = broker_order.status

        except Exception as e:
            self.logger.error(f"Error polling order {order.id}: {e}")

        # Wait before next poll
        time.sleep(2)
```

2. **Timeout & Cancel** (Cleanup):
```python
def _handle_stuck_order(self, order, age_seconds):
    """Handle order that's been stuck too long."""
    self.logger.warning(f"Handling stuck order {order.id} (age: {age_seconds}s)")

    # Strategy 1: Try to cancel
    try:
        self.logger.info(f"Attempting to cancel stuck order {order.id}")
        success = self.cancel_order(order)

        if success:
            self.logger.info(f"Successfully cancelled stuck order {order.id}")
            return 'cancelled'

        # Cancel failed, try to verify current state
        self.logger.warning(f"Cancel request returned false for {order.id}")

    except Exception as e:
        self.logger.error(f"Cancel failed for stuck order {order.id}: {e}")

    # Strategy 2: Force refresh from broker
    try:
        self.logger.info(f"Force-refreshing order {order.id} from broker")
        broker_order = self._pull_broker_order(order.id)

        if broker_order:
            # Update local cache
            self._orders_cache[order.id] = broker_order

            if broker_order.status != 'new':
                self.logger.info(f"Order {order.id} actual status: {broker_order.status}")
                return broker_order.status
        else:
            self.logger.error(f"Order {order.id} not found at broker (possibly rejected)")
            order.status = 'error'
            return 'error'

    except Exception as e:
        self.logger.error(f"Failed to refresh order {order.id}: {e}")

    # Strategy 3: Mark as error and alert
    self.logger.error(f"UNRECOVERABLE: Order {order.id} stuck, marking as error")
    order.status = 'error'
    order.error_message = f"Stuck in {order.status} for {age_seconds}s"

    # Trigger alert
    self._alert_stuck_order(order, age_seconds)

    return 'error'
```

3. **Proactive Timeout** (Prevention):
```python
def _submit_order_with_timeout(self, order, timeout_seconds=30):
    """Submit order with automatic timeout handling."""
    # Submit order
    submitted = self._submit_order(order)

    if not submitted or not submitted.id:
        self.logger.error("Order submission failed immediately")
        return None

    # Start timeout monitor thread
    def timeout_monitor():
        time.sleep(timeout_seconds)

        # Check if still pending
        current = self._orders_cache.get(submitted.id)
        if not current:
            return

        if current.status in ('new', 'open', 'submitted'):
            self.logger.warning(f"Order {submitted.id} timeout reached")
            self._handle_stuck_order(current, timeout_seconds)

    import threading
    monitor_thread = threading.Thread(target=timeout_monitor, daemon=True)
    monitor_thread.start()

    return submitted
```

4. **Alternative Order Types** (Workaround):
```python
def _submit_with_fallback(self, order):
    """Submit with fallback to simpler order type."""
    # Try original order
    submitted = self._submit_order(order)

    if not submitted:
        return None

    # Monitor for quick rejection or hang
    time.sleep(5)  # Brief wait

    refreshed = self._pull_broker_order(submitted.id)

    if not refreshed:
        self.logger.warning(f"Order {submitted.id} disappeared, trying market fallback")

        # Cancel original (best effort)
        try:
            self.cancel_order(submitted)
        except Exception:
            pass

        # Fallback to market order
        if order.order_type == 'limit':
            self.logger.info(f"Falling back to market order for {order.asset.symbol}")

            market_order = Order(
                strategy=order.strategy,
                asset=order.asset,
                quantity=order.quantity,
                side=order.side,
                order_type='market',  # Simpler type
                tag=f"{order.tag}_MARKET_FALLBACK"
            )

            return self._submit_order(market_order)

    return submitted
```

**Recovery Actions:**
1. ✅ **Poll status** every 2s for up to 60s
2. ✅ **Cancel stuck order** after timeout threshold
3. ✅ **Force refresh** from broker if cancel fails
4. ✅ **Mark as error** if unrecoverable
5. 🔔 **Alert operator** for manual intervention
6. 🔄 **Fallback to market** order if limit order hangs

---

## Failure Mode 5: Partial Fills on Exit Orders

### How It Happens

**Root Causes:**
1. **Thin liquidity** (small order book depth at TP/SL price)
2. **Large position sizes** (position larger than available liquidity)
3. **Rapid price movements** (price gaps through TP/SL level)
4. **Order book updates** (liquidity pulled before complete fill)
5. **Exchange mechanics** (FOK/IOC time-in-force restrictions)

**Example Scenario:**
```python
# Initial position
Position: +10 ES @ 5000.0
TP order: limit @ 5050.0 for 10 contracts
SL order: stop @ 4975.0 for 10 contracts

# Market moves up, TP triggered
[15:30:00] Limit order starts filling
[15:30:00] Filled 3 contracts @ 5050.0
[15:30:01] Filled 2 contracts @ 5049.75
[15:30:02] Filled 2 contracts @ 5049.50
[15:30:05] Order partially filled: 7/10
[15:30:10] No more fills, order still OPEN with 3 remaining

# Current state:
Position: +3 ES (still exposed!)
TP order: PARTIAL (3 contracts remaining)
SL order: OPEN for 10 contracts (wrong quantity!)
```

### What Goes Wrong

**Immediate Effects:**
- **Incomplete exit**: Position not fully closed
- **Residual exposure**: Remaining quantity still at risk
- **Size mismatch**: SL order sized for original quantity
- **Over-protection**: If SL triggers, could reverse position
- **Accounting errors**: P&L calculations incorrect

**Quantity Mismatch:**
```python
# After partial TP fill of 7/10
Actual Position: +3 ES
TP Remaining: 3 contracts @ 5050.0  ✅ Correct
SL Active: 10 contracts @ 4975.0   ❌ WRONG! Should be 3

# If SL triggers:
Expected: Flatten position (-3 ES)
Actual: Sell 10 ES → Position becomes -7 ES ⚠️ REVERSED!
```

### Recovery Strategy

**Detection:**
```python
def _detect_partial_fills(self):
    """Find orders with partial fills."""
    partial_fills = []

    for order_id, order in self._orders_cache.items():
        filled_qty = getattr(order, 'filled_quantity', 0)
        total_qty = getattr(order, 'quantity', 0)

        # Check if partially filled
        if 0 < filled_qty < total_qty:
            # Still in active state
            if order.status in ('open', 'partial_fill'):
                partial_fills.append({
                    'order': order,
                    'filled': filled_qty,
                    'remaining': total_qty - filled_qty
                })

    return partial_fills

def _check_bracket_quantity_sync(self):
    """Verify bracket children match actual position size."""
    mismatches = []

    for parent_id, meta in self._bracket_meta.items():
        if not meta.get('active'):
            continue

        parent = self._orders_cache.get(parent_id)
        if not parent:
            continue

        # Get current position size
        position = self._get_position_for_asset(parent.asset)
        actual_qty = position.quantity if position else 0

        # Check each child
        for child_kind, child_id in meta.get('children', {}).items():
            child = self._orders_cache.get(child_id)
            if not child:
                continue

            if child.quantity != abs(actual_qty):
                mismatches.append({
                    'parent_id': parent_id,
                    'child_kind': child_kind,
                    'child_id': child_id,
                    'child_qty': child.quantity,
                    'position_qty': actual_qty,
                    'delta': child.quantity - abs(actual_qty)
                })

    return mismatches
```

**Mitigation Strategies:**

1. **Dynamic Quantity Adjustment** (Reactive):
```python
def _adjust_sibling_for_partial_fill(self, filled_child, filled_qty, remaining_qty):
    """Adjust sibling order quantity when one child partially fills."""
    parent_id = self._bracket_parent_by_child_id.get(filled_child.id)
    if not parent_id:
        return

    parent = self._orders_cache.get(parent_id)
    if not parent or not getattr(parent, '_synthetic_bracket', None):
        return

    meta = parent._synthetic_bracket

    # Find sibling
    siblings = meta.get('children', {})
    sibling_id = None
    for k, v in siblings.items():
        if v != filled_child.id:
            sibling_id = v
            break

    if not sibling_id:
        return

    sibling = self._orders_cache.get(sibling_id)
    if not sibling:
        return

    # Calculate new sibling quantity (should match remaining position)
    new_sibling_qty = remaining_qty

    if sibling.quantity == new_sibling_qty:
        self.logger.debug(f"Sibling {sibling_id} already correct size: {new_sibling_qty}")
        return

    self.logger.info(f"Adjusting sibling {sibling_id} qty: {sibling.quantity} → {new_sibling_qty}")

    # Cancel old sibling
    try:
        self.cancel_order(sibling)
    except Exception as e:
        self.logger.error(f"Failed to cancel sibling {sibling_id}: {e}")
        return

    # Submit new sibling with adjusted quantity
    try:
        new_sibling = self._create_bracket_child(
            parent=parent,
            kind='sl' if sibling.tag.startswith('BRK_STOP_') else 'tp',
            price=sibling.stop_price or sibling.limit_price,
            base_tag=meta.get('base_tag', '')
        )

        # Update quantity
        new_sibling.quantity = new_sibling_qty

        # Resubmit
        submitted = self._submit_order(new_sibling)

        if submitted and submitted.id:
            # Update metadata
            for k, v in siblings.items():
                if v == sibling_id:
                    siblings[k] = submitted.id
                    self._bracket_parent_by_child_id[submitted.id] = parent_id
                    break

            self.logger.info(f"Sibling adjusted: {sibling_id} → {submitted.id} (qty={new_sibling_qty})")

    except Exception as e:
        self.logger.error(f"Failed to resubmit adjusted sibling: {e}")
```

2. **Position-Based Sizing** (Continuous):
```python
def _reconcile_bracket_quantities(self):
    """Periodically sync bracket children with actual positions."""
    for parent_id, meta in self._bracket_meta.items():
        if not meta.get('active'):
            continue

        parent = self._orders_cache.get(parent_id)
        if not parent:
            continue

        # Get real position
        position = self._get_position_for_asset(parent.asset)
        if not position or position.quantity == 0:
            # No position, cancel all children
            self.logger.info(f"No position for {parent.asset.symbol}, cancelling bracket children")
            self._cancel_all_bracket_children(parent_id)
            meta['active'] = False
            continue

        actual_qty = abs(position.quantity)

        # Check each child
        for child_kind, child_id in meta.get('children', {}).items():
            child = self._orders_cache.get(child_id)
            if not child:
                continue

            if child.status not in ('open', 'new'):
                continue

            if child.quantity != actual_qty:
                self.logger.warning(
                    f"Quantity mismatch: {child_kind} child {child_id} "
                    f"qty={child.quantity} vs position={actual_qty}"
                )

                # Cancel and replace with correct size
                try:
                    self.cancel_order(child)

                    # Create replacement with correct size
                    price = child.limit_price or child.stop_price
                    new_child = self._create_bracket_child(
                        parent=parent,
                        kind=child_kind,
                        price=price,
                        base_tag=meta.get('base_tag', '')
                    )
                    new_child.quantity = actual_qty

                    submitted = self._submit_order(new_child)
                    if submitted and submitted.id:
                        meta['children'][child_kind] = submitted.id
                        self._bracket_parent_by_child_id[submitted.id] = parent_id

                except Exception as e:
                    self.logger.error(f"Failed to reconcile child {child_id}: {e}")
```

3. **Partial Fill Events** (Real-time):
```python
def _handle_partial_fill_event(self, order, filled_qty):
    """Handle partial fill on bracket child."""
    self.logger.info(f"Partial fill: {order.id} filled {filled_qty}/{order.quantity}")

    # Check if this is a bracket child
    if not getattr(order, '_is_bracket_child', False):
        return

    # Calculate remaining position
    remaining_qty = order.quantity - filled_qty

    # Adjust sibling immediately
    self._adjust_sibling_for_partial_fill(order, filled_qty, remaining_qty)

    # If completely filled, trigger normal sibling cancel
    if filled_qty >= order.quantity:
        self._handle_bracket_child_fill(order)
```

4. **Limit-Only for TP, Market for SL** (Design Change):
```python
def _create_bracket_child(self, parent, kind, price, base_tag):
    """Create child with smart order type selection."""
    spec = build_bracket_child_spec(parent, kind, price, base_tag)

    # Modification: Use market order for stop-loss to ensure full fill
    if kind == 'sl':
        # SL becomes stop-market instead of stop-limit
        spec['order_type'] = 'stop'  # Market on trigger

        # OR: Use wider stop-limit to reduce partial fill risk
        # spec['order_type'] = 'stop_limit'
        # spec['limit_price'] = price - slippage_buffer

    child = Order(
        strategy=parent.strategy,
        asset=parent.asset,
        quantity=parent.quantity,
        side=spec['side'],
        order_type=spec['order_type']
    )

    # ... rest of implementation
```

**Recovery Actions:**
1. ✅ **Detect partial fills** via filled_quantity vs quantity comparison
2. ✅ **Adjust sibling** immediately when partial fill detected
3. ✅ **Position reconciliation** every 30 seconds comparing child qty vs position
4. ✅ **Cancel & replace** mismatched children with correct sizes
5. 🔄 **Smart order types** (market SL, limit TP) to minimize partial fills
6. 📊 **Partial fill alerts** for monitoring and analysis

---

## Cross-Cutting Recovery Patterns

### 1. Health Check System

```python
def _run_health_checks(self):
    """Comprehensive health check for bracket system."""
    issues = []

    # Check 1: Orphaned children
    for order_id, order in self._orders_cache.items():
        if getattr(order, '_is_bracket_child', False):
            parent_id = self._bracket_parent_by_child_id.get(order_id)
            if not parent_id or parent_id not in self._orders_cache:
                issues.append(f"Orphaned child: {order_id}")

    # Check 2: Active brackets without positions
    positions = {p.asset.symbol: p for p in self._get_positions_at_broker()}
    for parent_id, meta in self._bracket_meta.items():
        if not meta.get('active'):
            continue
        parent = self._orders_cache.get(parent_id)
        if parent and parent.asset.symbol not in positions:
            issues.append(f"Active bracket {parent_id} without position")

    # Check 3: Incomplete brackets
    for parent_id, meta in self._bracket_meta.items():
        if not meta.get('active'):
            continue
        children = meta.get('children', {})
        if meta.get('tp_price') and 'tp' not in children:
            issues.append(f"Bracket {parent_id} missing TP child")
        if meta.get('sl_price') and 'sl' not in children:
            issues.append(f"Bracket {parent_id} missing SL child")

    # Check 4: Quantity mismatches
    mismatches = self._check_bracket_quantity_sync()
    for mm in mismatches:
        issues.append(f"Quantity mismatch: child {mm['child_id']} qty={mm['child_qty']} vs position={mm['position_qty']}")

    # Check 5: Stuck orders
    stuck = self._detect_stuck_orders(timeout_seconds=60)
    for s in stuck:
        issues.append(f"Stuck order: {s['order'].id} in {s['status']} for {s['age']}s")

    if issues:
        self.logger.warning(f"Health check found {len(issues)} issues:")
        for issue in issues:
            self.logger.warning(f"  - {issue}")

    return issues
```

### 2. Alert & Notification System

```python
def _alert_bracket_failure(self, parent, kind, reason):
    """Send alert for bracket failure."""
    alert = {
        'type': 'BRACKET_FAILURE',
        'severity': 'HIGH',
        'parent_id': parent.id,
        'asset': parent.asset.symbol,
        'child_kind': kind,
        'reason': reason,
        'timestamp': datetime.now().isoformat(),
        'position': self._get_position_for_asset(parent.asset)
    }

    # Send to monitoring system
    self._send_alert(alert)

    # Log to dedicated bracket failure log
    with open('/var/log/lumibot/bracket_failures.log', 'a') as f:
        f.write(f"{json.dumps(alert)}\n")

def _alert_missing_protection(self, parent, missing):
    """Alert when position lacks protection."""
    alert = {
        'type': 'MISSING_PROTECTION',
        'severity': 'CRITICAL',
        'parent_id': parent.id,
        'asset': parent.asset.symbol,
        'missing': missing,  # ['TP'] or ['SL'] or ['TP', 'SL']
        'timestamp': datetime.now().isoformat()
    }

    self._send_alert(alert)

def _send_alert(self, alert):
    """Send alert via configured channels."""
    # Email
    if self.config.get('alert_email'):
        self._send_email_alert(alert)

    # Slack/Discord webhook
    if self.config.get('alert_webhook'):
        self._send_webhook_alert(alert)

    # SMS (Twilio)
    if self.config.get('alert_sms'):
        self._send_sms_alert(alert)
```

### 3. Metrics & Monitoring

```python
def _track_bracket_metrics(self):
    """Collect metrics for monitoring."""
    metrics = {
        'active_brackets': sum(1 for m in self._bracket_meta.values() if m.get('active')),
        'complete_brackets': sum(
            1 for m in self._bracket_meta.values()
            if m.get('active') and len(m.get('children', {})) == 2
        ),
        'incomplete_brackets': sum(
            1 for m in self._bracket_meta.values()
            if m.get('active') and len(m.get('children', {})) < 2
        ),
        'orphaned_children': len([
            o for o in self._orders_cache.values()
            if getattr(o, '_is_bracket_child', False) and
            self._bracket_parent_by_child_id.get(o.id) not in self._orders_cache
        ]),
        'stuck_orders': len(self._detect_stuck_orders()),
        'partial_fills': len(self._detect_partial_fills())
    }

    # Log metrics
    self.logger.info(f"Bracket Metrics: {metrics}")

    # Send to monitoring system (Prometheus, Datadog, etc.)
    if hasattr(self, 'metrics_client'):
        for metric, value in metrics.items():
            self.metrics_client.gauge(f'lumibot.bracket.{metric}', value)

    return metrics
```

---

## Testing Strategy

### Unit Tests

```python
# See: tests/test_projectx_bracket_lifecycle_unit.py

def test_parent_fills_child_rejected():
    """Test recovery when child submission fails."""
    broker = setup_broker()

    # Mock child submission to fail
    broker.client.order_place = Mock(return_value={'success': False, 'error': 'Invalid price'})

    # Submit parent
    parent = create_bracket_order(tp=5050, sl=4975)
    broker._submit_order(parent)

    # Trigger parent fill
    fill_order(broker, parent)

    # Check that failure was detected
    assert broker._bracket_meta[parent.id]['children'].get('tp') is None
    # Check that alert was sent
    assert len(broker.alerts) > 0
    assert broker.alerts[0]['type'] == 'BRACKET_FAILURE'

def test_tp_fills_sl_cancel_fails():
    """Test orphan detection when sibling cancel fails."""
    broker = setup_broker()

    # Submit and fill parent
    parent = create_bracket_order(tp=5050, sl=4975)
    broker._submit_order(parent)
    fill_order(broker, parent)

    # Get child IDs
    tp_id = broker._bracket_meta[parent.id]['children']['tp']
    sl_id = broker._bracket_meta[parent.id]['children']['sl']

    # Mock cancel to fail
    broker.cancel_order = Mock(return_value=False)

    # Fill TP child
    tp_order = broker._orders_cache[tp_id]
    fill_order(broker, tp_order)

    # Check that SL is detected as orphan
    orphans = broker._monitor_orphaned_orders()
    assert len(orphans) == 1
    assert orphans[0][0].id == sl_id
```

### Integration Tests

```python
def test_connection_loss_recovery():
    """Test full recovery after connection loss."""
    broker = setup_broker()

    # Setup initial state
    parent = create_bracket_order(tp=5050, sl=4975)
    broker._submit_order(parent)
    fill_order(broker, parent)

    # Simulate connection loss
    broker._on_disconnect()

    # Simulate external SL fill while disconnected
    external_fill_sl(broker.account_id, sl_id='1235', price=4975)

    # Reconnect
    broker._reconnect_with_backoff()
    broker._resync_after_reconnect()

    # Check state is correct
    position = broker.get_position(parent.asset)
    assert position.quantity == 0  # Position should be flat

    # Check TP order was cancelled
    tp_order = broker._orders_cache[broker._bracket_meta[parent.id]['children']['tp']]
    assert tp_order.status == 'cancelled'
```

---

## Operational Procedures

### Daily Health Check

```bash
# Run health check on all active accounts
python -m lumibot.tools.bracket_health_check --account TSX123456

# Output:
# ✅ Account TSX123456 Health Check
# - Active brackets: 5
# - Complete brackets: 5
# - Incomplete brackets: 0
# - Orphaned children: 0
# - Stuck orders: 0
# - Partial fills: 0
#
# Status: HEALTHY
```

### Emergency Procedures

**Orphaned Order Cleanup:**
```bash
# Find and cancel all orphaned bracket children
python -m lumibot.tools.cleanup_orphans --account TSX123456 --dry-run

# Review output, then execute
python -m lumibot.tools.cleanup_orphans --account TSX123456 --execute
```

**Force Flatten All:**
```bash
# Emergency: Flatten all positions and cancel all orders
python -m lumibot.tools.emergency_flatten --account TSX123456
```

### Monitoring Dashboards

**Key Metrics to Track:**
1. Bracket completion rate (successful children spawned / parents filled)
2. Orphan detection rate (orphans found / total brackets)
3. Sibling cancel success rate (successful cancels / cancel attempts)
4. Partial fill frequency (partial fills / total child fills)
5. Reconnection success rate (successful reconnects / connection losses)
6. State desync incidents (desyncs detected / total reconnects)

---

## Summary: Failure Mode Matrix

| Failure Mode | Impact | Detection | Recovery | Prevention |
|--------------|--------|-----------|----------|------------|
| **1. Child Rejected** | No protection on position | Missing child ID in metadata | Retry + Emergency exit | Pre-validate prices + margin |
| **2. Cancel Fails** | Orphaned orders | Monitor + Position reconciliation | Retry cancel + Force-cancel orphans | Idempotent cancel + Verification |
| **3. Connection Loss** | State desync | Heartbeat timeout | Reconnect + Full resync + Rebuild metadata | Persistent state + Broker-side stops |
| **4. Stuck Pending** | Resource leak, missed opportunity | Age threshold (60s) | Cancel + Refresh + Timeout | Active polling + Fallback order types |
| **5. Partial Fills** | Quantity mismatch, over-protection | filled_qty < quantity | Adjust sibling + Position reconciliation | Market SL + Continuous sync |

**Critical Success Factors:**
1. ✅ Robust metadata management (early store, restoration, reconciliation)
2. ✅ Idempotent operations (cancel, refresh, resync)
3. ✅ Continuous monitoring (health checks, orphan detection)
4. ✅ Graceful degradation (emergency exits, alerts)
5. ✅ Comprehensive testing (unit, integration, chaos engineering)

---

## References

**Implementation Files:**
- `/Users/marvin/repos/lumibot_fork/lumibot/brokers/projectx.py` - Main broker implementation
- `/Users/marvin/repos/lumibot_fork/lumibot/tools/projectx_helpers.py` - Bracket helper functions
- `/Users/marvin/repos/lumibot_fork/tests/test_projectx_bracket_lifecycle_unit.py` - Test coverage

**Key Functions:**
- `_maybe_spawn_bracket_children()` - Child spawning logic
- `_handle_bracket_child_fill()` - Sibling cancellation
- `_resync_after_reconnect()` - Connection recovery
- `_reconcile_bracket_quantities()` - Quantity synchronization
- `_monitor_orphaned_orders()` - Orphan detection
