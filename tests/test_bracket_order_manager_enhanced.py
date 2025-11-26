"""
Unit tests for the enhanced BracketOrderManager (Phase 2-10).

Tests cover:
- poll_cycle() - main entry point
- _process_fills() - fill processing with trade_search prices
- _ensure_brackets() - bracket recreation logic
- _price_past_target() - breach detection with tick buffer
- _try_recreate_bracket() - debounce, failure tracking, calendar gating
- _emergency_close() - market order and maintenance handling
- Helper methods

Author: LumiBot Multi-Strategy Team
Date: 2025-11-25
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class MockProjectXClient:
    """Mock ProjectX client for testing."""

    def __init__(self):
        self.api = MagicMock()
        self._order_search_response = {"success": True, "orders": []}
        self._trade_search_response = {"success": True, "trades": []}
        self._order_place_response = {"success": True, "orderId": 12345}
        self._order_cancel_response = {"success": True}

    def setup_responses(
        self,
        orders=None,
        trades=None,
        order_place=None,
        order_cancel=None,
    ):
        """Configure mock responses."""
        if orders is not None:
            self._order_search_response = {"success": True, "orders": orders}
        if trades is not None:
            self._trade_search_response = {"success": True, "trades": trades}
        if order_place is not None:
            self._order_place_response = order_place
        if order_cancel is not None:
            self._order_cancel_response = order_cancel

        self.api.order_search.return_value = self._order_search_response
        self.api.trade_search.return_value = self._trade_search_response
        self.api.order_place.return_value = self._order_place_response
        self.api.order_cancel.return_value = self._order_cancel_response


class MockEnhancedStrategyState:
    """Mock EnhancedStrategyState for testing."""

    def __init__(self, strategy_id: str, symbol: str):
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.entry_price = None
        self.take_profit_price = None
        self.stop_loss_price = None
        self.contract_id = f"CON.F.US.{symbol}.Z25"

        # Mock tracker
        self.tracker = MagicMock()
        self._position = None

    def set_position(self, quantity: float, avg_price: float = 100.0):
        """Set up mock position."""
        if quantity == 0:
            self._position = None
        else:
            self._position = MagicMock()
            self._position.quantity = quantity
            self._position.avg_entry_price = avg_price
        self.tracker.get_position.return_value = self._position


class MockCalendar:
    """Mock TradingCalendar for testing."""

    def __init__(self, platform_open=True, can_enter_orders=True, must_be_flat=False):
        self.platform_open = platform_open
        self.can_enter_orders = can_enter_orders
        self.must_be_flat = must_be_flat

    def get_status(self, symbol, dt):
        """Return mock status."""
        status = MagicMock()
        status.platform_open = self.platform_open
        status.can_enter_orders = self.can_enter_orders
        status.must_be_flat = self.must_be_flat
        status.session_reason = "mock_session"
        return status


@pytest.fixture
def bracket_manager():
    """Create BracketOrderManager with mock client."""
    from tools.bracket_order_manager import BracketOrderManager

    client = MockProjectXClient()
    client.setup_responses()  # Set up default responses
    manager = BracketOrderManager(client=client, account_id=12345)
    return manager


@pytest.fixture
def bracket_manager_with_states():
    """Create BracketOrderManager with mock client and strategy states."""
    from tools.bracket_order_manager import BracketOrderManager

    client = MockProjectXClient()
    client.setup_responses()

    # Create mock strategy states
    state1 = MockEnhancedStrategyState("GC_1M_01", "GC")
    state1.set_position(1, 2650.0)
    state1.entry_price = 2650.0
    state1.take_profit_price = 2660.0
    state1.stop_loss_price = 2640.0

    strategy_states = {"GC_1M_01": state1}

    manager = BracketOrderManager(
        client=client,
        account_id=12345,
        strategy_states=strategy_states,
    )
    return manager


class TestExtractStrategyIdFromTag:
    """Tests for _extract_strategy_id_from_tag helper method."""

    def test_entry_tag(self, bracket_manager):
        """Extract strategy ID from entry tag."""
        result = bracket_manager._extract_strategy_id_from_tag("BRK_ENTRY_GC_1M_01")
        assert result == "GC_1M_01"

    def test_tp_tag(self, bracket_manager):
        """Extract strategy ID from TP tag."""
        result = bracket_manager._extract_strategy_id_from_tag("BRK_TP_GC_1M_01")
        assert result == "GC_1M_01"

    def test_stop_tag(self, bracket_manager):
        """Extract strategy ID from STOP tag."""
        result = bracket_manager._extract_strategy_id_from_tag("BRK_STOP_GC_1M_01")
        assert result == "GC_1M_01"

    def test_close_tag_with_uuid(self, bracket_manager):
        """Extract strategy ID from CLOSE tag with UUID suffix."""
        result = bracket_manager._extract_strategy_id_from_tag("BRK_CLOSE_GC_1M_01_abc123")
        assert result == "GC_1M_01"

    def test_non_brk_tag(self, bracket_manager):
        """Return None for non-BRK tags."""
        result = bracket_manager._extract_strategy_id_from_tag("OTHER_TAG")
        assert result is None

    def test_empty_tag(self, bracket_manager):
        """Return None for empty tag."""
        result = bracket_manager._extract_strategy_id_from_tag("")
        assert result is None


class TestIsBeforeStartup:
    """Tests for _is_before_startup helper method."""

    def test_order_before_startup(self, bracket_manager):
        """Order from before startup should return True."""
        # Set startup time to now
        bracket_manager._startup_time = datetime.now(timezone.utc)
        # Order from 1 hour ago
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert bracket_manager._is_before_startup(old_time) is True

    def test_order_after_startup(self, bracket_manager):
        """Order from after startup should return False."""
        # Set startup time to 1 hour ago
        bracket_manager._startup_time = datetime.now(timezone.utc) - timedelta(hours=1)
        # Order from now
        new_time = datetime.now(timezone.utc).isoformat()
        assert bracket_manager._is_before_startup(new_time) is False

    def test_empty_timestamp(self, bracket_manager):
        """Empty timestamp should return True (safer to skip)."""
        assert bracket_manager._is_before_startup("") is True
        assert bracket_manager._is_before_startup(None) is True

    def test_invalid_timestamp(self, bracket_manager):
        """Invalid timestamp should return True (safer to skip)."""
        assert bracket_manager._is_before_startup("not-a-date") is True


class TestPricePastTarget:
    """Tests for _price_past_target breach detection."""

    def test_long_tp_breached(self, bracket_manager):
        """Long position TP breached when price > target + tick."""
        # GC tick size is 0.10, so buffer should be 0.10
        # The method imports get_tick_size from custom_portfolio.data.futures_metadata
        with patch("custom_portfolio.data.futures_metadata.get_tick_size", return_value=0.10):
            result = bracket_manager._price_past_target(
                current=2660.20,  # Above target + buffer
                target=2660.0,
                pos_qty=1,  # Long
                order_type="TP",
                symbol="GC",
            )
            assert result is True

    def test_long_tp_not_breached(self, bracket_manager):
        """Long position TP not breached when price <= target + tick."""
        with patch("custom_portfolio.data.futures_metadata.get_tick_size", return_value=0.10):
            result = bracket_manager._price_past_target(
                current=2660.05,  # Within buffer
                target=2660.0,
                pos_qty=1,
                order_type="TP",
                symbol="GC",
            )
            assert result is False

    def test_long_sl_breached(self, bracket_manager):
        """Long position SL breached when price < target - tick."""
        with patch("custom_portfolio.data.futures_metadata.get_tick_size", return_value=0.10):
            result = bracket_manager._price_past_target(
                current=2639.85,  # Below target - buffer
                target=2640.0,
                pos_qty=1,
                order_type="SL",
                symbol="GC",
            )
            assert result is True

    def test_short_tp_breached(self, bracket_manager):
        """Short position TP breached when price < target - tick."""
        with patch("custom_portfolio.data.futures_metadata.get_tick_size", return_value=0.10):
            result = bracket_manager._price_past_target(
                current=2639.85,  # Below target - buffer
                target=2640.0,
                pos_qty=-1,  # Short
                order_type="TP",
                symbol="GC",
            )
            assert result is True

    def test_short_sl_breached(self, bracket_manager):
        """Short position SL breached when price > target + tick."""
        with patch("custom_portfolio.data.futures_metadata.get_tick_size", return_value=0.10):
            result = bracket_manager._price_past_target(
                current=2660.20,  # Above target + buffer
                target=2660.0,
                pos_qty=-1,  # Short
                order_type="SL",
                symbol="GC",
            )
            assert result is True


class TestTryRecreateBracket:
    """Tests for _try_recreate_bracket with debounce and failure tracking."""

    def test_recreate_success(self, bracket_manager_with_states):
        """Successful bracket recreation."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]

        # Configure success response
        manager.client.api.order_place.return_value = {"success": True, "orderId": 99999}

        result = manager._try_recreate_bracket(state, "TP", 1, None)

        assert result is True
        assert manager.stats["brackets_recreated"] == 1
        manager.client.api.order_place.assert_called_once()

    def test_recreate_debounce(self, bracket_manager_with_states):
        """Second recreation attempt within cooldown should be blocked."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]

        # First attempt
        manager.client.api.order_place.return_value = {"success": True, "orderId": 99999}
        result1 = manager._try_recreate_bracket(state, "TP", 1, None)
        assert result1 is True

        # Second attempt immediately (should be debounced)
        result2 = manager._try_recreate_bracket(state, "TP", 1, None)
        assert result2 is False

        # Only one API call should have been made
        assert manager.client.api.order_place.call_count == 1

    def test_recreate_failure_tracking(self, bracket_manager_with_states):
        """Failed recreations should increment failure counter."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        manager.PLACEMENT_COOLDOWN_SECONDS = 0  # Disable debounce for test

        # Configure failure response
        manager.client.api.order_place.return_value = {"success": False, "errorCode": 99}

        result = manager._try_recreate_bracket(state, "TP", 1, None)

        assert result is False
        assert manager._failed_recreations.get("BRK_TP_GC_1M_01") == 1

    def test_recreate_max_failures_triggers_emergency(self, bracket_manager_with_states):
        """Max failures should trigger emergency close."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        manager.PLACEMENT_COOLDOWN_SECONDS = 0

        # Set failure count to max
        manager._failed_recreations["BRK_TP_GC_1M_01"] = manager.MAX_RECREATION_FAILURES

        # Mock emergency close
        with patch.object(manager, "_emergency_close") as mock_close:
            result = manager._try_recreate_bracket(state, "TP", 1, None)

            assert result is False
            mock_close.assert_called_once()

    def test_recreate_calendar_blocked(self, bracket_manager_with_states):
        """Recreation blocked during maintenance."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]

        calendar = MockCalendar(platform_open=False)

        result = manager._try_recreate_bracket(state, "TP", 1, calendar)

        assert result is False
        # Should NOT increment failure counter (calendar block is not a failure)
        assert manager._failed_recreations.get("BRK_TP_GC_1M_01", 0) == 0

    def test_recreate_errorcode_2_permanent(self, bracket_manager_with_states):
        """errorCode 2 (invalid price) should immediately max out failures."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        manager.PLACEMENT_COOLDOWN_SECONDS = 0

        # Configure errorCode 2 response
        manager.client.api.order_place.return_value = {"success": False, "errorCode": 2}

        result = manager._try_recreate_bracket(state, "TP", 1, None)

        assert result is False
        assert manager._failed_recreations["BRK_TP_GC_1M_01"] == manager.MAX_RECREATION_FAILURES


class TestProcessFills:
    """Tests for _process_fills method."""

    def test_process_entry_fill(self, bracket_manager_with_states):
        """Process entry fill updates virtual position."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(0)  # Start flat

        # Order filled after startup
        order_time = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        orders = [
            {
                "id": 1001,
                "status": manager.STATUS_FILLED,
                "customTag": "BRK_ENTRY_GC_1M_01",
                "side": 0,  # Buy
                "size": 1,
                "createdDateTime": order_time,
            }
        ]
        trades = [{"orderId": 1001, "price": 2655.0}]

        manager.client.setup_responses(orders=orders, trades=trades)
        fetched_orders, fetched_trades = manager._fetch_orders_and_trades()

        result = manager._process_fills(fetched_orders, fetched_trades)

        assert len(result) == 1
        assert result[0]["type"] == "entry"
        assert result[0]["price"] == 2655.0
        state.tracker.execute_order.assert_called_once()

    def test_process_exit_fill(self, bracket_manager_with_states):
        """Process exit fill updates virtual position."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)  # Long position

        order_time = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        orders = [
            {
                "id": 1002,
                "status": manager.STATUS_FILLED,
                "customTag": "BRK_TP_GC_1M_01",
                "side": 1,  # Sell
                "size": 1,
                "createdDateTime": order_time,
            }
        ]
        trades = [{"orderId": 1002, "price": 2660.0}]

        manager.client.setup_responses(orders=orders, trades=trades)
        fetched_orders, fetched_trades = manager._fetch_orders_and_trades()

        result = manager._process_fills(fetched_orders, fetched_trades)

        assert len(result) == 1
        assert result[0]["type"] == "exit"
        state.tracker.execute_order.assert_called()

    def test_skip_orders_before_startup(self, bracket_manager_with_states):
        """Orders from before startup should be skipped."""
        manager = bracket_manager_with_states

        # Order from before startup
        order_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        orders = [
            {
                "id": 1003,
                "status": manager.STATUS_FILLED,
                "customTag": "BRK_ENTRY_GC_1M_01",
                "side": 0,
                "size": 1,
                "createdDateTime": order_time,
            }
        ]
        trades = [{"orderId": 1003, "price": 2650.0}]

        manager.client.setup_responses(orders=orders, trades=trades)
        fetched_orders, fetched_trades = manager._fetch_orders_and_trades()

        result = manager._process_fills(fetched_orders, fetched_trades)

        assert len(result) == 0  # Should skip old order

    def test_idempotency(self, bracket_manager_with_states):
        """Same fill should not be processed twice."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(0)

        order_time = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        orders = [
            {
                "id": 1004,
                "status": manager.STATUS_FILLED,
                "customTag": "BRK_ENTRY_GC_1M_01",
                "side": 0,
                "size": 1,
                "createdDateTime": order_time,
            }
        ]
        trades = [{"orderId": 1004, "price": 2655.0}]

        manager.client.setup_responses(orders=orders, trades=trades)
        fetched_orders, fetched_trades = manager._fetch_orders_and_trades()

        # First call
        result1 = manager._process_fills(fetched_orders, fetched_trades)
        # Second call (same orders)
        result2 = manager._process_fills(fetched_orders, fetched_trades)

        assert len(result1) == 1
        assert len(result2) == 0  # Already processed


class TestEnsureBrackets:
    """Tests for _ensure_brackets method."""

    def test_recreate_missing_tp(self, bracket_manager_with_states):
        """Missing TP bracket should be recreated."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)

        # Only SL exists (open)
        orders = [
            {
                "id": 2001,
                "status": manager.STATUS_OPEN,
                "customTag": "BRK_STOP_GC_1M_01",
            }
        ]
        current_prices = {"GC": 2655.0}

        manager.client.setup_responses(orders=orders)
        manager.client.api.order_place.return_value = {"success": True, "orderId": 9001}

        result = manager._ensure_brackets(orders, current_prices, None)

        assert "GC_1M_01_TP" in result["recreated"]

    def test_no_action_when_both_exist(self, bracket_manager_with_states):
        """No action when both brackets exist."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)

        # Both brackets exist
        orders = [
            {"id": 2001, "status": manager.STATUS_OPEN, "customTag": "BRK_STOP_GC_1M_01"},
            {"id": 2002, "status": manager.STATUS_OPEN, "customTag": "BRK_TP_GC_1M_01"},
        ]
        current_prices = {"GC": 2655.0}

        result = manager._ensure_brackets(orders, current_prices, None)

        assert len(result["recreated"]) == 0
        assert len(result["closed"]) == 0

    def test_emergency_close_on_breached_tp(self, bracket_manager_with_states):
        """Emergency close when TP is breached and bracket missing."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)
        state.take_profit_price = 2660.0

        # No brackets exist, price past TP
        orders = []
        current_prices = {"GC": 2665.0}  # Well past TP

        with patch.object(manager, "_emergency_close") as mock_close:
            with patch("custom_portfolio.data.futures_metadata.get_tick_size", return_value=0.10):
                result = manager._ensure_brackets(orders, current_prices, None)

                assert "GC_1M_01" in result["closed"]
                mock_close.assert_called_once()


class TestEmergencyClose:
    """Tests for _emergency_close method."""

    def test_emergency_close_places_market_order(self, bracket_manager_with_states):
        """Emergency close places market order when platform is open."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)

        manager.client.api.order_place.return_value = {"success": True, "orderId": 8888}

        manager._emergency_close(state, "TEST_REASON", None)

        # Should have placed a market order
        manager.client.api.order_place.assert_called()
        call_kwargs = manager.client.api.order_place.call_args[1]
        assert call_kwargs["type"] == 2  # Market order

    def test_emergency_close_during_maintenance(self, bracket_manager_with_states):
        """During maintenance, zero out virtual position instead of placing order."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)

        calendar = MockCalendar(platform_open=False)

        manager._emergency_close(state, "MAINTENANCE", calendar)

        # Should zero out virtual position, not place order
        state.tracker.force_flat.assert_called_once_with(state.symbol)
        assert state.entry_price is None


class TestSessionTransitionCleanup:
    """Tests for _session_transition_cleanup method."""

    def test_cleanup_during_maintenance(self, bracket_manager_with_states):
        """Positions should be zeroed during maintenance window."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)

        calendar = MockCalendar(platform_open=False)
        result = {"errors": []}

        manager._session_transition_cleanup(calendar, result)

        state.tracker.force_flat.assert_called_once()
        assert state.entry_price is None
        assert state.take_profit_price is None
        assert state.stop_loss_price is None

    def test_no_cleanup_when_platform_open(self, bracket_manager_with_states):
        """No cleanup when platform is open."""
        manager = bracket_manager_with_states
        state = manager.strategy_states["GC_1M_01"]
        state.set_position(1, 2650.0)

        calendar = MockCalendar(platform_open=True)
        result = {"errors": []}

        manager._session_transition_cleanup(calendar, result)

        state.tracker.force_flat.assert_not_called()


class TestCleanupTrackingDicts:
    """Tests for _cleanup_tracking_dicts method."""

    def test_cleanup_old_placements(self, bracket_manager):
        """Old placement timestamps should be cleaned up."""
        manager = bracket_manager

        # Add old and recent placements
        old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        recent_time = datetime.now(timezone.utc) - timedelta(minutes=1)

        manager._recent_placements = {
            "OLD_TAG": old_time,
            "RECENT_TAG": recent_time,
        }

        manager._cleanup_tracking_dicts()

        assert "OLD_TAG" not in manager._recent_placements
        assert "RECENT_TAG" in manager._recent_placements

    def test_cleanup_processed_fills_overflow(self, bracket_manager):
        """Processed fills should be trimmed when over limit."""
        manager = bracket_manager

        # Add more than 1000 fills
        manager._processed_fills = set(range(1500))

        manager._cleanup_tracking_dicts()

        assert len(manager._processed_fills) == 500


class TestPollCycle:
    """Tests for poll_cycle main entry point."""

    def test_poll_cycle_returns_result(self, bracket_manager_with_states):
        """poll_cycle should return structured result."""
        manager = bracket_manager_with_states

        manager.client.setup_responses(orders=[], trades=[])

        result = manager.poll_cycle({"GC": 2655.0}, None)

        assert "fills_processed" in result
        assert "brackets_recreated" in result
        assert "positions_closed" in result
        assert "orphans_cancelled" in result
        assert "errors" in result

    def test_poll_cycle_with_calendar(self, bracket_manager_with_states):
        """poll_cycle should respect calendar during maintenance."""
        manager = bracket_manager_with_states

        manager.client.setup_responses(orders=[], trades=[])
        calendar = MockCalendar(platform_open=False)

        result = manager.poll_cycle({"GC": 2655.0}, calendar)

        # Should trigger session cleanup
        assert "errors" not in result or len(result["errors"]) == 0


class TestVirtualPositionTrackerForceFlat:
    """Tests for VirtualPositionTracker.force_flat() method."""

    def test_force_flat_zeroes_position(self):
        """force_flat should zero out position without recording trade."""
        from lumibot.tools.virtual_position_tracker import VirtualPositionTracker

        tracker = VirtualPositionTracker()

        # Create a position
        tracker.execute_order("GC", 1, "buy", 2650.0)
        pos = tracker.get_position("GC")
        assert pos.quantity == 1

        # Force flat
        result = tracker.force_flat("GC")

        assert result.quantity == 0
        assert result.total_cost == 0

    def test_force_flat_nonexistent_symbol(self):
        """force_flat on nonexistent symbol returns None."""
        from lumibot.tools.virtual_position_tracker import VirtualPositionTracker

        tracker = VirtualPositionTracker()

        result = tracker.force_flat("NONEXISTENT")

        assert result is None

    def test_force_flat_does_not_record_trade(self):
        """force_flat should NOT add to trade history."""
        from lumibot.tools.virtual_position_tracker import VirtualPositionTracker

        tracker = VirtualPositionTracker()

        # Create a position
        tracker.execute_order("GC", 1, "buy", 2650.0)
        initial_trades = len(tracker.trade_history)

        # Force flat
        tracker.force_flat("GC")

        # Trade history should be unchanged
        assert len(tracker.trade_history) == initial_trades


class TestEnhancedStrategyStateContractId:
    """Tests for contract_id field on EnhancedStrategyState."""

    def test_contract_id_field_exists(self):
        """EnhancedStrategyState should have contract_id field."""
        from custom_portfolio.multi_strategy_executor_enhanced import EnhancedStrategyState

        state = EnhancedStrategyState(
            strategy_id="test_strategy",
            symbol="GC",
            params={},
            contracts=1,
        )

        assert hasattr(state, "contract_id")
        assert state.contract_id == ""  # Default empty string

    def test_contract_id_can_be_set(self):
        """contract_id should be settable."""
        from custom_portfolio.multi_strategy_executor_enhanced import EnhancedStrategyState

        state = EnhancedStrategyState(
            strategy_id="test_strategy",
            symbol="GC",
            params={},
            contracts=1,
        )

        state.contract_id = "CON.F.US.GCE.Z25"

        assert state.contract_id == "CON.F.US.GCE.Z25"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
