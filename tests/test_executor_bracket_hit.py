"""
Tests for MultiStrategyExecutorEnhanced._check_bracket_hit()

This tests the backtest simulation of bracket order fills using bar data.
In backtest mode, we don't have real API to poll - we detect TP/SL hits
by checking if the bar's high/low crosses the bracket prices.

Test scenarios:
1. LONG position TP hit (high >= tp_price)
2. LONG position SL hit (low <= sl_price)
3. SHORT position TP hit (low <= tp_price)
4. SHORT position SL hit (high >= sl_price)
5. No hit when prices are between SL and TP
6. No hit when position is flat (qty=0)
7. SL takes precedence over TP on same bar (conservative assumption)
"""

from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

# Import the executor - we'll test _check_bracket_hit directly
from custom_portfolio.multi_strategy_executor_enhanced import (
    EnhancedStrategyState,
    MultiStrategyExecutorEnhanced,
)


class MockTracker:
    """Mock VirtualPositionTracker for testing."""

    def __init__(self, position_qty: float = 0.0):
        self._qty = position_qty

    def get_position(self, symbol: str):
        if self._qty == 0:
            return None
        return SimpleNamespace(quantity=self._qty)

    def execute_order(self, symbol, qty, side, price):
        if side == "buy":
            self._qty += qty
        else:
            self._qty -= qty


def make_strategy_state(
    symbol: str = "ES",
    position_qty: float = 0.0,
    tp_price: float = None,
    sl_price: float = None,
) -> EnhancedStrategyState:
    """Create a strategy state with mock tracker."""
    state = EnhancedStrategyState(
        strategy_id="test_strategy",
        symbol=symbol,
        params={"atr_period": 20},
        contracts=1,
        allowed_sessions=["New_York"],
    )
    state.tracker = MockTracker(position_qty)
    state.take_profit_price = tp_price
    state.stop_loss_price = sl_price
    return state


def make_bar_data(
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
) -> pd.DataFrame:
    """Create a single-bar DataFrame for testing."""
    return pd.DataFrame(
        {
            "open": [open_price],
            "high": [high_price],
            "low": [low_price],
            "close": [close_price],
        },
        index=[datetime.now()],
    )


class TestCheckBracketHit:
    """Test suite for _check_bracket_hit method."""

    @pytest.fixture
    def executor(self):
        """Create a minimal executor for testing."""
        # Minimal config - we only need the _check_bracket_hit method
        executor = MultiStrategyExecutorEnhanced(
            broker=None,
            data_source=None,
            calendar=None,
            strategy_configs=[],
            simulate_fills=True,
        )
        return executor

    # ==================== LONG POSITION TESTS ====================

    def test_long_tp_hit_high_exceeds_tp(self, executor):
        """LONG: TP should trigger when bar high >= tp_price."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)
        # Bar high (4105) exceeds TP (4100)
        data = make_bar_data(open_price=4080.0, high_price=4105.0, low_price=4075.0, close_price=4095.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4100.0  # Fill at TP price, not bar high
        assert reason == "bracket_tp"

    def test_long_tp_hit_high_equals_tp(self, executor):
        """LONG: TP should trigger when bar high == tp_price exactly."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)
        data = make_bar_data(open_price=4080.0, high_price=4100.0, low_price=4075.0, close_price=4095.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4100.0
        assert reason == "bracket_tp"

    def test_long_sl_hit_low_below_sl(self, executor):
        """LONG: SL should trigger when bar low <= sl_price."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)
        # Bar low (4045) below SL (4050)
        data = make_bar_data(open_price=4080.0, high_price=4090.0, low_price=4045.0, close_price=4055.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4050.0  # Fill at SL price, not bar low
        assert reason == "bracket_sl"

    def test_long_sl_hit_low_equals_sl(self, executor):
        """LONG: SL should trigger when bar low == sl_price exactly."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)
        data = make_bar_data(open_price=4080.0, high_price=4090.0, low_price=4050.0, close_price=4060.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4050.0
        assert reason == "bracket_sl"

    def test_long_no_hit_price_between_brackets(self, executor):
        """LONG: No hit when price stays between SL and TP."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)
        # Bar stays entirely between SL and TP
        data = make_bar_data(open_price=4070.0, high_price=4090.0, low_price=4060.0, close_price=4075.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is False
        assert price is None
        assert reason is None

    # ==================== SHORT POSITION TESTS ====================

    def test_short_tp_hit_low_below_tp(self, executor):
        """SHORT: TP should trigger when bar low <= tp_price."""
        state = make_strategy_state(position_qty=-1.0, tp_price=4000.0, sl_price=4050.0)
        # For SHORT: TP is below entry, SL is above entry
        # Bar low (3995) below TP (4000)
        data = make_bar_data(open_price=4020.0, high_price=4030.0, low_price=3995.0, close_price=4005.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4000.0
        assert reason == "bracket_tp"

    def test_short_tp_hit_low_equals_tp(self, executor):
        """SHORT: TP should trigger when bar low == tp_price exactly."""
        state = make_strategy_state(position_qty=-1.0, tp_price=4000.0, sl_price=4050.0)
        data = make_bar_data(open_price=4020.0, high_price=4030.0, low_price=4000.0, close_price=4010.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4000.0
        assert reason == "bracket_tp"

    def test_short_sl_hit_high_above_sl(self, executor):
        """SHORT: SL should trigger when bar high >= sl_price."""
        state = make_strategy_state(position_qty=-1.0, tp_price=4000.0, sl_price=4050.0)
        # Bar high (4055) above SL (4050)
        data = make_bar_data(open_price=4020.0, high_price=4055.0, low_price=4015.0, close_price=4045.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4050.0
        assert reason == "bracket_sl"

    def test_short_sl_hit_high_equals_sl(self, executor):
        """SHORT: SL should trigger when bar high == sl_price exactly."""
        state = make_strategy_state(position_qty=-1.0, tp_price=4000.0, sl_price=4050.0)
        data = make_bar_data(open_price=4020.0, high_price=4050.0, low_price=4015.0, close_price=4040.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4050.0
        assert reason == "bracket_sl"

    def test_short_no_hit_price_between_brackets(self, executor):
        """SHORT: No hit when price stays between SL and TP."""
        state = make_strategy_state(position_qty=-1.0, tp_price=4000.0, sl_price=4050.0)
        # Bar stays entirely between TP and SL
        data = make_bar_data(open_price=4020.0, high_price=4040.0, low_price=4010.0, close_price=4025.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is False
        assert price is None
        assert reason is None

    # ==================== EDGE CASES ====================

    def test_no_hit_when_flat(self, executor):
        """No bracket check when position is flat (qty=0)."""
        state = make_strategy_state(position_qty=0.0, tp_price=4100.0, sl_price=4050.0)
        data = make_bar_data(open_price=4000.0, high_price=4200.0, low_price=3900.0, close_price=4100.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is False
        assert price is None
        assert reason is None

    def test_no_hit_when_no_data(self, executor):
        """No bracket check when market data is empty."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)
        data = pd.DataFrame()  # Empty

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is False
        assert price is None
        assert reason is None

    def test_no_hit_when_data_is_none(self, executor):
        """No bracket check when market data is None."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)

        hit, price, reason = executor._check_bracket_hit(state, None)

        assert hit is False
        assert price is None
        assert reason is None

    def test_long_sl_priority_over_tp_same_bar(self, executor):
        """LONG: When both SL and TP could trigger on same bar, SL wins (conservative)."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=4050.0)
        # Bar hits BOTH SL (low=4040 < 4050) and TP (high=4110 > 4100)
        data = make_bar_data(open_price=4075.0, high_price=4110.0, low_price=4040.0, close_price=4080.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        # Current implementation checks SL first, so SL should win
        assert hit is True
        assert price == 4050.0
        assert reason == "bracket_sl"

    def test_short_sl_priority_over_tp_same_bar(self, executor):
        """SHORT: When both SL and TP could trigger on same bar, SL wins (conservative)."""
        state = make_strategy_state(position_qty=-1.0, tp_price=4000.0, sl_price=4050.0)
        # Bar hits BOTH SL (high=4060 > 4050) and TP (low=3990 < 4000)
        data = make_bar_data(open_price=4025.0, high_price=4060.0, low_price=3990.0, close_price=4010.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        # Current implementation checks SL first, so SL should win
        assert hit is True
        assert price == 4050.0
        assert reason == "bracket_sl"

    def test_no_sl_price_only_tp(self, executor):
        """LONG: Only TP set, no SL - should only check TP."""
        state = make_strategy_state(position_qty=1.0, tp_price=4100.0, sl_price=None)
        data = make_bar_data(open_price=4080.0, high_price=4105.0, low_price=4070.0, close_price=4095.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4100.0
        assert reason == "bracket_tp"

    def test_no_tp_price_only_sl(self, executor):
        """LONG: Only SL set, no TP - should only check SL."""
        state = make_strategy_state(position_qty=1.0, tp_price=None, sl_price=4050.0)
        data = make_bar_data(open_price=4080.0, high_price=4090.0, low_price=4045.0, close_price=4055.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is True
        assert price == 4050.0
        assert reason == "bracket_sl"

    def test_neither_tp_nor_sl_set(self, executor):
        """No bracket prices set - should return no hit."""
        state = make_strategy_state(position_qty=1.0, tp_price=None, sl_price=None)
        data = make_bar_data(open_price=4000.0, high_price=5000.0, low_price=3000.0, close_price=4000.0)

        hit, price, reason = executor._check_bracket_hit(state, data)

        assert hit is False
        assert price is None
        assert reason is None
