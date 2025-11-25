"""
StrategyState - Independent State Container for Multi-Strategy Trading

This module provides a dataclass that encapsulates all state for one strategy instance,
ensuring complete isolation between strategies trading the same symbol.

Key Features:
- One instance per strategy
- Independent VirtualPositionTracker
- Isolated pending orders and P&L tracking
- Trade history for attribution
- No shared mutable state between strategies

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from lumibot.entities import Order
from lumibot.tools.virtual_position_tracker import VirtualPositionTracker


@dataclass
class StrategyState:
    """
    Complete state container for one strategy instance.

    This dataclass holds all state for a single strategy, including its own
    VirtualPositionTracker for independent position management. Each strategy
    maintains completely isolated state with no shared mutable references.

    Attributes:
        strategy_id: Unique identifier for this strategy
        symbol: Symbol this strategy trades (e.g., 'ES', 'NQ')
        params: Strategy-specific parameters (dict)
        contracts: Number of contracts per trade
        allowed_sessions: List of allowed trading sessions

        tracker: Independent VirtualPositionTracker instance
        pending_orders: List of orders pending submission
        last_signal: Last signal generated ('BUY', 'SELL', 'HOLD', None)
        last_signal_time: Timestamp of last signal generation

        total_pnl: Cumulative P&L for this strategy
        trade_history: List of completed trades
        win_count: Number of winning trades
        loss_count: Number of losing trades

        created_at: Timestamp when strategy was created
        last_updated: Timestamp of last state update

    Example:
        >>> from lumibot.tools.virtual_position_tracker import VirtualPositionTracker
        >>> state = StrategyState(
        ...     strategy_id='strat_1',
        ...     symbol='ES',
        ...     params={'fast_sma': 10, 'slow_sma': 20},
        ...     contracts=1
        ... )
        >>> state.tracker.execute_order('ES', 1, 'buy', 4500.00)
        >>> state.pending_orders.append(order)
    """

    # Required fields
    strategy_id: str
    symbol: str
    params: Dict[str, Any]
    contracts: int = 1
    allowed_sessions: List[str] = field(default_factory=lambda: ["New_York"])

    # Virtual position tracking (independent instance)
    tracker: VirtualPositionTracker = field(default_factory=VirtualPositionTracker)

    # Order management
    pending_orders: List[Order] = field(default_factory=list)
    last_signal: Optional[str] = None
    last_signal_time: Optional[datetime] = None

    # Performance tracking
    total_pnl: float = 0.0
    trade_history: List[Dict[str, Any]] = field(default_factory=list)
    win_count: int = 0
    loss_count: int = 0

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)

    # Logger
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__), repr=False)

    def has_pending_order(self) -> bool:
        """
        Check if strategy has any pending orders.

        Returns:
            True if pending orders exist, False otherwise
        """
        return len(self.pending_orders) > 0

    def get_pending_order(self) -> Optional[Order]:
        """
        Get the first pending order without removing it.

        Returns:
            First Order object or None if no pending orders
        """
        if self.has_pending_order():
            return self.pending_orders[0]
        return None

    def pop_pending_order(self) -> Optional[Order]:
        """
        Get and remove the first pending order.

        Returns:
            First Order object or None if no pending orders
        """
        if self.has_pending_order():
            return self.pending_orders.pop(0)
        return None

    def clear_pending_orders(self) -> int:
        """
        Clear all pending orders.

        Returns:
            Number of orders cleared
        """
        count = len(self.pending_orders)
        self.pending_orders.clear()
        return count

    def record_trade(self, entry_price: float, exit_price: float, quantity: float, timestamp: datetime = None) -> dict:
        """
        Record a completed trade for attribution.

        Args:
            entry_price: Entry price per contract
            exit_price: Exit price per contract
            quantity: Number of contracts (signed: positive=long, negative=short)
            timestamp: Trade timestamp (defaults to now)

        Returns:
            Trade record dictionary
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Calculate P&L with proper contract multiplier
        from custom_portfolio.data.futures_metadata import get_multiplier

        multiplier = get_multiplier(self.symbol)
        pnl = (exit_price - entry_price) * quantity * multiplier

        # Create trade record
        trade = {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timestamp": timestamp,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "multiplier": multiplier,
            "pnl": pnl,
        }

        # Update statistics
        self.trade_history.append(trade)
        self.total_pnl += pnl

        if pnl > 0:
            self.win_count += 1
        elif pnl < 0:
            self.loss_count += 1
        # pnl == 0 is neither win nor loss

        self.last_updated = datetime.now()

        return trade

    def calculate_win_rate(self) -> float:
        """
        Calculate win rate as percentage.

        Returns:
            Win rate as float (0.0 to 1.0), or 0.0 if no trades
        """
        total_trades = self.win_count + self.loss_count
        if total_trades == 0:
            return 0.0
        return self.win_count / total_trades

    def get_current_position_qty(self) -> float:
        """
        Get current virtual position quantity.

        Returns:
            Position quantity (signed: positive=long, negative=short, 0=flat)
        """
        pos = self.tracker.get_position(self.symbol)
        return pos.quantity if pos else 0.0

    def is_flat(self) -> bool:
        """
        Check if strategy has no open position.

        Returns:
            True if position is flat, False otherwise
        """
        return self.tracker.is_flat(self.symbol)

    def get_unrealized_pnl(self, current_price: float) -> float:
        """
        Calculate unrealized P&L for current position.

        Args:
            current_price: Current market price

        Returns:
            Unrealized P&L in dollars
        """
        pos = self.tracker.get_position(self.symbol)
        if pos is None:
            return 0.0
        return pos.calculate_pnl(current_price)

    def get_total_trade_count(self) -> int:
        """
        Get total number of completed trades.

        Returns:
            Total trade count
        """
        return len(self.trade_history)

    def get_average_pnl(self) -> float:
        """
        Calculate average P&L per trade.

        Returns:
            Average P&L, or 0.0 if no trades
        """
        if len(self.trade_history) == 0:
            return 0.0
        return self.total_pnl / len(self.trade_history)

    def get_summary(self) -> dict:
        """
        Get complete strategy state summary.

        Returns:
            Dictionary with strategy summary including:
                - strategy_id
                - symbol
                - current_position
                - total_pnl
                - trade_count
                - win_rate
                - average_pnl
        """
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "current_position": self.get_current_position_qty(),
            "is_flat": self.is_flat(),
            "total_pnl": self.total_pnl,
            "trade_count": self.get_total_trade_count(),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": self.calculate_win_rate(),
            "average_pnl": self.get_average_pnl(),
            "pending_orders": len(self.pending_orders),
            "last_signal": self.last_signal,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
        }

    def __repr__(self) -> str:
        """String representation of strategy state."""
        summary = self.get_summary()
        return (
            f"StrategyState("
            f"id={self.strategy_id}, "
            f"symbol={self.symbol}, "
            f"pos={summary['current_position']:+.1f}, "
            f"pnl=${self.total_pnl:+.2f}, "
            f"trades={summary['trade_count']}, "
            f"win_rate={summary['win_rate']:.1%})"
        )
