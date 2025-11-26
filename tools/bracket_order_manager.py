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
from typing import Any, Dict, Optional

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
        self._processed_fills: set = set()

        # CRITICAL: Track order IDs WE placed during THIS run
        # Only process fills for orders in this set - ignore everything else
        self._our_order_ids: set = set()

        # Debounce tracking: tag -> timestamp (UTC)
        self._recent_placements: Dict[str, datetime] = {}
        self.PLACEMENT_COOLDOWN_SECONDS = 10

        # Failed placement tracking: tag -> failure count
        self._failed_recreations: Dict[str, int] = {}
        self.MAX_RECREATION_FAILURES = 3

        # Stats
        self.stats = {
            "brackets_registered": 0,
            "orphans_cancelled": 0,
            "polls_executed": 0,
            "fills_processed": 0,
            "brackets_recreated": 0,
            "emergency_closes": 0,
        }

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

    def startup_cleanup(self) -> Dict[str, any]:
        """
        Cancel ALL existing BRK_* orders on startup to ensure clean slate.

        MUST be called before any trading begins. Previous bot runs may have
        left orphan bracket orders that will block new orders with same tags.

        Returns:
            Dict with 'cancelled' count and 'errors' list
        """
        result = {"cancelled": 0, "errors": [], "found": 0}

        self.logger.info("[BRACKET] Starting cleanup of stale bracket orders...")

        # Query recent orders (24h lookback)
        start_date = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
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
                if self._cancel_order(order_id, tag[:10], "startup_cleanup"):
                    result["cancelled"] += 1
                    self.logger.info(f"[BRACKET] Startup cleanup: cancelled {tag} (order_id={order_id})")
                else:
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
        try:
            cancel_resp = self.client.api.order_cancel(self.account_id, order_id)

            if cancel_resp.get("success"):
                self.stats["orphans_cancelled"] += 1
                self.logger.info(f"[BRACKET] Cancelled orphan {order_type}: {order_id} | bracket={base_tag}")
                return True
            else:
                error_code = cancel_resp.get("errorCode")
                # errorCode 5 = order doesn't exist (already filled/cancelled)
                if error_code == 5:
                    self.logger.debug(f"[BRACKET] {order_type} {order_id} already gone (errorCode 5)")
                    return True  # Consider it handled
                else:
                    self.logger.warning(f"[BRACKET] Failed to cancel {order_type} {order_id}: {cancel_resp}")
                    return False
        except Exception as e:
            self.logger.error(f"[BRACKET] Error cancelling {order_type} {order_id}: {e}")
            return False

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
        4. Wait for cancels to propagate

        Args:
            strategy_tag: The strategy ID used as base tag (e.g., "rsi_mean_revert_1")
            wait_seconds: Time to wait after cancels for propagation (default: 0.5s)

        Returns:
            Dict with 'cancelled' list, 'errors' list, and 'waited' bool
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

        # Wait for cancels to propagate if we cancelled anything
        if result["cancelled"] and wait_seconds > 0:
            time.sleep(wait_seconds)
            result["waited"] = True
            self.logger.debug(
                f"[BRACKET-CLOSE] Waited {wait_seconds}s after cancelling "
                f"{len(result['cancelled'])} orders for {strategy_tag}"
            )

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
            return processed  # No strategy states, can't process fills

        # Build trade lookup: order_id -> fill_price
        trade_prices = {t.get("orderId"): t.get("price") for t in trades if t.get("orderId")}

        for order in orders:
            order_id = order.get("id")
            status = order.get("status")
            tag = order.get("customTag") or ""

            if status != self.STATUS_FILLED or order_id in self._processed_fills:
                continue

            # CRITICAL: Only process orders WE placed during THIS run
            # This prevents phantom positions from old fills
            if order_id not in self._our_order_ids:
                continue

            # Extract strategy ID (supports BRK_ tag format)
            strategy_id = self._extract_strategy_id_from_tag(tag)
            if not strategy_id or strategy_id not in self.strategy_states:
                continue

            state = self.strategy_states[strategy_id]

            # Get ACTUAL fill price from trades
            fill_price = trade_prices.get(order_id)
            if fill_price is None:
                self.logger.debug(f"[BRACKET] Fill price not yet available for {order_id}")
                continue  # DO NOT add to _processed_fills - retry next cycle

            try:
                # Check for entry fills (both old and new format)
                if tag.startswith("BRK_ENTRY_") or tag.startswith("ENT_"):
                    # Entry fill
                    side = "buy" if order.get("side") == 0 else "sell"
                    qty = order.get("size", 1)

                    state.tracker.execute_order(state.symbol, qty, side, fill_price, order_id=str(order_id))
                    state.entry_price = fill_price
                    processed.append({"type": "entry", "strategy": strategy_id, "price": fill_price})
                    self.stats["fills_processed"] += 1

                    # Register fill with order_registry if available
                    if self.order_registry:
                        self.order_registry.register_fill(order_id, fill_price, qty)

                # Check for exit fills (both old and new format)
                elif tag.startswith(("BRK_TP_", "BRK_STOP_", "BRK_CLOSE_", "TP_", "SL_", "CLOSE_")):
                    # Exit fill - use ORDER size, not position size
                    pos = state.tracker.get_position(state.symbol)
                    if pos and abs(pos.quantity) > 0:
                        exit_qty = min(order.get("size", 1), abs(pos.quantity))
                        exit_side = "sell" if pos.quantity > 0 else "buy"
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

                        processed.append({"type": "exit", "strategy": strategy_id, "price": fill_price})
                        self.stats["fills_processed"] += 1

                        # Register fill with order_registry if available
                        if self.order_registry:
                            self.order_registry.register_fill(order_id, fill_price, exit_qty)

                self._processed_fills.add(order_id)

            except Exception as e:
                self.logger.error(f"[BRACKET] Error processing fill {order_id}: {e}")
                # Don't add to processed - may retry

        return processed

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
                        self.logger.debug(f"[BRACKET] Orders blocked for {strategy_id}: {status.session_reason}")
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

                # Log when we skip recreation due to recent fill (helps debug race conditions)
                if tp_filled and not tp_exists:
                    self.logger.info(f"[BRACKET] Skipping TP recreation for {strategy_id} - TP just filled")
                if sl_filled and not sl_exists:
                    self.logger.info(f"[BRACKET] Skipping SL recreation for {strategy_id} - SL just filled")

                position_closed = False

                # Handle missing TP (skip if TP just filled - let fill processing handle it)
                if not tp_exists and not tp_filled and state.take_profit_price:
                    if self._price_past_target(
                        current_price, state.take_profit_price, pos.quantity, "TP", state.symbol
                    ):
                        if not position_closed:
                            self._emergency_close(state, "TP_BREACHED", calendar)
                            result["closed"].append(strategy_id)
                            position_closed = True
                    else:
                        if self._try_recreate_bracket(state, "TP", pos.quantity, calendar):
                            result["recreated"].append(f"{strategy_id}_TP")

                # Handle missing SL (only if not already closed, skip if SL just filled)
                if not position_closed and not sl_exists and not sl_filled and state.stop_loss_price:
                    if self._price_past_target(current_price, state.stop_loss_price, pos.quantity, "SL", state.symbol):
                        self._emergency_close(state, "SL_BREACHED", calendar)
                        result["closed"].append(strategy_id)
                    else:
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
        for base_tag, group in bracket_groups.items():
            tp = group.get("tp")
            sl = group.get("sl")

            if not tp or not sl:
                continue

            tp_status = tp.get("status")
            sl_status = sl.get("status")

            # TP filled and SL still open → cancel SL
            if tp_status == self.STATUS_FILLED and sl_status == self.STATUS_OPEN:
                if self._cancel_order(sl["id"], "SL", base_tag):
                    result["cancelled"].append(
                        {"order_id": sl["id"], "type": "SL", "reason": "TP_filled", "base_tag": base_tag}
                    )

            # SL filled and TP still open → cancel TP
            elif sl_status == self.STATUS_FILLED and tp_status == self.STATUS_OPEN:
                if self._cancel_order(tp["id"], "TP", base_tag):
                    result["cancelled"].append(
                        {"order_id": tp["id"], "type": "TP", "reason": "SL_filled", "base_tag": base_tag}
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
                self.logger.info(f"[BRACKET] Deferring {tag} recreation: {status.session_reason}")
                return False  # Don't count as failure

        # Debounce check (UTC)
        last_attempt = self._recent_placements.get(tag)
        if last_attempt:
            elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds()
            if elapsed < self.PLACEMENT_COOLDOWN_SECONDS:
                return False

        # Failure limit check
        failures = self._failed_recreations.get(tag, 0)
        if failures >= self.MAX_RECREATION_FAILURES:
            self.logger.critical(f"[BRACKET] Max failures ({failures}) for {tag} - emergency close")
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
                    f"[BRACKET] Recreated {order_type} for {strategy_id} @ {price} (order_id={order_id}, tag={tag})"
                )
                return True
            else:
                # Check errorCode for permanent vs transient
                error_code = resp.get("errorCode")
                if error_code == 2:  # Invalid price - permanent
                    self._failed_recreations[tag] = self.MAX_RECREATION_FAILURES
                else:
                    self._failed_recreations[tag] = failures + 1
                self.logger.warning(f"[BRACKET] Failed {tag}: {resp}")
                return False
        except Exception as e:
            self._failed_recreations[tag] = failures + 1
            self.logger.error(f"[BRACKET] Error {tag}: {e}")
            return False

    def _emergency_close(self, state, reason: str, calendar=None):
        """Emergency close: cancel brackets, place market order (or zero out during maintenance)."""
        import uuid as uuid_mod

        strategy_id = state.strategy_id

        # Check if platform allows orders
        if calendar:
            allowed_sessions = getattr(state, "allowed_sessions", None) or ["24/7"]
            status = calendar.get_status(
                state.symbol,
                datetime.now(timezone.utc),
                allowed_sessions=allowed_sessions,
            )
            if not status.platform_open:
                # During maintenance - zero out virtual position (system auto-flattens)
                pos = state.tracker.get_position(state.symbol)
                if pos and abs(pos.quantity) > 0:
                    state.tracker.force_flat(state.symbol)
                    state.entry_price = None
                    state.take_profit_price = None
                    state.stop_loss_price = None
                    self.logger.critical(
                        f"[BRACKET] Maintenance - zeroed {strategy_id} ({reason}). " f"System will auto-flatten."
                    )
                self.stats["emergency_closes"] += 1
                return

        # Cancel remaining brackets FIRST (race-safe close pattern)
        self.cancel_brackets_for_strategy(strategy_id, wait_seconds=0.2)

        pos = state.tracker.get_position(state.symbol)
        if pos is None or abs(pos.quantity) < 1e-9:
            self.logger.info(f"[BRACKET] {strategy_id} already flat, skipping emergency close")
            return

        # Check with registry if we can close (if registry available)
        if self.order_registry:
            from tools.order_registry import can_close_position

            if not can_close_position(strategy_id, state.symbol, self.order_registry, state.tracker):
                self.logger.info(f"[BRACKET] {strategy_id} can_close_position=False, skipping")
                return

        # Generate unique tag using registry (timestamp + UUID) or fallback to old format
        if self.order_registry:
            from tools.order_registry import OrderPurpose

            close_tag = self.order_registry.generate_unique_tag(OrderPurpose.CLOSE, strategy_id)
        else:
            close_tag = f"BRK_CLOSE_{strategy_id}_{uuid_mod.uuid4().hex[:6]}"

        side = 1 if pos.quantity > 0 else 0

        contract_id = getattr(state, "contract_id", "")
        if not contract_id:
            self.logger.error(f"[BRACKET] No contract_id for emergency close {strategy_id}")
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
                    f"[BRACKET] Emergency close {strategy_id} ({reason}): order_id={order_id}, tag={close_tag}"
                )
            else:
                self.logger.critical(f"[BRACKET] Emergency close FAILED for {strategy_id}: {resp}")
        except Exception as e:
            self.logger.critical(f"[BRACKET] Emergency close ERROR for {strategy_id}: {e}")

    def _extract_strategy_id_from_tag(self, tag: str) -> Optional[str]:
        """Extract strategy ID from order tag (both old BRK_ and new format)."""
        # Old format: BRK_ENTRY_STRATEGYID, BRK_TP_STRATEGYID, etc.
        old_prefixes = ["BRK_ENTRY_", "BRK_TP_", "BRK_STOP_", "BRK_CLOSE_"]
        for prefix in old_prefixes:
            if tag.startswith(prefix):
                remaining = tag[len(prefix) :]
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
                # Cancel any remaining brackets
                self.cancel_brackets_for_strategy(strategy_id, wait_seconds=0)

                # ZERO OUT virtual position - system auto-flattens us
                pos = state.tracker.get_position(state.symbol)
                if pos and abs(pos.quantity) > 0:
                    # Force flatten the virtual position (no actual order - system handles it)
                    state.tracker.force_flat(state.symbol)
                    state.entry_price = None
                    state.take_profit_price = None
                    state.stop_loss_price = None
                    self._failed_recreations.pop(f"BRK_TP_{strategy_id}", None)
                    self._failed_recreations.pop(f"BRK_STOP_{strategy_id}", None)
                    self.logger.critical(f"[BRACKET] Maintenance window - zeroed virtual position for {strategy_id}")

    def _cleanup_tracking_dicts(self):
        """Cleanup stale entries from tracking dicts."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        self._recent_placements = {k: v for k, v in self._recent_placements.items() if v > cutoff}

        # Limit _processed_fills growth (keep last 1000)
        if len(self._processed_fills) > 1000:
            # Convert to list, sort, keep recent
            sorted_ids = sorted(self._processed_fills)
            self._processed_fills = set(sorted_ids[-500:])


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
