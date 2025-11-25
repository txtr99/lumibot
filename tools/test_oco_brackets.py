#!/usr/bin/env python3
"""Test OCO bracket order behavior with ProjectX API.

This script tests the native bracket order mechanism using stopLossBracket
and takeProfitBracket parameters instead of manual linkedOrderId linking.

Usage:
    python tools/test_oco_brackets.py test1    # Native brackets (ticks-based)
    python tools/test_oco_brackets.py test2    # Manual linking (current approach)
    python tools/test_oco_brackets.py cleanup  # Cancel all open orders
    python tools/test_oco_brackets.py status   # Show current positions/orders
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from custom_portfolio.data.futures_metadata import round_to_tick  # noqa: E402
from lumibot.credentials import PROJECTX_CONFIG  # noqa: E402
from lumibot.tools.projectx_helpers import ProjectXClient  # noqa: E402

console = Console()

# Test with MGC (Micro Gold) - small contract, liquid
TEST_SYMBOL = "MGC"
CONTRACT_ID = "CON.F.US.MGC.Z25"
TICK_SIZE = 0.10  # MGC tick size

# Bracket parameters - tight for quick testing
SL_TICKS = 10  # Stop loss 10 ticks away ($1.00 for MGC)
TP_TICKS = 10  # Take profit 10 ticks away ($1.00 for MGC)


def print_header(text):
    console.print(Panel(f"[bold bright_cyan]{text}[/]", expand=False))


def print_success(text):
    console.print(f"[bright_green]✅ {text}[/]")


def print_error(text):
    console.print(f"[bright_red]❌ {text}[/]")


def print_warning(text):
    console.print(f"[bright_yellow]⚠️  {text}[/]")


def print_info(text):
    console.print(f"[bright_cyan]ℹ️  {text}[/]")


def get_current_price(client, contract_id):
    """Get current price from recent bars."""
    from datetime import timezone

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=10)

    bars = client.api.history_retrieve_bars(
        contract_id=contract_id,
        start_datetime=start_time.isoformat(),
        end_datetime=end_time.isoformat(),
        unit=2,  # Minutes
        unit_number=1,
        limit=5,
    )

    # API returns DataFrame
    if bars is not None and len(bars) > 0:
        return bars["close"].iloc[-1]
    return None


def show_status(client, account_id):
    """Show current positions and open orders."""
    print_header("Current Status")

    # Positions
    pos_resp = client.api.position_search_open(account_id)
    if pos_resp.get("success"):
        positions = pos_resp.get("positions", [])
        if positions:
            table = Table(title="Open Positions")
            table.add_column("Contract")
            table.add_column("Size")
            table.add_column("Avg Price")
            table.add_column("P&L")

            for pos in positions:
                table.add_row(
                    pos.get("contractId", ""),
                    str(pos.get("size", 0)),
                    f"{pos.get('averagePrice', 0):.2f}",
                    str(pos.get("profitAndLoss", "N/A")),
                )
            console.print(table)
        else:
            print_info("No open positions")

    # Orders
    start_date = (datetime.now() - timedelta(hours=2)).isoformat()
    orders_resp = client.api.order_search(account_id, start_date)
    if orders_resp.get("success"):
        orders = [o for o in orders_resp.get("orders", []) if o.get("status") == 1]
        if orders:
            table = Table(title="Open Orders")
            table.add_column("ID")
            table.add_column("Contract")
            table.add_column("Type")
            table.add_column("Side")
            table.add_column("Size")
            table.add_column("Price")
            table.add_column("Tag")
            table.add_column("LinkedTo")

            order_types = {1: "Limit", 2: "Market", 3: "StopLimit", 4: "Stop", 5: "Trail"}
            sides = {0: "Buy", 1: "Sell"}

            for order in orders:
                price = order.get("limitPrice") or order.get("stopPrice") or "-"
                if isinstance(price, (int, float)):
                    price = f"{price:.2f}"
                table.add_row(
                    str(order.get("id", "")),
                    order.get("contractId", "")[-8:],  # Show just expiry part
                    order_types.get(order.get("type"), str(order.get("type"))),
                    sides.get(order.get("side"), str(order.get("side"))),
                    str(order.get("size", 0)),
                    price,
                    (order.get("customTag") or "")[:25],
                    str(order.get("linkedOrderId") or "-"),
                )
            console.print(table)
        else:
            print_info("No open orders")


def cleanup_orders(client, account_id):
    """Cancel all open orders."""
    print_header("Cleanup: Cancelling All Open Orders")

    start_date = (datetime.now() - timedelta(hours=2)).isoformat()
    orders_resp = client.api.order_search(account_id, start_date)

    if orders_resp.get("success"):
        open_orders = [o for o in orders_resp.get("orders", []) if o.get("status") == 1]

        if not open_orders:
            print_info("No open orders to cancel")
            return

        for order in open_orders:
            order_id = order.get("id")
            tag = order.get("customTag") or "no-tag"
            cancel_resp = client.api.order_cancel(account_id, order_id)

            if cancel_resp.get("success"):
                print_success(f"Cancelled order {order_id} ({tag})")
            else:
                error = cancel_resp.get("errorCode", cancel_resp.get("error", "unknown"))
                if cancel_resp.get("errorCode") == 5:
                    print_info(f"Order {order_id} already gone")
                else:
                    print_warning(f"Failed to cancel {order_id}: {error}")


def close_positions(client, account_id):
    """Close all open positions at market."""
    print_header("Closing All Open Positions")

    pos_resp = client.api.position_search_open(account_id)
    if not pos_resp.get("success"):
        print_error("Failed to get positions")
        return

    positions = pos_resp.get("positions", [])
    if not positions:
        print_info("No positions to close")
        return

    for pos in positions:
        contract_id = pos.get("contractId")
        size = pos.get("size", 0)

        if size == 0:
            continue

        # Sell to close longs, buy to close shorts
        side = 1 if size > 0 else 0
        close_size = abs(size)

        close_resp = client.api.order_place(
            account_id=account_id,
            contract_id=contract_id,
            type=2,  # Market
            side=side,
            size=close_size,
            custom_tag=f"CLOSE_{TEST_SYMBOL}_{int(time.time())}",
        )

        if close_resp.get("success"):
            print_success(f"Closed {size} {contract_id}")
        else:
            print_error(f"Failed to close: {close_resp}")


def test1_native_brackets(client, account_id):
    """Test native bracket orders using stopLossBracket and takeProfitBracket."""
    print_header("TEST 1: Native Bracket Orders (ticks-based)")

    current_price = get_current_price(client, CONTRACT_ID)
    if not current_price:
        print_error("Could not get current price")
        return 1

    print_info(f"Current {TEST_SYMBOL} price: {current_price:.2f}")
    print_info(f"Bracket: SL={SL_TICKS} ticks, TP={TP_TICKS} ticks")

    # Place entry order with native brackets
    tag = f"NATIVE_OCO_{int(time.time())}"

    # Build the order payload manually to include brackets
    # The API wrapper might not support these params yet
    import requests

    api_url = f"{client.api.base_url}api/order/place"

    payload = {
        "accountId": account_id,
        "contractId": CONTRACT_ID,
        "type": 2,  # Market
        "side": 0,  # Buy (LONG)
        "size": 1,
        "customTag": tag,
        "stopLossBracket": {
            "ticks": SL_TICKS,
            "type": 4,  # Stop order
        },
        "takeProfitBracket": {
            "ticks": TP_TICKS,
            "type": 1,  # Limit order
        },
    }

    print_info(f"Payload: {json.dumps(payload, indent=2)}")

    response = requests.post(
        api_url,
        headers=client.api.headers,
        json=payload,
        timeout=10,
    )

    result = response.json()
    print_info(f"Response: {json.dumps(result, indent=2)}")

    if result.get("success"):
        order_id = result.get("orderId")
        print_success(f"Entry order placed: ID={order_id}")

        # Wait and check for bracket orders
        time.sleep(1)
        show_status(client, account_id)

        print_info("\nWaiting 3 seconds to see if brackets appear...")
        time.sleep(3)
        show_status(client, account_id)

        return 0
    else:
        print_error(f"Order failed: {result}")
        return 1


def test2_manual_linking(client, account_id):
    """Test manual linking approach (current implementation)."""
    print_header("TEST 2: Manual Linking (linkedOrderId)")

    current_price = get_current_price(client, CONTRACT_ID)
    if not current_price:
        print_error("Could not get current price")
        return 1

    print_info(f"Current {TEST_SYMBOL} price: {current_price:.2f}")

    # Calculate SL/TP prices
    sl_price = round_to_tick(current_price - (SL_TICKS * TICK_SIZE), TEST_SYMBOL)
    tp_price = round_to_tick(current_price + (TP_TICKS * TICK_SIZE), TEST_SYMBOL)

    print_info(f"SL price: {sl_price:.2f}")
    print_info(f"TP price: {tp_price:.2f}")

    timestamp = int(time.time())

    # 1. Entry order
    entry_tag = f"MANUAL_ENTRY_{timestamp}"
    entry_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=2,  # Market
        side=0,  # Buy
        size=1,
        custom_tag=entry_tag,
    )

    if not entry_resp.get("success"):
        print_error(f"Entry failed: {entry_resp}")
        return 1

    entry_id = entry_resp.get("orderId")
    print_success(f"Entry placed: ID={entry_id}")

    time.sleep(0.5)

    # Get actual fill price
    pos_resp = client.api.position_search_open(account_id)
    fill_price = current_price
    if pos_resp.get("success"):
        for pos in pos_resp.get("positions", []):
            if CONTRACT_ID in pos.get("contractId", ""):
                fill_price = pos.get("averagePrice", current_price)
                break

    # Recalculate based on fill
    sl_price = round_to_tick(fill_price - (SL_TICKS * TICK_SIZE), TEST_SYMBOL)
    tp_price = round_to_tick(fill_price + (TP_TICKS * TICK_SIZE), TEST_SYMBOL)

    print_info(f"Fill price: {fill_price:.2f}")
    print_info(f"Adjusted SL: {sl_price:.2f}, TP: {tp_price:.2f}")

    # 2. Place SL linked to entry
    sl_tag = f"MANUAL_SL_{timestamp}"
    sl_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=4,  # Stop
        side=1,  # Sell
        size=1,
        stop_price=sl_price,
        custom_tag=sl_tag,
        linked_order_id=entry_id,
    )

    if not sl_resp.get("success"):
        print_error(f"SL failed: {sl_resp}")
        return 1

    sl_id = sl_resp.get("orderId")
    print_success(f"SL placed: ID={sl_id}, linked to entry {entry_id}")

    # 3. Place TP linked to SL
    tp_tag = f"MANUAL_TP_{timestamp}"
    tp_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=1,  # Limit
        side=1,  # Sell
        size=1,
        limit_price=tp_price,
        custom_tag=tp_tag,
        linked_order_id=sl_id,
    )

    if not tp_resp.get("success"):
        print_error(f"TP failed: {tp_resp}")
        return 1

    tp_id = tp_resp.get("orderId")
    print_success(f"TP placed: ID={tp_id}, linked to SL {sl_id}")

    print_info("\nLinking structure:")
    print_info(f"  Entry {entry_id} <- SL {sl_id} <- TP {tp_id}")
    print_info("  (unidirectional: TP->SL->Entry)")

    show_status(client, account_id)

    print_warning("\nMonitor this position. When TP fills, SL should NOT auto-cancel.")
    print_warning("This demonstrates the OCO bug.")

    return 0


def test3_bidirectional_linking(client, account_id):
    """Test bidirectional linking via cancel-and-replace."""
    print_header("TEST 3: Bidirectional Linking (cancel-and-replace)")

    current_price = get_current_price(client, CONTRACT_ID)
    if not current_price:
        print_error("Could not get current price")
        return 1

    print_info(f"Current {TEST_SYMBOL} price: {current_price:.2f}")

    timestamp = int(time.time())

    # 1. Entry at market
    entry_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=2,  # Market
        side=0,  # Buy
        size=1,
        custom_tag=f"BIDIR_ENTRY_{timestamp}",
    )

    if not entry_resp.get("success"):
        print_error(f"Entry failed: {entry_resp}")
        return 1

    entry_id = entry_resp.get("orderId")
    print_success(f"Entry placed: ID={entry_id}")

    time.sleep(0.5)

    # Get fill price
    pos_resp = client.api.position_search_open(account_id)
    fill_price = current_price
    if pos_resp.get("success"):
        for pos in pos_resp.get("positions", []):
            if CONTRACT_ID in pos.get("contractId", ""):
                fill_price = pos.get("averagePrice", current_price)
                break

    sl_price = round_to_tick(fill_price - (SL_TICKS * TICK_SIZE), TEST_SYMBOL)
    tp_price = round_to_tick(fill_price + (TP_TICKS * TICK_SIZE), TEST_SYMBOL)

    print_info(f"Fill: {fill_price:.2f}, SL: {sl_price:.2f}, TP: {tp_price:.2f}")

    # 2. Place SL (no linking yet)
    sl_tag = f"BIDIR_SL_{timestamp}"
    sl_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=4,  # Stop
        side=1,  # Sell
        size=1,
        stop_price=sl_price,
        custom_tag=sl_tag,
    )

    if not sl_resp.get("success"):
        print_error(f"SL failed: {sl_resp}")
        return 1

    sl_id = sl_resp.get("orderId")
    print_success(f"SL placed: ID={sl_id} (no linking)")

    # 3. Place TP linked to SL
    tp_tag = f"BIDIR_TP_{timestamp}"
    tp_resp = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=1,  # Limit
        side=1,  # Sell
        size=1,
        limit_price=tp_price,
        custom_tag=tp_tag,
        linked_order_id=sl_id,
    )

    if not tp_resp.get("success"):
        print_error(f"TP failed: {tp_resp}")
        return 1

    tp_id = tp_resp.get("orderId")
    print_success(f"TP placed: ID={tp_id}, linked to SL {sl_id}")

    # 4. Cancel original SL
    print_info("Cancelling original SL to re-create with link to TP...")
    cancel_resp = client.api.order_cancel(account_id, sl_id)

    if not cancel_resp.get("success"):
        print_error(f"Failed to cancel SL: {cancel_resp}")
        return 1

    print_success(f"Original SL {sl_id} cancelled")

    time.sleep(0.3)

    # 5. Re-place SL linked to TP
    sl_tag2 = f"BIDIR_SL2_{timestamp}"
    sl_resp2 = client.api.order_place(
        account_id=account_id,
        contract_id=CONTRACT_ID,
        type=4,  # Stop
        side=1,  # Sell
        size=1,
        stop_price=sl_price,
        custom_tag=sl_tag2,
        linked_order_id=tp_id,  # NOW linked to TP
    )

    if not sl_resp2.get("success"):
        print_error(f"SL re-place failed: {sl_resp2}")
        return 1

    new_sl_id = sl_resp2.get("orderId")
    print_success(f"New SL placed: ID={new_sl_id}, linked to TP {tp_id}")

    print_info("\nBidirectional linking structure:")
    print_info(f"  SL {new_sl_id} -> TP {tp_id}")
    print_info(f"  TP {tp_id} -> SL {sl_id} (original, now cancelled)")
    print_warning("  Note: TP still points to OLD SL ID!")

    show_status(client, account_id)

    return 0


def main():
    parser = argparse.ArgumentParser(description="Test OCO bracket order behavior")
    parser.add_argument(
        "command",
        choices=["test1", "test2", "test3", "cleanup", "close", "status"],
        help="Test to run or action to take",
    )
    args = parser.parse_args()

    print_header("OCO Bracket Order Testing")

    client = ProjectXClient(PROJECTX_CONFIG)
    account_id = client.get_preferred_account_id()

    print_info(f"Account ID: {account_id}")
    print_info(f"Test symbol: {TEST_SYMBOL} ({CONTRACT_ID})")
    print_info(f"Tick size: {TICK_SIZE}")
    print()

    if args.command == "status":
        show_status(client, account_id)
        return 0
    elif args.command == "cleanup":
        cleanup_orders(client, account_id)
        return 0
    elif args.command == "close":
        close_positions(client, account_id)
        cleanup_orders(client, account_id)
        return 0
    elif args.command == "test1":
        return test1_native_brackets(client, account_id)
    elif args.command == "test2":
        return test2_manual_linking(client, account_id)
    elif args.command == "test3":
        return test3_bidirectional_linking(client, account_id)


if __name__ == "__main__":
    sys.exit(main())
