#!/usr/bin/env python3
"""
REST-based Bracket Order Manager for ProjectX.

Since ProjectX's linkedOrderId doesn't create true OCO behavior,
this manager tracks SL/TP pairs and cancels orphans via polling.

Usage:
    manager = BracketOrderManager(client, account_id)

    # After placing SL and TP orders:
    manager.register_bracket("ES_LONG_123", sl_order_id=111, tp_order_id=222, symbol="ES")

    # Call periodically (e.g., every 1-2 seconds):
    manager.poll_and_cleanup()

    # Or manually cancel a bracket:
    manager.cancel_bracket("ES_LONG_123")
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class BracketPair:
    """Represents a paired SL/TP bracket."""

    base_tag: str
    sl_order_id: Optional[int] = None
    tp_order_id: Optional[int] = None
    symbol: str = ""
    active: bool = True
    created_at: datetime = field(default_factory=datetime.now)

    # Track which orders have been confirmed filled/cancelled
    sl_terminal: bool = False
    tp_terminal: bool = False


class BracketOrderManager:
    """
    Manages bracket order pairs via REST API polling.

    When one leg (SL or TP) fills, automatically cancels the other.
    Also handles cleanup when positions are closed externally.
    """

    # Order status codes from ProjectX
    STATUS_OPEN = 1
    STATUS_FILLED = 2
    STATUS_CANCELLED = 3
    STATUS_EXPIRED = 4
    STATUS_REJECTED = 5

    TERMINAL_STATUSES = {STATUS_FILLED, STATUS_CANCELLED, STATUS_EXPIRED, STATUS_REJECTED}

    def __init__(
        self,
        client,
        account_id: int,
        strategy_states: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
        order_registry=None,
    ):
        """
        Initialize the bracket manager.

        Args:
            client: ProjectXClient instance
            account_id: Trading account ID
            strategy_states: Optional dict mapping strategy_id -> EnhancedStrategyState
                           When provided, enables fill processing and bracket recreation
            logger: Optional logger instance
            order_registry: OrderRegistry instance for centralized order tracking
        """
        self.client = client
        # Ensure account_id is int (API requires Int32)
        if account_id is None:
            raise ValueError("account_id cannot be None for BracketOrderManager")
        self.account_id = int(account_id)
        self.logger = logger or logging.getLogger(__name__)
        self.order_registry = order_registry  # Bulletproof order tracking

        # Strategy states for fill processing and bracket recreation
        # Type: Dict[str, EnhancedStrategyState] but using Any to avoid circular import
        self.strategy_states: Dict[str, Any] = strategy_states or {}

        # Active bracket pairs: base_tag -> BracketPair
        self.brackets: Dict[str, BracketPair] = {}

        # Order ID to base_tag mapping for quick lookups
        self._order_to_bracket: Dict[int, str] = {}

        # Idempotency tracking: processed fill order IDs
        self._processed_fills: set = set()  # Order IDs we've processed
        self._processed_trades: set = set()  # Trade IDs we've processed

        # CRITICAL: Track order IDs WE placed during THIS run
        # Only process fills for orders in this set - ignore everything else
        self._our_order_ids: set = set()

        # Debounce tracking: tag -> timestamp (UTC)
        self._recent_placements: Dict[str, datetime] = {}
        self.PLACEMENT_COOLDOWN_SECONDS = 10

        # Failed placement tracking: tag -> failure count
        self._failed_recreations: Dict[str, int] = {}
        self.MAX_RECREATION_FAILURES = 3

        # In-flight order tracking: skip position sync if order just placed
        self._last_order_submission_time: Optional[datetime] = None
        self.ORDER_INFLIGHT_WINDOW_SECONDS = 5.0

        # Contract rollover tracking: symbol -> contract_id
        # Used to detect when contract month changes (e.g., ESZ25 -> ESH26)
        self._known_contracts: Dict[str, str] = {}

        # Repair cooldown tracking: symbol -> datetime when cooldown expires
        # Used to avoid spamming repair attempts when market is closed
        self._repair_cooldown: Dict[str, datetime] = {}
        self.REPAIR_COOLDOWN_MINUTES = 15  # Wait 15 minutes after market-closed failure

        # Dedicated position sync log file
        self._setup_sync_logger()

        # Stats
        self.stats = {
            "brackets_registered": 0,
            "orphans_cancelled": 0,
            "polls_executed": 0,
            "fills_processed": 0,
            "brackets_recreated": 0,
            "emergency_closes": 0,
        }

    def _setup_sync_logger(self):
        """Setup dedicated file logger for position sync actions."""
        self.sync_logger = logging.getLogger("position_sync")
        self.sync_logger.setLevel(logging.INFO)

        # Avoid duplicate handlers if already setup
        if not self.sync_logger.handlers:
            handler = logging.FileHandler("portfolio_sync_log.log")
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            handler.setFormatter(formatter)
            self.sync_logger.addHandler(handler)
            # Don't propagate to root logger (avoid console spam)
            self.sync_logger.propagate = False

    def _log_sync(self, action: str, details: Dict[str, Any]):
        """Log a position sync action to the dedicated sync log file."""
        # Format details as key=value pairs
        detail_str = " | ".join(f"{k}={v}" for k, v in details.items())
        self.sync_logger.info(f"{action} | {detail_str}")

    def register_bracket(
        self,
        base_tag: str,
        sl_order_id: Optional[int] = None,
        tp_order_id: Optional[int] = None,
        symbol: str = "",
    ) -> BracketPair:
        """
        Register a new bracket pair.

        Args:
            base_tag: Unique identifier for this bracket (e.g., "ES_LONG_123")
            sl_order_id: Stop loss order ID
            tp_order_id: Take profit order ID
            symbol: Trading symbol for logging

        Returns:
            The created BracketPair
        """
        bracket = BracketPair(
            base_tag=base_tag,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
            symbol=symbol,
        )

        self.brackets[base_tag] = bracket

        # Build reverse lookup
        if sl_order_id:
            self._order_to_bracket[sl_order_id] = base_tag
        if tp_order_id:
            self._order_to_bracket[tp_order_id] = base_tag

        self.stats["brackets_registered"] += 1
        self.logger.info(f"[BRACKET] Registered: {base_tag} | SL={sl_order_id} TP={tp_order_id} | {symbol}")

        return bracket

    def register_our_order(self, order_id: int) -> None:
        """
        Register an order ID that WE placed during this run.

        Only fills for registered orders will be processed.
        Call this immediately after placing any order.

        Args:
            order_id: The order ID returned from order_place API
        """
        if order_id:
            self._our_order_ids.add(int(order_id))
            self.logger.debug(f"[BRACKET] Registered our order: {order_id}")

    def mark_order_submitted(self) -> None:
        """
        Mark that an order was just submitted.

        Call immediately after any order submission (entry, exit, bracket).
        This starts the in-flight window during which position sync will be skipped.
        """
        self._last_order_submission_time = datetime.now(timezone.utc)
        self.logger.debug("[BRACKET] Order submitted - starting in-flight window")

    def is_order_inflight(self) -> bool:
        """
        Check if an order was placed within the in-flight window.

        Position sync should be skipped when this returns True to avoid
        racing with order confirmation.

        Returns:
            True if an order was placed within ORDER_INFLIGHT_WINDOW_SECONDS
        """
        if not self._last_order_submission_time:
            return False
        elapsed = (datetime.now(timezone.utc) - self._last_order_submission_time).total_seconds()
        return elapsed < self.ORDER_INFLIGHT_WINDOW_SECONDS

    def startup_cleanup(self) -> Dict[str, any]:
        """
        Cancel ALL existing BRK_* orders on startup to ensure clean slate.

        MUST be called before any trading begins. Previous bot runs may have
        left orphan bracket orders that will block new orders with same tags.

        Returns:
            Dict with 'cancelled' count and 'errors' list
        """
        result = {"cancelled": 0, "errors": [], "found": 0}

        self.logger.info(
            "[BRACKET-REASONING] I'm starting up and need to check for leftover orders from previous runs. "
            "If there are stale bracket orders sitting around, they could block new orders with duplicate tags. "
            "Let me scan the last 24 hours and clean up anything I find."
        )

        # Query recent orders (24h lookback)
        start_date = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        self.logger.debug(f"[BRACKET-REASONING] Looking back 24h to {start_date} for any orphaned orders...")
        try:
            orders_resp = self.client.api.order_search(self.account_id, start_date)
        except Exception as e:
            self.logger.error(f"[BRACKET] Startup cleanup failed to query orders: {e}")
            result["errors"].append(f"Query failed: {e}")
            return result

        if not orders_resp.get("success"):
            result["errors"].append(f"Order search failed: {orders_resp}")
            return result

        orders = orders_resp.get("orders", [])
        self.logger.info(
            f"[BRACKET-REASONING] I found {len(orders)} orders in the last 24 hours. "
            f"Let me check which ones are stale bracket orders that need to be cancelled..."
        )

        # Find and cancel all OPEN BRK_* orders
        for order in orders:
            tag = order.get("customTag") or ""
            status = order.get("status")
            order_id = order.get("id")

            # Only cancel OPEN orders with our tag patterns
            if status != self.STATUS_OPEN:
                continue

            # Check both old (BRK_*) and new (ENT_, TP_, SL_, CLOSE_) tag patterns
            old_patterns = ("BRK_ENTRY_", "BRK_TP_", "BRK_STOP_", "BRK_CLOSE_")
            new_patterns = ("ENT_", "TP_", "SL_", "CLOSE_")
            if tag.startswith(old_patterns) or tag.startswith(new_patterns):
                result["found"] += 1
                self.logger.info(
                    f"[BRACKET-REASONING] Found a stale order! tag={tag}, order_id={order_id}. "
                    f"This is leftover from a previous run - I need to cancel it or it'll block new orders."
                )
                if self._cancel_order(order_id, tag[:10], "startup_cleanup"):
                    result["cancelled"] += 1
                    self.logger.info(f"[BRACKET-REASONING] Good, I cancelled stale order {order_id}.")
                else:
                    self.logger.warning(
                        f"[BRACKET-REASONING] Hmm, couldn't cancel order {order_id}. This might cause tag conflicts."
                    )
                    result["errors"].append(f"Failed to cancel {order_id}")

        if result["cancelled"] > 0:
            self.logger.warning(
                f"[BRACKET] Startup cleanup: cancelled {result['cancelled']}/{result['found']} stale orders"
            )
        else:
            self.logger.info("[BRACKET] Startup cleanup: no stale bracket orders found")

        return result

    def poll_and_cleanup(self) -> Dict[str, list]:
        """
        Poll order status and cancel orphaned orders.

        Returns:
            Dict with 'cancelled' and 'errors' lists
        """
        self.stats["polls_executed"] += 1
        result = {"cancelled": [], "errors": []}

        if not self.brackets:
            return result

        # Get all open orders
        start_date = (datetime.now() - timedelta(hours=24)).isoformat()
        orders_resp = self.client.api.order_search(self.account_id, start_date)

        if not orders_resp.get("success"):
            self.logger.error(f"[BRACKET] Failed to fetch orders: {orders_resp}")
            return result

        # Build order status map
        order_status: Dict[int, int] = {}
        for order in orders_resp.get("orders", []):
            order_id = order.get("id")
            status = order.get("status")
            if order_id and status is not None:
                order_status[order_id] = status

        # Check each active bracket
        brackets_to_deactivate = []

        for base_tag, bracket in self.brackets.items():
            if not bracket.active:
                continue

            sl_id = bracket.sl_order_id
            tp_id = bracket.tp_order_id

            # Get current status (default to "unknown" if not in recent orders)
            sl_status = order_status.get(sl_id) if sl_id else None
            tp_status = order_status.get(tp_id) if tp_id else None

            # Check if SL filled/terminal
            if sl_id and sl_status in self.TERMINAL_STATUSES and not bracket.sl_terminal:
                bracket.sl_terminal = True
                self.logger.info(f"[BRACKET] SL terminal: {base_tag} | SL={sl_id} status={sl_status}")

                # If SL filled, cancel TP
                if sl_status == self.STATUS_FILLED and tp_id and not bracket.tp_terminal:
                    if self._cancel_order(tp_id, "TP", base_tag):
                        result["cancelled"].append({"order_id": tp_id, "type": "TP", "bracket": base_tag})
                        bracket.tp_terminal = True

            # Check if TP filled/terminal
            if tp_id and tp_status in self.TERMINAL_STATUSES and not bracket.tp_terminal:
                bracket.tp_terminal = True
                self.logger.info(f"[BRACKET] TP terminal: {base_tag} | TP={tp_id} status={tp_status}")

                # If TP filled, cancel SL
                if tp_status == self.STATUS_FILLED and sl_id and not bracket.sl_terminal:
                    if self._cancel_order(sl_id, "SL", base_tag):
                        result["cancelled"].append({"order_id": sl_id, "type": "SL", "bracket": base_tag})
                        bracket.sl_terminal = True

            # Deactivate bracket if both legs are terminal
            if bracket.sl_terminal and bracket.tp_terminal:
                brackets_to_deactivate.append(base_tag)

        # Deactivate completed brackets
        for base_tag in brackets_to_deactivate:
            self.brackets[base_tag].active = False
            self.logger.info(f"[BRACKET] Deactivated: {base_tag}")

        return result

    def _cancel_order(self, order_id: int, order_type: str, base_tag: str) -> bool:
        """Cancel an order and log the result."""
        self.logger.debug(
            f"[BRACKET-REASONING] I need to cancel {order_type} order {order_id} (bracket: {base_tag}). "
            f"Sending cancel request to the exchange..."
        )
        try:
            cancel_resp = self.client.api.order_cancel(self.account_id, order_id)

            if cancel_resp.get("success"):
                self.stats["orphans_cancelled"] += 1
                self.logger.info(
                    f"[BRACKET-REASONING] Successfully cancelled {order_type} {order_id} for {base_tag}. "
                    f"This prevents a double-fill since the other leg already triggered."
                )
                return True
            else:
                error_code = cancel_resp.get("errorCode")
                # errorCode 5 = order doesn't exist (already filled/cancelled)
                if error_code == 5:
                    self.logger.info(
                        f"[BRACKET-REASONING] {order_type} {order_id} is already gone (errorCode=5). "
                        f"That's fine - it was already filled or cancelled. Nothing more to do."
                    )
                    return True  # Consider it handled
                else:
                    self.logger.warning(
                        f"[BRACKET-REASONING] Uh oh, couldn't cancel {order_type} {order_id} (errorCode={error_code}). "
                        f"This orphan order might still fill unexpectedly. I'll try again next poll."
                    )
                    return False
        except Exception as e:
            self.logger.error(
                f"[BRACKET-REASONING] Exception while cancelling {order_type} {order_id}: {e}. "
                f"I'll retry on the next poll cycle."
            )
            return False

    def _confirm_cancellation(self, order_ids: List[int], strategy_tag: str, max_attempts: int = 3) -> Dict[str, Any]:
        """
        Poll exchange to confirm orders are actually cancelled.

        Args:
            order_ids: List of order IDs that were cancelled
            strategy_tag: Strategy identifier for logging
            max_attempts: Maximum polling attempts (default 3, with 0.5s between)

        Returns:
            Dict with 'confirmed', 'still_open', and 'attempts' keys
        """
        result = {"confirmed": False, "still_open": [], "attempts": 0}

        if not order_ids:
            result["confirmed"] = True
            return result

        for attempt in range(max_attempts):
            result["attempts"] = attempt + 1
            time.sleep(0.5)

            # Query current open orders
            try:
                start_date = (datetime.now() - timedelta(hours=1)).isoformat()
                orders_resp = self.client.api.order_search(self.account_id, start_date)
                if not orders_resp.get("success"):
                    self.logger.warning(f"[CANCEL-CONFIRM] Failed to query orders on attempt {attempt + 1}")
                    continue

                # Check which of our cancelled orders are still open
                open_order_ids = {
                    o.get("id") for o in orders_resp.get("orders", []) if o.get("status") == self.STATUS_OPEN
                }
                still_open = [oid for oid in order_ids if oid in open_order_ids]

                if not still_open:
                    result["confirmed"] = True
                    self.logger.info(
                        f"[CANCEL-CONFIRM] All {len(order_ids)} orders confirmed cancelled for {strategy_tag} "
                        f"after {attempt + 1} attempt(s)"
                    )
                    return result

                result["still_open"] = still_open
                self.logger.warning(
                    f"[CANCEL-CONFIRM] Attempt {attempt + 1}/{max_attempts}: "
                    f"{len(still_open)} of {len(order_ids)} orders still open for {strategy_tag}"
                )

            except Exception as e:
                self.logger.error(f"[CANCEL-CONFIRM] Error polling orders on attempt {attempt + 1}: {e}")

        # Max attempts reached, some orders still open
        self.logger.error(
            f"[CANCEL-CONFIRM] FAILED to confirm cancellation for {strategy_tag} after {max_attempts} attempts. "
            f"Still open: {result['still_open']}. Proceeding anyway but bracket fills may occur!"
        )
        # Log to sync file - this is a dangerous situation
        self._log_sync(
            "CANCEL_CONFIRM_FAILED",
            {
                "strategy": strategy_tag,
                "attempts": max_attempts,
                "still_open": result["still_open"],
                "warning": "Proceeding without confirmation - bracket fills may occur unexpectedly",
            },
        )
        return result

    def cancel_bracket(self, base_tag: str) -> Dict[str, bool]:
        """
        Cancel both legs of a bracket.

        Args:
            base_tag: The bracket identifier

        Returns:
            Dict with 'sl_cancelled' and 'tp_cancelled' bools
        """
        result = {"sl_cancelled": False, "tp_cancelled": False}

        bracket = self.brackets.get(base_tag)
        if not bracket:
            self.logger.warning(f"[BRACKET] Unknown bracket: {base_tag}")
            return result

        if bracket.sl_order_id and not bracket.sl_terminal:
            result["sl_cancelled"] = self._cancel_order(bracket.sl_order_id, "SL", base_tag)
            bracket.sl_terminal = True

        if bracket.tp_order_id and not bracket.tp_terminal:
            result["tp_cancelled"] = self._cancel_order(bracket.tp_order_id, "TP", base_tag)
            bracket.tp_terminal = True

        bracket.active = False
        self.logger.info(f"[BRACKET] Manually cancelled: {base_tag}")

        return result

    def get_active_brackets(self) -> Dict[str, BracketPair]:
        """Return all active brackets."""
        return {k: v for k, v in self.brackets.items() if v.active}

    def get_bracket_for_order(self, order_id: int) -> Optional[BracketPair]:
        """Find the bracket containing a given order ID."""
        base_tag = self._order_to_bracket.get(order_id)
        if base_tag:
            return self.brackets.get(base_tag)
        return None

    def cleanup_stale_brackets(self, max_age_hours: int = 24):
        """Remove brackets older than max_age_hours."""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        stale = [k for k, v in self.brackets.items() if v.created_at < cutoff]

        for base_tag in stale:
            del self.brackets[base_tag]
            self.logger.debug(f"[BRACKET] Removed stale: {base_tag}")

        if stale:
            self.logger.info(f"[BRACKET] Cleaned up {len(stale)} stale brackets")

    def cancel_brackets_for_strategy(self, strategy_tag: str, wait_seconds: float = 0.5) -> Dict[str, any]:
        """
        Cancel all open bracket orders for a specific strategy before closing position.

        Race-safe close pattern:
        1. Query all open orders
        2. Find TP/SL orders matching this strategy's tag pattern
        3. Cancel them
        4. Poll to CONFIRM cancellations took effect (up to 3 attempts, 0.5s each)

        Args:
            strategy_tag: The strategy ID used as base tag (e.g., "rsi_mean_revert_1")
            wait_seconds: DEPRECATED - confirmation polling now handles timing

        Returns:
            Dict with:
                - cancelled: List of cancelled orders
                - errors: List of errors
                - found: Number of matching orders found
                - cancellation_confirmed: bool - True if all cancels verified
                - confirmation_attempts: int - Number of poll attempts
                - still_open: List of order IDs still open (if not confirmed)
        """
        result = {"cancelled": [], "errors": [], "waited": False, "found": 0}

        # Build expected tag patterns for this strategy
        # Tags look like: BRK_TP_ENT_STRATEGYID_..., BRK_STOP_ENT_STRATEGYID_...,
        # or legacy: BRK_TP_STRATEGYID, BRK_STOP_STRATEGYID
        # or new format: TP_STRATEGYID_..., SL_STRATEGYID_...
        tp_prefixes = [f"BRK_TP_ENT_{strategy_tag}", f"BRK_TP_{strategy_tag}", f"TP_{strategy_tag}"]
        sl_prefixes = [f"BRK_STOP_ENT_{strategy_tag}", f"BRK_STOP_{strategy_tag}", f"SL_{strategy_tag}"]

        # Query recent orders
        start_date = (datetime.now() - timedelta(hours=24)).isoformat()
        try:
            orders_resp = self.client.api.order_search(self.account_id, start_date)
        except Exception as e:
            self.logger.error(f"[BRACKET-CLOSE] Failed to query orders: {e}")
            result["errors"].append(f"Query failed: {e}")
            return result

        if not orders_resp.get("success"):
            result["errors"].append(f"Order search failed: {orders_resp}")
            return result

        orders = orders_resp.get("orders", [])

        # Find and cancel matching open orders
        for order in orders:
            tag = order.get("customTag") or ""
            status = order.get("status")
            order_id = order.get("id")

            # Only cancel OPEN orders (status 1)
            if status != self.STATUS_OPEN:
                continue

            # Check if this order belongs to our strategy's brackets (match any prefix)
            is_tp = any(tag.startswith(prefix) for prefix in tp_prefixes)
            is_sl = any(tag.startswith(prefix) for prefix in sl_prefixes)
            if is_tp or is_sl:
                result["found"] += 1
                order_type = "TP" if is_tp else "SL"

                if self._cancel_order(order_id, order_type, strategy_tag):
                    result["cancelled"].append(
                        {
                            "order_id": order_id,
                            "type": order_type,
                            "tag": tag,
                        }
                    )
                    self.logger.info(f"[BRACKET-CLOSE] Pre-close cancel {order_type} {order_id} for {strategy_tag}")
                else:
                    result["errors"].append(f"Failed to cancel {order_type} {order_id}")

        # Confirm cancellations actually took effect
        if result["cancelled"]:
            cancelled_ids = [c["order_id"] for c in result["cancelled"]]
            confirm_result = self._confirm_cancellation(cancelled_ids, strategy_tag)
            result["cancellation_confirmed"] = confirm_result["confirmed"]
            result["confirmation_attempts"] = confirm_result["attempts"]
            if not confirm_result["confirmed"]:
                result["still_open"] = confirm_result["still_open"]
                self.logger.warning(
                    f"[BRACKET-CLOSE] WARNING: Not all brackets confirmed cancelled for {strategy_tag}. "
                    f"Still open: {confirm_result['still_open']}. Proceeding with caution."
                )
        else:
            result["cancellation_confirmed"] = True  # Nothing to cancel = confirmed
            result["confirmation_attempts"] = 0

        return result

    def scan_and_cleanup_orphans(self, lookback_hours: float = 12.0) -> Dict[str, list]:
        """
        Stateless orphan cleanup - scans recent orders and cancels orphans based on tags.

        This method does NOT require prior registration. It works by:
        1. Querying recent orders (default 2 hours - most brackets resolve quickly)
        2. Identifying bracket children by tag pattern (BRK_TP_*, BRK_STOP_*)
        3. Grouping by base tag
        4. If TP is filled → cancel SL, if SL is filled → cancel TP

        Args:
            lookback_hours: How far back to search for orders (default 2 hours)

        Returns:
            Dict with 'cancelled' and 'errors' lists
        """
        result = {"cancelled": [], "errors": [], "scanned": 0, "bracket_pairs": 0}

        # Get orders from lookback window (default 2h instead of 24h for efficiency)
        # Use UTC time - API expects UTC timestamps

        now_utc = datetime.now(timezone.utc)
        start_date = (now_utc - timedelta(hours=lookback_hours)).isoformat()
        orders_resp = self.client.api.order_search(self.account_id, start_date)

        if not orders_resp.get("success"):
            self.logger.error(f"[BRACKET-SCAN] Failed to fetch orders: {orders_resp}")
            result["errors"].append("Failed to fetch orders")
            return result

        orders = orders_resp.get("orders", [])
        result["scanned"] = len(orders)

        # Group bracket children by base tag
        # Tag format: BRK_TP_BASE or BRK_STOP_BASE
        bracket_groups: Dict[str, Dict[str, dict]] = {}  # base_tag -> {"tp": order, "sl": order}

        for order in orders:
            tag = order.get("customTag") or ""
            order_id = order.get("id")
            status = order.get("status")

            if tag.startswith("BRK_TP_"):
                base_tag = tag[7:]  # Remove "BRK_TP_" prefix
                if base_tag not in bracket_groups:
                    bracket_groups[base_tag] = {}
                bracket_groups[base_tag]["tp"] = {"id": order_id, "status": status, "tag": tag}

            elif tag.startswith("BRK_STOP_"):
                base_tag = tag[9:]  # Remove "BRK_STOP_" prefix
                if base_tag not in bracket_groups:
                    bracket_groups[base_tag] = {}
                bracket_groups[base_tag]["sl"] = {"id": order_id, "status": status, "tag": tag}

        # Check each bracket group for orphans
        for base_tag, group in bracket_groups.items():
            tp = group.get("tp")
            sl = group.get("sl")

            if not tp or not sl:
                # Incomplete pair - skip
                continue

            tp_status = tp.get("status")
            sl_status = sl.get("status")

            # TP filled and SL still open → cancel SL
            if tp_status == self.STATUS_FILLED and sl_status == self.STATUS_OPEN:
                if self._cancel_order(sl["id"], "SL", base_tag):
                    result["cancelled"].append(
                        {
                            "order_id": sl["id"],
                            "type": "SL",
                            "reason": "TP_filled",
                            "base_tag": base_tag,
                        }
                    )
                    self.logger.info(f"[BRACKET-SCAN] Cancelled orphan SL {sl['id']} (TP filled) | base={base_tag}")

            # SL filled and TP still open → cancel TP
            elif sl_status == self.STATUS_FILLED and tp_status == self.STATUS_OPEN:
                if self._cancel_order(tp["id"], "TP", base_tag):
                    result["cancelled"].append(
                        {
                            "order_id": tp["id"],
                            "type": "TP",
                            "reason": "SL_filled",
                            "base_tag": base_tag,
                        }
                    )
                    self.logger.info(f"[BRACKET-SCAN] Cancelled orphan TP {tp['id']} (SL filled) | base={base_tag}")

        # Count bracket pairs found (complete pairs only)
        result["bracket_pairs"] = sum(1 for g in bracket_groups.values() if g.get("tp") and g.get("sl"))

        if result["cancelled"]:
            self.stats["orphans_cancelled"] += len(result["cancelled"])
            self.logger.info(f"[BRACKET-SCAN] Cancelled {len(result['cancelled'])} orphans")

        return result

    # =========================================================================
    # Enhanced Bracket Management Methods (Phase 2-10)
    # =========================================================================

    def poll_cycle(self, current_prices: Dict[str, float], calendar=None) -> Dict:
        """
        Single coordinated poll cycle. Call every 2 seconds in live trading.

        This is the main entry point for resilient bracket management. It:
        1. Handles session transition cleanup (maintenance windows)
        2. Fetches orders and trades once
        3. Processes fills to update virtual positions
        4. Ensures brackets exist for all positions (recreates if missing)
        5. Cleans up orphan brackets

        Args:
            current_prices: Dict[symbol, price] for all traded symbols
            calendar: Optional TradingCalendar for session gating

        Returns:
            Dict with all actions taken
        """
        result = {
            "fills_processed": [],
            "brackets_recreated": [],
            "positions_closed": [],
            "orphans_cancelled": [],
            "errors": [],
        }

        # 0. Session transition cleanup - cancel brackets if must_be_flat
        if calendar:
            self._session_transition_cleanup(calendar, result)

        # 1. Single fetch for entire cycle
        orders, trades = self._fetch_orders_and_trades()
        if orders is None:
            result["errors"].append("Failed to fetch orders")
            return result

        # 2. Process fills (update virtual positions)
        fills_result = self._process_fills(orders, trades)
        result["fills_processed"] = fills_result

        # 3. Ensure brackets for all positions (with calendar gating)
        brackets_result = self._ensure_brackets(orders, current_prices, calendar)
        result["brackets_recreated"] = brackets_result.get("recreated", [])
        result["positions_closed"] = brackets_result.get("closed", [])

        # 4. Orphan cleanup
        orphans_result = self._cleanup_orphans(orders)
        result["orphans_cancelled"] = orphans_result.get("cancelled", [])

        # 5. Cleanup stale tracking dicts
        self._cleanup_tracking_dicts()

        return result

    def _fetch_orders_and_trades(self) -> tuple:
        """Fetch orders and trades (24h lookback)."""
        start_date = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        try:
            orders_resp = self.client.api.order_search(self.account_id, start_date)
            if not orders_resp.get("success"):
                self.logger.error(f"[BRACKET] order_search failed: {orders_resp}")
                return None, None

            trades_resp = self.client.api.trade_search(self.account_id, start_date)
            if not trades_resp.get("success"):
                self.logger.error(f"[BRACKET] trade_search failed: {trades_resp}")
                return None, None

            return orders_resp.get("orders", []), trades_resp.get("trades", [])

        except Exception as e:
            self.logger.error(f"[BRACKET] API error: {e}")
            return None, None

    def _process_fills(self, orders: list, trades: list) -> list:
        """Process filled orders using trade_search for ACTUAL fill prices."""
        processed = []

        if not self.strategy_states:
            self.logger.debug("[BRACKET-REASONING] I don't have any strategy states to track, so nothing to process.")
            return processed  # No strategy states, can't process fills

        # Identify NEW fills that need processing (not already processed AND from this run)
        new_fills = [
            o
            for o in orders
            if o.get("status") == self.STATUS_FILLED
            and o.get("id") not in self._processed_fills
            and o.get("id") in self._our_order_ids
        ]

        # Identify NEW trades (for tracking and price lookup)
        new_trades = [t for t in trades if t.get("id") not in self._processed_trades]

        # Skip entirely if nothing new to process
        if not new_fills:
            # Still mark new trades as seen even if no fills to process
            for t in new_trades:
                self._processed_trades.add(t.get("id"))
            return processed

        # Build trade lookup: order_id -> fill_price
        trade_prices = {t.get("orderId"): t.get("price") for t in trades if t.get("orderId")}

        # DEBUG: Log trade details for fills we're about to process
        for order in new_fills:
            order_id = order.get("id")
            if order_id in trade_prices:
                # Find the matching trade for diagnostic logging
                matching_trade = next((t for t in trades if t.get("orderId") == order_id), None)
                if matching_trade:
                    self.logger.debug(
                        f"[TRADE-DEBUG] order_id={order_id} -> trade: price={matching_trade.get('price')}, "
                        f"contractId={matching_trade.get('contractId')}, timestamp={matching_trade.get('timestamp')}, "
                        f"id={matching_trade.get('id')}"
                    )

        # Only log when we have new fills to process
        self.logger.info(
            f"[BRACKET-REASONING] I see {len(new_fills)} new fills to process. "
            f"I also found {len(new_trades)} new trades to look up fill prices. Let me handle each one..."
        )

        # Mark all trades as processed
        for t in trades:
            if t.get("id"):
                self._processed_trades.add(t.get("id"))

        # Iterate only through new fills (already filtered above for STATUS_FILLED,
        # not in _processed_fills, and in _our_order_ids)
        for order in new_fills:
            order_id = order.get("id")
            tag = order.get("customTag") or ""

            # Defensive assertion (filtering done above, but verify)
            assert order_id in self._our_order_ids, f"Order {order_id} not in _our_order_ids"

            # Extract strategy ID (supports BRK_ tag format)
            strategy_id = self._extract_strategy_id_from_tag(tag)
            if not strategy_id or strategy_id not in self.strategy_states:
                self.logger.debug(
                    f"[BRACKET-REASONING] Skipping fill {order_id} - I couldn't find strategy '{strategy_id}' "
                    f"in my tracked states. Maybe it's from a different bot instance."
                )
                continue

            state = self.strategy_states[strategy_id]

            # Get ACTUAL fill price from trades
            fill_price = trade_prices.get(order_id)
            if fill_price is None:
                self.logger.debug(
                    f"[BRACKET-REASONING] I know fill {order_id} exists (tag={tag}) but I don't have the "
                    f"price yet from trade_search. I'll wait and try again next poll - don't want to "
                    f"update positions with a guessed price."
                )
                continue  # DO NOT add to _processed_fills - retry next cycle

            # SANITY CHECK: Validate fill price is reasonable
            # If executor estimated a price, the actual fill should be close (within 1% for futures)
            executor_price = getattr(state, "entry_price", None)
            if executor_price and executor_price > 0:
                price_diff_pct = abs(fill_price - executor_price) / executor_price * 100
                if price_diff_pct > 1.0:  # More than 1% difference is suspicious
                    self.logger.error(
                        f"[BRACKET-SANITY] SUSPICIOUS FILL PRICE for {strategy_id}! "
                        f"order_id={order_id}, trade_price={fill_price}, executor_assumed={executor_price}, "
                        f"diff={price_diff_pct:.2f}%. This looks like a data error - using executor price instead."
                    )
                    fill_price = executor_price  # Use the executor's price, not the suspicious trade price

            try:
                # Check for entry fills (both old and new format)
                if tag.startswith("BRK_ENTRY_") or tag.startswith("ENT_"):
                    # Entry fill - DO NOT update virtual position (executor already did it)
                    # We only log and update entry_price with actual fill price
                    side = "buy" if order.get("side") == 0 else "sell"
                    qty = order.get("size", 1)

                    self.logger.info(
                        f"[BRACKET-REASONING] Entry fill confirmed for {strategy_id}! "
                        f"The executor already updated my virtual position when it submitted the order, "
                        f"but now I have the ACTUAL fill price: {fill_price}. "
                        f"({side} {qty} contracts, order_id={order_id})"
                    )
                    # Update entry_price with ACTUAL fill price (executor may have used estimated price)
                    state.entry_price = fill_price
                    processed.append({"type": "entry", "strategy": strategy_id, "price": fill_price})
                    self.stats["fills_processed"] += 1

                    # Register fill with order_registry if available
                    if self.order_registry:
                        self.order_registry.register_fill(order_id, fill_price, qty)

                    # NOW submit brackets using actual fill price + stored ATR
                    # This is the critical fix: brackets calculated from REAL fill price, not stale bar data
                    if not getattr(state, "brackets_submitted", False):
                        self._submit_brackets_after_fill(state, fill_price, side, qty)
                    else:
                        self.logger.debug(
                            f"[BRACKET-REASONING] Brackets already submitted for {strategy_id}, skipping."
                        )

                # Check for exit fills (both old and new format)
                elif tag.startswith(("BRK_TP_", "BRK_STOP_", "BRK_CLOSE_", "TP_", "SL_", "CLOSE_")):
                    # Exit fill - use ORDER size, not position size
                    pos = state.tracker.get_position(state.symbol)
                    if pos and abs(pos.quantity) > 0:
                        exit_qty = min(order.get("size", 1), abs(pos.quantity))
                        exit_side = "sell" if pos.quantity > 0 else "buy"
                        exit_type = "TP" if "TP" in tag else ("SL" if "STOP" in tag or "SL" in tag else "CLOSE")

                        # Calculate P&L for this trade
                        entry_price = state.entry_price
                        trade_pnl = None
                        if entry_price is not None and fill_price is not None:
                            try:
                                from custom_portfolio.data.futures_metadata import get_multiplier

                                multiplier = get_multiplier(state.symbol)
                            except Exception:
                                multiplier = 1.0
                            # P&L = (exit - entry) * qty * multiplier * direction
                            direction = 1 if pos.quantity > 0 else -1  # Long = +1, Short = -1
                            trade_pnl = (fill_price - entry_price) * exit_qty * multiplier * direction

                        self.logger.info(
                            f"[BRACKET-REASONING] Exit fill for {strategy_id}! A {exit_type} order just filled. "
                            f"I'm closing {exit_qty} contracts at {fill_price}. "
                            f"Current position is {pos.quantity}, so after this I need to update the tracker."
                        )
                        state.tracker.execute_order(
                            state.symbol, exit_qty, exit_side, fill_price, order_id=str(order_id)
                        )

                        # Clear bracket prices if fully flat
                        new_pos = state.tracker.get_position(state.symbol)
                        if new_pos is None or abs(new_pos.quantity) < 1e-9:
                            state.entry_price = None
                            state.take_profit_price = None
                            state.stop_loss_price = None
                            # Clear failure counters when flat
                            self._failed_recreations.pop(f"BRK_TP_{strategy_id}", None)
                            self._failed_recreations.pop(f"BRK_STOP_{strategy_id}", None)
                            self.logger.info(
                                f"[BRACKET-REASONING] {strategy_id} is now completely flat! "
                                f"I've cleared entry_price, TP, and SL targets. Ready for the next trade."
                            )
                            # IMMEDIATE sibling cancellation: Don't wait for orphan cleanup
                            # This prevents the race condition where the sibling bracket fills
                            # between now and the next _cleanup_orphans() call (2+ seconds)
                            try:
                                self.logger.info(
                                    f"[BRACKET-SIBLING] Firing immediate cancel for any remaining "
                                    f"brackets of {strategy_id} to prevent double-fill"
                                )
                                # Fire-and-forget: don't block on confirmation
                                self.cancel_brackets_for_strategy(strategy_id, wait_seconds=0)
                            except Exception as cancel_err:
                                # Non-fatal: orphan cleanup will catch it anyway
                                self.logger.warning(
                                    f"[BRACKET-SIBLING] Failed to immediately cancel siblings "
                                    f"for {strategy_id}: {cancel_err}"
                                )
                        else:
                            self.logger.info(
                                f"[BRACKET-REASONING] Partial exit for {strategy_id} - still holding "
                                f"{new_pos.quantity} contracts. Keeping brackets active."
                            )

                        processed.append(
                            {
                                "type": "exit",
                                "exit_type": exit_type,
                                "strategy": strategy_id,
                                "price": fill_price,
                                "entry_price": entry_price,
                                "pnl": trade_pnl,
                            }
                        )
                        self.stats["fills_processed"] += 1
                    else:
                        # No position to close - this is a duplicate exit or we're already flat
                        # Mark as processed to avoid repeated processing
                        self.logger.debug(
                            f"[BRACKET] Exit fill for {strategy_id} already flat @ {fill_price} - skipping"
                        )
                        processed.append(
                            {
                                "type": "exit_ignored",
                                "strategy": strategy_id,
                                "reason": "already_flat",
                                "price": fill_price,  # Include price for logging/debugging
                            }
                        )

                self._processed_fills.add(order_id)

            except Exception as e:
                self.logger.error(f"[BRACKET] Error processing fill {order_id}: {e}")
                # Don't add to processed - may retry

        return processed

    def _submit_brackets_after_fill(self, state, fill_price: float, entry_side: str, qty: int) -> Dict[str, any]:
        """
        Submit TP and SL brackets AFTER entry fill is confirmed.

        This is the critical fix for stale bracket prices: we now calculate
        brackets from the ACTUAL fill price + stored ATR (stored at signal time).

        Args:
            state: EnhancedStrategyState with stored ATR parameters
            fill_price: Actual fill price from trade_search
            entry_side: 'buy' or 'sell' (entry direction)
            qty: Number of contracts filled

        Returns:
            Dict with 'tp_order_id', 'sl_order_id', 'errors'
        """
        result = {"tp_order_id": None, "sl_order_id": None, "errors": []}
        strategy_id = state.strategy_id

        # Get stored ATR parameters (calculated at signal time, not now)
        entry_atr = getattr(state, "entry_atr", None)
        pt_mult = getattr(state, "entry_pt_mult", None) or 0.0
        sl_mult = getattr(state, "entry_sl_mult", None) or 0.0

        if entry_atr is None or entry_atr <= 0:
            self.logger.warning(
                f"[BRACKET-REASONING] Cannot submit brackets for {strategy_id}: "
                f"no valid ATR stored (entry_atr={entry_atr}). Position is unprotected!"
            )
            result["errors"].append("No valid ATR stored at entry time")
            return result

        # Get contract_id for placing orders
        contract_id = getattr(state, "contract_id", "")
        if not contract_id:
            self.logger.error(
                f"[BRACKET-REASONING] Cannot submit brackets for {strategy_id}: "
                f"no contract_id available. Need to look up contract ID for {state.symbol}."
            )
            result["errors"].append("No contract_id for symbol")
            return result

        # Calculate bracket prices from ACTUAL fill price
        # Round to tick size for proper exchange acceptance
        try:
            from custom_portfolio.data.futures_metadata import round_to_tick
        except ImportError:

            def round_to_tick(price, symbol):
                return round(price, 2)  # Fallback

        if entry_side.lower() == "buy":
            # Long position: TP above entry, SL below entry
            tp_price = round_to_tick(fill_price + entry_atr * pt_mult, state.symbol) if pt_mult > 0 else None
            sl_price = round_to_tick(fill_price - entry_atr * sl_mult, state.symbol) if sl_mult > 0 else None
            exit_side = 1  # Sell to close long
        else:
            # Short position: TP below entry, SL above entry
            tp_price = round_to_tick(fill_price - entry_atr * pt_mult, state.symbol) if pt_mult > 0 else None
            sl_price = round_to_tick(fill_price + entry_atr * sl_mult, state.symbol) if sl_mult > 0 else None
            exit_side = 0  # Buy to close short

        self.logger.info(
            f"[BRACKET-REASONING] Calculating brackets for {strategy_id} from ACTUAL fill price:\n"
            f"  Fill price: {fill_price}\n"
            f"  Entry ATR: {entry_atr:.4f} (stored at signal time)\n"
            f"  TP: {tp_price} ({entry_side} position, {pt_mult}x ATR above/below)\n"
            f"  SL: {sl_price} ({entry_side} position, {sl_mult}x ATR below/above)"
        )

        # Store calculated prices in state for visibility/tracking
        state.take_profit_price = tp_price
        state.stop_loss_price = sl_price

        # Generate unique tags for TP and SL orders
        if self.order_registry:
            from tools.order_registry import OrderPurpose

            tp_tag = self.order_registry.generate_unique_tag(OrderPurpose.TAKE_PROFIT, strategy_id)
            sl_tag = self.order_registry.generate_unique_tag(OrderPurpose.STOP_LOSS, strategy_id)
        else:
            import time as time_mod
            import uuid

            timestamp = int(time_mod.time() * 1000)
            tp_tag = f"TP_{strategy_id}_{timestamp}_{uuid.uuid4().hex[:6].upper()}"
            sl_tag = f"SL_{strategy_id}_{timestamp}_{uuid.uuid4().hex[:6].upper()}"

        # Submit TP order (limit order)
        if tp_price is not None and pt_mult > 0:
            try:
                self.logger.info(
                    f"[BRACKET-REASONING] Submitting TP limit order for {strategy_id}: "
                    f"SELL {qty} @ {tp_price} (tag={tp_tag})"
                )
                tp_resp = self.client.api.order_place(
                    account_id=self.account_id,
                    contract_id=contract_id,
                    type=1,  # Limit order
                    side=exit_side,
                    size=abs(int(qty)),
                    limit_price=tp_price,
                    custom_tag=tp_tag,
                )

                if tp_resp.get("success"):
                    tp_order_id = tp_resp.get("orderId")
                    result["tp_order_id"] = tp_order_id
                    self.register_our_order(tp_order_id)
                    self.logger.info(
                        f"[BRACKET-REASONING] TP order placed successfully! "
                        f"order_id={tp_order_id}, price={tp_price}"
                    )

                    # Register with order_registry if available
                    if self.order_registry:
                        from tools.order_registry import OrderIntent, OrderPurpose

                        intent = OrderIntent(
                            strategy_id=strategy_id,
                            symbol=state.symbol,
                            side="SELL" if exit_side == 1 else "BUY",
                            qty=abs(int(qty)),
                            purpose=OrderPurpose.TAKE_PROFIT,
                            is_close=True,
                        )
                        self.order_registry.register_submission(tp_tag, tp_order_id, intent)
                else:
                    error_code = tp_resp.get("errorCode")
                    error_msg = f"TP order failed: errorCode={error_code}"
                    self.logger.error(
                        f"[BRACKET-REASONING] {error_msg}. Price was {tp_price}. "
                        f"The position at {fill_price} is now exposed without a profit target!"
                    )
                    result["errors"].append(error_msg)

            except Exception as e:
                error_msg = f"TP order exception: {e}"
                self.logger.error(f"[BRACKET-REASONING] {error_msg}")
                result["errors"].append(error_msg)

        # Submit SL order (stop order)
        if sl_price is not None and sl_mult > 0:
            try:
                self.logger.info(
                    f"[BRACKET-REASONING] Submitting SL stop order for {strategy_id}: "
                    f"SELL {qty} @ {sl_price} (tag={sl_tag})"
                )
                sl_resp = self.client.api.order_place(
                    account_id=self.account_id,
                    contract_id=contract_id,
                    type=4,  # Stop order
                    side=exit_side,
                    size=abs(int(qty)),
                    stop_price=sl_price,
                    custom_tag=sl_tag,
                )

                if sl_resp.get("success"):
                    sl_order_id = sl_resp.get("orderId")
                    result["sl_order_id"] = sl_order_id
                    self.register_our_order(sl_order_id)
                    self.logger.info(
                        f"[BRACKET-REASONING] SL order placed successfully! "
                        f"order_id={sl_order_id}, price={sl_price}"
                    )

                    # Register with order_registry if available
                    if self.order_registry:
                        from tools.order_registry import OrderIntent, OrderPurpose

                        intent = OrderIntent(
                            strategy_id=strategy_id,
                            symbol=state.symbol,
                            side="SELL" if exit_side == 1 else "BUY",
                            qty=abs(int(qty)),
                            purpose=OrderPurpose.STOP_LOSS,
                            is_close=True,
                        )
                        self.order_registry.register_submission(sl_tag, sl_order_id, intent)
                else:
                    error_code = sl_resp.get("errorCode")
                    error_msg = f"SL order failed: errorCode={error_code}"
                    self.logger.error(
                        f"[BRACKET-REASONING] {error_msg}. Price was {sl_price}. "
                        f"The position at {fill_price} is now UNPROTECTED - no stop loss!"
                    )
                    result["errors"].append(error_msg)

            except Exception as e:
                error_msg = f"SL order exception: {e}"
                self.logger.error(f"[BRACKET-REASONING] {error_msg}")
                result["errors"].append(error_msg)

        # Register bracket pair for orphan cleanup
        if result["tp_order_id"] or result["sl_order_id"]:
            base_tag = f"ENT_{strategy_id}"
            self.register_bracket(
                base_tag,
                sl_order_id=result["sl_order_id"],
                tp_order_id=result["tp_order_id"],
                symbol=state.symbol,
            )
            self.logger.info(
                f"[BRACKET-REASONING] Bracket pair registered for {strategy_id}: "
                f"TP={result['tp_order_id']}, SL={result['sl_order_id']}"
            )

        # Mark brackets as submitted to prevent duplicates
        state.brackets_submitted = True
        state.brackets_submitted_at = time.time()  # Track submission time for in-flight check

        if result["errors"]:
            self.logger.warning(
                f"[BRACKET-REASONING] Bracket submission for {strategy_id} completed with errors: "
                f"{result['errors']}. The position may be partially or fully unprotected."
            )
        else:
            self.logger.info(
                f"[BRACKET-REASONING] Brackets successfully submitted for {strategy_id}! "
                f"Position at {fill_price} is now protected with TP={tp_price}, SL={sl_price}."
            )

        # Check for immediate fill scenario (very fast markets)
        # If TP or SL already filled, we need to recognize we're flat
        self._check_immediate_bracket_fill(state, result["tp_order_id"], result["sl_order_id"])

        return result

    def _check_immediate_bracket_fill(self, state, tp_order_id: Optional[int], sl_order_id: Optional[int]) -> bool:
        """
        Check if TP or SL filled immediately after submission.

        In very fast markets, a bracket order might fill within milliseconds of
        submission. This method re-queries the order status to detect immediate
        fills and update our virtual position accordingly.

        Args:
            state: EnhancedStrategyState
            tp_order_id: Take profit order ID (if submitted)
            sl_order_id: Stop loss order ID (if submitted)

        Returns:
            True if immediate fill detected and handled, False otherwise
        """
        if not tp_order_id and not sl_order_id:
            return False

        strategy_id = state.strategy_id

        # Brief delay to let exchange process, then check status
        import time as time_mod

        time_mod.sleep(0.3)  # 300ms for exchange to process

        try:
            # Query recent orders to check fill status
            start_date = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            orders_resp = self.client.api.order_search(self.account_id, start_date)

            if not orders_resp.get("success"):
                return False

            orders = orders_resp.get("orders", [])

            # Check if TP or SL already filled
            for order in orders:
                order_id = order.get("id")
                status = order.get("status")

                if status != self.STATUS_FILLED:
                    continue

                # TP filled immediately
                if order_id == tp_order_id:
                    self.logger.warning(
                        f"[BRACKET-REASONING] IMMEDIATE TP FILL detected for {strategy_id}! "
                        f"The TP order {tp_order_id} filled right after submission. "
                        f"Market moved very fast - we're now flat. Cancelling SL."
                    )
                    # Cancel the orphan SL
                    if sl_order_id:
                        self._cancel_order(sl_order_id, "SL", f"{strategy_id}_immediate")
                    # Update virtual position to flat
                    self._flatten_virtual_position(state, "immediate_tp_fill")
                    return True

                # SL filled immediately
                if order_id == sl_order_id:
                    self.logger.warning(
                        f"[BRACKET-REASONING] IMMEDIATE SL FILL detected for {strategy_id}! "
                        f"The SL order {sl_order_id} filled right after submission. "
                        f"Market moved against us very fast - we're now flat. Cancelling TP."
                    )
                    # Cancel the orphan TP
                    if tp_order_id:
                        self._cancel_order(tp_order_id, "TP", f"{strategy_id}_immediate")
                    # Update virtual position to flat
                    self._flatten_virtual_position(state, "immediate_sl_fill")
                    return True

            return False

        except Exception as e:
            self.logger.error(f"[BRACKET] Error checking immediate fill for {strategy_id}: {e}")
            return False

    def _flatten_virtual_position(self, state, reason: str) -> None:
        """
        Flatten a strategy's virtual position after immediate bracket fill.

        Args:
            state: EnhancedStrategyState to flatten
            reason: Reason for flattening (for logging)
        """
        strategy_id = state.strategy_id
        pos = state.tracker.get_position(state.symbol)

        if pos is None or abs(pos.quantity) < 1e-9:
            self.logger.debug(f"[BRACKET] {strategy_id} already flat, nothing to do")
            return

        old_qty = pos.quantity
        state.tracker.force_flat(state.symbol)

        # Clear all entry tracking
        state.entry_price = None
        state.take_profit_price = None
        state.stop_loss_price = None
        state.entry_time = None
        state.entry_iteration = None
        state.entry_side = None
        state.bars_in_trade = 0
        state.entry_atr = None
        state.entry_pt_mult = None
        state.entry_sl_mult = None
        state.brackets_submitted = False

        self.logger.info(
            f"[BRACKET-REASONING] Virtual position flattened for {strategy_id}. "
            f"Was holding {old_qty} contracts, now flat. Reason: {reason}. "
            f"Cleared all entry tracking - ready for next signal."
        )

    def _ensure_brackets(self, orders: list, current_prices: Dict[str, float], calendar=None) -> Dict:
        """Check all positions have brackets; recreate or close if missing."""
        result = {"recreated": [], "closed": [], "errors": []}

        if not self.strategy_states:
            return result

        # Build set of open bracket tags
        open_brackets = {o.get("customTag") or "" for o in orders if o.get("status") == self.STATUS_OPEN}

        # CRITICAL: Also track recently FILLED brackets to prevent recreation race condition
        # If a TP/SL just filled but trade data isn't available yet, don't recreate
        filled_brackets = {o.get("customTag") or "" for o in orders if o.get("status") == self.STATUS_FILLED}

        # Use list() to prevent dict modification during iteration
        for strategy_id, state in list(self.strategy_states.items()):
            try:
                pos = state.tracker.get_position(state.symbol)
                if pos is None or abs(pos.quantity) < 1e-9:
                    continue

                current_price = current_prices.get(state.symbol)
                if current_price is None:
                    self.logger.warning(f"[BRACKET] No price for {state.symbol}")
                    continue

                # Calendar gate - skip if orders blocked
                if calendar:
                    allowed_sessions = getattr(state, "allowed_sessions", None) or ["24/7"]
                    status = calendar.get_status(
                        state.symbol,
                        datetime.now(timezone.utc),
                        allowed_sessions=allowed_sessions,
                    )
                    if not status.can_enter_orders:
                        self.logger.info(
                            f"[BRACKET-REASONING] I want to recreate brackets for {strategy_id} but the calendar "
                            f"says I can't place orders right now ({status.session_reason}). "
                            f"The position is exposed until the session opens. I'll try again then."
                        )
                        continue

                # Check for existing brackets (multiple tag formats)
                # Format 1: BRK_TP_ENT_{strategy_id}_... (current entry brackets)
                # Format 2: BRK_TP_{strategy_id}_... (legacy)
                # Format 3: TP_{strategy_id}_{timestamp}_{uuid} (new standalone)
                tp_exists = any(
                    t.startswith(f"BRK_TP_ENT_{strategy_id}")
                    or t.startswith(f"BRK_TP_{strategy_id}")
                    or (t.startswith("TP_") and self._extract_strategy_id_from_tag(t) == strategy_id)
                    for t in open_brackets
                )
                sl_exists = any(
                    t.startswith(f"BRK_STOP_ENT_{strategy_id}")
                    or t.startswith(f"BRK_STOP_{strategy_id}")
                    or (t.startswith("SL_") and self._extract_strategy_id_from_tag(t) == strategy_id)
                    for t in open_brackets
                )

                # Check if TP/SL recently FILLED (prevents recreation race condition)
                # If filled but not yet processed, don't recreate - let fill processing handle it
                tp_filled = any(
                    t.startswith(f"BRK_TP_ENT_{strategy_id}")
                    or t.startswith(f"BRK_TP_{strategy_id}")
                    or (t.startswith("TP_") and self._extract_strategy_id_from_tag(t) == strategy_id)
                    for t in filled_brackets
                )
                sl_filled = any(
                    t.startswith(f"BRK_STOP_ENT_{strategy_id}")
                    or t.startswith(f"BRK_STOP_{strategy_id}")
                    or (t.startswith("SL_") and self._extract_strategy_id_from_tag(t) == strategy_id)
                    for t in filled_brackets
                )

                # If EITHER bracket just filled, skip ALL checks for this strategy
                # The fill processing will handle updating the virtual position
                if tp_filled or sl_filled:
                    # Check if position is already flat - if so, fill was processed, just skip silently
                    if abs(pos.quantity) < 1e-9:
                        continue  # Already flat, fill was processed, nothing to do

                    # Position not yet flat - log once that we're waiting
                    which_filled = "TP" if tp_filled else "SL"
                    fill_key = f"{strategy_id}_{which_filled}_filled"
                    if fill_key not in self._processed_fills:
                        self.logger.info(
                            f"[BRACKET-REASONING] The {which_filled} for {strategy_id} just FILLED. "
                            f"Skipping bracket checks - fill processing will sync virtual position shortly."
                        )
                        self._processed_fills.add(fill_key)
                    continue  # Skip to next strategy

                position_closed = False

                # IN-FLIGHT CHECK: Skip recreation if brackets were just submitted
                # The order_search API takes 3-5 seconds to propagate new orders
                brackets_age = time.time() - getattr(state, "brackets_submitted_at", 0)
                if getattr(state, "brackets_submitted", False) and brackets_age < self.ORDER_INFLIGHT_WINDOW_SECONDS:
                    self.logger.debug(
                        f"[BRACKET] Skipping recreation check for {strategy_id}: "
                        f"brackets submitted {brackets_age:.1f}s ago (window={self.ORDER_INFLIGHT_WINDOW_SECONDS}s)"
                    )
                    continue

                # Handle missing TP (skip if TP just filled - let fill processing handle it)
                if not tp_exists and not tp_filled and state.take_profit_price:
                    if self._price_past_target(
                        current_price, state.take_profit_price, pos.quantity, "TP", state.symbol
                    ):
                        if not position_closed:
                            self.logger.critical(
                                f"[BRACKET-REASONING] CRITICAL: Price has blown past our TP for {strategy_id}! "
                                f"Current price is {current_price} but TP was {state.take_profit_price}. "
                                f"We missed the exit with {pos.quantity} contracts. Triggering emergency close NOW."
                            )
                            self._emergency_close(state, "TP_BREACHED", calendar)
                            result["closed"].append(strategy_id)
                            position_closed = True
                    else:
                        self.logger.info(
                            f"[BRACKET-REASONING] TP order is missing for {strategy_id}. "
                            f"Price is {current_price}, target is {state.take_profit_price}. "
                            f"Let me try to recreate it before we miss the exit."
                        )
                        if self._try_recreate_bracket(state, "TP", pos.quantity, calendar):
                            result["recreated"].append(f"{strategy_id}_TP")

                # Handle missing SL (only if not already closed, skip if SL just filled)
                if not position_closed and not sl_exists and not sl_filled and state.stop_loss_price:
                    if self._price_past_target(current_price, state.stop_loss_price, pos.quantity, "SL", state.symbol):
                        self.logger.critical(
                            f"[BRACKET-REASONING] CRITICAL: Price has blown past our SL for {strategy_id}! "
                            f"Current price is {current_price} but SL was {state.stop_loss_price}. "
                            f"We're taking more loss than planned with {pos.quantity} contracts. "
                            f"Triggering emergency close NOW."
                        )
                        self._emergency_close(state, "SL_BREACHED", calendar)
                        result["closed"].append(strategy_id)
                    else:
                        self.logger.info(
                            f"[BRACKET-REASONING] SL order is missing for {strategy_id}. "
                            f"Price is {current_price}, stop is {state.stop_loss_price}. "
                            f"I need to recreate it ASAP to protect the position."
                        )
                        if self._try_recreate_bracket(state, "SL", pos.quantity, calendar):
                            result["recreated"].append(f"{strategy_id}_SL")

            except Exception as e:
                self.logger.error(f"[BRACKET] Error checking {strategy_id}: {e}")
                result["errors"].append(str(e))

        return result

    def _cleanup_orphans(self, orders: list) -> Dict:
        """Clean up orphaned brackets from the fetched orders."""
        result = {"cancelled": []}

        # Group bracket children by strategy_id
        bracket_groups: Dict[str, Dict[str, dict]] = {}

        for order in orders:
            tag = order.get("customTag") or ""
            order_id = order.get("id")
            status = order.get("status")

            # Handle old format: BRK_TP_*, BRK_STOP_*
            if tag.startswith("BRK_TP_"):
                strategy_id = tag[7:]  # Remove "BRK_TP_" prefix
                if strategy_id not in bracket_groups:
                    bracket_groups[strategy_id] = {}
                bracket_groups[strategy_id]["tp"] = {"id": order_id, "status": status, "tag": tag}

            elif tag.startswith("BRK_STOP_"):
                strategy_id = tag[9:]  # Remove "BRK_STOP_" prefix
                if strategy_id not in bracket_groups:
                    bracket_groups[strategy_id] = {}
                bracket_groups[strategy_id]["sl"] = {"id": order_id, "status": status, "tag": tag}

            # Handle new format: TP_*, SL_*
            elif tag.startswith("TP_"):
                strategy_id = self._extract_strategy_id_from_tag(tag)
                if strategy_id:
                    if strategy_id not in bracket_groups:
                        bracket_groups[strategy_id] = {}
                    bracket_groups[strategy_id]["tp"] = {"id": order_id, "status": status, "tag": tag}

            elif tag.startswith("SL_"):
                strategy_id = self._extract_strategy_id_from_tag(tag)
                if strategy_id:
                    if strategy_id not in bracket_groups:
                        bracket_groups[strategy_id] = {}
                    bracket_groups[strategy_id]["sl"] = {"id": order_id, "status": status, "tag": tag}

        # Check each bracket group for orphans
        self.logger.debug(
            f"[BRACKET-REASONING] I'm scanning {len(bracket_groups)} bracket groups for orphans. "
            f"Since ProjectX's linkedOrderId doesn't create true OCO behavior, I have to manually "
            f"cancel the other leg when one side fills."
        )
        for base_tag, group in bracket_groups.items():
            tp = group.get("tp")
            sl = group.get("sl")

            if not tp or not sl:
                continue

            tp_status = tp.get("status")
            sl_status = sl.get("status")

            # TP filled and SL still open → cancel SL
            if tp_status == self.STATUS_FILLED and sl_status == self.STATUS_OPEN:
                self.logger.info(
                    f"[BRACKET-REASONING] Found an orphan for {base_tag}! The TP filled but the SL "
                    f"(order {sl['id']}) is still sitting there. I need to cancel it or we might "
                    f"get an unwanted exit later."
                )
                if self._cancel_order(sl["id"], "SL", base_tag):
                    result["cancelled"].append(
                        {"order_id": sl["id"], "type": "SL", "reason": "TP_filled", "base_tag": base_tag}
                    )

            # SL filled and TP still open → cancel TP
            elif sl_status == self.STATUS_FILLED and tp_status == self.STATUS_OPEN:
                self.logger.info(
                    f"[BRACKET-REASONING] Found an orphan for {base_tag}! The SL filled but the TP "
                    f"(order {tp['id']}) is still sitting there. I need to cancel it or we might "
                    f"get an unwanted exit later."
                )
                if self._cancel_order(tp["id"], "TP", base_tag):
                    result["cancelled"].append(
                        {"order_id": tp["id"], "type": "TP", "reason": "SL_filled", "base_tag": base_tag}
                    )

        if result["cancelled"]:
            self.logger.info(
                f"[BRACKET-REASONING] Orphan cleanup done - I cancelled {len(result['cancelled'])} orphan orders. "
                f"These were leftover brackets from filled legs that could have caused double-exits."
            )
        return result

    def _price_past_target(self, current: float, target: float, pos_qty: float, order_type: str, symbol: str) -> bool:
        """Check if price has breached target with symbol-specific tick buffer."""
        try:
            from custom_portfolio.data.futures_metadata import get_tick_size

            tick_buffer = get_tick_size(symbol) or 0.25
        except ImportError:
            tick_buffer = 0.25

        if pos_qty > 0:  # Long
            if order_type == "TP":
                return current > target + tick_buffer
            else:  # SL
                return current < target - tick_buffer
        else:  # Short
            if order_type == "TP":
                return current < target - tick_buffer
            else:  # SL
                return current > target + tick_buffer

    def _try_recreate_bracket(self, state, order_type: str, pos_qty: float, calendar=None) -> bool:
        """Try to recreate a missing bracket with debounce, failure tracking, and calendar check."""

        strategy_id = state.strategy_id

        # Generate tag using registry (timestamp + UUID) or fallback to old format
        if self.order_registry:
            from tools.order_registry import OrderPurpose

            purpose = OrderPurpose.TAKE_PROFIT if order_type == "TP" else OrderPurpose.STOP_LOSS
            tag = self.order_registry.generate_unique_tag(purpose, strategy_id)
        else:
            tag = f"BRK_{order_type}_{strategy_id}"

        # Calendar gate
        if calendar:
            allowed_sessions = getattr(state, "allowed_sessions", None) or ["24/7"]
            status = calendar.get_status(
                state.symbol,
                datetime.now(timezone.utc),
                allowed_sessions=allowed_sessions,
            )
            if not status.platform_open or not status.can_enter_orders:
                self.logger.info(
                    f"[BRACKET-REASONING] I want to recreate {tag} but the calendar blocks it: "
                    f"{status.session_reason}. This isn't a failure - I'll try again when the session opens."
                )
                return False  # Don't count as failure

        # Debounce check (UTC)
        last_attempt = self._recent_placements.get(tag)
        if last_attempt:
            elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds()
            if elapsed < self.PLACEMENT_COOLDOWN_SECONDS:
                self.logger.debug(
                    f"[BRACKET-REASONING] Skipping {tag} recreation - I just tried {elapsed:.1f}s ago. "
                    f"Cooldown is {self.PLACEMENT_COOLDOWN_SECONDS}s to avoid spamming the exchange."
                )
                return False

        # Failure limit check
        failures = self._failed_recreations.get(tag, 0)
        if failures >= self.MAX_RECREATION_FAILURES:
            self.logger.critical(
                f"[BRACKET-REASONING] I've failed to recreate {tag} {failures} times now. "
                f"Something is systematically wrong (bad price? API issue?). "
                f"I'm giving up and triggering an emergency close to protect capital."
            )
            self._emergency_close(state, f"RECREATION_FAILED_{order_type}", calendar)
            return False

        # Record attempt time
        self._recent_placements[tag] = datetime.now(timezone.utc)

        # Build order params
        price = state.take_profit_price if order_type == "TP" else state.stop_loss_price
        if price is None:
            return False

        side = 1 if pos_qty > 0 else 0  # Sell for long, Buy for short
        order_type_code = 1 if order_type == "TP" else 4  # Limit vs Stop

        # Get contract_id from state
        contract_id = getattr(state, "contract_id", "")
        if not contract_id:
            self.logger.error(f"[BRACKET] No contract_id for {strategy_id}")
            return False

        params = {
            "account_id": self.account_id,
            "contract_id": contract_id,
            "type": order_type_code,
            "side": side,
            "size": abs(int(pos_qty)),
            "custom_tag": tag,
        }
        if order_type == "TP":
            params["limit_price"] = price
        else:
            params["stop_price"] = price

        try:
            resp = self.client.api.order_place(**params)
            if resp.get("success"):
                # Register this order ID so we track its fill
                order_id = resp.get("orderId")
                if order_id:
                    self.register_our_order(order_id)
                    # Register with order_registry if available
                    if self.order_registry:
                        from tools.order_registry import OrderIntent, OrderPurpose

                        purpose = OrderPurpose.TAKE_PROFIT if order_type == "TP" else OrderPurpose.STOP_LOSS
                        intent = OrderIntent(
                            strategy_id=strategy_id,
                            symbol=state.symbol,
                            side="SELL" if side == 1 else "BUY",
                            qty=abs(int(pos_qty)),
                            purpose=purpose,
                            is_close=True,  # TP/SL are exit orders
                        )
                        self.order_registry.register_submission(tag, order_id, intent)
                self._failed_recreations[tag] = 0
                self.stats["brackets_recreated"] += 1
                self.logger.info(
                    f"[BRACKET-REASONING] Successfully recreated {order_type} for {strategy_id}! "
                    f"order_id={order_id}, price={price}. The position is protected again."
                )
                return True
            else:
                # Check errorCode for permanent vs transient
                error_code = resp.get("errorCode")
                if error_code == 2:  # Invalid price - permanent
                    self._failed_recreations[tag] = self.MAX_RECREATION_FAILURES
                    self.logger.critical(
                        f"[BRACKET-REASONING] {order_type} recreation for {strategy_id} permanently failed! "
                        f"The exchange rejected price {price} as invalid (errorCode=2). "
                        f"I'll trigger an emergency close on the next cycle."
                    )
                else:
                    self._failed_recreations[tag] = failures + 1
                    self.logger.warning(
                        f"[BRACKET-REASONING] {order_type} recreation failed for {strategy_id} (attempt "
                        f"{failures + 1}/{self.MAX_RECREATION_FAILURES}). Price was {price}. I'll retry."
                    )
                return False
        except Exception as e:
            self._failed_recreations[tag] = failures + 1
            self.logger.error(f"[BRACKET] Error {tag}: {e}")
            return False

    def _emergency_close(self, state, reason: str, calendar=None):
        """Emergency close: cancel brackets, place market order (or zero out during maintenance)."""
        import uuid as uuid_mod

        strategy_id = state.strategy_id

        self.logger.info(
            f"[BRACKET-REASONING] EMERGENCY CLOSE for {strategy_id}! Reason: {reason}. "
            f"Something exceptional happened and I need to flatten this position immediately to limit damage."
        )

        # Check if platform allows orders
        if calendar:
            allowed_sessions = getattr(state, "allowed_sessions", None) or ["24/7"]
            status = calendar.get_status(
                state.symbol,
                datetime.now(timezone.utc),
                allowed_sessions=allowed_sessions,
            )
            self.logger.debug(
                f"[BRACKET-REASONING] Checking if I can place orders for {strategy_id}: "
                f"platform_open={status.platform_open} ({getattr(status, 'platform_reason', 'N/A')}). "
                f"I need to verify this before attempting the emergency close."
            )
            if not status.platform_open:
                # During maintenance - zero out virtual position (system auto-flattens)
                pos = state.tracker.get_position(state.symbol)
                if pos and abs(pos.quantity) > 0:
                    old_qty = pos.quantity
                    state.tracker.force_flat(state.symbol)
                    state.entry_price = None
                    state.take_profit_price = None
                    state.stop_loss_price = None
                    self.logger.critical(
                        f"[BRACKET-REASONING] Platform is in maintenance! I can't place orders right now. "
                        f"TopStepX auto-flattens all positions during maintenance (15:10-17:00 CT), "
                        f"so I'm zeroing our virtual position for {strategy_id} to match. "
                        f"Was holding {old_qty} contracts. Reason: {reason}."
                    )
                else:
                    self.logger.info(
                        f"[BRACKET-REASONING] Platform is in maintenance but {strategy_id} is already flat. "
                        f"Nothing for me to do."
                    )
                self.stats["emergency_closes"] += 1
                return

        # Cancel remaining brackets FIRST (race-safe close pattern)
        self.logger.info(
            f"[BRACKET-REASONING] First, I need to cancel any existing brackets for {strategy_id} "
            f"BEFORE placing the close order. This prevents a double-exit if a bracket fills "
            f"while my close order is in flight."
        )
        self.cancel_brackets_for_strategy(strategy_id, wait_seconds=0.2)

        pos = state.tracker.get_position(state.symbol)
        if pos is None or abs(pos.quantity) < 1e-9:
            self.logger.info(
                f"[BRACKET-REASONING] Wait, {strategy_id} is already flat (qty={pos.quantity if pos else 0}). "
                f"No position to close - I don't need to do anything."
            )
            return

        self.logger.debug(
            f"[BRACKET-REASONING] {strategy_id} still has a position: {pos.quantity} {state.symbol}. "
            f"I need to close this."
        )

        # Check with registry if we can close (if registry available)
        if self.order_registry:
            from tools.order_registry import can_close_position

            can_close = can_close_position(strategy_id, state.symbol, self.order_registry, state.tracker)
            if not can_close:
                self.logger.info(
                    f"[BRACKET-REASONING] The registry says I can't close {strategy_id} right now. "
                    f"There's probably a close order already in-flight or recently submitted. "
                    f"I'll skip this to avoid a duplicate close that could flip our position."
                )
                return

        # Generate unique tag using registry (timestamp + UUID) or fallback to old format
        if self.order_registry:
            from tools.order_registry import OrderPurpose

            close_tag = self.order_registry.generate_unique_tag(OrderPurpose.CLOSE, strategy_id)
        else:
            close_tag = f"BRK_CLOSE_{strategy_id}_{uuid_mod.uuid4().hex[:6]}"

        side = 1 if pos.quantity > 0 else 0
        side_str = "SELL" if side == 1 else "BUY"

        self.logger.info(
            f"[BRACKET-REASONING] I'm placing a market {side_str} for {abs(int(pos.quantity))} contracts "
            f"to close the {pos.quantity:+.0f} position on {strategy_id}. tag={close_tag}. "
            f"Using market order to ensure immediate fill regardless of price."
        )

        contract_id = getattr(state, "contract_id", "")
        if not contract_id:
            self.logger.error(
                f"[BRACKET-REASONING] Problem! I don't have a contract_id for {strategy_id}. "
                f"I can't place an order without knowing which contract to trade."
            )
            return

        try:
            resp = self.client.api.order_place(
                account_id=self.account_id,
                contract_id=contract_id,
                type=2,  # Market
                side=side,
                size=abs(int(pos.quantity)),
                custom_tag=close_tag,
            )
            if resp.get("success"):
                # Register this order ID so we track its fill
                order_id = resp.get("orderId")
                if order_id:
                    self.register_our_order(order_id)
                    # Register with order_registry if available
                    if self.order_registry:
                        from tools.order_registry import OrderIntent, OrderPurpose

                        intent = OrderIntent(
                            strategy_id=strategy_id,
                            symbol=state.symbol,
                            side="SELL" if side == 1 else "BUY",
                            qty=abs(int(pos.quantity)),
                            purpose=OrderPurpose.CLOSE,
                            is_close=True,
                        )
                        self.order_registry.register_submission(close_tag, order_id, intent)
                self.stats["emergency_closes"] += 1
                self.logger.critical(
                    f"[BRACKET-REASONING] Emergency close order placed for {strategy_id}! "
                    f"order_id={order_id}, {side_str} {abs(int(pos.quantity))} contracts. "
                    f"Once this market order fills, we'll be flat."
                )
            else:
                self.logger.critical(
                    f"[BRACKET-REASONING] Emergency close FAILED for {strategy_id}! "
                    f"The broker rejected the order: {resp}. "
                    f"The position is still open - this is dangerous!"
                )
        except Exception as e:
            self.logger.critical(
                f"[BRACKET-REASONING] Exception during emergency close for {strategy_id}: {e}. "
                f"The position is still open - this is dangerous!"
            )

    def _cancel_all_orders_for_contract(self, contract_id: str) -> int:
        """
        Cancel all open orders for a specific contract.

        Used when closing untracked positions to prevent bracket fills
        from reopening the position after we flatten it.

        Args:
            contract_id: The full contract ID (e.g., "CON.F.US.MGC.Z25")

        Returns:
            Number of orders successfully cancelled
        """
        start_date = (datetime.now() - timedelta(hours=24)).isoformat()
        try:
            orders_resp = self.client.api.order_search(self.account_id, start_date)
            if not orders_resp.get("success"):
                self.logger.warning(f"[POSITION-REPAIR] Failed to query orders for cancellation: {orders_resp}")
                return 0

            cancelled = 0
            for order in orders_resp.get("orders", []):
                if order.get("contractId") == contract_id and order.get("status") == self.STATUS_OPEN:
                    order_id = order.get("id")
                    tag = order.get("customTag", "")[:20]
                    try:
                        cancel_resp = self.client.api.order_cancel(self.account_id, order_id)
                        if cancel_resp.get("success") or cancel_resp.get("errorCode") == 5:
                            # errorCode 5 = order doesn't exist (already filled/cancelled)
                            cancelled += 1
                            self.logger.info(f"[POSITION-REPAIR] Cancelled order {order_id} ({tag}) for {contract_id}")
                        else:
                            self.logger.warning(f"[POSITION-REPAIR] Failed to cancel order {order_id}: {cancel_resp}")
                    except Exception as e:
                        self.logger.warning(f"[POSITION-REPAIR] Exception cancelling order {order_id}: {e}")
            return cancelled
        except Exception as e:
            self.logger.error(f"[POSITION-REPAIR] Error querying orders for cancellation: {e}")
            return 0

    def _close_untracked_exchange_position(self, symbol: str, exchange_qty: float, contract_id: str) -> bool:
        """
        Close an untracked exchange position (no local virtual positions).

        This handles the case where exchange has exposure but local is flat.
        We cancel all open orders first, then place a market order to flatten.

        Args:
            symbol: Trading symbol (e.g., "MGC")
            exchange_qty: Signed quantity on exchange (+1 = long, -1 = short)
            contract_id: Full contract ID for order placement

        Returns:
            Tuple of (success: bool, market_closed: bool)
            - success: True if close order was successfully placed
            - market_closed: True if failure was due to market being closed
        """
        import uuid as uuid_mod

        # 1. Cancel all open orders for this contract FIRST
        # This prevents bracket fills from reopening the position
        cancelled_count = self._cancel_all_orders_for_contract(contract_id)
        if cancelled_count > 0:
            self.logger.info(
                f"[POSITION-REPAIR] Cancelled {cancelled_count} open orders for {symbol} "
                f"before closing untracked position"
            )

        # 2. Place market order to close the position
        side = 1 if exchange_qty > 0 else 0  # SELL if long, BUY if short
        side_str = "SELL" if side == 1 else "BUY"
        close_tag = f"DESYNC_CLOSE_{symbol}_{uuid_mod.uuid4().hex[:6]}"

        self.logger.critical(
            f"[POSITION-REPAIR] Auto-closing untracked {symbol} position: "
            f"{side_str} {abs(int(exchange_qty))} contracts to flatten exchange. tag={close_tag}"
        )

        try:
            resp = self.client.api.order_place(
                account_id=self.account_id,
                contract_id=contract_id,
                type=2,  # Market order
                side=side,
                size=abs(int(exchange_qty)),
                custom_tag=close_tag,
            )
            if resp.get("success"):
                order_id = resp.get("orderId")
                if order_id:
                    self.register_our_order(order_id)
                self.logger.critical(
                    f"[POSITION-REPAIR] Successfully closed untracked {symbol} position! "
                    f"order_id={order_id}, {side_str} {abs(int(exchange_qty))} contracts"
                )
                return (True, False)
            else:
                error_msg = resp.get("errorMessage", "").lower()
                market_closed = "market" in error_msg and "closed" in error_msg
                self.logger.error(f"[POSITION-REPAIR] Failed to close untracked {symbol} position: {resp}")
                return (False, market_closed)
        except Exception as e:
            self.logger.error(f"[POSITION-REPAIR] Exception closing untracked {symbol} position: {e}")
            return (False, False)

    def _extract_strategy_id_from_tag(self, tag: str) -> Optional[str]:
        """Extract strategy ID from order tag (both old BRK_ and new format).

        Handles nested formats like BRK_TP_ENT_GC_1M_04_1764156176554_2249DE
        where the entry tag (ENT_GC_1M_04_...) is embedded in the bracket tag.
        """
        # Old format: BRK_ENTRY_STRATEGYID, BRK_TP_STRATEGYID, etc.
        # But now may have new format entry tag embedded: BRK_TP_ENT_STRATEGYID_TIMESTAMP_UUID
        old_prefixes = ["BRK_ENTRY_", "BRK_TP_", "BRK_STOP_", "BRK_CLOSE_"]
        for prefix in old_prefixes:
            if tag.startswith(prefix):
                remaining = tag[len(prefix) :]

                # Check if remaining starts with a new-format prefix (nested tag)
                # e.g., BRK_TP_ENT_GC_1M_04_... → remaining = ENT_GC_1M_04_...
                new_prefixes_inner = ["ENT_", "TP_", "SL_", "CLOSE_"]
                for new_prefix in new_prefixes_inner:
                    if remaining.startswith(new_prefix):
                        # Recursively extract from the nested tag
                        return self._extract_strategy_id_from_tag(remaining)

                # Old format without nested tag: BRK_TP_STRATEGYID
                if prefix == "BRK_CLOSE_" and "_" in remaining:
                    return remaining.rsplit("_", 1)[0]  # Strip UUID suffix
                return remaining

        # New format: {PURPOSE}_{STRATEGY_ID}_{UNIX_MS}_{UUID6}
        # e.g., ENT_ES_1M_08_1732556789123_A3F7C9
        new_prefixes = ["ENT_", "TP_", "SL_", "CLOSE_"]
        for prefix in new_prefixes:
            if tag.startswith(prefix):
                remaining = tag[len(prefix) :]
                # Strategy ID is everything except last two parts (timestamp and UUID)
                parts = remaining.split("_")
                if len(parts) >= 3:
                    # Last part is UUID (6 chars), second-to-last is timestamp (13 digits)
                    # Check if last part looks like UUID (6 hex chars) and second-to-last is numeric
                    if len(parts[-1]) == 6 and parts[-2].isdigit():
                        return "_".join(parts[:-2])
                # Fallback: return everything (might be simple format)
                return remaining

        return None

    def _session_transition_cleanup(self, calendar, result: Dict):
        """Handle maintenance window: cancel brackets and zero out virtual positions.

        During maintenance (15:10-17:00 CT), TopStepX auto-flattens all positions.
        We cannot place orders during this time. We must zero out our virtual
        positions to stay in sync - when maintenance ends, we WILL be flat.
        """
        # Track strategies we've already notified about session closure (avoid log spam)
        if not hasattr(self, "_session_cleanup_notified"):
            self._session_cleanup_notified: set = set()

        strategies_cleaned = 0
        for strategy_id, state in list(self.strategy_states.items()):
            # Get allowed_sessions from strategy state (defaults to New_York if not set)
            allowed_sessions = getattr(state, "allowed_sessions", None) or ["24/7"]
            status = calendar.get_status(
                state.symbol,
                datetime.now(timezone.utc),
                allowed_sessions=allowed_sessions,
            )

            # During maintenance window or must_be_flat
            if not status.platform_open or status.must_be_flat:
                # Get the appropriate reason for logging
                if not status.platform_open:
                    reason_detail = getattr(status, "platform_reason", "N/A")
                else:
                    reason_detail = getattr(status, "close_reason", "N/A")

                # Check if strategy has position or brackets to clean
                pos = state.tracker.get_position(state.symbol)
                has_position = pos and abs(pos.quantity) > 0
                # Extract strategy_id from base_tag (format: "ENT_{strategy_id}")
                has_brackets = any(
                    tag.replace("ENT_", "") == strategy_id for tag in self.brackets.keys() if self.brackets[tag].active
                )

                # Only log if we have something to clean AND haven't notified yet
                if (has_position or has_brackets) and strategy_id not in self._session_cleanup_notified:
                    self.logger.info(
                        f"[BRACKET-REASONING] Session/platform closing for {strategy_id}! "
                        f"Reason: {reason_detail}. has_position={has_position}, has_brackets={has_brackets}. "
                        f"I need to clean this up because we must be flat during closure."
                    )
                    self._session_cleanup_notified.add(strategy_id)

                # Cancel any remaining brackets
                if has_brackets:
                    self.cancel_brackets_for_strategy(strategy_id, wait_seconds=0)

                # ZERO OUT virtual position - system auto-flattens us
                if has_position:
                    old_qty = pos.quantity
                    old_entry = getattr(state, "entry_price", None)
                    # Force flatten the virtual position (no actual order - system handles it)
                    state.tracker.force_flat(state.symbol)
                    state.entry_price = None
                    state.take_profit_price = None
                    state.stop_loss_price = None
                    self._failed_recreations.pop(f"BRK_TP_{strategy_id}", None)
                    self._failed_recreations.pop(f"BRK_STOP_{strategy_id}", None)
                    strategies_cleaned += 1
                    self.logger.critical(
                        f"[BRACKET-REASONING] I just zeroed the virtual position for {strategy_id}. "
                        f"Was holding {old_qty} contracts. Reason: {reason_detail}. "
                        f"The system auto-flattens during closure so I'm syncing my tracker to match."
                    )
                    # Log to sync file
                    self._log_sync(
                        "SESSION_CLEANUP",
                        {
                            "strategy": strategy_id,
                            "symbol": state.symbol,
                            "old_qty": f"{old_qty:.0f}",
                            "entry_price_lost": old_entry or "N/A",
                            "reason": reason_detail,
                            "action": "Zeroed virtual to sync with platform auto-flatten",
                        },
                    )
            else:
                # Session is open - remove from notified set so we log again if it closes
                self._session_cleanup_notified.discard(strategy_id)

        if strategies_cleaned > 0:
            self.logger.info(
                f"[BRACKET-REASONING] Session transition cleanup complete. "
                f"I zeroed {strategies_cleaned} strategies to sync with the system's auto-flatten."
            )

            # Check for contract rollover at session end (when we've just flattened)
            rollover_result = self.check_and_record_contracts()
            if rollover_result.get("rollovers"):
                for rollover in rollover_result["rollovers"]:
                    self.logger.critical(
                        f"[CONTRACT-ROLLOVER] {rollover['symbol']} rolled: "
                        f"{rollover['old']} -> {rollover['new']}. "
                        f"Starting fresh next session with new contract."
                    )

    def _cleanup_tracking_dicts(self):
        """Cleanup stale entries from tracking dicts."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        self._recent_placements = {k: v for k, v in self._recent_placements.items() if v > cutoff}

        # Limit _processed_fills growth (keep last 1000)
        if len(self._processed_fills) > 1000:
            # Convert to list, sort, keep recent
            sorted_ids = sorted(self._processed_fills)
            self._processed_fills = set(sorted_ids[-500:])

        # Limit _processed_trades growth (keep last 2000)
        if len(self._processed_trades) > 2000:
            sorted_ids = sorted(self._processed_trades)
            self._processed_trades = set(sorted_ids[-1000:])

    def _clear_processed_fills_for_strategy(self, strategy_id: str) -> int:
        """
        Clear processed fills for a specific strategy to allow re-sync.

        This is called after a position repair to ensure the next poll cycle
        can re-evaluate fills for that strategy from scratch.

        Returns:
            Number of fill IDs cleared
        """
        cleared_count = 0

        # Method 1: Use order_registry if available (most accurate)
        if self.order_registry:
            try:
                # Get all orders for this strategy from registry
                strategy_order_ids = set()
                for _tag, record in self.order_registry.orders.items():
                    if record.strategy_id == strategy_id and record.order_id:
                        strategy_order_ids.add(record.order_id)

                # Remove from processed fills
                before_count = len(self._processed_fills)
                self._processed_fills -= strategy_order_ids
                cleared_count = before_count - len(self._processed_fills)

                if cleared_count > 0:
                    self.logger.debug(
                        f"[POSITION-REPAIR] Cleared {cleared_count} fills from registry for {strategy_id}"
                    )
            except Exception as e:
                self.logger.warning(f"[POSITION-REPAIR] Error clearing from registry: {e}")

        # Method 2: Also check brackets dict for order IDs (backup)
        try:
            bracket_order_ids = set()
            for tag, bracket in self.brackets.items():
                # Tags are like "ENT_<strategy_id>_..." - check if strategy matches
                if f"_{strategy_id}_" in tag or tag.endswith(f"_{strategy_id}"):
                    if bracket.sl_order_id:
                        bracket_order_ids.add(bracket.sl_order_id)
                    if bracket.tp_order_id:
                        bracket_order_ids.add(bracket.tp_order_id)

            # Remove from processed fills
            before_count = len(self._processed_fills)
            self._processed_fills -= bracket_order_ids
            additional_cleared = before_count - len(self._processed_fills)

            if additional_cleared > 0:
                cleared_count += additional_cleared
                self.logger.debug(
                    f"[POSITION-REPAIR] Cleared {additional_cleared} additional fills from brackets for {strategy_id}"
                )
        except Exception as e:
            self.logger.warning(f"[POSITION-REPAIR] Error clearing from brackets: {e}")

        # Method 3: Clear any string keys that contain the strategy_id
        # (these are the "fill_key" entries like "ES_1M_01_TP_filled")
        try:
            string_keys_to_remove = {k for k in self._processed_fills if isinstance(k, str) and strategy_id in k}
            self._processed_fills -= string_keys_to_remove
            if string_keys_to_remove:
                cleared_count += len(string_keys_to_remove)
                self.logger.debug(
                    f"[POSITION-REPAIR] Cleared {len(string_keys_to_remove)} string fill keys for {strategy_id}"
                )
        except Exception as e:
            self.logger.warning(f"[POSITION-REPAIR] Error clearing string keys: {e}")

        return cleared_count

    def check_position_sync(self) -> Dict[str, Any]:
        """
        Check synchronization between exchange positions and virtual positions.

        Compares actual positions from the exchange with the sum of all virtual
        positions from strategy_states. Reports any discrepancies.

        Returns:
            Dict with keys:
                - synced: bool - True if positions match
                - exchange_positions: Dict[symbol, qty] - actual positions
                - virtual_positions: Dict[symbol, qty] - sum of virtual positions
                - discrepancies: List[Dict] - list of mismatches
                - error: Optional[str] - error message if API call failed
        """
        result = {
            "synced": True,
            "exchange_positions": {},
            "virtual_positions": {},
            "symbol_to_contract": {},  # Maps symbol -> contract_id for closing untracked positions
            "discrepancies": [],
            "error": None,
            "skipped": None,
        }

        # Skip sync if an order was just placed (in-flight window)
        if self.is_order_inflight():
            elapsed = (datetime.now(timezone.utc) - self._last_order_submission_time).total_seconds()
            self.logger.debug(
                f"[POSITION-SYNC] Skipping - order in-flight ({elapsed:.1f}s < {self.ORDER_INFLIGHT_WINDOW_SECONDS}s)"
            )
            self._log_sync("SYNC_SKIPPED", {"reason": "order_inflight", "elapsed_sec": f"{elapsed:.1f}"})
            result["skipped"] = "order_inflight"
            return result

        if not self.strategy_states:
            self.logger.debug("[POSITION-SYNC] No strategy_states available, skipping sync check")
            return result

        # 1. Fetch exchange positions
        try:
            pos_response = self.client.api.position_search_open(self.account_id)
            if not pos_response.get("success"):
                error_msg = pos_response.get("error", "Unknown error")
                self.logger.warning(f"[POSITION-SYNC] Failed to fetch exchange positions: {error_msg}")
                result["error"] = error_msg
                result["synced"] = False
                return result

            exchange_positions = {}
            for pos in pos_response.get("positions", []):
                # Extract symbol from contractId (e.g., "CON.F.US.MGC.Z25" -> "MGC")
                contract_id = pos.get("contractId", "")
                parts = contract_id.split(".")
                if len(parts) >= 4:
                    raw_symbol = parts[3]
                    # Map API symbols to trading symbols
                    symbol_map = {"EP": "ES", "ENQ": "NQ", "GCE": "GC"}
                    symbol = symbol_map.get(raw_symbol, raw_symbol)
                else:
                    symbol = contract_id

                # Get signed quantity (type: 1=LONG, 2=SHORT)
                size = pos.get("size", 0)
                pos_type = pos.get("type", 1)
                qty = size if pos_type == 1 else -size

                # Aggregate by symbol (in case of multiple positions)
                exchange_positions[symbol] = exchange_positions.get(symbol, 0) + qty

                # Save symbol -> contract_id mapping (needed for closing untracked positions)
                result["symbol_to_contract"][symbol] = contract_id

            result["exchange_positions"] = exchange_positions

        except Exception as e:
            self.logger.warning(f"[POSITION-SYNC] Exception fetching exchange positions: {e}")
            result["error"] = str(e)
            result["synced"] = False
            return result

        # 2. Calculate virtual positions from strategy states
        virtual_positions = {}
        for _strategy_id, state in self.strategy_states.items():
            symbol = getattr(state, "symbol", None)
            if not symbol:
                continue

            # Get virtual position from tracker
            tracker = getattr(state, "tracker", None)
            if tracker:
                pos = tracker.get_position(symbol)
                qty = pos.quantity if pos else 0.0
            else:
                qty = 0.0

            if qty != 0:
                virtual_positions[symbol] = virtual_positions.get(symbol, 0) + qty

        result["virtual_positions"] = virtual_positions

        # 3. Compare and find discrepancies
        all_symbols = set(exchange_positions.keys()) | set(virtual_positions.keys())

        for symbol in all_symbols:
            exchange_qty = exchange_positions.get(symbol, 0)
            virtual_qty = virtual_positions.get(symbol, 0)

            # Allow for small floating point differences
            if abs(exchange_qty - virtual_qty) > 0.001:
                discrepancy = {
                    "symbol": symbol,
                    "exchange_qty": exchange_qty,
                    "virtual_qty": virtual_qty,
                    "diff": exchange_qty - virtual_qty,
                }
                result["discrepancies"].append(discrepancy)
                result["synced"] = False

        # 4. Log results (filter out symbols in repair cooldown to avoid spam)
        non_cooldown_discrepancies = [
            d
            for d in result["discrepancies"]
            if d["symbol"] not in self._repair_cooldown or datetime.now() >= self._repair_cooldown[d["symbol"]]
        ]
        # Include filtered list in result for run_portfolio.py to use
        result["non_cooldown_discrepancies"] = non_cooldown_discrepancies
        if non_cooldown_discrepancies:
            self.logger.warning("[POSITION-SYNC] ⚠️ DESYNC DETECTED! Exchange vs Virtual positions differ:")
            for d in non_cooldown_discrepancies:
                self.logger.warning(
                    f"  {d['symbol']}: exchange={d['exchange_qty']}, virtual={d['virtual_qty']}, "
                    f"diff={d['diff']:+.0f}"
                )
                # Determine likely cause
                if d["exchange_qty"] == 0 and d["virtual_qty"] != 0:
                    likely_cause = (
                        "PHANTOM_POSITION: Virtual has position but exchange flat. "
                        "Causes: order rejected, timeout, manual close"
                    )
                elif d["exchange_qty"] != 0 and d["virtual_qty"] == 0:
                    likely_cause = (
                        "UNTRACKED_POSITION: Exchange has position we don't track. "
                        "Causes: manual trade, other bot, previous session"
                    )
                elif d["exchange_qty"] * d["virtual_qty"] < 0:
                    likely_cause = (
                        "DIRECTION_MISMATCH: Exchange and virtual opposite directions. "
                        "Causes: bracket fill not processed, concurrent closes"
                    )
                else:
                    likely_cause = "QUANTITY_MISMATCH: Partial fill or concurrent order fills"

                # Log to sync file with full context
                self._log_sync(
                    "DESYNC_DETECTED",
                    {
                        "symbol": d["symbol"],
                        "exchange_qty": d["exchange_qty"],
                        "virtual_qty": d["virtual_qty"],
                        "diff": f"{d['diff']:+.0f}",
                        "likely_cause": likely_cause,
                    },
                )
        else:
            self.logger.debug(
                f"[POSITION-SYNC] ✓ Positions synced. Exchange: {exchange_positions}, Virtual: {virtual_positions}"
            )

        return result

    def repair_position_desync(self, sync_result: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Take corrective action when exchange and virtual positions don't match.

        For each symbol with a discrepancy:
        - If exchange < virtual (phantom positions): Zero out virtual positions
        - If exchange > virtual (untracked positions): Log warning only (too dangerous to auto-add)

        Args:
            sync_result: Optional result from check_position_sync(). If None, will call it.

        Returns:
            Dict with repair results:
                - repairs_made: List of {strategy, symbol, old_qty, new_qty, reason}
                - warnings: List of issues that couldn't be auto-repaired
                - brackets_cancelled: List of bracket tags cancelled
        """
        result = {
            "repairs_made": [],
            "warnings": [],
            "brackets_cancelled": [],
            "remaining_discrepancies": 0,
        }

        if sync_result is None:
            sync_result = self.check_position_sync()

        if sync_result.get("synced") or sync_result.get("error"):
            return result  # Nothing to repair or can't check

        if not self.strategy_states:
            return result

        discrepancies = sync_result.get("discrepancies", [])
        if not discrepancies:
            return result

        # IMPORTANT: Process ONE discrepancy per SYMBOL per call
        # This prevents repair from fighting with bracket fills for the SAME symbol
        # but allows parallel repair of DIFFERENT symbols (e.g., ES, NQ, GC all desynced)
        # Remaining discrepancies for each symbol will be handled in subsequent poll cycles
        seen_symbols = set()
        discrepancies_to_process = []
        for d in discrepancies:
            sym = d.get("symbol")
            if sym not in seen_symbols:
                discrepancies_to_process.append(d)
                seen_symbols.add(sym)

        result["remaining_discrepancies"] = len(discrepancies) - len(discrepancies_to_process)

        if len(discrepancies_to_process) > 1:
            self.logger.info(
                f"[POSITION-REPAIR] Processing {len(discrepancies_to_process)} discrepancies "
                f"(one per symbol: {list(seen_symbols)}). "
                f"{result['remaining_discrepancies']} duplicate symbol entries deferred to next loop."
            )
        elif result["remaining_discrepancies"] > 0:
            self.logger.info(
                f"[POSITION-REPAIR] Processing 1 of {len(discrepancies)} discrepancies. "
                f"Will check remaining {result['remaining_discrepancies']} next loop."
            )

        # Process each selected discrepancy (one per unique symbol)
        for discrepancy in discrepancies_to_process:
            symbol = discrepancy["symbol"]
            exchange_qty = discrepancy["exchange_qty"]
            virtual_qty = discrepancy["virtual_qty"]

            # Check if this symbol is in cooldown (market was closed)
            # Skip silently to avoid log spam
            cooldown_until = self._repair_cooldown.get(symbol)
            if cooldown_until and datetime.now() < cooldown_until:
                # Silently skip - already logged once when cooldown was set
                continue

            # Calculate excess exposure: positive = too long, negative = too short
            excess = virtual_qty - exchange_qty  # How much we need to REDUCE

            if abs(excess) < 0.001:
                continue  # No meaningful difference, check next symbol

            # Determine what direction of positions we need to zero
            # If excess > 0: we're too LONG, zero LONG positions (qty > 0)
            # If excess < 0: we're too SHORT, zero SHORT positions (qty < 0)
            reduce_longs = excess > 0
            target_direction = "long" if reduce_longs else "short"

            self.logger.warning(
                f"[POSITION-REPAIR] {symbol}: Excess {target_direction} exposure of {abs(excess):.0f} contracts. "
                f"Exchange={exchange_qty:.0f}, Virtual={virtual_qty:.0f}. "
                f"Will zero {target_direction} positions to reduce exposure..."
            )

            # Find strategies with positions IN THE DIRECTION WE NEED TO REDUCE
            strategies_with_pos = []
            strategies_wrong_direction = []
            for sid, state in self.strategy_states.items():
                if getattr(state, "symbol", None) != symbol:
                    continue
                tracker = getattr(state, "tracker", None)
                if not tracker:
                    continue
                pos = tracker.get_position(symbol)
                if pos and abs(pos.quantity) > 0.001:
                    # Check if position is in the direction we need to reduce
                    is_target_direction = (reduce_longs and pos.quantity > 0) or (not reduce_longs and pos.quantity < 0)
                    if is_target_direction:
                        strategies_with_pos.append((sid, state, pos.quantity))
                    else:
                        # Track positions in wrong direction for logging
                        strategies_wrong_direction.append((sid, pos.quantity))

            # SAFETY CHECK: If we have no positions in the target direction, something is wrong
            if not strategies_with_pos:
                if strategies_wrong_direction:
                    # We have positions but all in the OPPOSITE direction of our excess
                    # This means exchange has exposure we're not tracking at all
                    self.logger.error(
                        f"[POSITION-REPAIR] DIRECTION CONFLICT for {symbol}! "
                        f"Need to reduce {target_direction} exposure but only have opposite positions: "
                        f"{strategies_wrong_direction}. Exchange may have untracked {target_direction} position."
                    )
                    result["warnings"].append(
                        {
                            "symbol": symbol,
                            "exchange_qty": exchange_qty,
                            "virtual_qty": virtual_qty,
                            "message": f"Direction conflict: need to reduce {target_direction} but only have "
                            f"opposite positions. Manual intervention required.",
                        }
                    )
                else:
                    # No virtual positions at all but exchange has some - AUTO-CLOSE
                    # (Cooldown already checked at start of loop)
                    self.logger.warning(
                        f"[POSITION-REPAIR] UNTRACKED POSITION: {symbol} has {exchange_qty:.0f} on exchange "
                        f"but no virtual positions. Auto-closing to sync both to flat."
                    )

                    # Get contract_id from sync_result (populated by check_position_sync)
                    contract_id = sync_result.get("symbol_to_contract", {}).get(symbol)
                    if not contract_id:
                        # Fallback: try to find from any strategy_state
                        for state in self.strategy_states.values():
                            if state.symbol == symbol and getattr(state, "contract_id", None):
                                contract_id = state.contract_id
                                break

                    if contract_id:
                        success, market_closed = self._close_untracked_exchange_position(
                            symbol, exchange_qty, contract_id
                        )
                        if success:
                            # Clear any cooldown on success
                            self._repair_cooldown.pop(symbol, None)
                            result["repairs_made"].append(
                                {
                                    "symbol": symbol,
                                    "type": "untracked_closed",
                                    "exchange_qty": exchange_qty,
                                    "action": "market_close",
                                }
                            )
                        else:
                            if market_closed:
                                # Set cooldown - don't spam when market is closed
                                cooldown_until = datetime.now() + timedelta(minutes=self.REPAIR_COOLDOWN_MINUTES)
                                self._repair_cooldown[symbol] = cooldown_until
                                self.logger.warning(
                                    f"[POSITION-REPAIR] Market closed for {symbol}. "
                                    f"Will retry in {self.REPAIR_COOLDOWN_MINUTES} minutes."
                                )
                                result["warnings"].append(
                                    {
                                        "symbol": symbol,
                                        "exchange_qty": exchange_qty,
                                        "message": f"Market closed - will retry in {self.REPAIR_COOLDOWN_MINUTES}m",
                                    }
                                )
                            else:
                                result["warnings"].append(
                                    {
                                        "symbol": symbol,
                                        "exchange_qty": exchange_qty,
                                        "message": f"Failed to auto-close untracked position of {exchange_qty}",
                                    }
                                )
                    else:
                        self.logger.error(f"[POSITION-REPAIR] Cannot auto-close {symbol}: no contract_id found")
                        result["warnings"].append(
                            {
                                "symbol": symbol,
                                "exchange_qty": exchange_qty,
                                "message": f"No contract_id found for {symbol}, cannot auto-close",
                            }
                        )
                continue  # Move to next symbol (either closed or warned)

            # Sort by absolute quantity (zero out smallest positions first to minimize impact)
            strategies_with_pos.sort(key=lambda x: abs(x[2]))

            # Zero out positions until we've reduced exposure by the required amount
            remaining_to_zero = abs(excess)
            for sid, state, qty in strategies_with_pos:
                if remaining_to_zero <= 0.001:
                    break

                # Determine how much to zero from this strategy
                zero_qty = min(abs(qty), remaining_to_zero)
                remaining_to_zero -= zero_qty

                # Only fully zero if the whole position needs to go
                if zero_qty >= abs(qty) - 0.001:
                    # Capture state before zeroing for logging
                    old_qty = qty
                    old_entry_price = getattr(state, "entry_price", None)
                    old_tp = getattr(state, "take_profit_price", None)
                    old_sl = getattr(state, "stop_loss_price", None)

                    # Zero out completely
                    state.tracker.reset()  # Clear all positions for this strategy
                    state.entry_price = None
                    state.take_profit_price = None
                    state.stop_loss_price = None

                    # Cancel any brackets for this strategy
                    brackets_cancelled_count = 0
                    try:
                        cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
                        if cancel_result.get("cancelled"):
                            result["brackets_cancelled"].extend(cancel_result["cancelled"])
                            brackets_cancelled_count = len(cancel_result["cancelled"])
                    except Exception as ce:
                        self.logger.warning(f"[POSITION-REPAIR] Failed to cancel brackets for {sid}: {ce}")

                    # CRITICAL: Clear processed fills for this strategy so next poll can re-sync
                    cleared_count = self._clear_processed_fills_for_strategy(sid)
                    if cleared_count > 0:
                        self.logger.info(
                            f"[POSITION-REPAIR] Cleared {cleared_count} processed fills for {sid} to allow re-sync"
                        )

                    result["repairs_made"].append(
                        {
                            "strategy": sid,
                            "symbol": symbol,
                            "old_qty": old_qty,
                            "new_qty": 0,
                            "reason": f"excess_{target_direction}_zeroed",
                            "fills_cleared": cleared_count,
                        }
                    )
                    self.logger.warning(
                        f"[POSITION-REPAIR] Zeroed {target_direction} position: {sid} was {old_qty:.0f} -> 0"
                    )

                    # Log to sync file with full repair details
                    self._log_sync(
                        "REPAIR_EXECUTED",
                        {
                            "strategy": sid,
                            "symbol": symbol,
                            "old_qty": f"{old_qty:.0f}",
                            "new_qty": "0",
                            "direction": target_direction,
                            "entry_price_lost": old_entry_price or "N/A",
                            "tp_cancelled": old_tp or "N/A",
                            "sl_cancelled": old_sl or "N/A",
                            "brackets_cancelled": brackets_cancelled_count,
                            "fills_cleared": cleared_count,
                            "exchange_qty": exchange_qty,
                            "virtual_qty": virtual_qty,
                        },
                    )

            # SAFETY CHECK: Did we zero enough?
            if remaining_to_zero > 0.001:
                self.logger.warning(
                    f"[POSITION-REPAIR] Could only reduce {abs(excess) - remaining_to_zero:.0f} of "
                    f"{abs(excess):.0f} excess {target_direction} contracts for {symbol}. "
                    f"Remaining {remaining_to_zero:.0f} may need manual intervention."
                )
                result["warnings"].append(
                    {
                        "symbol": symbol,
                        "exchange_qty": exchange_qty,
                        "virtual_qty": virtual_qty,
                        "message": f"Partial repair: reduced {abs(excess) - remaining_to_zero:.0f} of "
                        f"{abs(excess):.0f} excess contracts. {remaining_to_zero:.0f} remain.",
                    }
                )

        if result["repairs_made"]:
            self.logger.warning(
                f"[POSITION-REPAIR] Completed {len(result['repairs_made'])} repairs, "
                f"cancelled {len(result['brackets_cancelled'])} brackets"
            )

        return result

    def check_and_record_contracts(self) -> Dict[str, Any]:
        """
        Record current contract IDs and detect rollovers.

        Call at session start and end to track contract month changes.
        When a rollover is detected, the symbol has moved to a new contract
        (e.g., ESZ25 -> ESH26) and all positions should be treated as fresh.

        Returns:
            Dict with:
                - contracts: Dict[symbol, contract_id] - current contract IDs
                - rollovers: List of {symbol, old, new} for changed contracts
                - error: Optional error message
        """
        result = {"contracts": {}, "rollovers": [], "error": None}

        try:
            pos_response = self.client.api.position_search_open(self.account_id)
            if not pos_response.get("success"):
                result["error"] = pos_response.get("error", "Failed to fetch positions")
                return result

            for pos in pos_response.get("positions", []):
                contract_id = pos.get("contractId", "")
                parts = contract_id.split(".")
                if len(parts) >= 4:
                    raw_symbol = parts[3]
                    # Map API symbols to trading symbols
                    symbol_map = {"EP": "ES", "ENQ": "NQ", "GCE": "GC"}
                    symbol = symbol_map.get(raw_symbol, raw_symbol)
                else:
                    continue  # Skip invalid contract IDs

                # Check for rollover
                if symbol in self._known_contracts:
                    if self._known_contracts[symbol] != contract_id:
                        result["rollovers"].append(
                            {
                                "symbol": symbol,
                                "old": self._known_contracts[symbol],
                                "new": contract_id,
                            }
                        )
                        self.logger.warning(
                            f"[CONTRACT-ROLLOVER] {symbol} contract changed: "
                            f"{self._known_contracts[symbol]} -> {contract_id}"
                        )
                        # Log rollover to sync file
                        self._log_sync(
                            "CONTRACT_ROLLOVER",
                            {
                                "symbol": symbol,
                                "old_contract": self._known_contracts[symbol],
                                "new_contract": contract_id,
                                "action": "Starting fresh with new contract next session",
                            },
                        )

                # Update known contract
                self._known_contracts[symbol] = contract_id
                result["contracts"][symbol] = contract_id

        except Exception as e:
            self.logger.error(f"[CONTRACT-ROLLOVER] Error checking contracts: {e}")
            result["error"] = str(e)

        return result


# =============================================================================
# Test / Demo
# =============================================================================


def test_bracket_manager():
    """Test the bracket manager with a real MGC position."""
    from dotenv import load_dotenv

    load_dotenv()

    from custom_portfolio.data.futures_metadata import round_to_tick
    from lumibot.credentials import PROJECTX_CONFIG
    from lumibot.tools.projectx_helpers import ProjectXClient

    print("=" * 60)
    print("BRACKET ORDER MANAGER TEST")
    print("=" * 60)

    client = ProjectXClient(PROJECTX_CONFIG)
    account_id = client.get_preferred_account_id()

    print(f"Account ID: {account_id}")

    # Initialize manager
    manager = BracketOrderManager(client, account_id)

    CONTRACT_ID = "CON.F.US.MGC.Z25"
    SYMBOL = "MGC"
    TICK_SIZE = 0.10
    SL_TICKS = 3
    TP_TICKS = 3

    # Get current price
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=5)
    bars = client.api.history_retrieve_bars(
        contract_id=CONTRACT_ID,
        start_datetime=start_time.isoformat(),
        end_datetime=end_time.isoformat(),
        unit=2,
        unit_number=1,
        limit=5,
    )
    current_price = bars["close"].iloc[-1]
    print(f"Current {SYMBOL} price: {current_price:.2f}")

    timestamp = int(time.time())
    base_tag = f"BRACKET_TEST_{timestamp}"

    # 1. Entry at market
    print("\n1. Placing entry order...")
    entry_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=2,  # Market
        side=0,  # Buy
        size=1,
        custom_tag=f"{base_tag}_ENTRY",
    )

    if not entry_resp.get("success"):
        print(f"Entry failed: {entry_resp}")
        return

    entry_id = entry_resp.get("orderId")
    print(f"   Entry placed: ID={entry_id}")

    time.sleep(0.5)

    # Get fill price
    pos_resp = client.api.position_search_open(account_id)
    fill_price = current_price
    for pos in pos_resp.get("positions", []):
        if CONTRACT_ID in pos.get("contractId", ""):
            fill_price = pos.get("averagePrice", current_price)
            break

    sl_price = round_to_tick(fill_price - (SL_TICKS * TICK_SIZE), SYMBOL)
    tp_price = round_to_tick(fill_price + (TP_TICKS * TICK_SIZE), SYMBOL)

    print(f"   Fill: {fill_price:.2f}, SL: {sl_price:.2f}, TP: {tp_price:.2f}")

    # 2. Place SL
    print("\n2. Placing SL order...")
    sl_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=4,  # Stop
        side=1,  # Sell
        size=1,
        stop_price=sl_price,
        custom_tag=f"{base_tag}_SL",
    )
    sl_id = sl_resp.get("orderId")
    print(f"   SL placed: ID={sl_id}")

    # 3. Place TP
    print("\n3. Placing TP order...")
    tp_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=1,  # Limit
        side=1,  # Sell
        size=1,
        limit_price=tp_price,
        custom_tag=f"{base_tag}_TP",
    )
    tp_id = tp_resp.get("orderId")
    print(f"   TP placed: ID={tp_id}")

    # 4. Register bracket
    print("\n4. Registering bracket with manager...")
    manager.register_bracket(base_tag, sl_order_id=sl_id, tp_order_id=tp_id, symbol=SYMBOL)

    # 5. Poll until position closes or timeout
    print("\n5. Polling for fills (5 min timeout)...")
    print("   Watching for SL or TP to fill, then cancel the other.")
    print()

    start_poll = time.time()
    max_duration = 300  # 5 minutes

    while time.time() - start_poll < max_duration:
        # Poll bracket manager
        result = manager.poll_and_cleanup()

        if result["cancelled"]:
            print(f"\n*** ORPHAN CANCELLED: {result['cancelled']} ***")

        # Check if bracket is done
        bracket = manager.brackets.get(base_tag)
        if bracket and not bracket.active:
            print("\n*** BRACKET COMPLETE - both legs terminal ***")
            break

        # Show status
        elapsed = int(time.time() - start_poll)
        sl_status = "TERMINAL" if bracket.sl_terminal else "OPEN"
        tp_status = "TERMINAL" if bracket.tp_terminal else "OPEN"
        print(f"{elapsed:3d}s | SL: {sl_status} | TP: {tp_status}")

        time.sleep(2)

    print("\n" + "=" * 60)
    print("STATS:")
    print(f"  Brackets registered: {manager.stats['brackets_registered']}")
    print(f"  Orphans cancelled: {manager.stats['orphans_cancelled']}")
    print(f"  Polls executed: {manager.stats['polls_executed']}")
    print("=" * 60)


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Suppress noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    test_bracket_manager()
