"""
Hybrid Bracket Order System - Implementation Examples
======================================================

This file contains complete implementation examples for the hybrid bracket system.
These are production-ready code snippets that can be integrated into lumibot.

Key Files to Modify:
- lumibot/brokers/projectx.py (add hybrid manager)
- lumibot/brokers/broker.py (add monitoring hook)
- custom_portfolio/strategies/portfolio_manager.py (enable monitoring)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Dict, Optional, Tuple

from lumibot.entities import Asset, Order, Position

# =============================================================================
# PART 1: Data Structures
# =============================================================================


@dataclass
class HybridBracketState:
    """Complete state tracking for a hybrid bracket order."""

    # Identity
    parent_order_id: str
    position_asset: Asset
    position_side: str  # "long" or "short"
    position_qty: int
    entry_price: float

    # Bracket levels
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None

    # Broker component
    broker_tp_order_id: Optional[str] = None
    broker_sl_order_id: Optional[str] = None
    broker_last_seen: Optional[datetime] = None
    broker_state: str = "active"  # active, failed, triggered, unknown

    # Client component
    client_monitoring: bool = True
    client_enabled: bool = False
    client_last_check: Optional[datetime] = None

    # Status tracking
    state: str = "BROKER_ACTIVE"  # BROKER_ACTIVE, BROKER_FAILED, CLOSED
    closed_by: Optional[str] = None
    closed_at: Optional[datetime] = None

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    notes: str = ""


# =============================================================================
# PART 2: Broker Status Detector
# =============================================================================


class BrokerBracketDetector:
    """Detects whether broker bracket is active, triggered, or failed."""

    def __init__(self, broker, data_source, logger=None):
        self.broker = broker
        self.data_source = data_source
        self.logger = logger or logging.getLogger(__name__)

        # Cache for order status (reduces API calls)
        self._order_status_cache: Dict[str, dict] = {}
        self._cache_ttl = timedelta(seconds=10)

        # Timeouts
        self.staleness_timeout = timedelta(seconds=30)
        self.failure_timeout = timedelta(seconds=60)

    def detect_broker_status(self, bracket: HybridBracketState) -> str:
        """
        Detect current broker bracket status.

        Returns:
            "active" - Broker orders are working normally
            "triggered_tp" - Broker TP order filled
            "triggered_sl" - Broker SL order filled
            "triggered_unknown" - Broker closed position (unknown which)
            "failed" - Broker orders failed/canceled
            "unknown" - Ambiguous state, keep monitoring
        """

        # Step 1: Check position state
        position = self._get_current_position(bracket.position_asset)
        position_open = position and position.quantity != 0

        # Step 2: Get cached order status (fast path)
        tp_status = self._get_order_status(bracket.broker_tp_order_id)
        sl_status = self._get_order_status(bracket.broker_sl_order_id)

        now = self.data_source.get_datetime()

        # CASE 1: Position closed → Broker succeeded
        if not position_open:
            if tp_status == "filled":
                return "triggered_tp"
            elif sl_status == "filled":
                return "triggered_sl"
            else:
                return "triggered_unknown"

        # CASE 2: Both orders explicitly failed
        if tp_status in ["canceled", "cancelled", "rejected", "error"] and sl_status in [
            "canceled",
            "cancelled",
            "rejected",
            "error",
        ]:
            self.logger.warning(f"Broker orders failed: TP={tp_status}, SL={sl_status}")
            return "failed"

        # CASE 3: Staleness timeout (broker not responding)
        if bracket.broker_last_seen:
            staleness = now - bracket.broker_last_seen

            if staleness > self.failure_timeout:
                self.logger.warning(f"Broker stale for {staleness.total_seconds()}s, verifying...")

                # Perform fresh API check (slow path)
                fresh_tp = self._fetch_fresh_order_status(bracket.broker_tp_order_id)
                fresh_sl = self._fetch_fresh_order_status(bracket.broker_sl_order_id)

                if fresh_tp in ["canceled", "cancelled", "rejected", None] and fresh_sl in [
                    "canceled",
                    "cancelled",
                    "rejected",
                    None,
                ]:
                    self.logger.error("Broker orders confirmed dead after fresh check")
                    return "failed"

        # CASE 4: Orders active and recently updated
        if tp_status in ["open", "new"] or sl_status in ["open", "new"]:
            if bracket.broker_last_seen and (now - bracket.broker_last_seen) < self.staleness_timeout:
                return "active"

        # CASE 5: Ambiguous state
        return "unknown"

    def _get_order_status(self, order_id: str) -> Optional[str]:
        """Get cached order status (fast path)."""
        if not order_id:
            return None

        # Check cache
        if order_id in self._order_status_cache:
            cached = self._order_status_cache[order_id]
            if (datetime.now() - cached["timestamp"]) < self._cache_ttl:
                return cached["status"]

        return None

    def _fetch_fresh_order_status(self, order_id: str) -> Optional[str]:
        """Fetch live order status from broker API (slow path)."""
        if not order_id:
            return None

        try:
            order = self.broker._pull_broker_order(order_id)
            if order:
                status = order.status
                # Update cache
                self._order_status_cache[order_id] = {"status": status, "timestamp": datetime.now()}
                return status
        except Exception as e:
            self.logger.warning(f"Failed to fetch order {order_id}: {e}")

        return None

    def _get_current_position(self, asset: Asset) -> Optional[Position]:
        """Get current position for asset from broker."""
        try:
            return self.broker._pull_position(strategy=None, asset=asset)
        except Exception as e:
            self.logger.error(f"Failed to fetch position for {asset.symbol}: {e}")
            return None


# =============================================================================
# PART 3: Duplicate Prevention Guard
# =============================================================================


class DuplicatePreventionGuard:
    """Prevents client from executing when broker is active or already executed."""

    def __init__(self, detector: BrokerBracketDetector, data_source, logger=None):
        self.detector = detector
        self.data_source = data_source
        self.logger = logger or logging.getLogger(__name__)
        self.grace_period = timedelta(seconds=15)

    def should_client_execute(self, bracket: HybridBracketState, trigger_reason: str) -> Tuple[bool, str]:
        """
        Determines if client should execute TP/SL.

        Returns: (should_execute: bool, reason: str)

        This is THE critical function that prevents duplicate closures.
        """

        # CHECK 1: Never execute if broker status is "active"
        broker_status = self.detector.detect_broker_status(bracket)
        if broker_status == "active":
            return False, "broker_active"

        # CHECK 2: Never execute if broker already triggered
        if broker_status.startswith("triggered"):
            return False, f"broker_already_closed_{broker_status}"

        # CHECK 3: Only execute if broker provably failed
        if broker_status != "failed":
            return False, f"broker_status_ambiguous_{broker_status}"

        # CHECK 4: Verify position still exists
        position = self.detector._get_current_position(bracket.position_asset)
        if not position or position.quantity == 0:
            return False, "position_already_closed"

        # CHECK 5: Grace period after last broker update
        now = self.data_source.get_datetime()
        if bracket.broker_last_seen:
            time_since_update = now - bracket.broker_last_seen
            if time_since_update < self.grace_period:
                return False, f"grace_period_active_{time_since_update.total_seconds():.1f}s"

        # CHECK 6: Final fresh status verification before executing
        final_tp_status = self.detector._fetch_fresh_order_status(bracket.broker_tp_order_id)
        final_sl_status = self.detector._fetch_fresh_order_status(bracket.broker_sl_order_id)

        if final_tp_status in ["open", "new"] or final_sl_status in ["open", "new"]:
            return False, "broker_orders_still_active_on_fresh_check"

        # ALL CHECKS PASSED
        self.logger.info(f"✅ All safety checks passed for {bracket.parent_order_id}")
        return True, "broker_failed_client_takeover_approved"


# =============================================================================
# PART 4: Client Backup Executor
# =============================================================================


class ClientBackupExecutor:
    """Executes TP/SL on client side when broker fails."""

    def __init__(
        self, broker, data_source, guard: DuplicatePreventionGuard, detector: BrokerBracketDetector, logger=None
    ):
        self.broker = broker
        self.data_source = data_source
        self.guard = guard
        self.detector = detector
        self.logger = logger or logging.getLogger(__name__)

    def monitor_and_execute(self, bracket: HybridBracketState):
        """
        Main monitoring loop: check if client should take over.

        Called every strategy iteration.
        """

        # Skip if bracket already closed
        if bracket.state == "CLOSED":
            return

        # Update last check time
        bracket.client_last_check = self.data_source.get_datetime()

        # Step 1: Check broker status
        broker_status = self.detector.detect_broker_status(bracket)

        # Step 2: Update bracket state machine
        if broker_status == "active":
            bracket.broker_last_seen = self.data_source.get_datetime()
            bracket.state = "BROKER_ACTIVE"
            return

        if broker_status.startswith("triggered"):
            bracket.state = "CLOSED"
            bracket.closed_by = broker_status
            bracket.closed_at = self.data_source.get_datetime()
            self.logger.info(f"✅ Bracket {bracket.parent_order_id} closed by broker: {broker_status}")
            return

        if broker_status == "failed":
            if bracket.state != "BROKER_FAILED":
                self.logger.warning(f"⚠️ Broker failed for {bracket.parent_order_id}, client taking over")
            bracket.state = "BROKER_FAILED"
            bracket.client_enabled = True

        # Step 3: If client enabled, check if should act
        if bracket.client_enabled:
            self._check_and_execute_client_backup(bracket)

    def _check_and_execute_client_backup(self, bracket: HybridBracketState):
        """Execute client-side TP/SL if conditions met."""

        # Get current market price
        current_price = self._get_current_price(bracket.position_asset)
        if not current_price:
            self.logger.warning(f"Cannot get current price for {bracket.position_asset.symbol}")
            return

        # Check TP trigger
        tp_triggered = False
        if bracket.tp_price:
            if bracket.position_side == "long" and current_price >= bracket.tp_price:
                tp_triggered = True
            elif bracket.position_side == "short" and current_price <= bracket.tp_price:
                tp_triggered = True

        # Check SL trigger
        sl_triggered = False
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
        else:
            return  # Not triggered yet

        # Final duplicate prevention check
        should_execute, reason = self.guard.should_client_execute(bracket, trigger_reason)

        if not should_execute:
            self.logger.debug(f"Client execution blocked: {reason}")
            return

        # EXECUTE CLIENT CLOSURE
        self.logger.warning(f"🚨 CLIENT BACKUP EXECUTING: {trigger_reason} at price={current_price:.2f}")

        try:
            close_order = self._create_close_order(bracket, trigger_reason, current_price)
            self.broker.submit_order(close_order)

            # Mark as closed
            bracket.state = "CLOSED"
            bracket.closed_by = trigger_reason
            bracket.closed_at = self.data_source.get_datetime()

            self.logger.info("✅ Client backup closed position successfully")

        except Exception as e:
            self.logger.error(f"❌ Client backup execution failed: {e}")
            # Don't mark as closed on failure, will retry next iteration

    def _create_close_order(self, bracket: HybridBracketState, reason: str, current_price: float) -> Order:
        """Create market order to close position."""

        # Reverse the side
        close_side = "sell" if bracket.position_side == "long" else "buy"

        order = Order(
            asset=bracket.position_asset,
            quantity=abs(bracket.position_qty),
            side=close_side,
            order_type="market",
            tag=f"CLIENT_BACKUP_{reason.upper()}_{bracket.parent_order_id[:8]}",
        )

        return order

    def _get_current_price(self, asset: Asset) -> Optional[float]:
        """Get current market price for asset."""
        try:
            return self.data_source.get_last_price(asset)
        except Exception as e:
            self.logger.error(f"Failed to get price for {asset.symbol}: {e}")
            return None


# =============================================================================
# PART 5: Position Reconciler
# =============================================================================


class PositionReconciler:
    """Reconciles position state between broker truth and local tracking."""

    def __init__(self, detector: BrokerBracketDetector, logger=None):
        self.detector = detector
        self.logger = logger or logging.getLogger(__name__)

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

        reconciliation = {"position_exists": False, "quantity": 0, "broker_orders_exist": False, "discrepancies": []}

        try:
            # 1. Fetch ground truth from broker
            position = self.detector._get_current_position(bracket.position_asset)

            if position and position.quantity != 0:
                reconciliation["position_exists"] = True
                reconciliation["quantity"] = position.quantity

                # Check if quantity matches expected
                if abs(position.quantity) != abs(bracket.position_qty):
                    reconciliation["discrepancies"].append(
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

            reconciliation["broker_orders_exist"] = tp_exists or sl_exists

            # 3. Check for state inconsistencies
            if reconciliation["position_exists"] and not reconciliation["broker_orders_exist"]:
                # Position open but no bracket orders → need client backup
                reconciliation["discrepancies"].append("position_unprotected")

            if not reconciliation["position_exists"] and reconciliation["broker_orders_exist"]:
                # Orders exist but no position → orphaned orders
                reconciliation["discrepancies"].append("orphaned_orders")

        except Exception as e:
            self.logger.error(f"Reconciliation failed for {bracket.parent_order_id}: {e}")
            reconciliation["discrepancies"].append(f"error: {str(e)}")

        return reconciliation


# =============================================================================
# PART 6: Stream Event Handler
# =============================================================================


class BracketStreamHandler:
    """Handles real-time updates for hybrid brackets."""

    def __init__(self, brackets: Dict[str, HybridBracketState], detector: BrokerBracketDetector, logger=None):
        self.brackets = brackets
        self.detector = detector
        self.logger = logger or logging.getLogger(__name__)

    def on_order_update(self, order_update: dict):
        """
        Called when broker streams order update.

        Updates bracket tracking and staleness timestamps.
        """

        try:
            order_id = str(order_update.get("id"))
            status = order_update.get("status")

            # Find bracket that contains this order
            bracket = self._find_bracket_by_order_id(order_id)
            if not bracket:
                return  # Not a bracket order

            # Update broker last seen timestamp
            bracket.broker_last_seen = datetime.now()

            # Update order status cache
            self.detector._order_status_cache[order_id] = {"status": status, "timestamp": datetime.now()}

            # Handle status transitions
            if status in ["filled", "fill"]:
                # Bracket order filled → position closed by broker
                if order_id == bracket.broker_tp_order_id:
                    bracket.state = "CLOSED"
                    bracket.closed_by = "broker_tp"
                    bracket.closed_at = datetime.now()
                    self.logger.info("✅ Bracket TP filled by broker")

                elif order_id == bracket.broker_sl_order_id:
                    bracket.state = "CLOSED"
                    bracket.closed_by = "broker_sl"
                    bracket.closed_at = datetime.now()
                    self.logger.info("✅ Bracket SL filled by broker")

            elif status in ["canceled", "cancelled", "rejected", "error"]:
                self.logger.warning(f"⚠️ Bracket order {order_id} failed: {status}")

                # Check if ALL bracket orders failed
                broker_status = self.detector.detect_broker_status(bracket)
                if broker_status == "failed":
                    self.logger.warning("All bracket orders failed, enabling client backup")
                    bracket.state = "BROKER_FAILED"
                    bracket.client_enabled = True

        except Exception as e:
            self.logger.error(f"Error handling order update: {e}")

    def on_position_update(self, position_update: dict):
        """
        Called when broker streams position update.

        Detects position closure to infer bracket execution.
        """

        try:
            asset_symbol = position_update.get("symbol")
            quantity = position_update.get("quantity", 0)

            # Find brackets for this asset
            for bracket in self.brackets.values():
                if bracket.position_asset.symbol == asset_symbol:

                    # Position closed → broker succeeded
                    if quantity == 0 and bracket.state != "CLOSED":
                        self.logger.info(f"Position closed for {asset_symbol}, marking bracket as closed")
                        bracket.state = "CLOSED"
                        bracket.closed_by = "broker_unknown"
                        bracket.closed_at = datetime.now()
                        bracket.broker_last_seen = datetime.now()

        except Exception as e:
            self.logger.error(f"Error handling position update: {e}")

    def _find_bracket_by_order_id(self, order_id: str) -> Optional[HybridBracketState]:
        """Find bracket that contains this order ID."""
        for bracket in self.brackets.values():
            if order_id in [bracket.broker_tp_order_id, bracket.broker_sl_order_id]:
                return bracket
        return None


# =============================================================================
# PART 7: Hybrid Bracket Manager (Main Orchestrator)
# =============================================================================


class HybridBracketManager:
    """
    Main orchestrator for hybrid bracket system.

    Coordinates all components and provides high-level API.
    """

    def __init__(self, broker, data_source, logger=None):
        self.broker = broker
        self.data_source = data_source
        self.logger = logger or logging.getLogger(__name__)

        # State storage
        self.brackets: Dict[str, HybridBracketState] = {}
        self._lock = RLock()

        # Components
        self.detector = BrokerBracketDetector(broker, data_source, logger)
        self.guard = DuplicatePreventionGuard(self.detector, data_source, logger)
        self.executor = ClientBackupExecutor(broker, data_source, self.guard, self.detector, logger)
        self.reconciler = PositionReconciler(self.detector, logger)
        self.stream_handler = BracketStreamHandler(self.brackets, self.detector, logger)

        # Tracking
        self._last_reconciliation: Optional[datetime] = None
        self._reconciliation_interval = timedelta(minutes=1)

    def register_bracket(self, bracket: HybridBracketState):
        """Register a new bracket for monitoring."""
        with self._lock:
            self.brackets[bracket.parent_order_id] = bracket
            self.logger.info(f"Registered hybrid bracket: {bracket.parent_order_id}")

    def monitor_all_brackets(self):
        """
        Main monitoring loop called each strategy iteration.

        This is the entry point called by strategy.
        """

        with self._lock:
            for bracket_id, bracket in list(self.brackets.items()):
                try:
                    self.executor.monitor_and_execute(bracket)

                    # Remove closed brackets
                    if bracket.state == "CLOSED":
                        self.logger.info(f"Archiving closed bracket: {bracket_id}")
                        # Could move to archive dict instead of deleting
                        # del self.brackets[bracket_id]

                except Exception as e:
                    self.logger.error(f"Error monitoring bracket {bracket_id}: {e}")

    def reconcile_all_brackets(self):
        """Periodic reconciliation across all brackets."""

        now = self.data_source.get_datetime()
        if self._last_reconciliation and (now - self._last_reconciliation) < self._reconciliation_interval:
            return  # Too soon

        self.logger.info("Starting periodic bracket reconciliation")

        with self._lock:
            stats = {"total": len(self.brackets), "healthy": 0, "unprotected": 0, "orphaned": 0, "errors": 0}

            for bracket_id, bracket in self.brackets.items():
                try:
                    recon = self.reconciler.reconcile_bracket_position(bracket)

                    if not recon["discrepancies"]:
                        stats["healthy"] += 1

                    if "position_unprotected" in recon["discrepancies"]:
                        stats["unprotected"] += 1
                        self.logger.warning(f"Bracket {bracket_id}: Position unprotected, enabling client")
                        bracket.client_enabled = True
                        bracket.state = "BROKER_FAILED"

                    if "orphaned_orders" in recon["discrepancies"]:
                        stats["orphaned"] += 1
                        self.logger.warning(f"Bracket {bracket_id}: Orphaned orders, cleaning up")
                        self._cleanup_orphaned_orders(bracket)

                except Exception as e:
                    stats["errors"] += 1
                    self.logger.error(f"Reconciliation failed for {bracket_id}: {e}")

            self.logger.info(f"Reconciliation complete: {stats}")
            self._last_reconciliation = now

    def _cleanup_orphaned_orders(self, bracket: HybridBracketState):
        """Cancel orphaned bracket orders."""
        if bracket.broker_tp_order_id:
            try:
                # Assuming broker has cancel_order_by_id method
                self.broker.cancel_order(Order(identifier=bracket.broker_tp_order_id))
            except Exception as e:
                self.logger.error(f"Failed to cancel orphaned TP order: {e}")

        if bracket.broker_sl_order_id:
            try:
                self.broker.cancel_order(Order(identifier=bracket.broker_sl_order_id))
            except Exception as e:
                self.logger.error(f"Failed to cancel orphaned SL order: {e}")

    def get_bracket_stats(self) -> dict:
        """Get statistics about tracked brackets."""
        with self._lock:
            stats = {
                "total_brackets": len(self.brackets),
                "broker_active": sum(1 for b in self.brackets.values() if b.state == "BROKER_ACTIVE"),
                "broker_failed": sum(1 for b in self.brackets.values() if b.state == "BROKER_FAILED"),
                "closed": sum(1 for b in self.brackets.values() if b.state == "CLOSED"),
            }
            return stats


# =============================================================================
# PART 8: Integration Example
# =============================================================================


def integrate_into_projectx_broker():
    """
    Example of how to integrate hybrid bracket manager into ProjectX broker.

    Add to lumibot/brokers/projectx.py:
    """

    class ProjectX_Modified:  # Pseudo-code
        """
        Modified ProjectX broker with hybrid bracket support.
        """

        def __init__(self, config, data_source, **kwargs):
            # ... existing init code ...

            # Add hybrid bracket manager
            self.hybrid_bracket_manager = HybridBracketManager(broker=self, data_source=data_source, logger=self.logger)

        def _submit_order(self, order: Order) -> Order:
            """Override to support hybrid brackets."""

            # Check if this is a bracket order
            if order.order_class == Order.OrderClass.BRACKET:

                # Submit parent with broker brackets (existing logic)
                submitted = self._submit_bracket_to_broker(order)

                # Register in hybrid manager for monitoring
                if submitted and submitted.id:
                    bracket_state = HybridBracketState(
                        parent_order_id=submitted.id,
                        position_asset=order.asset,
                        position_side="long" if order.side == "buy" else "short",
                        position_qty=order.quantity,
                        entry_price=order.limit_price or 0,
                        tp_price=getattr(order, "secondary_limit_price", None),
                        sl_price=getattr(order, "secondary_stop_price", None),
                        broker_tp_order_id=submitted._synthetic_bracket.get("children", {}).get("tp"),
                        broker_sl_order_id=submitted._synthetic_bracket.get("children", {}).get("sl"),
                        broker_last_seen=datetime.now(),
                    )

                    self.hybrid_bracket_manager.register_bracket(bracket_state)

                return submitted

            else:
                # Regular order (existing logic)
                return self._submit_order_original(order)

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


def integrate_into_strategy():
    """
    Example of how to enable monitoring in strategy.

    Add to custom_portfolio/strategies/portfolio_manager.py:
    """

    class PortfolioManager_Modified:  # Pseudo-code
        """
        Modified strategy with hybrid bracket monitoring.
        """

        def on_trading_iteration(self):
            """Override to add bracket monitoring."""

            # Regular strategy logic
            # ... existing code ...

            # Monitor hybrid brackets (client backup)
            if hasattr(self.broker, "hybrid_bracket_manager"):
                self.broker.hybrid_bracket_manager.monitor_all_brackets()

            # Periodic reconciliation (every 10 iterations)
            if self.iteration_count % 10 == 0:
                if hasattr(self.broker, "hybrid_bracket_manager"):
                    self.broker.hybrid_bracket_manager.reconcile_all_brackets()

                    # Log stats
                    stats = self.broker.hybrid_bracket_manager.get_bracket_stats()
                    self.log_message(f"Bracket stats: {stats}")


# =============================================================================
# PART 9: Usage Example
# =============================================================================


def example_usage():
    """Complete example of hybrid bracket lifecycle."""

    # Assume broker and data_source are initialized
    broker = None  # ProjectX broker instance
    data_source = None  # Data source instance

    # 1. Create hybrid bracket manager
    manager = HybridBracketManager(broker, data_source)

    # 2. Submit bracket order (this happens in strategy)
    bracket_order = Order(
        asset=Asset("MES", asset_type=Asset.AssetType.CONT_FUTURE),
        quantity=1,
        side="buy",
        order_type="limit",
        limit_price=5000.0,
        order_class=Order.OrderClass.BRACKET,
    )
    bracket_order.secondary_limit_price = 5050.0  # TP
    bracket_order.secondary_stop_price = 4950.0  # SL

    # Broker submits and returns filled order with child IDs
    submitted = broker.submit_order(bracket_order)

    # 3. Register bracket for monitoring
    bracket_state = HybridBracketState(
        parent_order_id=submitted.id,
        position_asset=bracket_order.asset,
        position_side="long",
        position_qty=1,
        entry_price=5000.0,
        tp_price=5050.0,
        sl_price=4950.0,
        broker_tp_order_id="tp_12345",  # From submitted._synthetic_bracket
        broker_sl_order_id="sl_12346",  # From submitted._synthetic_bracket
        broker_last_seen=datetime.now(),
    )

    manager.register_bracket(bracket_state)

    # 4. In strategy loop: monitor brackets
    while True:
        # Every iteration
        manager.monitor_all_brackets()

        # Every 10 iterations
        manager.reconcile_all_brackets()

        # ... rest of strategy logic ...


if __name__ == "__main__":
    print("Hybrid Bracket Implementation - Production Ready")
    print("=" * 60)
    print()
    print("Key Components:")
    print("1. HybridBracketState - State tracking")
    print("2. BrokerBracketDetector - Status detection")
    print("3. DuplicatePreventionGuard - Safety checks")
    print("4. ClientBackupExecutor - Backup execution")
    print("5. PositionReconciler - Reconciliation")
    print("6. BracketStreamHandler - Event handling")
    print("7. HybridBracketManager - Main orchestrator")
    print()
    print("Integration Points:")
    print("- lumibot/brokers/projectx.py: Add hybrid_bracket_manager")
    print("- custom_portfolio/strategies/portfolio_manager.py: Add monitoring calls")
    print()
    print("See HYBRID_BRACKET_DESIGN.md for detailed design documentation")
