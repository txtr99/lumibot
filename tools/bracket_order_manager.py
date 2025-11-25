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
from datetime import datetime, timedelta
from typing import Dict, Optional

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

    def __init__(self, client, account_id: int, logger: Optional[logging.Logger] = None):
        """
        Initialize the bracket manager.

        Args:
            client: ProjectXClient instance
            account_id: Trading account ID
            logger: Optional logger instance
        """
        self.client = client
        self.account_id = account_id
        self.logger = logger or logging.getLogger(__name__)

        # Active bracket pairs: base_tag -> BracketPair
        self.brackets: Dict[str, BracketPair] = {}

        # Order ID to base_tag mapping for quick lookups
        self._order_to_bracket: Dict[int, str] = {}

        # Stats
        self.stats = {
            "brackets_registered": 0,
            "orphans_cancelled": 0,
            "polls_executed": 0,
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


# =============================================================================
# Test / Demo
# =============================================================================


def test_bracket_manager():
    """Test the bracket manager with a real MGC position."""
    from dotenv import load_dotenv

    load_dotenv()

    from datetime import timezone

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
