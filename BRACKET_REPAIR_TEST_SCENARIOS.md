# Bracket Order Repair: Test Scenarios

This document provides concrete test scenarios to validate fixes for the edge cases described in `BRACKET_ORDER_ORPHANING_EDGE_CASES.md`.

---

## Test Suite Structure

```python
# tests/test_bracket_repair_edge_cases.py

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call
from tools.bracket_order_manager import BracketOrderManager, BracketPair
from lumibot.tools.virtual_position_tracker import VirtualPositionTracker
```

---

## Test Group 1: Cancel Confirmation

### Test 1.1: Cancel Response Lost but Order Actually Cancelled

```python
def test_cancel_response_lost_but_order_cancelled():
    """
    Test: Cancel request succeeds on exchange, but response is lost.
    Expected: retry mechanism should detect order is terminal and confirm success.
    """
    # Setup
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    # First call: cancel request times out
    mock_client.api.order_cancel.side_effect = TimeoutError("Response lost")

    # Second call: order_search shows order is CANCELLED
    mock_client.api.order_search.return_value = {
        "success": True,
        "orders": [
            {
                "id": 12345,
                "status": BracketOrderManager.STATUS_CANCELLED
            }
        ]
    }

    # Action
    result = manager._cancel_order_with_confirmation(12345, "TP", "ES_LONG_001")

    # Assert
    assert result is True, "Should confirm cancel despite response loss"
    # ← Requires implementation of _cancel_order_with_confirmation


def test_cancel_api_returns_error_but_order_terminal():
    """
    Test: Cancel API returns error code, but order is actually terminal.
    Expected: should still confirm success and remove bracket.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    # API returns error
    mock_client.api.order_cancel.return_value = {
        "success": False,
        "errorCode": 5  # Order doesn't exist
    }

    # But order_search shows it's filled
    mock_client.api.order_search.return_value = {
        "success": True,
        "orders": [
            {
                "id": 12345,
                "status": BracketOrderManager.STATUS_FILLED
            }
        ]
    }

    result = manager._cancel_order_with_confirmation(12345, "SL", "ES_SHORT_001")

    assert result is True, "Should recognize order is terminal even with API error"
```

---

## Test Group 2: Position Zeroing Atomicity

### Test 2.1: Cancel Fails, Position Not Zeroed

```python
def test_cancel_fails_prevents_position_zero():
    """
    Test: Bracket cancel fails → position should NOT be zeroed.
    Expected: repair is aborted with warning.
    """
    mock_client = MagicMock()
    mock_client.api.order_search.return_value = {"success": True, "orders": []}

    manager = BracketOrderManager(mock_client, account_id=123)

    # Setup strategy state
    state = MagicMock()
    state.symbol = "ES"
    state.tracker = VirtualPositionTracker()
    state.tracker.execute_order("ES", 2, "buy", 5800)

    manager.strategy_states = {"ES_01": state}

    # Register bracket
    manager.register_bracket("ES_LONG_001", sl_order_id=111, tp_order_id=222, symbol="ES")

    # Mock cancel failure
    with patch.object(manager, "cancel_brackets_for_strategy") as mock_cancel:
        mock_cancel.side_effect = ConnectionError("API timeout")

        # Action: call repair (not the full repair_position_desync, just the atomic zero)
        result = manager._zero_position_atomic("ES_01", state, 2.0, "long")

        # Assert
        assert result["success"] is False, "Should fail atomically"
        assert result["reason"] == "cancel_failed"

        # Position should still be +2, not zeroed
        pos = state.tracker.get_position("ES")
        assert pos is not None, "Position should still exist"
        assert pos.quantity == 2, "Position should not be zeroed on cancel failure"


def test_partial_cancel_failure_rollback():
    """
    Test: First bracket cancels successfully, second fails.
    Expected: attempt rollback of first cancel.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    state = MagicMock()
    state.symbol = "MES"
    state.tracker = VirtualPositionTracker()
    state.tracker.execute_order("MES", 5, "sell", 4950)

    manager.strategy_states = {"MES_01": state}
    manager.register_bracket("MES_SHORT_001", sl_order_id=333, tp_order_id=334, symbol="MES")

    # Mock: SL cancel succeeds, TP cancel fails
    with patch.object(manager, "_cancel_order") as mock_cancel:
        mock_cancel.side_effect = [
            True,   # SL cancel succeeds
            False   # TP cancel fails
        ]

        result = manager._zero_position_atomic("MES_01", state, 5.0, "short")

        assert result["success"] is False
        assert result["compensation"] == "attempted_restore_sl"  # Tried to restore SL

        # Position should not be zeroed
        pos = state.tracker.get_position("MES")
        assert pos.quantity == -5
```

---

## Test Group 3: Concurrent Access

### Test 3.1: Position Reset During Fill Processing

```python
def test_position_reset_with_concurrent_fill():
    """
    Test: reset() called while execute_order() is in progress.
    Expected: no corruption, atomic guarantee via lock.
    """
    import threading
    import time

    tracker = VirtualPositionTracker()
    tracker.execute_order("ES", 2, "buy", 5800)

    corruption_detected = False
    fill_result = None
    reset_result = None

    def apply_fill():
        nonlocal fill_result
        time.sleep(0.001)  # Stagger slightly
        try:
            tracker.execute_order("ES", 1, "sell", 5850, order_id="FILL_001")
            fill_result = "success"
        except Exception as e:
            fill_result = f"error: {e}"

    def reset_pos():
        nonlocal reset_result
        try:
            tracker.reset()
            reset_result = "success"
        except Exception as e:
            reset_result = f"error: {e}"

    # Run concurrently
    t1 = threading.Thread(target=apply_fill)
    t2 = threading.Thread(target=reset_pos)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Assert no corruption
    assert fill_result is not None
    assert reset_result == "success"

    # Position should be valid (either reset empty or fill applied)
    pos = tracker.get_position("ES")
    if pos:
        assert isinstance(pos.quantity, (int, float))
        assert not (pos.quantity > 100 or pos.quantity < -100)  # No corruption


def test_concurrent_cancel_and_fill_processing():
    """
    Test: Cancel order while _process_fills() is running.
    Expected: no race condition, consistent state.
    """
    import threading

    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    state = MagicMock()
    state.symbol = "GC"
    state.tracker = VirtualPositionTracker()
    state.tracker.execute_order("GC", 1, "buy", 2000)

    manager.strategy_states = {"GC_01": state}

    # Mock: trade_search returns a fill
    mock_client.api.trade_search.return_value = {
        "success": True,
        "trades": [
            {
                "id": 555,
                "orderId": 12345,
                "symbol": "GC",
                "price": 2010,
                "quantity": 1,
                "side": 1  # buy
            }
        ]
    }

    # Mock: order_search returns filled order
    mock_client.api.order_search.return_value = {
        "success": True,
        "orders": [
            {
                "id": 12345,
                "status": BracketOrderManager.STATUS_FILLED
            }
        ]
    }

    errors = []

    def cancel_bracket():
        try:
            manager.cancel_bracket("GC_LONG_001")
        except Exception as e:
            errors.append(f"cancel: {e}")

    def process_fills():
        try:
            manager._process_fills()
        except Exception as e:
            errors.append(f"process_fills: {e}")

    t1 = threading.Thread(target=cancel_bracket)
    t2 = threading.Thread(target=process_fills)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent operations should not error: {errors}"
```

---

## Test Group 4: Bracket Pair Cleanup

### Test 4.1: Orphaned Bracket Cleanup

```python
def test_cleanup_orphaned_brackets_after_timeout():
    """
    Test: Bracket pairs older than timeout are removed.
    Expected: cleanup removes stale pairs.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    # Create old bracket
    old_time = datetime.now(timezone.utc) - manager._bracket_timeout - timedelta(seconds=10)
    old_pair = BracketPair(
        base_tag="OLD_ES_001",
        sl_order_id=111,
        tp_order_id=222,
        symbol="ES",
        active=True,
        created_at=old_time
    )
    old_pair.sl_terminal = True
    old_pair.tp_terminal = True

    # Create recent bracket
    recent_pair = BracketPair(
        base_tag="NEW_ES_001",
        sl_order_id=333,
        tp_order_id=444,
        symbol="ES"
    )

    manager.brackets = {
        "OLD_ES_001": old_pair,
        "NEW_ES_001": recent_pair
    }

    # Action
    removed_count = manager._cleanup_orphaned_brackets()

    # Assert
    assert removed_count == 1, "Should remove 1 old bracket"
    assert "OLD_ES_001" not in manager.brackets
    assert "NEW_ES_001" in manager.brackets


def test_no_cleanup_of_active_brackets():
    """
    Test: Active (non-terminal) brackets are not cleaned up.
    Expected: only truly orphaned (terminal but never removed) pairs cleaned.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    old_time = datetime.now(timezone.utc) - manager._bracket_timeout - timedelta(seconds=10)

    # Old but still active
    active_pair = BracketPair(
        base_tag="ACTIVE_OLD",
        sl_order_id=111,
        tp_order_id=222,
        symbol="ES",
        active=True,  # Still active
        created_at=old_time
    )
    # Note: sl_terminal=False, tp_terminal=False (not terminal)

    manager.brackets = {"ACTIVE_OLD": active_pair}

    removed_count = manager._cleanup_orphaned_brackets()

    assert removed_count == 0, "Should NOT remove active brackets"
    assert "ACTIVE_OLD" in manager.brackets
```

---

## Test Group 5: State Reconciliation

### Test 5.1: Fill on Zeroed Position is Orphaned

```python
def test_fill_on_zeroed_position_is_orphaned():
    """
    Test: Fill arrives for position that was just zeroed.
    Expected: fill is detected as orphaned and logged.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    state = MagicMock()
    state.symbol = "NQ"
    state.tracker = VirtualPositionTracker()
    # Position is FLAT

    manager.strategy_states = {"NQ_01": state}

    # Mock: trade_search returns fill
    mock_client.api.trade_search.return_value = {
        "success": True,
        "trades": [
            {
                "id": 999,
                "orderId": 45678,
                "symbol": "NQ",
                "price": 18000,
                "quantity": 2,
                "side": 0  # buy
            }
        ]
    }

    # Action
    result = manager._process_fills()

    # Assert
    assert len(result.get("orphaned", [])) > 0, "Should detect orphaned fill"
    orphaned = result["orphaned"][0]
    assert orphaned["reason"] == "position_flat_at_fill_time"
    assert orphaned["strategy"] == "NQ_01"


def test_fill_matches_position_direction():
    """
    Test: Verify fill matches position direction before applying.
    Expected: mismatched fills are rejected.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    state = MagicMock()
    state.symbol = "MES"
    state.tracker = VirtualPositionTracker()
    state.tracker.execute_order("MES", 5, "sell", 4950)  # SHORT position
    # Current position: -5

    manager.strategy_states = {"MES_01": state}

    # Mock: fill is a BUY (opposite to SHORT position)
    mock_client.api.trade_search.return_value = {
        "success": True,
        "trades": [
            {
                "id": 888,
                "orderId": 56789,
                "symbol": "MES",
                "price": 4940,
                "quantity": 3,
                "side": 0  # BUY - opposite direction!
            }
        ]
    }

    # Action
    result = manager._process_fills()

    # Assert
    assert len(result.get("errors", [])) > 0, "Should detect direction mismatch"
    error = result["errors"][0]
    assert error["reason"] == "direction_mismatch"

    # Position should NOT be updated
    pos = state.tracker.get_position("MES")
    assert pos.quantity == -5, "Position should remain unchanged"
```

---

## Test Group 6: Repair Atomicity

### Test 6.1: Full Repair Fails Safely

```python
def test_repair_position_desync_validation_phase():
    """
    Test: repair_position_desync validates all preconditions before executing any changes.
    Expected: if validation fails, no position is zeroed.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    state1 = MagicMock()
    state1.symbol = "ES"
    state1.tracker = VirtualPositionTracker()
    state1.tracker.execute_order("ES", 3, "buy", 5800)

    state2 = MagicMock()
    state2.symbol = "ES"
    state2.tracker = VirtualPositionTracker()
    state2.tracker.execute_order("ES", 2, "sell", 5810)

    manager.strategy_states = {"ES_01": state1, "ES_02": state2}

    # Register brackets for both
    manager.register_bracket("ES_LONG_001", sl_order_id=111, tp_order_id=222, symbol="ES")
    manager.register_bracket("ES_SHORT_001", sl_order_id=333, tp_order_id=444, symbol="ES")

    # Mock sync result: exchange shows 0, virtual shows +1
    sync_result = {
        "synced": False,
        "discrepancies": [
            {
                "symbol": "ES",
                "exchange_qty": 0,
                "virtual_qty": 1,  # +3 long + (-2) short = +1
            }
        ]
    }

    # Mock cancel failures for both strategies
    with patch.object(manager, "cancel_brackets_for_strategy") as mock_cancel:
        mock_cancel.side_effect = Exception("API timeout")

        # Action: call atomic repair
        result = manager.repair_position_desync_atomic(sync_result)

        # Assert: repair aborted, NO positions zeroed
        assert result["status"] == "aborted"
        assert len(result["warnings"]) > 0

        pos1 = state1.tracker.get_position("ES")
        pos2 = state2.tracker.get_position("ES")
        assert pos1.quantity == 3, "State 1 position should not be zeroed"
        assert pos2.quantity == -2, "State 2 position should not be zeroed"


def test_repair_rollback_on_partial_failure():
    """
    Test: If repair fails midway, attempt to restore previous state.
    Expected: no partial repairs left in place.
    """
    # (Implementation depends on transaction log/rollback capability)
    pass
```

---

## Test Group 7: Integration Tests

### Test 7.1: Full Repair Cycle with Real Sync Result

```python
def test_full_repair_cycle_happy_path():
    """
    Test: Complete repair cycle with successful cancel and zero.
    Expected: position zeroed, brackets removed, state clean.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    # Setup
    state = MagicMock()
    state.symbol = "ES"
    state.tracker = VirtualPositionTracker()
    state.tracker.execute_order("ES", 4, "buy", 5800)
    state.entry_price = 5800
    state.take_profit_price = 5850
    state.stop_loss_price = 5750
    state.entry_time = datetime.now()

    manager.strategy_states = {"ES_01": state}

    # Register bracket
    manager.register_bracket("ES_LONG_001", sl_order_id=111, tp_order_id=222, symbol="ES")

    # Mock: position sync detects excess
    with patch.object(manager, "check_position_sync") as mock_sync:
        mock_sync.return_value = {
            "synced": False,
            "discrepancies": [
                {
                    "symbol": "ES",
                    "exchange_qty": 0,
                    "virtual_qty": 4,
                }
            ],
            "exchange_positions": {"ES": 0},
            "virtual_positions": {"ES": 4}
        }

        # Mock: cancels succeed
        mock_client.api.order_cancel.return_value = {"success": True}
        mock_client.api.order_search.return_value = {
            "success": True,
            "orders": [
                {"id": 111, "status": BracketOrderManager.STATUS_CANCELLED},
                {"id": 222, "status": BracketOrderManager.STATUS_CANCELLED}
            ]
        }

        # Action
        result = manager.repair_position_desync()

        # Assert
        assert len(result["repairs_made"]) == 1
        assert result["repairs_made"][0]["strategy"] == "ES_01"
        assert result["repairs_made"][0]["old_qty"] == 4
        assert result["repairs_made"][0]["new_qty"] == 0

        # Position should be zeroed
        pos = state.tracker.get_position("ES")
        assert pos is None or pos.quantity == 0

        # State should be cleared
        assert state.entry_price is None
        assert state.take_profit_price is None
        assert state.stop_loss_price is None

        # Bracket should be removed
        assert "ES_LONG_001" not in manager.brackets


def test_repair_handles_mixed_strategies():
    """
    Test: Repair with multiple strategies, some succeed, some fail.
    Expected: successful repairs applied, failed ones listed in warnings.
    """
    # (Multi-strategy scenario as described in Scenario 6)
    pass
```

---

## Performance Tests

### Test P1: Cancel Confirmation Performance

```python
def test_cancel_confirmation_overhead():
    """
    Verify cancel confirmation doesn't significantly slow down operations.
    Expected: <100ms overhead per cancel.
    """
    import time

    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    # Mock immediate responses
    mock_client.api.order_cancel.return_value = {"success": True}
    mock_client.api.order_search.return_value = {
        "success": True,
        "orders": [{"id": 123, "status": BracketOrderManager.STATUS_CANCELLED}]
    }

    start = time.time()
    for i in range(10):
        manager._cancel_order_with_confirmation(100+i, "TP", f"ES_{i}")
    elapsed = time.time() - start

    assert elapsed < 1.0, f"10 cancels should take <1 second, took {elapsed}s"


def test_cleanup_performance():
    """
    Verify bracket cleanup scales well with many brackets.
    Expected: cleanup of 1000 brackets in <100ms.
    """
    import time

    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)

    # Create 1000 old orphaned brackets
    old_time = datetime.now(timezone.utc) - manager._bracket_timeout - timedelta(seconds=10)
    for i in range(1000):
        pair = BracketPair(
            base_tag=f"OLD_{i}",
            sl_order_id=2000+i,
            tp_order_id=3000+i,
            symbol="ES",
            created_at=old_time
        )
        pair.sl_terminal = True
        pair.tp_terminal = True
        manager.brackets[f"OLD_{i}"] = pair

    start = time.time()
    removed = manager._cleanup_orphaned_brackets()
    elapsed = time.time() - start

    assert removed == 1000
    assert elapsed < 0.1, f"Cleanup should take <100ms, took {elapsed*1000}ms"
```

---

## Edge Case Tests

### Test E1: Network Partition During Cancel

```python
def test_cancel_during_network_partition():
    """
    Test: Network is unavailable during cancel attempt.
    Expected: timeout handling, graceful degradation.
    """
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123, timeout=1.0)

    mock_client.api.order_cancel.side_effect = TimeoutError("Network timeout")
    mock_client.api.order_search.side_effect = TimeoutError("Network still down")

    # Should timeout gracefully, not hang forever
    with pytest.raises(TimeoutError):
        manager._cancel_order_with_confirmation(12345, "TP", "ES_001")


def test_repair_during_market_open():
    """
    Test: Position repair triggered when market is actively trading.
    Expected: no interference with active orders/fills.
    """
    # This is more of an integration test
    # Ensure repair doesn't block polling or new order placement
    pass
```

---

## Fixture and Helper Functions

```python
@pytest.fixture
def mock_bracket_manager():
    """Fixture providing a configured mock BracketOrderManager."""
    mock_client = MagicMock()
    manager = BracketOrderManager(mock_client, account_id=123)
    return manager, mock_client


@pytest.fixture
def mock_strategy_state():
    """Fixture providing a mock EnhancedStrategyState."""
    state = MagicMock()
    state.symbol = "ES"
    state.tracker = VirtualPositionTracker()
    state.entry_price = None
    state.take_profit_price = None
    state.stop_loss_price = None
    state.entry_time = None
    state.brackets_submitted = False
    return state


def assert_position_flat(tracker, symbol):
    """Helper: assert position is flat."""
    pos = tracker.get_position(symbol)
    assert pos is None or pos.quantity == 0, f"Position for {symbol} should be flat"


def assert_no_orphaned_brackets(manager, strategy_id):
    """Helper: assert no brackets remain for strategy."""
    remaining = manager._get_brackets_for_strategy(strategy_id)
    assert not remaining, f"Strategy {strategy_id} has orphaned brackets: {remaining}"
```

---

## Running the Test Suite

```bash
# Run all edge case tests
pytest tests/test_bracket_repair_edge_cases.py -v

# Run specific test group
pytest tests/test_bracket_repair_edge_cases.py::TestCancelConfirmation -v

# Run with coverage
pytest tests/test_bracket_repair_edge_cases.py --cov=tools.bracket_order_manager --cov-report=html

# Run performance tests
pytest tests/test_bracket_repair_edge_cases.py -v -m performance

# Run with threading checks (for concurrency tests)
pytest tests/test_bracket_repair_edge_cases.py::TestConcurrentAccess -v --tb=short
```

