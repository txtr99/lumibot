#!/usr/bin/env python3
"""
ProjectX API Explorer - Interactive Test Tool

An interactive CLI tool to explore and validate ProjectX API endpoints.
Captures full response payloads for analysis and verification.

Usage:
    source venv/bin/activate && python tools/projectx_api_explorer.py

Features:
- Interactive menu-driven interface
- Full response payload capture
- Auto-save responses to JSON files
- Formatted console output with Rich
- Safe testing (cancel operations require confirmation)
- Destructive tests: Bracket order creation with ATR-based stops/profits
- P&L validation: Compare calculated vs actual unrealized P&L
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path (must be before lumibot imports)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import futures metadata for tick size and P&L calculations
from lumibot.credentials import PROJECTX_CONFIG  # noqa: E402
from lumibot.tools.projectx_helpers import ProjectXClient  # noqa: E402

# Test instruments for P&L validation (standard + micro contracts)
TEST_INSTRUMENTS = ["GC", "NQ", "ES", "MGC", "MNQ", "MES"]

# Try to import Rich for pretty output, fallback to basic if not available
try:
    from rich.console import Console
    from rich.json import JSON as RichJSON
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table

    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


# ============================================================================
# Output Directory Setup
# ============================================================================

OUTPUT_DIR = PROJECT_ROOT / "tools" / "api_responses"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# Futures Metadata Import (needed for P&L calculations)
# ============================================================================

# Import futures metadata helpers - using try/except for robustness
try:
    from custom_portfolio.data.futures_metadata import (
        FUTURES_METADATA,
        get_multiplier,
        get_tick_size,
        get_tick_value,
        round_to_tick,
    )

    FUTURES_METADATA_AVAILABLE = True
except ImportError:
    FUTURES_METADATA_AVAILABLE = False
    FUTURES_METADATA = {}

    def get_tick_size(symbol):
        return 0.01

    def get_tick_value(symbol):
        return 1.0

    def get_multiplier(symbol):
        return 1.0

    def round_to_tick(price, symbol):
        return price


def save_response(name: str, data: dict) -> Path:
    """Save API response to JSON file with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_{timestamp}.json"
    filepath = OUTPUT_DIR / filename

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return filepath


def print_header(text: str):
    """Print a formatted header."""
    if RICH_AVAILABLE:
        console.print(Panel(text, style="bold cyan"))
    else:
        print("\n" + "=" * 60)
        print(text)
        print("=" * 60)


def print_json(data: dict, title: str = None):
    """Print JSON data formatted."""
    if title:
        if RICH_AVAILABLE:
            console.print(f"\n[bold yellow]{title}[/bold yellow]")
        else:
            print(f"\n{title}")

    if RICH_AVAILABLE:
        console.print(RichJSON(json.dumps(data, default=str)))
    else:
        print(json.dumps(data, indent=2, default=str))


def print_success(msg: str):
    if RICH_AVAILABLE:
        console.print(f"[bold green]{msg}[/bold green]")
    else:
        print(f"SUCCESS: {msg}")


def print_error(msg: str):
    if RICH_AVAILABLE:
        console.print(f"[bold red]{msg}[/bold red]")
    else:
        print(f"ERROR: {msg}")


def print_info(msg: str):
    if RICH_AVAILABLE:
        console.print(f"[cyan]{msg}[/cyan]")
    else:
        print(msg)


def get_input(prompt: str, default: str = None) -> str:
    """Get user input with optional default."""
    if RICH_AVAILABLE:
        return Prompt.ask(prompt, default=default) if default else Prompt.ask(prompt)
    else:
        if default:
            result = input(f"{prompt} [{default}]: ").strip()
            return result if result else default
        return input(f"{prompt}: ").strip()


def confirm(prompt: str, default: bool = False) -> bool:
    """Get yes/no confirmation."""
    if RICH_AVAILABLE:
        return Confirm.ask(prompt, default=default)
    else:
        response = input(f"{prompt} [y/N]: ").strip().lower()
        return response in ("y", "yes")


# ============================================================================
# API Test Functions
# ============================================================================


class ProjectXExplorer:
    """Interactive ProjectX API Explorer."""

    def __init__(self):
        self.client = None
        self.account_id = None
        self.account_name = None
        self.accounts = []

    def connect(self) -> bool:
        """Authenticate and connect to ProjectX."""
        print_header("Connecting to ProjectX API")

        try:
            print_info(f"Firm: {PROJECTX_CONFIG.get('firm', 'unknown')}")
            print_info(f"Username: {PROJECTX_CONFIG.get('username', 'unknown')}")

            self.client = ProjectXClient(PROJECTX_CONFIG)
            print_success("Authentication successful!")

            # Get accounts
            accounts_resp = self.client.api.account_search(only_active_accounts=True)

            if not accounts_resp.get("success"):
                print_error(f"Failed to get accounts: {accounts_resp}")
                return False

            self.accounts = accounts_resp.get("accounts", [])
            print_success(f"Found {len(self.accounts)} active account(s)")

            # Select account
            preferred_name = PROJECTX_CONFIG.get("preferred_account_name")

            for acct in self.accounts:
                if preferred_name and acct.get("name") == preferred_name:
                    self.account_id = acct.get("id")
                    self.account_name = acct.get("name")
                    break

            if not self.account_id and self.accounts:
                self.account_id = self.accounts[0].get("id")
                self.account_name = self.accounts[0].get("name")

            print_success(f"Using account: {self.account_name} (ID: {self.account_id})")

            # Save connection info
            save_response(
                "connection_info",
                {
                    "success": True,
                    "firm": PROJECTX_CONFIG.get("firm"),
                    "account_id": self.account_id,
                    "account_name": self.account_name,
                    "accounts": self.accounts,
                },
            )

            return True

        except Exception as e:
            print_error(f"Connection failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    def test_accounts(self):
        """Test: List all accounts with full details."""
        print_header("Test: Account Search")

        # Raw API call to capture full response
        response = self.client.api.account_search(only_active_accounts=True)

        filepath = save_response("accounts", response)
        print_info(f"Response saved to: {filepath}")

        print_json(response, "Full API Response:")

        if response.get("success"):
            accounts = response.get("accounts", [])
            print_success(f"Retrieved {len(accounts)} account(s)")

            if RICH_AVAILABLE and accounts:
                table = Table(title="Accounts")
                table.add_column("ID", style="cyan")
                table.add_column("Name", style="green")
                table.add_column("Balance", style="yellow")
                table.add_column("Status", style="magenta")

                for acct in accounts:
                    table.add_row(
                        str(acct.get("id", "")),
                        acct.get("name", ""),
                        f"${acct.get('balance', 0):,.2f}",
                        acct.get("status", ""),
                    )
                console.print(table)

    def test_positions(self):
        """Test: Query open positions (net exposure)."""
        print_header("Test: Position Search (Net Exposure)")

        # Raw API call
        response = self.client.api.position_search_open(self.account_id)

        filepath = save_response("positions", response)
        print_info(f"Response saved to: {filepath}")

        print_json(response, "Full API Response:")

        if response.get("success"):
            positions = response.get("positions", [])
            print_success(f"Retrieved {len(positions)} position(s)")

            if positions:
                # Analyze position fields
                fields = set()
                for pos in positions:
                    fields.update(pos.keys())

                print_info(f"\nPosition fields available: {sorted(fields)}")

                if RICH_AVAILABLE:
                    table = Table(title="Open Positions")
                    table.add_column("Contract", style="cyan")
                    table.add_column("Qty", style="green")
                    table.add_column("Avg Price", style="yellow")
                    table.add_column("Side", style="magenta")

                    for pos in positions:
                        qty = pos.get("qty", 0)
                        side = "LONG" if qty > 0 else "SHORT" if qty < 0 else "FLAT"
                        table.add_row(pos.get("contractId", ""), str(qty), f"{pos.get('avgPrice', 0):.2f}", side)
                    console.print(table)
            else:
                print_info("Account is currently FLAT (no open positions)")

    def test_orders(self, days: int = 7):
        """Test: Query orders with full status analysis."""
        print_header(f"Test: Order Search (last {days} days)")

        start_date = (datetime.now() - timedelta(days=days)).isoformat()

        # Raw API call
        response = self.client.api.order_search(self.account_id, start_date)

        filepath = save_response(f"orders_{days}d", response)
        print_info(f"Response saved to: {filepath}")

        # Show summary first (full response can be huge)
        if response.get("success"):
            orders = response.get("orders", [])
            print_success(f"Retrieved {len(orders)} order(s)")

            # Analyze statuses
            status_counts = {}
            status_map = {1: "open", 2: "filled", 3: "cancelled", 4: "expired", 5: "rejected", 6: "pending"}

            for order in orders:
                status = order.get("status")
                status_name = status_map.get(status, f"unknown_{status}")
                status_counts[status_name] = status_counts.get(status_name, 0) + 1

            print_info(f"\nStatus breakdown: {status_counts}")

            # Show fields available
            if orders:
                fields = set()
                for order in orders[:10]:
                    fields.update(order.keys())
                print_info(f"Order fields available: {sorted(fields)}")

            # Show sample orders
            if orders:
                print_json({"sample_orders": orders[:3]}, f"\nSample Orders (first 3 of {len(orders)}):")

            # Show full response option
            if orders and confirm("\nShow full response?", default=False):
                print_json(response, "Full API Response:")
        else:
            print_error(f"Failed: {response}")
            print_json(response, "Error Response:")

    def test_open_orders(self):
        """Test: Filter for currently open orders only."""
        print_header("Test: Open Orders Only (status=1)")

        # Query last 7 days and filter
        start_date = (datetime.now() - timedelta(days=7)).isoformat()
        response = self.client.api.order_search(self.account_id, start_date)

        if response.get("success"):
            all_orders = response.get("orders", [])
            open_orders = [o for o in all_orders if o.get("status") == 1]

            result = {
                "success": True,
                "total_orders_queried": len(all_orders),
                "open_order_count": len(open_orders),
                "open_orders": open_orders,
                "filter_method": "status == 1",
            }

            filepath = save_response("open_orders", result)
            print_info(f"Response saved to: {filepath}")

            print_json(result, "Filtered Open Orders:")

            if open_orders:
                print_success(f"Found {len(open_orders)} open order(s) on exchange")

                if RICH_AVAILABLE:
                    table = Table(title="Open Orders")
                    table.add_column("ID", style="cyan")
                    table.add_column("Contract", style="green")
                    table.add_column("Side", style="yellow")
                    table.add_column("Type", style="magenta")
                    table.add_column("Size", style="blue")
                    table.add_column("Limit", style="cyan")
                    table.add_column("Stop", style="red")
                    table.add_column("Tag", style="dim")

                    side_map = {0: "BUY", 1: "SELL"}
                    type_map = {1: "LIMIT", 2: "MARKET", 4: "STOP", 5: "TRAIL"}

                    for order in open_orders:
                        table.add_row(
                            str(order.get("id", "")),
                            order.get("contractId", ""),
                            side_map.get(order.get("side"), str(order.get("side"))),
                            type_map.get(order.get("type"), str(order.get("type"))),
                            str(order.get("size", "")),
                            str(order.get("limitPrice") or "-"),
                            str(order.get("stopPrice") or "-"),
                            (order.get("customTag") or "-")[:20],
                        )
                    console.print(table)
            else:
                print_info("No open orders on exchange")
        else:
            print_error(f"Failed: {response}")

    def test_trades(self, days: int = 7):
        """Test: Query trade history (fills - ground truth)."""
        print_header(f"Test: Trade Search (last {days} days)")

        start_date = (datetime.now() - timedelta(days=days)).isoformat()

        # Raw API call
        response = self.client.api.trade_search(self.account_id, start_date)

        filepath = save_response(f"trades_{days}d", response)
        print_info(f"Response saved to: {filepath}")

        if response.get("success"):
            trades = response.get("trades", [])
            print_success(f"Retrieved {len(trades)} trade(s)")

            # Show fields available
            if trades:
                fields = set()
                for trade in trades[:10]:
                    fields.update(trade.keys())
                print_info(f"Trade fields available: {sorted(fields)}")

                print_json({"sample_trades": trades[:3]}, f"\nSample Trades (first 3 of {len(trades)}):")
            else:
                print_info("No trades in this period")

            if trades and confirm("\nShow full response?", default=False):
                print_json(response, "Full API Response:")
        else:
            print_error(f"Failed: {response}")
            print_json(response, "Error Response:")

    def test_cancel_order(self):
        """Test: Cancel order functionality (with confirmation)."""
        print_header("Test: Cancel Order")

        # First show open orders
        start_date = (datetime.now() - timedelta(days=7)).isoformat()
        response = self.client.api.order_search(self.account_id, start_date)

        if not response.get("success"):
            print_error("Failed to query orders")
            return

        all_orders = response.get("orders", [])
        open_orders = [o for o in all_orders if o.get("status") == 1]

        if not open_orders:
            print_info("No open orders to cancel")
            return

        print_info(f"Found {len(open_orders)} open order(s):")
        for i, order in enumerate(open_orders):
            side_map = {0: "BUY", 1: "SELL"}
            print_info(
                f"  [{i}] ID={order.get('id')} {side_map.get(order.get('side'))} "
                f"{order.get('size')} {order.get('contractId')} tag={order.get('customTag')}"
            )

        print_error("\nWARNING: This will actually cancel an order!")

        if not confirm("Do you want to cancel an order?", default=False):
            print_info("Cancelled - no orders modified")
            return

        order_idx = get_input("Enter order index to cancel", "0")
        try:
            idx = int(order_idx)
            if 0 <= idx < len(open_orders):
                order_to_cancel = open_orders[idx]
                order_id = order_to_cancel.get("id")

                print_info(f"Cancelling order {order_id}...")
                cancel_response = self.client.api.order_cancel(self.account_id, order_id)

                filepath = save_response(
                    "cancel_order", {"order_cancelled": order_to_cancel, "cancel_response": cancel_response}
                )
                print_info(f"Response saved to: {filepath}")

                print_json(cancel_response, "Cancel Response:")

                if cancel_response.get("success"):
                    print_success(f"Order {order_id} cancelled successfully")
                else:
                    print_error(f"Cancel failed: {cancel_response}")
            else:
                print_error(f"Invalid index: {idx}")
        except ValueError:
            print_error(f"Invalid input: {order_idx}")

    def test_cancel_all_orders(self):
        """Test: Cancel ALL open orders (with strong confirmation)."""
        print_header("Test: Cancel ALL Orders")

        # First show open orders
        start_date = (datetime.now() - timedelta(days=7)).isoformat()
        response = self.client.api.order_search(self.account_id, start_date)

        if not response.get("success"):
            print_error("Failed to query orders")
            return

        all_orders = response.get("orders", [])
        open_orders = [o for o in all_orders if o.get("status") == 1]

        if not open_orders:
            print_info("No open orders to cancel")
            return

        print_info(f"Found {len(open_orders)} open order(s) to cancel:")
        for order in open_orders:
            side_map = {0: "BUY", 1: "SELL"}
            print_info(
                f"  ID={order.get('id')} {side_map.get(order.get('side'))} "
                f"{order.get('size')} {order.get('contractId')}"
            )

        print_error("\nDANGER: This will cancel ALL open orders!")
        print_error("Type 'CANCEL ALL' to confirm:")

        confirmation = get_input("Confirmation")
        if confirmation != "CANCEL ALL":
            print_info("Cancelled - no orders modified")
            return

        results = []
        for order in open_orders:
            order_id = order.get("id")
            print_info(f"Cancelling order {order_id}...")

            cancel_response = self.client.api.order_cancel(self.account_id, order_id)
            results.append({"order_id": order_id, "response": cancel_response})

            if cancel_response.get("success"):
                print_success(f"  Order {order_id} cancelled")
            else:
                print_error(f"  Order {order_id} failed: {cancel_response}")

        filepath = save_response("cancel_all_orders", {"orders_targeted": len(open_orders), "results": results})
        print_info(f"Response saved to: {filepath}")

    def test_contract_search(self):
        """Test: Search for contracts."""
        print_header("Test: Contract Search")

        search_text = get_input("Contract search text", "ES")

        response = self.client.api.contract_search(search_text, live=False)

        filepath = save_response(f"contracts_{search_text}", response)
        print_info(f"Response saved to: {filepath}")

        if response.get("success"):
            contracts = response.get("contracts", [])
            print_success(f"Found {len(contracts)} contract(s)")

            if contracts:
                print_json({"sample_contracts": contracts[:5]}, "Sample Contracts (first 5):")

                if RICH_AVAILABLE and len(contracts) > 5:
                    table = Table(title=f"All Contracts for '{search_text}'")
                    table.add_column("ID", style="cyan")
                    table.add_column("Name", style="green")
                    table.add_column("Description", style="yellow")

                    for contract in contracts[:20]:
                        table.add_row(
                            contract.get("id", ""), contract.get("name", ""), contract.get("description", "")[:40]
                        )
                    console.print(table)
        else:
            print_error(f"Failed: {response}")

    # ========================================================================
    # DESTRUCTIVE TESTS - Bracket Order Creation
    # ========================================================================

    def _resolve_contract_id(self, symbol: str) -> str | None:
        """Resolve a base symbol to a tradeable contract ID (e.g., 'ES' -> 'CON.F.US.ESZ25')."""
        response = self.client.api.contract_search(symbol, live=False)
        if not response.get("success"):
            print_error(f"Contract search failed: {response}")
            return None

        contracts = response.get("contracts", [])
        if not contracts:
            print_error(f"No contracts found for {symbol}")
            return None

        # Prefer the first futures contract (usually front month)
        for contract in contracts:
            contract_id = contract.get("id", "")
            if contract_id.startswith("CON.F."):
                return contract_id

        # Fallback to first contract
        return contracts[0].get("id")

    def _fetch_historical_bars(self, contract_id: str, bars_needed: int = 20) -> list[dict]:
        """
        Fetch 1-minute historical bars from ProjectX API.

        Args:
            contract_id: The contract ID (e.g., 'CON.F.US.ESZ25')
            bars_needed: Number of bars to fetch (default 20 for ATR(14) + buffer)

        Returns:
            List of bar dictionaries with open, high, low, close, volume
        """
        end_time = datetime.now()
        # Fetch extra bars to account for gaps
        start_time = end_time - timedelta(minutes=bars_needed * 2)

        print_info(f"Fetching {bars_needed} bars for {contract_id}...")
        print_info(f"  Time range: {start_time} to {end_time}")

        try:
            # unit=2 is minute bars, unit_number=1 is 1-minute
            df = self.client.api.history_retrieve_bars(
                contract_id=contract_id,
                start_datetime=start_time,
                end_datetime=end_time,
                unit=2,  # Minute
                unit_number=1,  # 1-minute bars
                limit=bars_needed * 2,
                include_partial_bar=False,
                live=False,
                is_est=True,
            )

            if df is None or df.empty:
                print_error("No historical bars returned")
                return []

            # Convert to list of dicts
            bars = df.to_dict("records")
            print_success(f"Fetched {len(bars)} bars")
            return bars[-bars_needed:]  # Return only the most recent bars_needed

        except Exception as e:
            print_error(f"Error fetching historical bars: {e}")
            import traceback

            traceback.print_exc()
            return []

    def _calculate_atr(self, bars: list[dict], period: int = 14) -> float | None:
        """
        Calculate Average True Range (ATR) from bar data.

        ATR = Average of True Range over N periods
        True Range = max(high - low, |high - prev_close|, |low - prev_close|)

        Args:
            bars: List of bar dictionaries with 'high', 'low', 'close' keys
            period: ATR period (default 14)

        Returns:
            ATR value or None if insufficient data
        """
        if len(bars) < period + 1:
            print_error(f"Insufficient bars for ATR({period}): have {len(bars)}, need {period + 1}")
            return None

        true_ranges = []

        for i in range(1, len(bars)):
            high = float(bars[i].get("high", bars[i].get("h", 0)))
            low = float(bars[i].get("low", bars[i].get("l", 0)))
            prev_close = float(bars[i - 1].get("close", bars[i - 1].get("c", 0)))

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)

            true_range = max(tr1, tr2, tr3)
            true_ranges.append(true_range)

        # Calculate simple moving average of true ranges for the period
        if len(true_ranges) < period:
            print_error(f"Insufficient true ranges: {len(true_ranges)}")
            return None

        atr = sum(true_ranges[-period:]) / period
        return atr

    def _get_last_price(self, bars: list[dict]) -> float | None:
        """Get the last close price from bar data."""
        if not bars:
            return None
        last_bar = bars[-1]
        return float(last_bar.get("close", last_bar.get("c", 0)))

    def test_create_long_bracket(self):
        """
        DESTRUCTIVE TEST: Create a LONG bracket order.

        - Entry: Market order BUY at current price
        - Stop Loss: 2 ATR below entry (calculated from 1-min bars)
        - Take Profit: 2 ATR above entry

        Uses OCO (One Cancels Other) via linkedOrderId.
        """
        print_header("DESTRUCTIVE TEST: Create LONG Bracket Order")
        print_error("⚠️  WARNING: This will place REAL orders on your account!")
        print_info("")

        # Select symbol from test instruments
        print_info("Available test instruments:")
        for i, symbol in enumerate(TEST_INSTRUMENTS):
            info = FUTURES_METADATA.get(symbol, {})
            print_info(f"  [{i}] {symbol} - {info.get('name', 'Unknown')} (tick: {info.get('tick_size', '?')})")

        symbol_idx = get_input("Select instrument (0-5)", "0")
        try:
            symbol = TEST_INSTRUMENTS[int(symbol_idx)]
        except (ValueError, IndexError):
            print_error("Invalid selection")
            return

        print_info(f"\nSelected: {symbol}")

        # Resolve contract
        contract_id = self._resolve_contract_id(symbol)
        if not contract_id:
            return

        print_info(f"Contract ID: {contract_id}")

        # Fetch historical bars for ATR
        bars = self._fetch_historical_bars(contract_id, bars_needed=20)
        if not bars:
            print_error("Cannot proceed without historical data")
            return

        # Calculate ATR
        atr = self._calculate_atr(bars, period=14)
        if atr is None:
            print_error("Cannot calculate ATR")
            return

        last_price = self._get_last_price(bars)
        if last_price is None:
            print_error("Cannot determine last price")
            return

        # Calculate stop loss and take profit levels
        tick_size = get_tick_size(symbol)
        atr_multiplier = 2.0

        stop_loss_price = round_to_tick(last_price - (atr * atr_multiplier), symbol)
        take_profit_price = round_to_tick(last_price + (atr * atr_multiplier), symbol)

        print_info("\n📊 Order Parameters:")
        print_info(f"  Symbol: {symbol}")
        print_info(f"  Last Price: {last_price:.4f}")
        print_info(f"  ATR(14): {atr:.4f}")
        print_info(f"  Tick Size: {tick_size}")
        print_info(f"  ATR Multiplier: {atr_multiplier}")
        print_info("  ---")
        print_info(f"  ENTRY: MARKET BUY @ ~{last_price:.4f}")
        print_info(f"  STOP LOSS: {stop_loss_price:.4f} (2 ATR below)")
        print_info(f"  TAKE PROFIT: {take_profit_price:.4f} (2 ATR above)")

        # Final confirmation
        print_error("\n⚠️  FINAL CONFIRMATION REQUIRED")
        if not confirm("Place these REAL orders?", default=False):
            print_info("Cancelled - no orders placed")
            return

        # Place market entry order
        print_info("\n📤 Placing MARKET BUY order...")
        entry_response = self.client.api.order_place(
            account_id=self.account_id,
            contract_id=contract_id,
            type=2,  # Market
            side=0,  # Buy
            size=1,
            custom_tag=f"LONG_ENTRY_{symbol}_{datetime.now().strftime('%H%M%S')}",
        )

        save_response(f"long_entry_{symbol}", entry_response)
        print_json(entry_response, "Entry Order Response:")

        if not entry_response.get("success"):
            print_error("Entry order failed!")
            return

        entry_order_id = entry_response.get("orderId")
        print_success(f"Entry order placed: ID={entry_order_id}")

        # Place stop loss order (STOP order, side=SELL)
        print_info("\n📤 Placing STOP LOSS order...")
        sl_response = self.client.api.order_place(
            account_id=self.account_id,
            contract_id=contract_id,
            type=4,  # Stop
            side=1,  # Sell
            size=1,
            stop_price=stop_loss_price,
            custom_tag=f"LONG_SL_{symbol}_{datetime.now().strftime('%H%M%S')}",
            linked_order_id=entry_order_id,
        )

        save_response(f"long_sl_{symbol}", sl_response)
        print_json(sl_response, "Stop Loss Order Response:")

        sl_order_id = sl_response.get("orderId") if sl_response.get("success") else None
        if sl_order_id:
            print_success(f"Stop loss order placed: ID={sl_order_id}")

        # Place take profit order (LIMIT order, side=SELL)
        print_info("\n📤 Placing TAKE PROFIT order...")
        tp_response = self.client.api.order_place(
            account_id=self.account_id,
            contract_id=contract_id,
            type=1,  # Limit
            side=1,  # Sell
            size=1,
            limit_price=take_profit_price,
            custom_tag=f"LONG_TP_{symbol}_{datetime.now().strftime('%H%M%S')}",
            linked_order_id=sl_order_id if sl_order_id else entry_order_id,
        )

        save_response(f"long_tp_{symbol}", tp_response)
        print_json(tp_response, "Take Profit Order Response:")

        if tp_response.get("success"):
            print_success(f"Take profit order placed: ID={tp_response.get('orderId')}")

        # Save complete bracket info
        bracket_info = {
            "symbol": symbol,
            "contract_id": contract_id,
            "direction": "LONG",
            "entry_price": last_price,
            "atr": atr,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "entry_response": entry_response,
            "sl_response": sl_response,
            "tp_response": tp_response,
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "tick_size": tick_size,
                "tick_value": get_tick_value(symbol),
                "multiplier": get_multiplier(symbol),
            },
        }
        filepath = save_response(f"long_bracket_{symbol}", bracket_info)
        print_info(f"\n✅ Complete bracket info saved to: {filepath}")

    def test_create_short_bracket(self):
        """
        DESTRUCTIVE TEST: Create a SHORT bracket order.

        - Entry: Market order SELL at current price
        - Stop Loss: 2 ATR above entry (calculated from 1-min bars)
        - Take Profit: 2 ATR below entry

        Uses OCO (One Cancels Other) via linkedOrderId.
        """
        print_header("DESTRUCTIVE TEST: Create SHORT Bracket Order")
        print_error("⚠️  WARNING: This will place REAL orders on your account!")
        print_info("")

        # Select symbol from test instruments
        print_info("Available test instruments:")
        for i, symbol in enumerate(TEST_INSTRUMENTS):
            info = FUTURES_METADATA.get(symbol, {})
            print_info(f"  [{i}] {symbol} - {info.get('name', 'Unknown')} (tick: {info.get('tick_size', '?')})")

        symbol_idx = get_input("Select instrument (0-5)", "0")
        try:
            symbol = TEST_INSTRUMENTS[int(symbol_idx)]
        except (ValueError, IndexError):
            print_error("Invalid selection")
            return

        print_info(f"\nSelected: {symbol}")

        # Resolve contract
        contract_id = self._resolve_contract_id(symbol)
        if not contract_id:
            return

        print_info(f"Contract ID: {contract_id}")

        # Fetch historical bars for ATR
        bars = self._fetch_historical_bars(contract_id, bars_needed=20)
        if not bars:
            print_error("Cannot proceed without historical data")
            return

        # Calculate ATR
        atr = self._calculate_atr(bars, period=14)
        if atr is None:
            print_error("Cannot calculate ATR")
            return

        last_price = self._get_last_price(bars)
        if last_price is None:
            print_error("Cannot determine last price")
            return

        # Calculate stop loss and take profit levels (INVERTED for short)
        tick_size = get_tick_size(symbol)
        atr_multiplier = 2.0

        stop_loss_price = round_to_tick(last_price + (atr * atr_multiplier), symbol)
        take_profit_price = round_to_tick(last_price - (atr * atr_multiplier), symbol)

        print_info("\n📊 Order Parameters:")
        print_info(f"  Symbol: {symbol}")
        print_info(f"  Last Price: {last_price:.4f}")
        print_info(f"  ATR(14): {atr:.4f}")
        print_info(f"  Tick Size: {tick_size}")
        print_info(f"  ATR Multiplier: {atr_multiplier}")
        print_info("  ---")
        print_info(f"  ENTRY: MARKET SELL @ ~{last_price:.4f}")
        print_info(f"  STOP LOSS: {stop_loss_price:.4f} (2 ATR above)")
        print_info(f"  TAKE PROFIT: {take_profit_price:.4f} (2 ATR below)")

        # Final confirmation
        print_error("\n⚠️  FINAL CONFIRMATION REQUIRED")
        if not confirm("Place these REAL orders?", default=False):
            print_info("Cancelled - no orders placed")
            return

        # Place market entry order
        print_info("\n📤 Placing MARKET SELL order...")
        entry_response = self.client.api.order_place(
            account_id=self.account_id,
            contract_id=contract_id,
            type=2,  # Market
            side=1,  # Sell
            size=1,
            custom_tag=f"SHORT_ENTRY_{symbol}_{datetime.now().strftime('%H%M%S')}",
        )

        save_response(f"short_entry_{symbol}", entry_response)
        print_json(entry_response, "Entry Order Response:")

        if not entry_response.get("success"):
            print_error("Entry order failed!")
            return

        entry_order_id = entry_response.get("orderId")
        print_success(f"Entry order placed: ID={entry_order_id}")

        # Place stop loss order (STOP order, side=BUY for short)
        print_info("\n📤 Placing STOP LOSS order...")
        sl_response = self.client.api.order_place(
            account_id=self.account_id,
            contract_id=contract_id,
            type=4,  # Stop
            side=0,  # Buy (to close short)
            size=1,
            stop_price=stop_loss_price,
            custom_tag=f"SHORT_SL_{symbol}_{datetime.now().strftime('%H%M%S')}",
            linked_order_id=entry_order_id,
        )

        save_response(f"short_sl_{symbol}", sl_response)
        print_json(sl_response, "Stop Loss Order Response:")

        sl_order_id = sl_response.get("orderId") if sl_response.get("success") else None
        if sl_order_id:
            print_success(f"Stop loss order placed: ID={sl_order_id}")

        # Place take profit order (LIMIT order, side=BUY for short)
        print_info("\n📤 Placing TAKE PROFIT order...")
        tp_response = self.client.api.order_place(
            account_id=self.account_id,
            contract_id=contract_id,
            type=1,  # Limit
            side=0,  # Buy (to close short)
            size=1,
            limit_price=take_profit_price,
            custom_tag=f"SHORT_TP_{symbol}_{datetime.now().strftime('%H%M%S')}",
            linked_order_id=sl_order_id if sl_order_id else entry_order_id,
        )

        save_response(f"short_tp_{symbol}", tp_response)
        print_json(tp_response, "Take Profit Order Response:")

        if tp_response.get("success"):
            print_success(f"Take profit order placed: ID={tp_response.get('orderId')}")

        # Save complete bracket info
        bracket_info = {
            "symbol": symbol,
            "contract_id": contract_id,
            "direction": "SHORT",
            "entry_price": last_price,
            "atr": atr,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "entry_response": entry_response,
            "sl_response": sl_response,
            "tp_response": tp_response,
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "tick_size": tick_size,
                "tick_value": get_tick_value(symbol),
                "multiplier": get_multiplier(symbol),
            },
        }
        filepath = save_response(f"short_bracket_{symbol}", bracket_info)
        print_info(f"\n✅ Complete bracket info saved to: {filepath}")

    # ========================================================================
    # P&L VALIDATION TESTS
    # ========================================================================

    def _calculate_pnl_backtest_style(
        self,
        entry_price: float,
        current_price: float,
        quantity: int,
        direction: str,
        symbol: str,
    ) -> dict:
        """
        Calculate P&L using our backtest methodology.

        Formula:
            For LONG: PnL = (current_price - entry_price) * quantity * multiplier
            For SHORT: PnL = (entry_price - current_price) * quantity * multiplier

        Where multiplier = tick_value / tick_size

        Returns dict with calculation details for comparison.
        """
        tick_size = get_tick_size(symbol)
        tick_value = get_tick_value(symbol)
        multiplier = get_multiplier(symbol)

        price_diff = current_price - entry_price
        ticks_moved = price_diff / tick_size

        if direction.upper() == "LONG":
            pnl = price_diff * quantity * multiplier
        else:  # SHORT
            pnl = -price_diff * quantity * multiplier

        return {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "current_price": current_price,
            "price_diff": price_diff,
            "quantity": quantity,
            "tick_size": tick_size,
            "tick_value": tick_value,
            "multiplier": multiplier,
            "ticks_moved": ticks_moved,
            "tick_pnl": ticks_moved * tick_value * quantity,
            "calculated_pnl": pnl,
            "calculation_method": "backtest_style",
            "formula": (
                "(current - entry) * qty * mult" if direction.upper() == "LONG" else "(entry - current) * qty * mult"
            ),
        }

    def test_pnl_validation(self):
        """
        Validate P&L calculations against actual exchange-reported unrealized P&L.

        This test:
        1. Queries current open positions
        2. For each position, calculates P&L using our backtest methodology
        3. Compares against the unrealized P&L reported by the exchange
        4. Reports any discrepancies
        """
        print_header("P&L Validation Test")
        print_info("Comparing calculated P&L against exchange-reported values")

        # Get current positions
        response = self.client.api.position_search_open(self.account_id)

        if not response.get("success"):
            print_error(f"Failed to get positions: {response}")
            return

        positions = response.get("positions", [])

        if not positions:
            print_info("No open positions to validate.")
            print_info("\nTo test P&L validation:")
            print_info("  1. Create a bracket order (options 10 or 11)")
            print_info("  2. Wait for the entry to fill")
            print_info("  3. Run this test again")
            return

        validation_results = []

        for pos in positions:
            contract_id = pos.get("contractId", "")
            qty = pos.get("qty", 0)
            avg_price = pos.get("avgPrice", 0)
            exchange_pnl = pos.get("pnl", pos.get("unrealizedPnl", 0))

            # Extract base symbol from contract ID (e.g., 'CON.F.US.ESZ25' -> 'ES')
            symbol = self._extract_symbol_from_contract(contract_id)
            direction = "LONG" if qty > 0 else "SHORT"
            qty_abs = abs(qty)

            print_info(f"\n📊 Position: {symbol} ({contract_id})")
            print_info(f"   Direction: {direction}")
            print_info(f"   Quantity: {qty_abs}")
            print_info(f"   Avg Price: {avg_price}")
            print_info(f"   Exchange P&L: ${exchange_pnl:.2f}")

            # Get current price from historical bars
            bars = self._fetch_historical_bars(contract_id, bars_needed=5)
            if not bars:
                print_error(f"   Cannot get current price for {symbol}")
                continue

            current_price = self._get_last_price(bars)
            if current_price is None:
                print_error("   Cannot determine current price")
                continue

            print_info(f"   Current Price: {current_price}")

            # Calculate P&L using our methodology
            calc_result = self._calculate_pnl_backtest_style(
                entry_price=avg_price,
                current_price=current_price,
                quantity=qty_abs,
                direction=direction,
                symbol=symbol,
            )

            calculated_pnl = calc_result["calculated_pnl"]
            difference = calculated_pnl - exchange_pnl
            pct_diff = (difference / exchange_pnl * 100) if exchange_pnl != 0 else 0

            print_info(f"   Calculated P&L: ${calculated_pnl:.2f}")
            print_info(f"   Difference: ${difference:.2f} ({pct_diff:.2f}%)")

            if abs(difference) < 0.01:
                print_success("   ✅ MATCH!")
            elif abs(difference) < 1.00:
                print_info("   ⚠️ Minor discrepancy (likely rounding)")
            else:
                print_error("   ❌ SIGNIFICANT DISCREPANCY")

            validation_results.append(
                {
                    "contract_id": contract_id,
                    "symbol": symbol,
                    "direction": direction,
                    "quantity": qty_abs,
                    "avg_price": avg_price,
                    "current_price": current_price,
                    "exchange_pnl": exchange_pnl,
                    "calculated_pnl": calculated_pnl,
                    "difference": difference,
                    "pct_difference": pct_diff,
                    "calculation_details": calc_result,
                    "position_raw": pos,
                }
            )

        # Summary
        print_header("Validation Summary")

        total_exchange_pnl = sum(r["exchange_pnl"] for r in validation_results)
        total_calculated_pnl = sum(r["calculated_pnl"] for r in validation_results)
        total_diff = total_calculated_pnl - total_exchange_pnl

        print_info(f"Total Exchange P&L: ${total_exchange_pnl:.2f}")
        print_info(f"Total Calculated P&L: ${total_calculated_pnl:.2f}")
        print_info(f"Total Difference: ${total_diff:.2f}")

        # Save validation report
        filepath = save_response(
            "pnl_validation",
            {
                "timestamp": datetime.now().isoformat(),
                "positions_validated": len(validation_results),
                "total_exchange_pnl": total_exchange_pnl,
                "total_calculated_pnl": total_calculated_pnl,
                "total_difference": total_diff,
                "results": validation_results,
            },
        )
        print_info(f"\n📁 Validation report saved to: {filepath}")

    def _extract_symbol_from_contract(self, contract_id: str) -> str:
        """Extract base symbol from contract ID (e.g., 'CON.F.US.ESZ25' -> 'ES')."""
        # Common pattern: CON.F.US.{symbol}{month}{year}
        parts = contract_id.split(".")
        if len(parts) >= 4:
            full_symbol = parts[3]
            # Remove month code and year (last 3 characters typically)
            # ES Z 25 -> ES
            # NQ Z 25 -> NQ
            # MES Z 25 -> MES
            # Try stripping the month/year suffix first
            if len(full_symbol) > 3:
                base = full_symbol[:-3]
                if base in FUTURES_METADATA:
                    return base

            # Try common prefixes (3-char like MES, then 2-char like ES)
            for length in [3, 2]:
                if len(full_symbol) >= length:
                    candidate = full_symbol[:length]
                    if candidate in FUTURES_METADATA:
                        return candidate

        return "ES"  # Default fallback

    def test_pnl_instrument_capture(self):
        """
        Capture P&L test data for all 6 test instruments.

        This creates placeholder data structures for:
        - GC, NQ, ES (standard contracts)
        - MGC, MNQ, MES (micro contracts)

        Both LONG and SHORT directions.
        """
        print_header("P&L Instrument Test Data Capture")
        print_info("Capturing metadata for all test instruments")

        test_data = {
            "timestamp": datetime.now().isoformat(),
            "instruments": {},
        }

        for symbol in TEST_INSTRUMENTS:
            info = FUTURES_METADATA.get(symbol, {})

            print_info(f"\n📊 {symbol}: {info.get('name', 'Unknown')}")

            # Resolve contract
            contract_id = self._resolve_contract_id(symbol)
            if not contract_id:
                print_error(f"   Could not resolve contract for {symbol}")
                test_data["instruments"][symbol] = {"error": "Contract not found"}
                continue

            # Fetch current price
            bars = self._fetch_historical_bars(contract_id, bars_needed=20)
            if not bars:
                print_error(f"   Could not fetch bars for {symbol}")
                test_data["instruments"][symbol] = {"error": "No historical data"}
                continue

            last_price = self._get_last_price(bars)
            atr = self._calculate_atr(bars, period=14)

            # Create test scenarios for both directions
            tick_size = get_tick_size(symbol)
            tick_value = get_tick_value(symbol)
            multiplier = get_multiplier(symbol)

            # Simulate 10-tick move for P&L calculation example
            test_move_ticks = 10
            test_move_price = test_move_ticks * tick_size

            long_scenario = self._calculate_pnl_backtest_style(
                entry_price=last_price,
                current_price=last_price + test_move_price,
                quantity=1,
                direction="LONG",
                symbol=symbol,
            )

            short_scenario = self._calculate_pnl_backtest_style(
                entry_price=last_price,
                current_price=last_price + test_move_price,  # Price moved against short
                quantity=1,
                direction="SHORT",
                symbol=symbol,
            )

            instrument_data = {
                "contract_id": contract_id,
                "metadata": {
                    "name": info.get("name"),
                    "tick_size": tick_size,
                    "tick_value": tick_value,
                    "multiplier": multiplier,
                    "exchange": info.get("exchange"),
                    "category": info.get("category"),
                },
                "current_market": {
                    "last_price": last_price,
                    "atr_14": atr,
                    "bars_fetched": len(bars),
                    "fetch_time": datetime.now().isoformat(),
                },
                "test_scenarios": {
                    "test_move_ticks": test_move_ticks,
                    "test_move_price": test_move_price,
                    "long_10_ticks_up": long_scenario,
                    "short_10_ticks_up": short_scenario,
                    "expected_long_pnl": test_move_ticks * tick_value,
                    "expected_short_pnl": -test_move_ticks * tick_value,
                },
                "validation_placeholders": {
                    "long_entry_filled": None,
                    "long_entry_price": None,
                    "long_current_price": None,
                    "long_exchange_pnl": None,
                    "long_calculated_pnl": None,
                    "long_match": None,
                    "short_entry_filled": None,
                    "short_entry_price": None,
                    "short_current_price": None,
                    "short_exchange_pnl": None,
                    "short_calculated_pnl": None,
                    "short_match": None,
                },
            }

            test_data["instruments"][symbol] = instrument_data

            print_info(f"   Contract: {contract_id}")
            print_info(f"   Last Price: {last_price}")
            print_info(f"   ATR(14): {atr:.4f}" if atr else "   ATR: N/A")
            print_info(f"   Tick: {tick_size} @ ${tick_value}")
            print_info(f"   Multiplier: {multiplier}")
            print_info(f"   10-tick LONG P&L: ${test_move_ticks * tick_value:.2f}")
            print_info(f"   10-tick SHORT P&L: ${-test_move_ticks * tick_value:.2f}")

        # Save test data template
        filepath = save_response("pnl_test_data_template", test_data)
        print_header("Test Data Template Created")
        print_info(f"📁 Saved to: {filepath}")
        print_info("")
        print_info("Next steps:")
        print_info("  1. Create LONG bracket orders for each instrument")
        print_info("  2. Wait for fills")
        print_info("  3. Run P&L validation (option 12)")
        print_info("  4. Repeat for SHORT direction")

    def run_all_safe_tests(self):
        """Run all safe (non-destructive) tests."""
        print_header("Running All Safe Tests")

        tests = [
            ("Accounts", self.test_accounts),
            ("Positions", self.test_positions),
            ("Orders (7 days)", lambda: self.test_orders(7)),
            ("Open Orders", self.test_open_orders),
            ("Trades (7 days)", lambda: self.test_trades(7)),
        ]

        for name, test_func in tests:
            print_info(f"\n{'='*40}")
            print_info(f"Running: {name}")
            print_info(f"{'='*40}")
            try:
                test_func()
            except Exception as e:
                print_error(f"Test failed: {e}")
                import traceback

                traceback.print_exc()

        print_header("All Safe Tests Complete")
        print_info(f"Responses saved to: {OUTPUT_DIR}")


def main():
    """Main interactive loop."""
    print_header("ProjectX API Explorer")
    print_info("Interactive tool to test and explore ProjectX API endpoints")
    print_info(f"Responses will be saved to: {OUTPUT_DIR}\n")

    explorer = ProjectXExplorer()

    if not explorer.connect():
        print_error("Failed to connect. Exiting.")
        return

    menu = """
    ═══════════════════════════════════════════════════════════════
    SAFE TESTS (Read-Only)
    ═══════════════════════════════════════════════════════════════
    [1] Test Accounts - List all accounts
    [2] Test Positions - Query net exposure
    [3] Test Orders - Query order history
    [4] Test Open Orders - Filter open orders only
    [5] Test Trades - Query fill history
    [6] Test Contract Search - Search contracts
    [9] Run All Safe Tests - Run all non-destructive tests

    ═══════════════════════════════════════════════════════════════
    DESTRUCTIVE TESTS (Place Real Orders!)
    ═══════════════════════════════════════════════════════════════
    [7] Test Cancel Order - Cancel single order (CAREFUL!)
    [8] Test Cancel ALL - Cancel all orders (DANGER!)
    [10] Create LONG Bracket - Market BUY + ATR stops (DANGER!)
    [11] Create SHORT Bracket - Market SELL + ATR stops (DANGER!)

    ═══════════════════════════════════════════════════════════════
    P&L VALIDATION (For Testing Calculations)
    ═══════════════════════════════════════════════════════════════
    [12] Validate P&L - Compare calculated vs exchange P&L
    [13] Capture Test Data - Prep all 6 instruments for testing

    [0] Exit
    """

    while True:
        if RICH_AVAILABLE:
            console.print(Panel(menu, title="Menu", style="cyan"))
        else:
            print(menu)

        choice = get_input("Select option", "0")

        try:
            if choice == "1":
                explorer.test_accounts()
            elif choice == "2":
                explorer.test_positions()
            elif choice == "3":
                days = int(get_input("Days to query", "7"))
                explorer.test_orders(days)
            elif choice == "4":
                explorer.test_open_orders()
            elif choice == "5":
                days = int(get_input("Days to query", "7"))
                explorer.test_trades(days)
            elif choice == "6":
                explorer.test_contract_search()
            elif choice == "7":
                explorer.test_cancel_order()
            elif choice == "8":
                explorer.test_cancel_all_orders()
            elif choice == "9":
                explorer.run_all_safe_tests()
            elif choice == "10":
                explorer.test_create_long_bracket()
            elif choice == "11":
                explorer.test_create_short_bracket()
            elif choice == "12":
                explorer.test_pnl_validation()
            elif choice == "13":
                explorer.test_pnl_instrument_capture()
            elif choice == "0":
                print_info("Goodbye!")
                break
            else:
                print_error(f"Unknown option: {choice}")
        except KeyboardInterrupt:
            print_info("\nInterrupted")
        except Exception as e:
            print_error(f"Error: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
