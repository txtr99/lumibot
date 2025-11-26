"""
Virtual Position Tracker for Unreliable Broker APIs

This module provides a virtual position tracking system that operates independently
of broker position APIs. It assumes all market orders fill immediately and tracks
positions locally.

This is particularly useful for brokers like TopStepX where the position API
doesn't reliably report positions created from API-placed orders.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Set

# Epsilon for floating point comparisons (1e-9 is effectively zero for position quantities)
POSITION_EPSILON = 1e-9

# Module-level tracking for symbols where multiplier defaulted to 1.0
# This allows end-of-run warnings about potentially incorrect P/L
_multiplier_defaults: Set[str] = set()


def get_multiplier_defaults() -> Set[str]:
    """Return symbols that had multiplier default to 1.0 during this session."""
    return _multiplier_defaults.copy()


def clear_multiplier_defaults() -> None:
    """Clear the multiplier defaults tracking (call at session start)."""
    _multiplier_defaults.clear()


@dataclass
class VirtualPosition:
    """Represents a virtual position tracked independently of broker."""

    symbol: str
    quantity: float  # Positive for long, negative for short
    avg_entry_price: float
    last_update: datetime
    entry_time: datetime
    total_cost: float = 0.0  # Total cost basis for averaging

    @property
    def side(self) -> str:
        """Return 'LONG', 'SHORT', or 'FLAT'."""
        if self.quantity > 0:
            return "LONG"
        elif self.quantity < 0:
            return "SHORT"
        else:
            return "FLAT"

    @property
    def exposure(self) -> float:
        """Return absolute quantity (exposure)."""
        return abs(self.quantity)

    def calculate_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L based on current price (includes multiplier when available)."""
        if self.quantity == 0:
            return 0.0
        try:
            from custom_portfolio.data.futures_metadata import get_multiplier

            multiplier = get_multiplier(self.symbol)
            # Track if multiplier returned default value of 1.0
            if multiplier == 1.0:
                _multiplier_defaults.add(self.symbol)
        except Exception:
            multiplier = 1.0
            _multiplier_defaults.add(self.symbol)
        return self.quantity * (current_price - self.avg_entry_price) * multiplier


class VirtualPositionTracker:
    """
    Tracks virtual positions independently of broker APIs.

    Features:
    - Assumes all market orders fill immediately
    - Tracks positions from strategy start (assumes flat initially)
    - Calculates P&L based on entry prices
    - Provides formatted display table
    """

    def __init__(self):
        """Initialize the virtual position tracker."""
        self.positions: Dict[str, VirtualPosition] = {}
        self.trade_history = []
        self.start_time = datetime.now()
        # Track processed order IDs to prevent double-counting
        self._processed_order_ids: Set[str] = set()

    def execute_order(
        self, symbol: str, quantity: float, side: str, price: float = None, order_id: str = None
    ) -> Optional[VirtualPosition]:
        """
        Execute a virtual order and update position.

        Args:
            symbol: Symbol to trade
            quantity: Number of contracts (always positive)
            side: "buy" or "sell"
            price: Fill price (if known), otherwise uses last known price
            order_id: Unique order ID for idempotency (prevents double-counting)

        Returns:
            Updated virtual position, or None if order was already processed
        """
        # Idempotency check - prevent double-counting if same order processed twice
        if order_id:
            if order_id in self._processed_order_ids:
                return self.positions.get(symbol)  # Return current position without update
            self._processed_order_ids.add(order_id)

        # Validate quantity
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")
        # Convert side to signed quantity
        signed_qty = quantity if side.lower() == "buy" else -quantity

        # Get current position or create new one
        if symbol not in self.positions:
            # New position
            self.positions[symbol] = VirtualPosition(
                symbol=symbol,
                quantity=signed_qty,
                avg_entry_price=price or 0.0,
                last_update=datetime.now(),
                entry_time=datetime.now(),
                total_cost=abs(signed_qty * (price or 0.0)),
            )
        else:
            # Update existing position
            pos = self.positions[symbol]
            old_qty = pos.quantity
            new_qty = old_qty + signed_qty

            # Calculate new average price if adding to position
            if abs(new_qty) > abs(old_qty):
                # Adding to position - calculate weighted average
                if price:
                    new_cost = abs(signed_qty * price)
                    pos.total_cost += new_cost
                    if abs(new_qty) > POSITION_EPSILON:
                        pos.avg_entry_price = pos.total_cost / abs(new_qty)
            elif abs(new_qty) < POSITION_EPSILON:
                # Position closed (use epsilon for floating point safety)
                pos.quantity = 0
                pos.total_cost = 0
                new_qty = 0  # Normalize to exactly zero
                # Keep last entry price for record
            else:
                # Reducing position - keep same average
                if abs(new_qty) > POSITION_EPSILON:
                    pos.total_cost = abs(new_qty * pos.avg_entry_price)

            pos.quantity = new_qty
            pos.last_update = datetime.now()

            # Reset entry time if switching sides
            if (old_qty > 0 and new_qty < 0) or (old_qty < 0 and new_qty > 0):
                pos.entry_time = datetime.now()
                if price:
                    pos.avg_entry_price = price
                    pos.total_cost = abs(new_qty * price)

        # Record trade
        self.trade_history.append(
            {
                "time": datetime.now(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "position_after": self.positions[symbol].quantity,
                "order_id": order_id,
            }
        )

        return self.positions[symbol]

    def was_order_processed(self, order_id: str) -> bool:
        """Check if an order ID has already been processed (for idempotency checks)."""
        return order_id in self._processed_order_ids

    def get_processed_order_count(self) -> int:
        """Get count of processed orders (for debugging/stats)."""
        return len(self._processed_order_ids)

    def get_position(self, symbol: str) -> Optional[VirtualPosition]:
        """Get current virtual position for a symbol."""
        return self.positions.get(symbol)

    def is_flat(self, symbol: str) -> bool:
        """Check if position is flat (no exposure)."""
        pos = self.positions.get(symbol)
        return pos is None or pos.quantity == 0

    def format_position_table(self, symbol: str, current_price: float = None) -> str:
        """
        Format a minimalist position info table.

        Args:
            symbol: Symbol to display
            current_price: Current market price for P&L calculation

        Returns:
            Formatted table string
        """
        pos = self.positions.get(symbol)

        if pos is None or pos.quantity == 0:
            # Flat position
            table = f"""
╔══════════════════════════════════╗
║     VIRTUAL POSITION: {symbol:<10} ║
╠══════════════════════════════════╣
║ Status:     FLAT (0)             ║
║ Entry:      N/A                  ║
║ P&L:        N/A                  ║
╚══════════════════════════════════╝"""
        else:
            # Active position
            pnl = pos.calculate_pnl(current_price) if current_price else 0.0
            pnl_str = f"${pnl:+.2f}" if current_price else "N/A"

            # Format quantity with sign
            qty_str = f"{pos.quantity:+.1f}"

            table = f"""
╔══════════════════════════════════╗
║     VIRTUAL POSITION: {symbol:<10} ║
╠══════════════════════════════════╣
║ Status:     {pos.side:<8} ({qty_str:<5}) ║
║ Entry:      ${pos.avg_entry_price:<8.2f}         ║
║ P&L:        {pnl_str:<10}       ║
╚══════════════════════════════════╝"""

        return table

    def format_simple_line(self, symbol: str, current_price: float = None) -> str:
        """
        Format a simple one-line position summary.

        Args:
            symbol: Symbol to display
            current_price: Current market price for P&L calculation

        Returns:
            One-line position summary
        """
        pos = self.positions.get(symbol)

        if pos is None or pos.quantity == 0:
            return f"📊 {symbol}: FLAT (0)"
        else:
            pnl = pos.calculate_pnl(current_price) if current_price else None
            pnl_str = f" | P&L: ${pnl:+.2f}" if pnl is not None else ""
            return f"📊 {symbol}: {pos.side} {pos.quantity:+.1f} @ ${pos.avg_entry_price:.2f}{pnl_str}"

    def reset(self):
        """Reset all virtual positions to flat."""
        self.positions.clear()
        self.trade_history.clear()
        self.start_time = datetime.now()

    def force_flat(self, symbol: str) -> Optional[VirtualPosition]:
        """
        Force a symbol's position to flat without recording a trade.

        Used during maintenance windows when the exchange auto-flattens positions.
        Does NOT record a trade since no actual fill occurred - only syncs
        virtual state with known exchange state.

        Args:
            symbol: Symbol to flatten

        Returns:
            The flattened position (with quantity=0), or None if no position existed
        """
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        # Zero out the position
        pos.quantity = 0
        pos.total_cost = 0
        pos.last_update = datetime.now()

        return pos
