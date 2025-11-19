import os
import time

import pandas as pd
import pytz

from lumibot.credentials import IS_BACKTESTING
from lumibot.entities import Asset, Order, TradingFee
from lumibot.strategies.strategy import Strategy
from lumibot.tools.trading_calendar import TradingCalendar
from lumibot.traders import Trader

# Timezone constants for trading windows
MOUNTAIN_TZ = pytz.timezone("America/Denver")  # TopStepX timezone
EASTERN_TZ = pytz.timezone("America/New_York")  # NYSE/CME settlement
CHICAGO_TZ = pytz.timezone("America/Chicago")  # CME local time

# Session definitions (all times in Mountain Time 24-hour format)
TRADING_SESSIONS = {
    "24/7": {
        "start": "00:00",  # Midnight MT
        "stop_new_orders": "23:59",  # 11:59 PM MT
        "force_flat": "23:59",  # 11:59 PM MT
        "description": "24/7 trading - follows platform rules only (no session restrictions)",
    },
    "Australia": {
        "start": "17:00",  # 5:00 PM MT
        "stop_new_orders": "01:30",  # 1:30 AM MT (30-min buffer)
        "force_flat": "02:00",  # 2:00 AM MT
        "description": "Sydney/ASX session",
    },
    "Asia": {
        "start": "17:00",  # 5:00 PM MT
        "stop_new_orders": "01:30",  # 1:30 AM MT (30-min buffer)
        "force_flat": "02:00",  # 2:00 AM MT
        "description": "Tokyo, Hong Kong, Singapore combined",
    },
    "London": {
        "start": "02:00",  # 2:00 AM MT
        "stop_new_orders": "08:30",  # 8:30 AM MT (30-min buffer)
        "force_flat": "09:00",  # 9:00 AM MT
        "description": "London/European session",
    },
    "New_York": {
        "start": "07:30",  # 7:30 AM MT
        "stop_new_orders": "13:30",  # 1:30 PM MT
        "force_flat": "14:05",  # 2:05 PM MT (TopStepX deadline)
        "description": "CME/NYMEX peak hours",
    },
}

# TopStepX platform restrictions (ALWAYS enforced, overrides session settings)
TOPSTEPX_PLATFORM = {
    "daily_force_flat": "14:05",  # 2:05 PM MT (5-min buffer before 2:10 PM hard deadline)
    "daily_stop_new_orders": "13:30",  # 1:30 PM MT (35-min buffer)
    "daily_resume": "16:05",  # 4:05 PM MT (5-min buffer after 4:00 PM platform open)
    "weekend_close": {"day": 4, "time": "14:05"},  # Friday 2:05 PM MT
    "weekend_open": {"day": 6, "time": "16:05"},  # Sunday 4:05 PM MT
}

# Instrument → Sessions mapping (symbol root to list of allowed sessions)
SESSION_DEFAULTS = {
    # Forex - Australian & New Zealand dollars (all sessions)
    "6A": ["Australia", "Asia", "London", "New_York"],  # AUD/USD
    "6N": ["Australia", "Asia", "London", "New_York"],  # NZD/USD
    # Metals (all sessions)
    "GC": ["Australia", "Asia", "London", "New_York"],  # Gold
    "MGC": ["Australia", "Asia", "London", "New_York"],  # Micro Gold
    # Forex - European (London + NY only)
    "6B": ["London", "New_York"],  # GBP/USD
    "6E": ["London", "New_York"],  # EUR/USD
    # US Equity Indices (NY only)
    "ES": ["New_York"],  # E-mini S&P 500
    "MES": ["New_York"],  # Micro E-mini S&P 500
    "NQ": ["New_York"],  # E-mini NASDAQ
    "MNQ": ["New_York"],  # Micro E-mini NASDAQ
    "YM": ["New_York"],  # E-mini Dow
    "MYM": ["New_York"],  # Micro E-mini Dow
    # Energy (London + NY)
    "CL": ["London", "New_York"],  # Crude Oil
    "MCL": ["London", "New_York"],  # Micro Crude Oil
    "NG": ["London", "New_York"],  # Natural Gas
}


def get_bool_env(env_var_name, default=True):
    """Helper function to convert env var to boolean, defaulting to True.

    Recognizes 'true', '1', 'yes', 'on' as True (case-insensitive).
    Recognizes 'false', '0', 'no', 'off' as False (case-insensitive).
    Returns default value if env var is not set or has an unrecognized value.
    """
    value = os.getenv(env_var_name)
    if value is None:
        return default
    value_lower = value.lower().strip()
    if value_lower in ("true", "1", "yes", "on"):
        return True
    elif value_lower in ("false", "0", "no", "off"):
        return False
    else:
        # Unrecognized value, return default
        return default


class EvenMinuteTradingStrategy(Strategy):
    """
    Time-based futures trading strategy that alternates between long and short positions.

    - Even minutes: Go long (target +1 contract)
    - Odd minutes: Go short (target -1 contract)

    Ensures flat position before switching directions with wait-and-confirm logic.
    Maximum exposure: +1 (long) or -1 (short) only.
    """

    parameters = {
        "symbol": "MGC",  # Futures symbol to trade
        "contracts_to_trade": 1,
        "allowed_sessions": ["24/7"],  # Use 24/7 to trade anytime platform allows (TopStepX rules only)
    }

    def initialize(self):
        # Run every minute to align with the trading timeframe
        self.sleeptime = "1M"
        # Futures trade nearly around the clock during the week
        self.set_market("24/5")
        # Store the contract asset in self.vars so it persists safely
        if not hasattr(self.vars, "contract_asset"):
            symbol = self.parameters["symbol"]
            self.vars.contract_asset = Asset(symbol, asset_type=Asset.AssetType.CONT_FUTURE)

        # Initialize virtual position tracker (independent of broker)
        if not hasattr(self.vars, "virtual_tracker"):
            from lumibot.tools.virtual_position_tracker import VirtualPositionTracker

            self.vars.virtual_tracker = VirtualPositionTracker()
            self.log_message("🎯 Virtual position tracker initialized (starting FLAT)", color="cyan")

        # Initialize order tracking DataFrame
        if not hasattr(self.vars, "order_history"):
            self.vars.order_history = pd.DataFrame(
                columns=[
                    "timestamp",
                    "order_id",
                    "position_id",
                    "side",
                    "quantity",
                    "initial_status",
                    "final_status",
                    "fill_price",
                    "minute",
                ]
            )

        # Initialize rejected orders log file
        if not hasattr(self.vars, "rejected_orders_log_path"):
            # Get the absolute path to this strategy file
            strategy_file = os.path.abspath(__file__)
            # Create log file in the same directory as lumibot_fork root
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(strategy_file)))
            self.vars.rejected_orders_log_path = os.path.join(project_root, "rejected_orders.log")

            # Log the path for reference
            self.log_message(f"Rejected orders will be logged to: {self.vars.rejected_orders_log_path}", color="cyan")

        # Initialize TradingCalendar (reusable calendar-based trading window manager)
        if not hasattr(self.vars, "calendar"):
            self.vars.calendar = TradingCalendar(MOUNTAIN_TZ, TOPSTEPX_PLATFORM)
            self.vars.calendar.register_sessions(TRADING_SESSIONS)
            self.vars.calendar.map_symbols(SESSION_DEFAULTS)
            self.log_message("Trading calendar initialized with platform and session restrictions", color="cyan")

    def _submit_order_and_wait_for_fill(
        self, asset, quantity, side, symbol, current_minute, purpose, max_wait_seconds=3
    ):
        """
        Submit an order and wait for fill confirmation.

        Args:
            asset: The asset to trade
            quantity: Number of contracts to trade
            side: Order.OrderSide.BUY or Order.OrderSide.SELL
            symbol: Symbol name for logging
            current_minute: Current minute for tracking
            purpose: Description of order purpose (e.g., 'Long Entry', 'Short Cover')
            max_wait_seconds: Maximum seconds to wait for fill (default 3)

        Returns:
            tuple: (success: bool, order_id: str, fill_price: float or None)
        """
        # Create and submit order
        order = self.create_order(asset, quantity, side)
        submitted_order = self.submit_order(order)

        # Extract order ID and initial status
        order_id = submitted_order.identifier if hasattr(submitted_order, "identifier") else "UNKNOWN"
        initial_status = submitted_order.status if hasattr(submitted_order, "status") else "UNKNOWN"

        self.log_message(
            f"[{purpose}] Order submitted: ID={order_id}, Side={side}, Qty={quantity}, "
            f"Initial Status={initial_status}",
            color="cyan",
        )

        # Check if order was rejected immediately
        if initial_status in ["error", "rejected", "ERROR", "REJECTED"]:
            # Order was rejected by broker - log it with full details
            order_details = {
                "side": str(side),
                "quantity": quantity,
                "symbol": symbol,
                "current_minute": current_minute,
            }

            # Call the rejected order logger
            self._log_rejected_order(order_details, None, purpose, submitted_order)

            # Return failure immediately - no need to wait
            return False, order_id, None

        # Poll for order fill by checking POSITION (order status is cached and unreliable!)
        # Market orders fill immediately, so position should appear within a few seconds
        max_retries = 10
        retry_interval = 1  # Check every 1 second
        final_status = None
        fill_price = None
        position_found = False
        found_position = None  # Save the position object when found

        for attempt in range(1, max_retries + 1):
            time.sleep(retry_interval)

            try:
                # FORCE FRESH position fetch from broker API (not cached!)
                # self.get_position() returns cached data - use broker's _pull_position() instead
                position = self.broker._pull_position(self, asset)
                current_qty = position.quantity if position is not None else 0

                # For buy orders, position should be positive; for sell, negative
                expected_sign = 1 if side == "buy" else -1
                position_matches = (current_qty * expected_sign) > 0

                if position_matches:
                    position_found = True
                    found_position = position  # Save for getting position_id later
                    final_status = "fill"
                    fill_price = position.avg_fill_price if hasattr(position, "avg_fill_price") else None

                    self.log_message(
                        f"[{purpose}] ✅ Position detected after {attempt * retry_interval}s: "
                        f"Qty={current_qty}, Fill Price={fill_price}",
                        color="green",
                    )
                    break
                else:
                    # Position not found yet or wrong direction
                    self.log_message(
                        f"[{purpose}] Attempt {attempt}/{max_retries}: "
                        f"Checking position... (current qty={current_qty})",
                        color="cyan",
                    )

            except Exception as e:
                self.log_message(
                    f"[{purpose}] Error checking position (attempt {attempt}/{max_retries}): {e}", color="yellow"
                )
                final_status = "ERROR"
                fill_price = None

        # If we exhausted all retries and no position found
        if not position_found:
            self.log_message(
                f"[{purpose}] ⚠️ No position found after {max_retries * retry_interval}s - order may have been rejected",
                color="yellow",
            )
            final_status = "error"

        # Get position ID from the found position (or try cached if not found)
        position_id = None
        if found_position and hasattr(found_position, "identifier"):
            position_id = found_position.identifier
        else:
            # Fallback to cached position if fresh one wasn't found
            cached_pos = self.get_position(asset)
            if cached_pos and hasattr(cached_pos, "identifier"):
                position_id = cached_pos.identifier

        # Log the final order status summary
        self.log_message(
            f"[{purpose}] Final status: ID={order_id}, Status={final_status}, "
            f"Fill Price={fill_price}, Position ID={position_id}",
            color="green" if final_status in ["fill", "filled"] else "yellow",
        )

        # Store in order history
        new_row = pd.DataFrame(
            [
                {
                    "timestamp": time.time(),
                    "order_id": order_id,
                    "position_id": position_id,
                    "side": str(side),
                    "quantity": quantity,
                    "initial_status": initial_status,
                    "final_status": final_status,
                    "fill_price": fill_price,
                    "minute": current_minute,
                }
            ]
        )
        # Avoid FutureWarning by checking if DataFrame is empty first
        if self.vars.order_history.empty:
            self.vars.order_history = new_row
        else:
            self.vars.order_history = pd.concat([self.vars.order_history, new_row], ignore_index=True)

        # Return success status (Lumibot uses lowercase status values)
        is_filled = final_status in ["fill", "filled", "partial_fill", "partially_filled"]
        return is_filled, order_id, fill_price

    def _wait_and_confirm_flat(self, asset, symbol, max_attempts=3):
        """
        Wait and confirm that position is flat (0) before proceeding.

        After closing a position, waits 2 seconds, then checks position.
        If not flat, waits 2 more seconds and checks again (up to max_attempts times).

        Returns:
            bool: True if position is confirmed flat, False otherwise
        """
        for attempt in range(1, max_attempts + 1):
            # Wait 2 seconds between checks
            time.sleep(2)

            # Refresh position
            position = self.get_position(asset)
            current_qty = position.quantity if position is not None else 0
            position_id = position.identifier if (position and hasattr(position, "identifier")) else None

            # Log diagnostic info
            self.log_message(
                f"[Position Check] Attempt {attempt}/{max_attempts}: Qty={current_qty}, " f"Position ID={position_id}",
                color="cyan",
            )

            if current_qty == 0:
                self.log_message(
                    f"✓ Position confirmed flat after {attempt} check(s).",
                    color="green",
                )
                return True
            else:
                self.log_message(
                    f"Position not flat yet (qty: {current_qty}), attempt {attempt}/{max_attempts}.",
                    color="yellow",
                )

        # Position still not flat after all attempts
        self.log_message(
            f"⚠️ Position still not flat after {max_attempts} attempts (qty={current_qty}). "
            f"Skipping entry for this minute.",
            color="red",
        )
        return False

    def _log_rejected_order(self, order_details, error_response, purpose, submitted_order):
        """
        Log rejected order to both console and file with full details.

        Args:
            order_details: Dict with side, quantity, symbol, current_minute
            error_response: Broker's error response dict (if available from exception)
            purpose: String describing order intent (e.g., 'Long Entry')
            submitted_order: The order object returned from submit_order()
        """
        import ast
        import json
        from datetime import datetime as dt

        # Get current timestamp in Mountain Time
        now_mt = dt.now(MOUNTAIN_TZ)
        timestamp_str = now_mt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # milliseconds
        timezone_str = now_mt.strftime("%Z")

        # Extract error details from order object or error_response
        if hasattr(submitted_order, "error") and submitted_order.error:
            # Error might be stored in order.error
            error_info = submitted_order.error
        else:
            error_info = error_response or "No error details available"

        # Try to parse error if it's a string containing JSON or dict representation
        if isinstance(error_info, str):
            # Check if it's a ProjectX error format: "Failed to place order: {...}"
            if "Failed to place order: " in error_info:
                try:
                    # Extract the dict portion after the prefix
                    dict_str = error_info.split("Failed to place order: ", 1)[1]
                    # Use ast.literal_eval to safely parse the dict string
                    error_dict = ast.literal_eval(dict_str)
                except (ValueError, SyntaxError) as e:
                    # If parsing fails, store as raw error
                    error_dict = {"raw_error": error_info, "parse_error": str(e)}
            else:
                # Try JSON parsing
                try:
                    error_dict = json.loads(error_info)
                except (json.JSONDecodeError, ValueError):
                    error_dict = {"raw_error": error_info}
        elif isinstance(error_info, dict):
            error_dict = error_info
        else:
            error_dict = {"error": str(error_info)}

        # Extract key fields
        order_id = error_dict.get("orderId", "UNKNOWN")
        success = error_dict.get("success", False)
        error_code = error_dict.get("errorCode", "N/A")
        error_message = error_dict.get("errorMessage", "No message")

        # Get strategy name and file path
        strategy_name = self.__class__.__name__
        strategy_file = os.path.abspath(__file__)

        # Console logging with high visibility
        console_msg = f"""
{'🚨' * 32}
⚠️  ORDER REJECTED BY BROKER
{'🚨' * 32}
Strategy: {strategy_name}
Purpose: {purpose}
Symbol: {order_details['symbol']}
Side: {order_details['side']}
Quantity: {order_details['quantity']}

Broker Response:
  Order ID: {order_id}
  Success: {success}
  Error Code: {error_code}
  Error Message: {error_message}

Timestamp: {timestamp_str} {timezone_str}
Logged to: {self.vars.rejected_orders_log_path}
{'🚨' * 32}
"""
        self.log_message(console_msg, color="red")

        # File logging with structured format
        file_entry = f"""
{'=' * 80}
[REJECTED ORDER] {timestamp_str} {timezone_str}
Strategy: {strategy_name}
File: {strategy_file}
{'-' * 80}
Order Details:
  Side: {order_details['side']}
  Quantity: {order_details['quantity']}
  Symbol: {order_details['symbol']}
  Purpose: {purpose}
  Minute: {order_details.get('current_minute', 'N/A')}

Broker Response:
  Order ID: {order_id}
  Success: {success}
  Error Code: {error_code}
  Error Message: {error_message}

Full Response:
{json.dumps(error_dict, indent=2)}
{'=' * 80}

"""

        # Append to log file
        try:
            with open(self.vars.rejected_orders_log_path, "a") as f:
                f.write(file_entry)
        except Exception as e:
            self.log_message(f"Failed to write to rejected_orders.log: {e}", color="red")

    def on_trading_iteration(self):
        asset = self.vars.contract_asset
        symbol = self.parameters["symbol"]
        trade_qty = self.parameters["contracts_to_trade"]

        # Get current datetime
        current_dt = self.get_datetime()

        # Get VIRTUAL position (independent of broker)
        virtual_pos = self.vars.virtual_tracker.get_position(symbol)
        virtual_qty = virtual_pos.quantity if virtual_pos else 0

        # Get current price for display
        last_price = self.get_last_price(asset)

        # Display virtual position table at the beginning of each iteration
        if last_price:
            position_table = self.vars.virtual_tracker.format_position_table(symbol, last_price)
        else:
            position_table = self.vars.virtual_tracker.format_position_table(symbol)

        # Show the position table
        for line in position_table.split("\n"):
            self.log_message(line, color="cyan")

        # Get calendar status (single call with all timing info)
        # Use virtual position for calendar status
        status = self.vars.calendar.get_status(
            symbol, current_dt, virtual_qty, None, allowed_sessions=self.parameters.get("allowed_sessions")
        )

        # Extract minute info for trading logic
        current_minute = status.minute
        is_even_minute = status.is_even_minute

        # Display compact visual status with countdown timers
        self.vars.calendar.log_status(status, self.log_message)

        # Log virtual position state
        self.log_message(
            f"🔍 VIRTUAL POSITION: qty={virtual_qty:.1f}, "
            f"side={'FLAT' if virtual_qty == 0 else ('LONG' if virtual_qty > 0 else 'SHORT')}",
            color="magenta",
        )

        # 🔍 DEBUG: Log ALL cached orders to see what's in the framework's memory
        all_orders = self.get_orders()
        if all_orders:
            self.log_message(f"🔍 ALL CACHED ORDERS ({len(all_orders)} total):", color="magenta")
            for i, ord in enumerate(all_orders[-5:], 1):  # Show last 5 orders to avoid spam
                self.log_message(
                    f"  [{i}] ID={ord.identifier}, status={ord.status}, "
                    f"side={ord.side}, qty={ord.quantity}, "
                    f"filled={ord.is_filled() if hasattr(ord, 'is_filled') else 'N/A'}",
                    color="cyan",
                )
        else:
            self.log_message("🔍 ALL CACHED ORDERS: Empty (no orders in cache)", color="magenta")

        # CHECK PENDING ORDERS FROM PREVIOUS ITERATION (test if cache updates)
        if not hasattr(self.vars, "pending_orders"):
            self.vars.pending_orders = []

        if self.vars.pending_orders:
            self.log_message(
                f"📋 Checking {len(self.vars.pending_orders)} pending order(s) from previous iteration...",
                color="yellow",
            )
            for pending in self.vars.pending_orders[:]:  # Copy list to allow removal
                order_id = pending["order_id"]
                submit_time = pending["submit_time"]
                purpose = pending["purpose"]

                # Check cached order status (should update every minute via sync_broker)
                cached_order = self.get_order(order_id)
                if cached_order:
                    self.log_message(
                        f"  Order {order_id} ({purpose}): status={cached_order.status}, "
                        f"fill_price={getattr(cached_order, 'avg_fill_price', None)}, "
                        f"age={int(current_dt.timestamp() - submit_time)}s",
                        color="cyan",
                    )

                    # Check if filled
                    if cached_order.is_filled():
                        fill_price = cached_order.avg_fill_price
                        self.log_message(f"✅ Cached order shows FILLED! {purpose}: {fill_price}", color="green")
                        self.vars.pending_orders.remove(pending)
                    elif (current_dt.timestamp() - submit_time) > 120:  # 2 minutes old
                        self.log_message(
                            f"⏱️ Order {order_id} still pending after 2min, removing from tracking",
                            color="yellow",
                        )
                        self.vars.pending_orders.remove(pending)
                else:
                    self.log_message(f"  Order {order_id} not found in cache", color="yellow")

        # Force close if required by calendar (needs price data)
        if status.must_be_flat and virtual_qty != 0:
            self.log_message(f"⚠️ FORCE CLOSING POSITION: {status.close_reason}", color="red")
            # Get price for force close order
            last_price = self.get_last_price(asset)
            if last_price is None:
                self.log_message(f"⚠️ Cannot force close - no price data available for {symbol}", color="red")
                return
            # Submit market order to close position
            close_side = Order.OrderSide.SELL if virtual_qty > 0 else Order.OrderSide.BUY
            is_filled, order_id, fill_price = self._submit_order_and_wait_for_fill(
                asset, abs(virtual_qty), close_side, symbol, status.minute, "Force Close"
            )
            if is_filled:
                self.log_message(f"✓ Position force closed: {abs(virtual_qty)} contracts @ {fill_price}", color="green")
                # Update virtual position tracker
                self.vars.virtual_tracker.execute_order(symbol, abs(virtual_qty), close_side.value.lower(), fill_price)
            else:
                self.log_message(f"⚠️ Force close failed (Order ID={order_id})", color="red")
            return  # Exit after force close

        # Skip if platform doesn't allow trading
        if not status.platform_open:
            self.log_message(f"🚫 Skipping iteration: {status.platform_reason}", color="yellow")
            return

        # Skip if can't enter new orders (but can hold existing positions)
        if not status.can_enter_orders and virtual_qty == 0:
            self.log_message(f"🚫 No new orders allowed: {status.session_reason}", color="yellow")
            return

        # Get current price for trading logic
        last_price = self.get_last_price(asset)
        if last_price is None:
            self.log_message(f"No price data available for {symbol} yet, skipping this minute.", color="yellow")
            return

        # Add price to log
        self.log_message(f"  Current Price: {last_price:.2f}", color="cyan")

        # Plot the latest price
        self.add_line(symbol, last_price, color="black", width=2, detail_text="Latest price")

        # SIMPLIFIED TRADING LOGIC: Only trade when FLAT
        # This tests the theory that Lumibot updates cached positions/orders every 60 seconds

        # 🔍 DEBUG: Log trading decision logic entry point
        self.log_message(
            f"🔍 TRADING LOGIC: minute={current_minute:02d}, is_even={is_even_minute}, "
            f"virtual_qty={virtual_qty}, checking conditions...",
            color="magenta",
        )

        # Even minutes: Go long (if flat or short)
        if is_even_minute and virtual_qty <= 0:
            # Determine action based on current position
            if virtual_qty == 0:
                action = "ENTER LONG from FLAT"
                exit_qty = 0
            else:  # virtual_qty < 0 (SHORT)
                action = f"EXIT SHORT ({abs(virtual_qty)}) and GO LONG"
                exit_qty = abs(virtual_qty)

            self.log_message(
                f"🔍 EVEN MINUTE CHECK: qty={virtual_qty}, Action: {action}",
                color="green",
            )

            # If SHORT, first exit the short position
            if exit_qty > 0:
                exit_order = self.create_order(asset, exit_qty, Order.OrderSide.BUY)
                exit_submitted = self.submit_order(exit_order)
                exit_id = exit_submitted.identifier if hasattr(exit_submitted, "identifier") else "UNKNOWN"
                self.log_message(f"📤 Exiting SHORT position: Order ID={exit_id}", color="yellow")
                # Update virtual position to flat
                self.vars.virtual_tracker.execute_order(symbol, exit_qty, "buy", last_price)

            # Now enter LONG position
            order = self.create_order(asset, trade_qty, Order.OrderSide.BUY)
            submitted_order = self.submit_order(order)
            order_id = submitted_order.identifier if hasattr(submitted_order, "identifier") else "UNKNOWN"

            self.log_message(
                f"📤 Even min ({current_minute:02d}): Submitted LONG order ID={order_id}, "
                f"will check status on next iteration (60s)",
                color="cyan",
            )

            # Update virtual position tracker (assume market orders fill immediately)
            self.vars.virtual_tracker.execute_order(symbol, trade_qty, "buy", last_price)
            self.log_message(f"📊 Virtual position updated: LONG {trade_qty} @ ${last_price:.2f}", color="green")

            # Track this order to check next iteration
            self.vars.pending_orders.append(
                {
                    "order_id": order_id,
                    "submit_time": current_dt.timestamp(),
                    "purpose": "Long Entry",
                    "side": "buy",
                    "qty": trade_qty,
                    "price": last_price,
                }
            )

        elif is_even_minute and virtual_qty > 0:
            self.log_message(
                f"🔍 EVEN MINUTE CHECK: qty={virtual_qty}, Already LONG ✓, holding position",
                color="yellow",
            )
            self.log_message(
                f"⏭️ Even min ({current_minute:02d}): Already LONG (qty={virtual_qty}), holding", color="yellow"
            )

        # Odd minutes: Go short (if flat or long)
        elif not is_even_minute and virtual_qty >= 0:
            # Determine action based on current position
            if virtual_qty == 0:
                action = "ENTER SHORT from FLAT"
                exit_qty = 0
            else:  # virtual_qty > 0 (LONG)
                action = f"EXIT LONG ({virtual_qty}) and GO SHORT"
                exit_qty = virtual_qty

            self.log_message(
                f"🔍 ODD MINUTE CHECK: qty={virtual_qty}, Action: {action}",
                color="green",
            )

            # If LONG, first exit the long position
            if exit_qty > 0:
                exit_order = self.create_order(asset, exit_qty, Order.OrderSide.SELL)
                exit_submitted = self.submit_order(exit_order)
                exit_id = exit_submitted.identifier if hasattr(exit_submitted, "identifier") else "UNKNOWN"
                self.log_message(f"📤 Exiting LONG position: Order ID={exit_id}", color="yellow")
                # Update virtual position to flat
                self.vars.virtual_tracker.execute_order(symbol, exit_qty, "sell", last_price)

            # Now enter SHORT position
            order = self.create_order(asset, trade_qty, Order.OrderSide.SELL)
            submitted_order = self.submit_order(order)
            order_id = submitted_order.identifier if hasattr(submitted_order, "identifier") else "UNKNOWN"

            # Update virtual position tracker (assume market orders fill immediately)
            self.vars.virtual_tracker.execute_order(symbol, trade_qty, "sell", last_price)
            self.log_message(f"📊 Virtual position updated: SHORT {trade_qty} @ ${last_price:.2f}", color="red")

            self.log_message(
                f"📤 Odd min ({current_minute:02d}): Submitted SHORT order ID={order_id}, "
                f"will check status on next iteration (60s)",
                color="cyan",
            )

            # Track this order to check next iteration
            self.vars.pending_orders.append(
                {
                    "order_id": order_id,
                    "submit_time": current_dt.timestamp(),
                    "purpose": "Short Entry",
                    "side": "sell",
                    "qty": trade_qty,
                    "price": last_price,
                }
            )

        elif not is_even_minute and virtual_qty < 0:
            self.log_message(
                f"🔍 ODD MINUTE CHECK: qty={virtual_qty}, Already SHORT ✓, holding position",
                color="yellow",
            )
            self.log_message(
                f"⏭️ Odd min ({current_minute:02d}): Already SHORT (qty={virtual_qty}), holding", color="yellow"
            )

    def on_filled_order(self, position, order, price, quantity, multiplier):
        # Provide immediate feedback when fills occur
        symbol = self.parameters["symbol"]
        self.log_message(
            f"Order filled: {order.side} {quantity} {symbol} @ {price}.",
            color="green",
        )


if __name__ == "__main__":
    quote_asset = Asset("USD", asset_type=Asset.AssetType.FOREX)
    if IS_BACKTESTING:
        from lumibot.backtesting import DataBentoDataBacktesting

        # Get values from environment variables, defaulting to True if not set
        show_plot = get_bool_env("SHOW_PLOT", default=True)
        show_tearsheet = get_bool_env("SHOW_TEARSHEET", default=True)
        show_indicators = get_bool_env("SHOW_INDICATORS", default=True)

        trading_fee = TradingFee(flat_fee=2.0)
        EvenMinuteTradingStrategy.backtest(
            datasource_class=DataBentoDataBacktesting,
            benchmark_asset=Asset("SPY", Asset.AssetType.STOCK),
            buy_trading_fees=[trading_fee],
            sell_trading_fees=[trading_fee],
            quote_asset=quote_asset,
            show_plot=show_plot,
            show_tearsheet=show_tearsheet,
            show_indicators=show_indicators,
        )
    else:
        trader = Trader()
        strategy = EvenMinuteTradingStrategy(quote_asset=quote_asset)
        trader.add_strategy(strategy)
        trader.run_all()
