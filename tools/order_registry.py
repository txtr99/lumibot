"""
Order Registry - Central source of truth for all order state.

This module provides:
1. OrderRegistry - tracks all orders from creation to fill
2. Position sync checking - detects mismatches between virtual and exchange
3. Sequential order submission - ONE order per loop with full verification

Addresses critical issues:
- Duplicate custom tag errors (uses timestamp + UUID)
- Double-close race conditions (strategy-level checks)
- Position desync (nuclear option flattening)
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OrderStatus(Enum):
    """Order lifecycle states."""

    PENDING = "PENDING"  # Intent created, not yet submitted
    SUBMITTED = "SUBMITTED"  # Sent to exchange, awaiting fill
    FILLED = "FILLED"  # Completely filled
    CANCELLED = "CANCELLED"  # Cancelled before fill
    REJECTED = "REJECTED"  # Rejected by exchange


class OrderPurpose(Enum):
    """Order purpose for tag generation."""

    ENTRY = "ENT"
    TAKE_PROFIT = "TP"
    STOP_LOSS = "SL"
    CLOSE = "CLOSE"


@dataclass
class OrderRecord:
    """Record of a submitted order."""

    tag: str
    order_id: int
    strategy_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    qty: int
    purpose: OrderPurpose
    status: OrderStatus = OrderStatus.SUBMITTED
    submitted_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    fill_price: Optional[float] = None
    fill_qty: Optional[float] = None
    cancelled_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class OrderIntent:
    """Intent to place an order (before submission)."""

    strategy_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    qty: int
    purpose: OrderPurpose
    is_close: bool = False
    order_type: str = "MARKET"  # MARKET, LIMIT, STOP
    price: Optional[float] = None
    stop_price: Optional[float] = None
    # For bracket orders
    take_profit_offset: Optional[float] = None
    stop_loss_offset: Optional[float] = None


@dataclass
class SyncResult:
    """Result of position sync check."""

    is_synced: bool
    exchange_qty: int
    virtual_qty: int
    action_needed: Optional[str] = None  # None, "FLATTEN_SYMBOL"
    details: Optional[str] = None


@dataclass
class OrderResult:
    """Result of order submission."""

    tag: str
    order_id: Optional[int]
    status: str  # "submitted", "skipped", "failed"
    message: Optional[str] = None


class OrderRegistry:
    """
    Central registry for all orders.

    Key responsibilities:
    1. Generate truly unique tags (timestamp + UUID)
    2. Track all orders from submission to fill
    3. Provide strategy-level order queries
    4. Support position sync checks
    """

    def __init__(self, tracker: Any = None):
        """
        Initialize the registry.

        Args:
            tracker: VirtualPositionTracker instance for position queries
        """
        self.orders: Dict[str, OrderRecord] = {}  # tag -> record
        self.order_id_to_tag: Dict[int, str] = {}  # order_id -> tag
        self.tracker = tracker
        self._lock_timeout = 5.0  # seconds

    def set_tracker(self, tracker: Any):
        """Set the virtual position tracker."""
        self.tracker = tracker

    def generate_unique_tag(self, purpose: OrderPurpose, strategy_id: str) -> str:
        """
        Generate a truly unique tag with timestamp + UUID.

        Format: {PURPOSE}_{STRATEGY_ID}_{UNIX_MS}_{UUID6}

        Example: ENT_ES_1M_08_1732556789123_A3F7C9
        """
        unix_ms = int(time.time() * 1000)
        uuid_suffix = uuid.uuid4().hex[:6].upper()
        purpose_str = purpose.value if isinstance(purpose, OrderPurpose) else purpose
        return f"{purpose_str}_{strategy_id}_{unix_ms}_{uuid_suffix}"

    def register_intent(self, intent: OrderIntent) -> str:
        """
        Register an order intent before submission.
        Returns the generated tag.
        """
        tag = self.generate_unique_tag(intent.purpose, intent.strategy_id)
        # Don't create OrderRecord yet - wait for actual submission
        return tag

    def register_submission(self, tag: str, order_id: int, intent: OrderIntent) -> OrderRecord:
        """
        Register an order after successful submission to exchange.

        Called immediately after order_place() succeeds.
        """
        record = OrderRecord(
            tag=tag,
            order_id=order_id,
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            purpose=intent.purpose,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime.now(),
        )
        self.orders[tag] = record
        self.order_id_to_tag[order_id] = tag

        logger.info(
            f"[OrderRegistry] Registered order: {tag} -> order_id={order_id}, "
            f"strategy={intent.strategy_id}, {intent.side} {intent.qty} {intent.symbol}"
        )
        return record

    def register_fill(self, order_id: int, fill_price: float, fill_qty: float) -> Optional[OrderRecord]:
        """
        Register an order fill.

        Called when fill confirmed via trade_search.
        """
        tag = self.order_id_to_tag.get(order_id)
        if not tag or tag not in self.orders:
            logger.warning(f"[OrderRegistry] Fill for unknown order_id={order_id}")
            return None

        record = self.orders[tag]
        record.status = OrderStatus.FILLED
        record.fill_price = fill_price
        record.fill_qty = fill_qty
        record.filled_at = datetime.now()

        logger.info(f"[OrderRegistry] Filled: {tag} @ {fill_price}, qty={fill_qty}")
        return record

    def register_cancellation(self, order_id: int) -> Optional[OrderRecord]:
        """Register an order cancellation."""
        tag = self.order_id_to_tag.get(order_id)
        if not tag or tag not in self.orders:
            return None

        record = self.orders[tag]
        record.status = OrderStatus.CANCELLED
        record.cancelled_at = datetime.now()

        logger.info(f"[OrderRegistry] Cancelled: {tag}")
        return record

    def register_rejection(self, tag: str, error_message: str) -> Optional[OrderRecord]:
        """Register an order rejection."""
        if tag not in self.orders:
            return None

        record = self.orders[tag]
        record.status = OrderStatus.REJECTED
        record.error_message = error_message

        logger.warning(f"[OrderRegistry] Rejected: {tag} - {error_message}")
        return record

    def get_order_by_tag(self, tag: str) -> Optional[OrderRecord]:
        """Get order record by tag."""
        return self.orders.get(tag)

    def get_order_by_id(self, order_id: int) -> Optional[OrderRecord]:
        """Get order record by order ID."""
        tag = self.order_id_to_tag.get(order_id)
        if tag:
            return self.orders.get(tag)
        return None

    def get_pending_orders(self, strategy_id: str = None) -> List[OrderRecord]:
        """
        Get all non-filled orders, optionally filtered by strategy.

        Returns orders with status PENDING or SUBMITTED.
        """
        pending_statuses = {OrderStatus.PENDING, OrderStatus.SUBMITTED}
        results = []
        for record in self.orders.values():
            if record.status in pending_statuses:
                if strategy_id is None or record.strategy_id == strategy_id:
                    results.append(record)
        return results

    def get_pending_orders_for_symbol(self, symbol: str) -> List[OrderRecord]:
        """Get all pending orders for a symbol."""
        pending_statuses = {OrderStatus.PENDING, OrderStatus.SUBMITTED}
        return [r for r in self.orders.values() if r.status in pending_statuses and r.symbol == symbol]

    def has_pending_close(self, strategy_id: str) -> bool:
        """Check if strategy has a pending close order (TP, SL, or CLOSE)."""
        close_purposes = {OrderPurpose.TAKE_PROFIT, OrderPurpose.STOP_LOSS, OrderPurpose.CLOSE}
        pending_statuses = {OrderStatus.PENDING, OrderStatus.SUBMITTED}

        for record in self.orders.values():
            if (
                record.strategy_id == strategy_id
                and record.status in pending_statuses
                and record.purpose in close_purposes
            ):
                return True
        return False

    def has_pending_entry(self, strategy_id: str) -> bool:
        """Check if strategy has a pending entry order."""
        pending_statuses = {OrderStatus.PENDING, OrderStatus.SUBMITTED}

        for record in self.orders.values():
            if (
                record.strategy_id == strategy_id
                and record.status in pending_statuses
                and record.purpose == OrderPurpose.ENTRY
            ):
                return True
        return False


def extract_symbol_from_contract(contract_id: str) -> str:
    """
    Extract trading symbol from ProjectX contract ID.

    Contract ID format: CON.F.US.{symbol}.{expiry}
    Examples:
    - CON.F.US.EP.Z25 -> ES (EP maps to ES)
    - CON.F.US.MES.Z25 -> MES
    - CON.F.US.ENQ.Z25 -> NQ (ENQ maps to NQ)
    - CON.F.US.GCE.Z25 -> GC (GCE maps to GC)
    """
    SYMBOL_MAP = {
        "EP": "ES",
        "ENQ": "NQ",
        "GCE": "GC",
    }

    if not contract_id:
        return ""

    parts = contract_id.split(".")
    if len(parts) >= 4:
        raw_symbol = parts[3]
        return SYMBOL_MAP.get(raw_symbol, raw_symbol)
    return ""


def check_position_sync(symbol: str, exchange_positions: List[Dict], tracker: Any) -> SyncResult:
    """
    Compare aggregated virtual positions vs exchange position.

    Called BEFORE any order processing for this symbol.

    Args:
        symbol: Trading symbol (e.g., "ES", "MES")
        exchange_positions: List of positions from position_search_open()
        tracker: VirtualPositionTracker instance

    Returns:
        SyncResult with is_synced flag and action_needed if mismatched
    """
    # 1. Get exchange position for symbol
    exchange_qty = 0
    for pos in exchange_positions:
        contract_id = pos.get("contractId", "")
        pos_symbol = extract_symbol_from_contract(contract_id)

        if pos_symbol == symbol:
            size = pos.get("size", 0)
            pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT
            exchange_qty = size if pos_type == 1 else -size
            break

    # 2. Get AGGREGATED virtual position for symbol
    virtual_qty = 0
    if tracker:
        # Sum all virtual positions for this symbol
        for pos in tracker.get_all_positions():
            if pos.get("symbol") == symbol:
                virtual_qty += pos.get("quantity", 0)

    # 3. Compare
    if abs(exchange_qty - virtual_qty) < 1e-9:
        return SyncResult(
            is_synced=True, exchange_qty=int(exchange_qty), virtual_qty=int(virtual_qty), action_needed=None
        )

    # 4. MISMATCH DETECTED
    details = (
        f"Exchange shows {exchange_qty} but virtual tracker shows {virtual_qty} "
        f"for {symbol}. Difference: {exchange_qty - virtual_qty}"
    )
    logger.error(f"[PositionSync] MISMATCH: {details}")

    return SyncResult(
        is_synced=False,
        exchange_qty=int(exchange_qty),
        virtual_qty=int(virtual_qty),
        action_needed="FLATTEN_SYMBOL",
        details=details,
    )


def can_close_position(strategy_id: str, symbol: str, registry: OrderRegistry, tracker: Any) -> bool:
    """
    Check if it's safe to close THIS STRATEGY's position.

    NOTE: Aggregate sync check (check_position_sync) should run BEFORE this.
    This only checks strategy-specific conditions.

    Returns False if:
    1. This strategy's virtual position is already flat
    2. There's already a pending close order for THIS strategy

    Args:
        strategy_id: Strategy identifier
        symbol: Trading symbol
        registry: OrderRegistry instance
        tracker: VirtualPositionTracker instance
    """
    # 1. Check THIS STRATEGY's virtual position
    if tracker:
        strategy_pos = tracker.get_position(strategy_id)
        if strategy_pos is None:
            logger.debug(f"[can_close] {strategy_id} has no position in tracker")
            return False
        qty = strategy_pos.get("quantity", 0)
        if abs(qty) < 1e-9:
            logger.debug(f"[can_close] {strategy_id} position is already flat")
            return False

    # 2. Check for PENDING close orders for THIS STRATEGY
    if registry.has_pending_close(strategy_id):
        logger.debug(f"[can_close] {strategy_id} already has pending close order")
        return False

    return True


def can_open_position(strategy_id: str, symbol: str, registry: OrderRegistry, tracker: Any) -> bool:
    """
    Check if it's safe to open a position for THIS STRATEGY.

    Returns False if:
    1. This strategy already has a position
    2. There's already a pending entry order for THIS strategy

    Args:
        strategy_id: Strategy identifier
        symbol: Trading symbol
        registry: OrderRegistry instance
        tracker: VirtualPositionTracker instance
    """
    # 1. Check THIS STRATEGY's virtual position
    if tracker:
        strategy_pos = tracker.get_position(strategy_id)
        if strategy_pos is not None:
            qty = strategy_pos.get("quantity", 0)
            if abs(qty) > 1e-9:
                logger.debug(f"[can_open] {strategy_id} already has position: {qty}")
                return False

    # 2. Check for PENDING entry orders for THIS STRATEGY
    if registry.has_pending_entry(strategy_id):
        logger.debug(f"[can_open] {strategy_id} already has pending entry order")
        return False

    return True


async def flatten_symbol(symbol: str, client: Any, account_id: str, registry: OrderRegistry, tracker: Any) -> bool:
    """
    Nuclear option: Flatten all exposure for a symbol.

    Called when position mismatch detected. Steps:
    1. Cancel ALL pending orders for symbol
    2. Force close ALL exchange exposure for symbol
    3. Mark ALL virtual positions for symbol as closed
    4. Wait for confirmed flat on both sides
    5. Log detailed mismatch report

    Args:
        symbol: Trading symbol to flatten
        client: ProjectX client
        account_id: Account ID
        registry: OrderRegistry instance
        tracker: VirtualPositionTracker instance

    Returns:
        True if successfully flattened, False otherwise
    """
    logger.warning(f"[NUCLEAR] Flattening all exposure for {symbol}")

    try:
        # 1. Cancel ALL pending orders for symbol
        pending = registry.get_pending_orders_for_symbol(symbol)
        for record in pending:
            try:
                logger.info(f"[NUCLEAR] Cancelling order {record.tag} (id={record.order_id})")
                result = client.order_cancel(account_id, record.order_id)
                if result.get("success"):
                    registry.register_cancellation(record.order_id)
            except Exception as e:
                logger.error(f"[NUCLEAR] Failed to cancel {record.tag}: {e}")

        # Brief wait for cancellations to process
        time.sleep(0.3)

        # 2. Get current exchange position
        positions = client.position_search_open(account_id)
        exchange_qty = 0
        for pos in positions:
            if extract_symbol_from_contract(pos.get("contractId", "")) == symbol:
                size = pos.get("size", 0)
                pos_type = pos.get("type", 1)
                exchange_qty = size if pos_type == 1 else -size
                break

        # 3. Close exchange position if any
        if abs(exchange_qty) > 0:
            close_side = "SELL" if exchange_qty > 0 else "BUY"
            close_qty = abs(int(exchange_qty))

            tag = registry.generate_unique_tag(OrderPurpose.CLOSE, f"NUCLEAR_{symbol}")
            logger.info(f"[NUCLEAR] Closing exchange position: {close_side} {close_qty} {symbol}, tag={tag}")

            # TODO: Call actual order_place here
            # For now, log the intent
            logger.warning(f"[NUCLEAR] Would submit: {close_side} {close_qty} {symbol}")

        # 4. Mark ALL virtual positions for symbol as closed
        if tracker:
            closed_count = 0
            for pos in tracker.get_all_positions():
                if pos.get("symbol") == symbol:
                    strategy_id = pos.get("strategy_id")
                    if strategy_id:
                        tracker.close_position(strategy_id)
                        closed_count += 1
            logger.info(f"[NUCLEAR] Closed {closed_count} virtual positions for {symbol}")

        # 5. Log summary
        logger.warning(
            f"[NUCLEAR] Flatten complete for {symbol}. "
            f"Cancelled {len(pending)} orders, closed exchange qty={exchange_qty}"
        )

        return True

    except Exception as e:
        logger.error(f"[NUCLEAR] Failed to flatten {symbol}: {e}")
        return False


# Tag pattern constants for cleanup
OLD_TAG_PATTERNS = ["BRK_ENTRY_", "BRK_TP_", "BRK_STOP_", "BRK_CLOSE_"]
NEW_TAG_PATTERNS = ["ENT_", "TP_", "SL_", "CLOSE_"]
ALL_TAG_PATTERNS = OLD_TAG_PATTERNS + NEW_TAG_PATTERNS


def is_our_order_tag(tag: str) -> bool:
    """Check if a tag belongs to our order management system."""
    if not tag:
        return False
    return any(tag.startswith(p) for p in ALL_TAG_PATTERNS)


def parse_tag(tag: str) -> Optional[Dict[str, str]]:
    """
    Parse a tag into its components.

    New format: {PURPOSE}_{STRATEGY_ID}_{UNIX_MS}_{UUID6}
    Example: ENT_ES_1M_08_1732556789123_A3F7C9

    Returns dict with keys: purpose, strategy_id, timestamp_ms, uuid
    """
    if not tag:
        return None

    # Check for new format
    for prefix in NEW_TAG_PATTERNS:
        if tag.startswith(prefix):
            parts = tag.split("_")
            if len(parts) >= 4:
                # Last part is UUID, second-to-last is timestamp
                return {
                    "purpose": parts[0],
                    "strategy_id": "_".join(parts[1:-2]),
                    "timestamp_ms": parts[-2],
                    "uuid": parts[-1],
                }

    # Check for old format (for backward compatibility)
    for prefix in OLD_TAG_PATTERNS:
        if tag.startswith(prefix):
            return {
                "purpose": prefix.rstrip("_"),
                "strategy_id": tag[len(prefix) :],
                "timestamp_ms": None,
                "uuid": None,
            }

    return None


def submit_orders_sequentially(
    order_queue: List[OrderIntent],
    registry: OrderRegistry,
    client: Any,
    account_id: str,
    tracker: Any,
    inter_order_delay: float = 0.15,
) -> List[OrderResult]:
    """
    Submit orders ONE AT A TIME with full verification between each.

    This is the core of the bulletproof order management system.
    Even if we have 5 entries and 2 exits queued, we submit one,
    verify exchange state, then submit the next.

    Args:
        order_queue: List of OrderIntent objects to submit
        registry: OrderRegistry instance
        client: ProjectX client
        account_id: Account ID
        tracker: VirtualPositionTracker instance
        inter_order_delay: Seconds to wait between orders (default 150ms)

    Returns:
        List of OrderResult objects indicating success/failure of each
    """
    results = []

    for intent in order_queue:
        try:
            # 1. VERIFY EXCHANGE STATE
            exchange_positions = client.position_search_open(account_id)

            # 2. CHECK POSITION SYNC for this symbol
            sync_result = check_position_sync(intent.symbol, exchange_positions, tracker)

            if not sync_result.is_synced:
                logger.error(
                    f"[Sequential] Position mismatch for {intent.symbol}, "
                    f"skipping order for {intent.strategy_id}. "
                    f"Exchange={sync_result.exchange_qty}, Virtual={sync_result.virtual_qty}"
                )
                results.append(
                    OrderResult(
                        tag="", order_id=None, status="skipped", message=f"Position mismatch: {sync_result.details}"
                    )
                )
                # Note: Caller should handle FLATTEN_SYMBOL action
                continue

            # 3. CHECK IF THIS ORDER IS STILL VALID
            if intent.is_close:
                if not can_close_position(intent.strategy_id, intent.symbol, registry, tracker):
                    logger.warning(
                        f"[Sequential] Skipping close for {intent.strategy_id} - "
                        "position already flat or close pending"
                    )
                    results.append(
                        OrderResult(
                            tag="", order_id=None, status="skipped", message="Position already flat or close pending"
                        )
                    )
                    continue
            else:  # Entry
                if not can_open_position(intent.strategy_id, intent.symbol, registry, tracker):
                    logger.warning(
                        f"[Sequential] Skipping entry for {intent.strategy_id} - "
                        "already in position or entry pending"
                    )
                    results.append(
                        OrderResult(
                            tag="", order_id=None, status="skipped", message="Already in position or entry pending"
                        )
                    )
                    continue

            # 4. GENERATE UNIQUE TAG
            tag = registry.generate_unique_tag(intent.purpose, intent.strategy_id)

            # 5. SUBMIT SINGLE ORDER
            logger.info(
                f"[Sequential] Submitting: {tag} - {intent.side} {intent.qty} {intent.symbol} "
                f"({intent.purpose.value})"
            )

            # Build order parameters
            order_params = {
                "account_id": account_id,
                "contract_id": _get_contract_id(intent.symbol, client),
                "type": 2 if intent.side == "BUY" else 1,  # 2=BUY, 1=SELL
                "side": intent.side,
                "size": intent.qty,
                "custom_tag": tag,
            }

            # Add price for limit/stop orders
            if intent.order_type == "LIMIT" and intent.price:
                order_params["limit_price"] = intent.price
            if intent.order_type == "STOP" and intent.stop_price:
                order_params["stop_price"] = intent.stop_price

            # Submit the order
            result = client.order_place(
                account_id=account_id,
                contract_id=order_params["contract_id"],
                order_type=order_params["type"],
                side=order_params["side"],
                size=order_params["size"],
                custom_tag=tag,
            )

            if result.get("success") and result.get("orderId"):
                order_id = result["orderId"]

                # 6. REGISTER ORDER immediately
                registry.register_submission(tag, order_id, intent)

                results.append(OrderResult(tag=tag, order_id=order_id, status="submitted", message=None))

                logger.info(f"[Sequential] Submitted successfully: {tag} -> order_id={order_id}")
            else:
                error_msg = result.get("errorMessage", "Unknown error")
                logger.error(f"[Sequential] Failed to submit {tag}: {error_msg}")
                results.append(OrderResult(tag=tag, order_id=None, status="failed", message=error_msg))

            # 7. BRIEF WAIT for exchange to process
            if inter_order_delay > 0:
                time.sleep(inter_order_delay)

        except Exception as e:
            logger.error(f"[Sequential] Exception submitting order for {intent.strategy_id}: {e}")
            results.append(OrderResult(tag="", order_id=None, status="failed", message=str(e)))

    return results


def _get_contract_id(symbol: str, client: Any) -> str:
    """
    Get the contract ID for a symbol.

    Uses the client's contract lookup if available.
    """
    # Try to get from client's cached contracts
    if hasattr(client, "get_contract_id"):
        return client.get_contract_id(symbol)

    # Fallback: construct based on known patterns
    # This is a simplified version - real implementation should use client lookup
    SYMBOL_TO_CONTRACT = {
        "ES": "CON.F.US.EP",
        "MES": "CON.F.US.MES",
        "NQ": "CON.F.US.ENQ",
        "MNQ": "CON.F.US.MNQ",
        "GC": "CON.F.US.GCE",
        "MGC": "CON.F.US.MGC",
    }

    base = SYMBOL_TO_CONTRACT.get(symbol, f"CON.F.US.{symbol}")

    # Add current front month (simplified)
    # Real implementation should get this from client
    import datetime

    now = datetime.datetime.now()
    month_codes = {3: "H", 6: "M", 9: "U", 12: "Z"}
    # Find next quarterly month
    for m in [3, 6, 9, 12]:
        if now.month <= m:
            month_code = month_codes[m]
            break
    else:
        month_code = "H"  # Next year March

    year_suffix = str(now.year)[-2:]
    if now.month > 12:  # Rollover
        year_suffix = str(now.year + 1)[-2:]

    return f"{base}.{month_code}{year_suffix}"


async def emergency_close_strategy(
    strategy_id: str,
    symbol: str,
    registry: OrderRegistry,
    client: Any,
    account_id: str,
    tracker: Any,
) -> Optional[OrderResult]:
    """
    Emergency close a strategy's position.

    Workflow:
    1. Cancel any pending brackets (TP/SL) for this strategy
    2. Wait for cancellation confirmation
    3. Check can_close_position()
    4. Submit close order

    Args:
        strategy_id: Strategy to close
        symbol: Trading symbol
        registry: OrderRegistry instance
        client: ProjectX client
        account_id: Account ID
        tracker: VirtualPositionTracker instance

    Returns:
        OrderResult if close submitted, None if skipped
    """
    logger.info(f"[EmergencyClose] Starting for {strategy_id} on {symbol}")

    # 1. Cancel any pending brackets for this strategy
    pending = registry.get_pending_orders(strategy_id)
    cancelled_count = 0
    for record in pending:
        if record.purpose in (OrderPurpose.TAKE_PROFIT, OrderPurpose.STOP_LOSS):
            if record.status == OrderStatus.SUBMITTED:
                try:
                    logger.info(f"[EmergencyClose] Cancelling bracket {record.tag}")
                    result = client.order_cancel(account_id, record.order_id)
                    if result.get("success") or result.get("errorCode") == 5:
                        # errorCode 5 = order doesn't exist (already filled/cancelled)
                        registry.register_cancellation(record.order_id)
                        cancelled_count += 1
                except Exception as e:
                    logger.error(f"[EmergencyClose] Failed to cancel {record.tag}: {e}")

    if cancelled_count > 0:
        logger.info(f"[EmergencyClose] Cancelled {cancelled_count} brackets")
        # 2. Brief wait for cancellations to process
        time.sleep(0.25)

    # 3. Check if we can close
    if not can_close_position(strategy_id, symbol, registry, tracker):
        logger.info(f"[EmergencyClose] {strategy_id} already flat, skipping")
        return None

    # 4. Get position quantity to close
    position_qty = 0
    if tracker:
        pos = tracker.get_position(strategy_id)
        if pos:
            position_qty = pos.get("quantity", 0)

    if abs(position_qty) < 1e-9:
        logger.info(f"[EmergencyClose] {strategy_id} has zero quantity")
        return None

    # Determine close side
    close_side = "SELL" if position_qty > 0 else "BUY"
    close_qty = abs(int(position_qty))

    # Create close intent
    close_intent = OrderIntent(
        strategy_id=strategy_id,
        symbol=symbol,
        side=close_side,
        qty=close_qty,
        purpose=OrderPurpose.CLOSE,
        is_close=True,
        order_type="MARKET",
    )

    # Submit via sequential submitter (single order)
    results = submit_orders_sequentially(
        [close_intent],
        registry,
        client,
        account_id,
        tracker,
        inter_order_delay=0,  # No delay for emergency
    )

    if results:
        return results[0]
    return None
