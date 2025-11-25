#!/usr/bin/env python3
"""
P&L Validation Workflow - CLI Tool

A sequential workflow tool for comprehensive bracket order and P&L validation
testing across 6 futures instruments (ES, MES, NQ, MNQ, GC, MGC).

Usage:
    python tools/pnl_validation_workflow.py start [--autonomous]
    python tools/pnl_validation_workflow.py step-1
    python tools/pnl_validation_workflow.py step-2
    ...
    python tools/pnl_validation_workflow.py status
    python tools/pnl_validation_workflow.py report

Features:
- CLI command-based (not interactive menu)
- Step-by-step guidance with copy-paste commands
- Progress visibility: Step X of Y (XX%)
- Auto-clear responses on start command
- Pre-flight flat check
- All 6 instruments tested per step
- LONG phase completes before SHORT phase
- --autonomous flag for auto-progression
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lumibot.credentials import PROJECTX_CONFIG  # noqa: E402
from lumibot.tools.projectx_helpers import ProjectXClient  # noqa: E402

# Import futures metadata
try:
    from custom_portfolio.data.futures_metadata import (
        FUTURES_METADATA,
        contract_id_to_symbol,
        get_multiplier,
        get_tick_size,
        get_tick_value,
        round_to_tick,
    )

    FUTURES_METADATA_AVAILABLE = True
except ImportError:
    FUTURES_METADATA_AVAILABLE = False
    FUTURES_METADATA = {}

    def contract_id_to_symbol(contract_id):
        """Fallback: just return the 4th part."""
        parts = contract_id.split(".")
        return parts[3] if len(parts) >= 4 else contract_id

    def get_tick_size(symbol):
        return 0.01

    def get_tick_value(symbol):
        return 1.0

    def get_multiplier(symbol):
        return 1.0

    def round_to_tick(price, symbol):
        return price


# Rich library for pretty output
try:
    from rich.console import Console
    from rich.panel import Panel

    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# ============================================================================
# Constants
# ============================================================================

TEST_INSTRUMENTS = ["ES", "MES", "NQ", "MNQ", "GC", "MGC"]
STRATEGY_NAME = "pnltest"
TOTAL_STEPS = 15  # Final step is BracketOrderManager integration test
OUTPUT_DIR = PROJECT_ROOT / "tools" / "api_responses"
STATE_FILE = OUTPUT_DIR / "workflow_state.json"

# Polling configuration (seconds) - adjust for rate limiting tolerance
DEFAULT_POLL_INTERVAL = 30
API_DELAY_MS = 100  # Delay between API calls to avoid rate limiting

# Rate limit tracking (populated during workflow)
RATE_LIMIT_EVENTS: list[dict] = []


def track_rate_limit(response: dict, operation: str) -> dict:
    """Track rate limit events from API responses."""
    # Check for rate limit indicators in response
    if not response.get("success"):
        error_msg = str(response.get("error", response.get("message", "")))
        if "429" in error_msg or "rate" in error_msg.lower() or "limit" in error_msg.lower():
            event = {
                "timestamp": datetime.now().isoformat(),
                "operation": operation,
                "error": error_msg,
            }
            RATE_LIMIT_EVENTS.append(event)
            print_warning(f"Rate limit detected: {operation}")
    return response


# Order status codes
ORDER_STATUS = {
    1: "open",
    2: "filled",
    3: "cancelled",
    4: "expired",
    5: "rejected",
    6: "pending",
}

# ============================================================================
# Output Helpers
# ============================================================================


def print_header(text: str, style: str = "bright_cyan"):
    """Print a formatted header."""
    if RICH_AVAILABLE:
        console.print(Panel(text, style=f"bold {style}"))
    else:
        print("\n" + "=" * 80)
        print(text)
        print("=" * 80)


def print_progress_bar(current_step: int, total_steps: int = TOTAL_STEPS):
    """Print workflow progress bar."""
    pct = int((current_step / total_steps) * 100)
    filled = int((current_step / total_steps) * 50)
    bar = "\u2588" * filled + "\u2591" * (50 - filled)

    if RICH_AVAILABLE:
        console.print("\n[bright_cyan]" + "\u2550" * 80 + "[/bright_cyan]")
        step_info = f"Step {current_step} of {total_steps} ({pct}%)"
        console.print(
            f"  [bold bright_white]P&L VALIDATION WORKFLOW[/bold bright_white]"
            f"              [bright_yellow]{step_info}[/bright_yellow]"
        )
        console.print(f"  [bright_green]{bar}[/bright_green]  {pct}%")
        console.print("[bright_cyan]" + "\u2550" * 80 + "[/bright_cyan]\n")
    else:
        print("\n" + "=" * 80)
        print(f"  P&L VALIDATION WORKFLOW                Step {current_step} of {total_steps} ({pct}%)")
        print(f"  {bar}  {pct}%")
        print("=" * 80 + "\n")


def print_success(msg: str):
    if RICH_AVAILABLE:
        console.print(f"[bold bright_green]\u2705 {msg}[/bold bright_green]")
    else:
        print(f"SUCCESS: {msg}")


def print_error(msg: str):
    if RICH_AVAILABLE:
        console.print(f"[bold bright_red]\u274c {msg}[/bold bright_red]")
    else:
        print(f"ERROR: {msg}")


def print_warning(msg: str):
    if RICH_AVAILABLE:
        console.print(f"[bold bright_yellow]\u26a0\ufe0f  {msg}[/bold bright_yellow]")
    else:
        print(f"WARNING: {msg}")


def print_info(msg: str):
    if RICH_AVAILABLE:
        console.print(f"[bright_cyan]{msg}[/bright_cyan]")
    else:
        print(msg)


def print_next_command(command: str):
    """Print the next command to run."""
    if RICH_AVAILABLE:
        console.print("\n[bold bright_white]Next command:[/bold bright_white]")
        console.print(f"  [bold bright_yellow]{command}[/bold bright_yellow]\n")
    else:
        print(f"\nNext command:\n  {command}\n")


# ============================================================================
# State Management
# ============================================================================


def init_state(autonomous: bool = False) -> dict:
    """Initialize workflow state."""
    workflow_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "workflow_id": workflow_id,
        "current_step": 0,
        "phase": "PRE-FLIGHT",
        "started_at": datetime.now().isoformat(),
        "autonomous": autonomous,
        "instruments": {
            symbol: {
                "contract_id": None,
                "atr": None,
                "last_price": None,
                "long": {
                    "entry_price": None,
                    "entry_order_id": None,
                    "sl_order_id": None,
                    "tp_order_id": None,
                    "sl_price": None,
                    "tp_price": None,
                    "position_qty": 0,
                    "sl_filled": False,
                    "tp_filled": False,
                    "closed": False,
                },
                "short": {
                    "entry_price": None,
                    "entry_order_id": None,
                    "sl_order_id": None,
                    "tp_order_id": None,
                    "sl_price": None,
                    "tp_price": None,
                    "position_qty": 0,
                    "sl_filled": False,
                    "tp_filled": False,
                    "closed": False,
                },
            }
            for symbol in TEST_INSTRUMENTS
        },
        "test_results": {
            "tag_persistence": None,
            "long_pnl_discrepancies": [],
            "short_pnl_discrepancies": [],
            "long_self_heal": None,
            "short_self_heal": None,
            "long_bracket_cleanup": None,
            "short_bracket_cleanup": None,
            "long_sl_fill_count": 0,
            "long_tp_fill_count": 0,
            "short_sl_fill_count": 0,
            "short_tp_fill_count": 0,
            "rate_limit_events": [],
            # Step 15: OCO verification results
            "oco_verification": None,
            "oco_details": {},
            # Step 16: Order modification / wrong-side detection
            "order_modification": None,
            "order_mod_details": {},
            "wrong_side_detection": None,
            "wrong_side_details": {},
        },
        "step_history": [],
    }


def save_state(state: dict):
    """Save workflow state to JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_state() -> dict | None:
    """Load workflow state from JSON."""
    if not STATE_FILE.exists():
        return None
    with open(STATE_FILE) as f:
        return json.load(f)


def update_step(state: dict, step: int, status: str = "completed"):
    """Update step in state."""
    state["current_step"] = step
    state["step_history"].append(
        {
            "step": step,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }
    )
    save_state(state)


def save_response(name: str, data: dict) -> Path:
    """Save API response to JSON file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_{timestamp}.json"
    filepath = OUTPUT_DIR / filename
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return filepath


# ============================================================================
# API Helpers (reused from projectx_api_explorer.py)
# ============================================================================


def resolve_contract_id(client: ProjectXClient, symbol: str) -> str | None:
    """Resolve a base symbol to a tradeable contract ID."""
    response = client.api.contract_search(symbol, live=False)
    if not response.get("success"):
        print_error(f"Contract search failed: {response}")
        return None

    contracts = response.get("contracts", [])
    if not contracts:
        print_error(f"No contracts found for {symbol}")
        return None

    # Prefer the first futures contract
    for contract in contracts:
        contract_id = contract.get("id", "")
        if contract_id.startswith("CON.F."):
            return contract_id

    return contracts[0].get("id")


def fetch_historical_bars(client: ProjectXClient, contract_id: str, bars_needed: int = 20) -> list[dict]:
    """Fetch 1-minute historical bars from ProjectX API."""
    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=bars_needed * 2)

    try:
        df = client.api.history_retrieve_bars(
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
            return []

        bars = df.to_dict("records")
        return bars[-bars_needed:]

    except Exception as e:
        print_error(f"Error fetching bars: {e}")
        return []


def calculate_atr(bars: list[dict], period: int = 14) -> float | None:
    """Calculate Average True Range (ATR) from bar data."""
    if len(bars) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(bars)):
        high = float(bars[i].get("high", bars[i].get("h", 0)))
        low = float(bars[i].get("low", bars[i].get("l", 0)))
        prev_close = float(bars[i - 1].get("close", bars[i - 1].get("c", 0)))

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period


def get_last_price(bars: list[dict]) -> float | None:
    """Get the last close price from bar data."""
    if not bars:
        return None
    last_bar = bars[-1]
    return float(last_bar.get("close", last_bar.get("c", 0)))


def calculate_pnl(entry_price: float, current_price: float, quantity: int, direction: str, symbol: str) -> dict:
    """Calculate P&L using backtest methodology."""
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
        "calculated_pnl": pnl,
    }


def extract_symbol_from_contract(contract_id: str) -> str:
    """Extract base symbol from contract ID (e.g., 'CON.F.US.EP.Z25' -> 'ES').

    Delegates to contract_id_to_symbol() from futures_metadata for centralized mapping.
    """
    return contract_id_to_symbol(contract_id)


def build_tag(symbol: str, direction: str, order_type: str, workflow_id: str = None) -> str:
    """Build order tag in format: Symbol_Direction_Type_StrategyName_WorkflowID.

    Tags must be unique per account, so we include workflow_id to avoid collisions.
    """
    if workflow_id:
        # Use last 6 chars of workflow_id for uniqueness (e.g., "143052" from "20241124_143052")
        short_id = workflow_id.replace("_", "")[-6:]
        return f"{symbol}_{direction}_{order_type}_{STRATEGY_NAME}_{short_id}"
    return f"{symbol}_{direction}_{order_type}_{STRATEGY_NAME}"


# ============================================================================
# Workflow Steps
# ============================================================================


def cmd_start(args):
    """Start workflow: clear data, check flat, initialize state."""
    print_header("P&L VALIDATION WORKFLOW - START")

    # Clear previous responses
    if OUTPUT_DIR.exists():
        print_info("Clearing previous API responses...")
        for f in OUTPUT_DIR.glob("*.json"):
            f.unlink()
        print_success("Previous data cleared")

    # Initialize state
    state = init_state(autonomous=args.autonomous)

    # Connect to API
    print_info("Connecting to ProjectX API...")
    try:
        client = ProjectXClient(PROJECTX_CONFIG)
        account_id = client.get_preferred_account_id()
        state["account_id"] = account_id
        print_success(f"Connected to account ID: {account_id}")
    except Exception as e:
        print_error(f"Connection failed: {e}")
        return 1

    # Pre-flight flat check
    print_info("\nPre-flight check: verifying account is FLAT...")
    try:
        response = client.api.position_search_open(account_id)
        if not response.get("success"):
            print_error(f"Position query failed: {response}")
            return 1

        positions = response.get("positions", [])
        if positions:
            print_error("GATE FAILED: Account has open positions!")
            print_error("Must be FLAT on all instruments to start workflow.")
            for pos in positions:
                qty = pos.get("size", 0)
                contract = pos.get("contractId", "")
                print_error(f"  - {contract}: {qty} contracts")
            return 1

        print_success("Account is FLAT - ready to proceed")

    except Exception as e:
        print_error(f"Pre-flight check failed: {e}")
        return 1

    # Save initial state
    save_state(state)
    save_response(
        "start_preflight",
        {
            "workflow_id": state["workflow_id"],
            "account_id": account_id,
            "flat_check": "PASSED",
            "timestamp": datetime.now().isoformat(),
        },
    )

    print_progress_bar(0)
    print_success("Workflow initialized!")

    if args.autonomous:
        print_info("Autonomous mode: proceeding to step-1...")
        time.sleep(2)
        return cmd_step_1(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-1")

    return 0


def cmd_step_1(args, state=None, client=None):
    """Step 1: Capture baseline metadata for all instruments."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 1: Capture Baseline Metadata")
    print_progress_bar(1)

    # Connect if needed
    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    update_step(state, 1, "in_progress")

    results = {}
    for symbol in TEST_INSTRUMENTS:
        print_info(f"\nProcessing {symbol}...")

        # Resolve contract
        contract_id = resolve_contract_id(client, symbol)
        if not contract_id:
            print_warning(f"Could not resolve contract for {symbol}")
            continue

        state["instruments"][symbol]["contract_id"] = contract_id
        print_info(f"  Contract: {contract_id}")

        # Fetch bars
        bars = fetch_historical_bars(client, contract_id, bars_needed=20)
        if not bars:
            print_warning(f"Could not fetch bars for {symbol}")
            continue

        # Calculate ATR
        atr = calculate_atr(bars, period=14)
        if atr is None:
            print_warning(f"Could not calculate ATR for {symbol}")
            continue

        last_price = get_last_price(bars)
        state["instruments"][symbol]["atr"] = atr
        state["instruments"][symbol]["last_price"] = last_price

        results[symbol] = {
            "contract_id": contract_id,
            "last_price": last_price,
            "atr": atr,
            "tick_size": get_tick_size(symbol),
            "tick_value": get_tick_value(symbol),
            "multiplier": get_multiplier(symbol),
        }

        print_success(f"  {symbol}: Price={last_price:.2f}, ATR={atr:.4f}")

    # Save results
    save_response("step1_baseline", results)
    update_step(state, 1, "completed")
    state["phase"] = "LONG"
    save_state(state)

    print_success(f"\nStep 1 completed: {len(results)}/{len(TEST_INSTRUMENTS)} instruments captured")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-2...")
        time.sleep(2)
        return cmd_step_2(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-2")

    return 0


def cmd_step_2(args, state=None, client=None):
    """Step 2: Create LONG bracket orders on all instruments."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 2: Create LONG Bracket Orders")
    print_progress_bar(2)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    if not account_id:
        account_id = client.get_preferred_account_id()
        state["account_id"] = account_id

    update_step(state, 2, "in_progress")
    atr_multiplier = 2.0

    for symbol in TEST_INSTRUMENTS:
        print_info(f"\nCreating LONG bracket for {symbol}...")

        inst = state["instruments"][symbol]
        contract_id = inst.get("contract_id")
        atr = inst.get("atr")

        if not contract_id or not atr:
            print_warning(f"Missing baseline data for {symbol}, skipping")
            continue

        # Place ENTRY order (Market BUY) first
        workflow_id = state.get("workflow_id")
        entry_tag = build_tag(symbol, "LONG", "ENTRY", workflow_id)
        entry_resp = client.api.order_place(
            account_id=account_id,
            contract_id=contract_id,
            type=2,  # Market
            side=0,  # Buy
            size=1,
            custom_tag=entry_tag,
        )
        save_response(f"step2_long_entry_{symbol}", entry_resp)

        if not entry_resp.get("success"):
            print_error(f"  Entry order failed: {entry_resp}")
            continue

        entry_order_id = entry_resp.get("orderId")
        inst["long"]["entry_order_id"] = entry_order_id
        print_success(f"  Entry placed: ID={entry_order_id}, tag={entry_tag}")

        # Wait for fill and get actual fill price from position
        # Poll up to 3 times with 0.5s intervals for position to appear
        actual_fill_price = None
        for _ in range(3):
            time.sleep(0.5)
            pos_resp = client.api.position_search_open(account_id)
            if pos_resp.get("success"):
                for pos in pos_resp.get("positions", []):
                    pos_contract = pos.get("contractId", "")
                    # Try exact contract match
                    if contract_id == pos_contract:
                        # API returns "averagePrice" not "avgPrice"
                        actual_fill_price = pos.get("averagePrice")
                        break
            if actual_fill_price:
                break

        if not actual_fill_price:
            # Last resort: fetch fresh quote as fallback
            bars = fetch_historical_bars(client, contract_id, bars_needed=1)
            if bars:
                actual_fill_price = get_last_price(bars)
                print_warning(f"  Position not found, using current price: {actual_fill_price}")
            else:
                actual_fill_price = inst.get("last_price", 0)
                print_warning(f"  Could not get fill price, using baseline: {actual_fill_price}")
        else:
            print_info(f"  Actual fill price: {actual_fill_price:.2f}")

        inst["long"]["entry_price"] = actual_fill_price

        # Calculate SL/TP from ACTUAL fill price (not stale baseline)
        sl_price = round_to_tick(actual_fill_price - (atr * atr_multiplier), symbol)
        tp_price = round_to_tick(actual_fill_price + (atr * atr_multiplier), symbol)

        inst["long"]["sl_price"] = sl_price
        inst["long"]["tp_price"] = tp_price

        print_info(f"  SL: {sl_price:.2f} ({atr_multiplier}x ATR below fill)")
        print_info(f"  TP: {tp_price:.2f} ({atr_multiplier}x ATR above fill)")

        # Place STOP LOSS order
        sl_tag = build_tag(symbol, "LONG", "SL", workflow_id)
        sl_resp = client.api.order_place(
            account_id=account_id,
            contract_id=contract_id,
            type=4,  # Stop
            side=1,  # Sell
            size=1,
            stop_price=sl_price,
            custom_tag=sl_tag,
            linked_order_id=entry_order_id,
        )
        save_response(f"step2_long_sl_{symbol}", sl_resp)

        if sl_resp.get("success"):
            inst["long"]["sl_order_id"] = sl_resp.get("orderId")
            print_success(f"  SL placed: ID={sl_resp.get('orderId')}, tag={sl_tag}")
        else:
            print_warning(f"  SL order failed: {sl_resp}")

        # Place TAKE PROFIT order
        tp_tag = build_tag(symbol, "LONG", "TP", workflow_id)
        tp_resp = client.api.order_place(
            account_id=account_id,
            contract_id=contract_id,
            type=1,  # Limit
            side=1,  # Sell
            size=1,
            limit_price=tp_price,
            custom_tag=tp_tag,
            linked_order_id=inst["long"].get("sl_order_id") or entry_order_id,
        )
        save_response(f"step2_long_tp_{symbol}", tp_resp)

        if tp_resp.get("success"):
            inst["long"]["tp_order_id"] = tp_resp.get("orderId")
            print_success(f"  TP placed: ID={tp_resp.get('orderId')}, tag={tp_tag}")
        else:
            print_warning(f"  TP order failed: {tp_resp}")

        time.sleep(0.1)  # Rate limiting

    update_step(state, 2, "completed")
    save_state(state)

    print_success("\nStep 2 completed: LONG bracket orders placed")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-3...")
        time.sleep(2)
        return cmd_step_3(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-3")

    return 0


def cmd_step_3(args, state=None, client=None):
    """Step 3: Verify LONG positions and tag persistence."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 3: Verify LONG Positions & Tag Persistence")
    print_progress_bar(3)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 3, "in_progress")

    # Query positions
    print_info("Querying open positions...")
    pos_resp = client.api.position_search_open(account_id)
    save_response("step3_positions", pos_resp)

    if not pos_resp.get("success"):
        print_error(f"Position query failed: {pos_resp}")
        return 1

    positions = pos_resp.get("positions", [])
    print_info(f"Found {len(positions)} position(s)")

    # Map positions to instruments
    position_count = 0
    for pos in positions:
        contract_id = pos.get("contractId", "")
        qty = pos.get("size", 0)
        avg_price = pos.get("averagePrice", 0)
        pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT

        symbol = extract_symbol_from_contract(contract_id)
        if symbol in state["instruments"] and pos_type == 1:  # LONG position
            state["instruments"][symbol]["long"]["entry_price"] = avg_price
            state["instruments"][symbol]["long"]["position_qty"] = qty
            position_count += 1
            print_success(f"  {symbol}: LONG {qty} @ {avg_price:.2f}")

    # Verify tags persist
    print_info("\nVerifying tag persistence on orders...")
    start_date = (datetime.now() - timedelta(hours=1)).isoformat()
    orders_resp = client.api.order_search(account_id, start_date)
    save_response("step3_orders", orders_resp)

    tag_check_passed = True
    tags_found = []
    if orders_resp.get("success"):
        orders = orders_resp.get("orders", [])
        for order in orders:
            tag = order.get("customTag") or ""
            if STRATEGY_NAME in tag:
                tags_found.append(tag)

        if tags_found:
            print_success(f"  Tags persisted: {len(tags_found)} orders with '{STRATEGY_NAME}' tag")
            for tag in tags_found[:6]:  # Show first 6
                print_info(f"    - {tag}")
        else:
            print_warning("  No tags with strategy name found!")
            tag_check_passed = False
    else:
        print_warning(f"  Order query failed: {orders_resp}")
        tag_check_passed = False

    state["test_results"]["tag_persistence"] = "PASS" if tag_check_passed else "FAIL"

    update_step(state, 3, "completed")
    save_state(state)

    print_success(f"\nStep 3 completed: {position_count} positions verified")
    print_info(f"Tag persistence: {'PASS' if tag_check_passed else 'FAIL'}")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-4...")
        time.sleep(2)
        return cmd_step_4(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-4")

    return 0


def cmd_step_4(args, state=None, client=None):
    """Step 4: Validate LONG P&L calculations."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 4: Validate LONG P&L Calculations")
    print_progress_bar(4)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 4, "in_progress")

    # Get current positions with P&L
    pos_resp = client.api.position_search_open(account_id)
    if not pos_resp.get("success"):
        print_error(f"Position query failed: {pos_resp}")
        return 1

    positions = pos_resp.get("positions", [])
    pnl_records = []

    # Note: ProjectX API does not provide P&L in position data
    # We calculate P&L ourselves and record for verification
    print_info("Note: API does not provide P&L - showing calculated values\n")

    for pos in positions:
        contract_id = pos.get("contractId", "")
        qty = pos.get("size", 0)
        avg_price = pos.get("averagePrice", 0)
        pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT

        symbol = extract_symbol_from_contract(contract_id)
        direction = "LONG" if pos_type == 1 else "SHORT"

        # Fetch current price
        bars = fetch_historical_bars(client, contract_id, bars_needed=5)
        current_price = get_last_price(bars) if bars else avg_price

        # Calculate P&L
        calc = calculate_pnl(avg_price, current_price, abs(qty), direction, symbol)
        calculated_pnl = calc["calculated_pnl"]
        price_move = current_price - avg_price
        tick_value = calc.get("tick_value", 0)
        tick_size = calc.get("tick_size", 0)

        print_info(f"\n{symbol} ({direction}):")
        print_info(f"  Entry: {avg_price:.2f}, Current: {current_price:.2f}")
        print_info(f"  Price Move: {price_move:+.2f} ({price_move/tick_size:.1f} ticks)")
        print_info(f"  Tick Value: ${tick_value:.2f}, Tick Size: {tick_size}")
        print_success(f"  Calculated P&L: ${calculated_pnl:+.2f}")

        # Record for later comparison when positions close
        pnl_records.append(
            {
                "symbol": symbol,
                "direction": direction,
                "entry_price": avg_price,
                "current_price": current_price,
                "calculated_pnl": calculated_pnl,
                "price_move": price_move,
            }
        )

    state["test_results"]["long_pnl_records"] = pnl_records
    save_response(
        "step4_pnl_validation",
        {
            "positions": len(positions),
            "pnl_records": pnl_records,
        },
    )

    update_step(state, 4, "completed")
    save_state(state)

    print_success(f"\nStep 4 completed: {len(positions)} P&L calculations recorded")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-5...")
        time.sleep(2)
        return cmd_step_5(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-5")

    return 0


def cmd_step_5(args, state=None, client=None):
    """Step 5: Self-healing test (LONG) - Cancel SL, detect, recreate."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 5: Self-Healing Test (LONG)")
    print_progress_bar(5)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 5, "in_progress")

    # Find first instrument with SL order
    target_symbol = None
    target_sl_id = None
    for symbol in TEST_INSTRUMENTS:
        sl_id = state["instruments"][symbol]["long"].get("sl_order_id")
        if sl_id:
            target_symbol = symbol
            target_sl_id = sl_id
            break

    if not target_symbol or not target_sl_id:
        print_warning("No SL order found to test self-healing")
        state["test_results"]["long_self_heal"] = "SKIPPED"
        update_step(state, 5, "completed")
        save_state(state)
        return 0

    print_info(f"Target: {target_symbol} SL order ID={target_sl_id}")
    print_warning(f"\nCANCELLING SL ORDER {target_sl_id} for {target_symbol}...")
    print_info("(Check TopStep to verify the order disappears)")

    # Cancel the SL order
    cancel_resp = client.api.order_cancel(account_id, target_sl_id)
    save_response(f"step5_cancel_sl_{target_symbol}", cancel_resp)

    if not cancel_resp.get("success"):
        error_code = cancel_resp.get("errorCode")
        # errorCode 5 = order doesn't exist (already filled or cancelled)
        if error_code == 5:
            print_warning(f"SL order {target_sl_id} no longer exists (already filled or cancelled)")
            print_info("This is expected if the position was closed by TP fill or manual action.")
            state["test_results"]["long_self_heal"] = "SKIPPED_ORDER_GONE"
            update_step(state, 5, "completed")
            save_state(state)
            if state.get("autonomous"):
                print_info("Autonomous mode: proceeding to step-6...")
                time.sleep(2)
                return cmd_step_6(args, state, client)
            else:
                print_next_command("python tools/pnl_validation_workflow.py step-6")
            return 0
        else:
            print_error(f"Cancel failed: {cancel_resp}")
            state["test_results"]["long_self_heal"] = "CANCEL_FAILED"
            update_step(state, 5, "completed")
            save_state(state)
            return 1

    print_success(f"SL order {target_sl_id} cancel request accepted")

    # Poll to confirm cancellation propagated (0.5s intervals, up to 5 attempts)
    print_info("\nPolling to confirm SL order cancellation...")
    max_attempts = 5
    poll_interval = 0.5
    cancellation_confirmed = False

    for attempt in range(1, max_attempts + 1):
        time.sleep(poll_interval)
        start_date = (datetime.now() - timedelta(hours=1)).isoformat()
        orders_resp = client.api.order_search(account_id, start_date)

        open_sl_orders = []
        if orders_resp.get("success"):
            orders = orders_resp.get("orders", [])
            for order in orders:
                if order.get("status") == 1:  # Open
                    tag = order.get("customTag") or ""
                    # Use startswith to avoid "ES" matching "MES"
                    if tag.startswith(f"{target_symbol}_LONG_SL"):
                        open_sl_orders.append(order)

        if not open_sl_orders:
            print_success(f"  Attempt {attempt}/{max_attempts}: Confirmed cancelled")
            cancellation_confirmed = True
            break
        else:
            print_info(f"  Attempt {attempt}/{max_attempts}: Order still open, waiting...")

    if not cancellation_confirmed:
        print_error(f"\nSL order {target_sl_id} still exists after {max_attempts} polling attempts!")
        print_warning("The cancel API returned success, but the order remains open.")
        print_info("\nPossible causes:")
        print_info("  1. API latency - cancellation still propagating")
        print_info("  2. Order already filled - check if position changed")
        print_info("  3. Order locked by exchange - check TopStep UI")
        print_info("\nRecommended actions:")
        print_info("  - Check TopStep UI to verify order status")
        print_info("  - If order is gone in UI, re-run step-5")
        print_info("  - If order persists, manually cancel via TopStep UI")
        state["test_results"]["long_self_heal"] = "CANCEL_NOT_CONFIRMED"
        update_step(state, 5, "completed")
        save_state(state)
        return 1

    # Only recreate if cancellation was confirmed
    print_info("\nRecreating SL order...")
    inst = state["instruments"][target_symbol]
    contract_id = inst.get("contract_id")
    sl_price = inst["long"].get("sl_price")

    # Use unique tag for recreated order (add HEAL suffix)
    heal_suffix = datetime.now().strftime("%H%M%S")
    sl_tag = f"{target_symbol}_LONG_SLHEAL_{STRATEGY_NAME}_{heal_suffix}"
    sl_resp = client.api.order_place(
        account_id=account_id,
        contract_id=contract_id,
        type=4,  # Stop
        side=1,  # Sell
        size=1,
        stop_price=sl_price,
        custom_tag=sl_tag,
    )
    save_response(f"step5_recreate_sl_{target_symbol}", sl_resp)

    if sl_resp.get("success"):
        new_sl_id = sl_resp.get("orderId")
        inst["long"]["sl_order_id"] = new_sl_id
        print_success(f"SL order recreated: ID={new_sl_id}")
        state["test_results"]["long_self_heal"] = "PASS"
    else:
        print_error(f"Failed to recreate SL: {sl_resp}")
        state["test_results"]["long_self_heal"] = "RECREATE_FAILED"

    update_step(state, 5, "completed")
    save_state(state)

    print_success("\nStep 5 completed: Self-healing test done")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-6...")
        time.sleep(2)
        return cmd_step_6(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-6")

    return 0


def cmd_step_6(args, state=None, client=None):
    """Step 6: Wait for natural SL/TP fills (LONG)."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 6: Wait for Natural Fills (LONG)")
    print_progress_bar(6)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 6, "in_progress")

    poll_interval = DEFAULT_POLL_INTERVAL
    sl_fill_required = 1
    tp_fill_required = 1

    print_info(f"Waiting for at least {sl_fill_required} SL and {tp_fill_required} TP fills...")
    print_info(f"Polling every {poll_interval} seconds (Ctrl+C to stop)")

    try:
        while True:
            sl_fills = 0
            tp_fills = 0

            # Check order statuses
            start_date = (datetime.now() - timedelta(hours=2)).isoformat()
            orders_resp = client.api.order_search(account_id, start_date)

            if orders_resp.get("success"):
                orders = orders_resp.get("orders", [])
                for order in orders:
                    tag = order.get("customTag") or ""
                    status = order.get("status")

                    if STRATEGY_NAME not in tag:
                        continue

                    if "_LONG_SL_" in tag and status == 2:  # Filled
                        sl_fills += 1
                    elif "_LONG_TP_" in tag and status == 2:  # Filled
                        tp_fills += 1

            state["test_results"]["long_sl_fill_count"] = sl_fills
            state["test_results"]["long_tp_fill_count"] = tp_fills
            save_state(state)

            sl_ok = "\u2705" if sl_fills >= sl_fill_required else "\u23f3"
            tp_ok = "\u2705" if tp_fills >= tp_fill_required else "\u23f3"

            # Display phase and fill status
            print_info("  Phase: LONG TESTS")
            print_info(
                f"  SL Fills: {sl_fills}/{sl_fill_required} {sl_ok}  |  "
                f"TP Fills: {tp_fills}/{tp_fill_required} {tp_ok}"
            )

            # Show position status
            pos_resp = client.api.position_search_open(account_id)
            if pos_resp.get("success"):
                positions = pos_resp.get("positions", [])
                if positions:
                    print_info("  Open Positions:")
                    for pos in positions:
                        contract_id = pos.get("contractId", "")
                        qty = pos.get("size", 0)
                        avg_price = pos.get("averagePrice", 0)
                        sym = extract_symbol_from_contract(contract_id)
                        print_info(f"    {sym}: LONG {qty} @ {avg_price:.2f}")

            if sl_fills >= sl_fill_required and tp_fills >= tp_fill_required:
                print_success("Gate passed: Required fills achieved!")
                break

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print_warning("\nPolling interrupted by user")
        print_info("Resume with: python tools/pnl_validation_workflow.py step-6")
        return 0

    update_step(state, 6, "completed")
    save_state(state)

    print_success("\nStep 6 completed: Natural fills detected")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-7...")
        time.sleep(2)
        return cmd_step_7(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-7")

    return 0


def cmd_step_7(args, state=None, client=None):
    """Step 7: Close remaining LONG positions.

    IMPORTANT: Cancels bracket orders FIRST to prevent race condition where
    a bracket fills while we're sending close orders, which would cause our
    SELL order to create a new SHORT instead of closing the LONG.
    """
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 7: Close Remaining LONG Positions")
    print_progress_bar(7)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 7, "in_progress")

    # CRITICAL: Cancel bracket orders FIRST to prevent race condition
    # If we don't do this, a bracket can fill between position query and close order,
    # causing our SELL to create a new SHORT instead of closing the LONG
    print_info("Cancelling bracket orders FIRST (prevent race condition)...")
    start_date = (datetime.now() - timedelta(hours=2)).isoformat()
    orders_resp = client.api.order_search(account_id, start_date)
    cancelled_count = 0
    if orders_resp.get("success"):
        for order in orders_resp.get("orders", []):
            tag = order.get("customTag") or ""
            status = order.get("status")
            order_id = order.get("id")
            # Cancel any open LONG bracket orders
            if ("_LONG_SL_" in tag or "_LONG_TP_" in tag) and status == 1:
                cancel_resp = client.api.order_cancel(account_id, order_id)
                if cancel_resp.get("success"):
                    print_success(f"  Cancelled: {tag}")
                    cancelled_count += 1
                elif cancel_resp.get("errorCode") == 5:
                    print_info(f"  Already gone: {tag}")
                else:
                    print_warning(f"  Failed to cancel {tag}: {cancel_resp}")

    print_info(f"Cancelled {cancelled_count} bracket orders")
    time.sleep(0.5)  # Wait for cancels to propagate

    # NOW query positions (after brackets are cancelled)
    pos_resp = client.api.position_search_open(account_id)
    if not pos_resp.get("success"):
        print_error(f"Position query failed: {pos_resp}")
        return 1

    positions = pos_resp.get("positions", [])
    print_info(f"\nFound {len(positions)} open position(s)")

    closed_count = 0
    close_counter = 0  # Unique counter for close order tags
    for pos in positions:
        contract_id = pos.get("contractId", "")
        qty = pos.get("size", 0)
        pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT
        symbol = extract_symbol_from_contract(contract_id)

        if pos_type == 1:  # LONG position
            print_info(f"\nClosing {symbol} LONG {qty} @ market...")

            # Market SELL to close
            workflow_id = state.get("workflow_id")
            close_counter += 1
            # Add counter to ensure unique tag even for same-symbol positions
            close_tag = f"{symbol}_LONG_CLOSE{close_counter}_{STRATEGY_NAME}_{workflow_id}"
            close_resp = client.api.order_place(
                account_id=account_id,
                contract_id=contract_id,
                type=2,  # Market
                side=1,  # Sell
                size=abs(qty),
                custom_tag=close_tag,
            )
            save_response(f"step7_close_{symbol}_{close_counter}", close_resp)

            if close_resp.get("success"):
                print_success(f"  Close order placed: ID={close_resp.get('orderId')}")
                closed_count += 1
                if symbol in state["instruments"]:
                    state["instruments"][symbol]["long"]["closed"] = True
            else:
                print_error(f"  Close failed: {close_resp}")

            time.sleep(0.2)

    # Wait for closes to fill
    time.sleep(2)

    # Verify flat
    pos_resp = client.api.position_search_open(account_id)
    remaining = len(pos_resp.get("positions", [])) if pos_resp.get("success") else -1

    # Verify all bracket orders are now gone
    time.sleep(0.5)
    orders_resp = client.api.order_search(account_id, start_date)
    open_bracket_orders = 0
    if orders_resp.get("success"):
        for order in orders_resp.get("orders", []):
            tag = order.get("customTag") or ""
            if ("_LONG_SL_" in tag or "_LONG_TP_" in tag) and order.get("status") == 1:
                open_bracket_orders += 1
                print_warning(f"  Still open after cleanup: {tag}")

    if remaining == 0 and open_bracket_orders == 0:
        print_success(f"Account is FLAT, {cancelled_count} orphan orders cleaned up")
        state["test_results"]["long_bracket_cleanup"] = "PASS"
    elif remaining == 0:
        print_warning(f"Account FLAT but {open_bracket_orders} bracket orders still open")
        state["test_results"]["long_bracket_cleanup"] = "PARTIAL"
    else:
        print_warning(f"Still have {remaining} position(s)")
        state["test_results"]["long_bracket_cleanup"] = "PARTIAL"

    update_step(state, 7, "completed")
    state["phase"] = "SHORT"
    save_state(state)

    print_success(f"\nStep 7 completed: {closed_count} positions closed")
    print_info("LONG phase complete. Starting SHORT phase.")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-8...")
        time.sleep(2)
        return cmd_step_8(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-8")

    return 0


def cmd_step_8(args, state=None, client=None):
    """Step 8: Create SHORT bracket orders on all instruments."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 8: Create SHORT Bracket Orders")
    print_progress_bar(8)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 8, "in_progress")
    atr_multiplier = 0.25  # 8x tighter for faster testing

    # Refresh prices
    for symbol in TEST_INSTRUMENTS:
        inst = state["instruments"][symbol]
        contract_id = inst.get("contract_id")

        if not contract_id:
            continue

        bars = fetch_historical_bars(client, contract_id, bars_needed=20)
        if bars:
            inst["last_price"] = get_last_price(bars)
            inst["atr"] = calculate_atr(bars, period=14) or inst.get("atr")

    for symbol in TEST_INSTRUMENTS:
        print_info(f"\nCreating SHORT bracket for {symbol}...")

        inst = state["instruments"][symbol]
        contract_id = inst.get("contract_id")
        atr = inst.get("atr")

        if not contract_id or not atr:
            print_warning(f"Missing baseline data for {symbol}, skipping")
            continue

        # Place ENTRY order (Market SELL) first
        workflow_id = state.get("workflow_id")
        entry_tag = build_tag(symbol, "SHORT", "ENTRY", workflow_id)
        entry_resp = client.api.order_place(
            account_id=account_id,
            contract_id=contract_id,
            type=2,  # Market
            side=1,  # Sell
            size=1,
            custom_tag=entry_tag,
        )
        save_response(f"step8_short_entry_{symbol}", entry_resp)

        if not entry_resp.get("success"):
            print_error(f"  Entry order failed: {entry_resp}")
            continue

        entry_order_id = entry_resp.get("orderId")
        inst["short"]["entry_order_id"] = entry_order_id
        print_success(f"  Entry placed: ID={entry_order_id}, tag={entry_tag}")

        # Wait for fill and get actual fill price from position
        # Poll up to 3 times with 0.5s intervals for position to appear
        actual_fill_price = None
        for _ in range(3):
            time.sleep(0.5)
            pos_resp = client.api.position_search_open(account_id)
            if pos_resp.get("success"):
                for pos in pos_resp.get("positions", []):
                    pos_contract = pos.get("contractId", "")
                    # Try exact contract match
                    if contract_id == pos_contract:
                        # API returns "averagePrice" not "avgPrice"
                        actual_fill_price = pos.get("averagePrice")
                        break
            if actual_fill_price:
                break

        if not actual_fill_price:
            # Last resort: fetch fresh quote as fallback
            bars = fetch_historical_bars(client, contract_id, bars_needed=1)
            if bars:
                actual_fill_price = get_last_price(bars)
                print_warning(f"  Position not found, using current price: {actual_fill_price}")
            else:
                actual_fill_price = inst.get("last_price", 0)
                print_warning(f"  Could not get fill price, using baseline: {actual_fill_price}")
        else:
            print_info(f"  Actual fill price: {actual_fill_price:.2f}")

        inst["short"]["entry_price"] = actual_fill_price

        # Calculate SL/TP from ACTUAL fill price (inverted for short)
        sl_price = round_to_tick(actual_fill_price + (atr * atr_multiplier), symbol)
        tp_price = round_to_tick(actual_fill_price - (atr * atr_multiplier), symbol)

        inst["short"]["sl_price"] = sl_price
        inst["short"]["tp_price"] = tp_price

        print_info(f"  SL: {sl_price:.2f} ({atr_multiplier}x ATR above fill)")
        print_info(f"  TP: {tp_price:.2f} ({atr_multiplier}x ATR below fill)")

        # Place STOP LOSS order (BUY to close short)
        sl_tag = build_tag(symbol, "SHORT", "SL", workflow_id)
        sl_resp = client.api.order_place(
            account_id=account_id,
            contract_id=contract_id,
            type=4,  # Stop
            side=0,  # Buy
            size=1,
            stop_price=sl_price,
            custom_tag=sl_tag,
            linked_order_id=entry_order_id,
        )
        save_response(f"step8_short_sl_{symbol}", sl_resp)

        if sl_resp.get("success"):
            inst["short"]["sl_order_id"] = sl_resp.get("orderId")
            print_success(f"  SL placed: ID={sl_resp.get('orderId')}, tag={sl_tag}")
        else:
            print_warning(f"  SL order failed: {sl_resp}")

        # Place TAKE PROFIT order (LIMIT BUY to close short)
        tp_tag = build_tag(symbol, "SHORT", "TP", workflow_id)
        tp_resp = client.api.order_place(
            account_id=account_id,
            contract_id=contract_id,
            type=1,  # Limit
            side=0,  # Buy
            size=1,
            limit_price=tp_price,
            custom_tag=tp_tag,
            linked_order_id=inst["short"].get("sl_order_id") or entry_order_id,
        )
        save_response(f"step8_short_tp_{symbol}", tp_resp)

        if tp_resp.get("success"):
            inst["short"]["tp_order_id"] = tp_resp.get("orderId")
            print_success(f"  TP placed: ID={tp_resp.get('orderId')}, tag={tp_tag}")
        else:
            print_warning(f"  TP order failed: {tp_resp}")

        time.sleep(0.1)

    update_step(state, 8, "completed")
    save_state(state)

    print_success("\nStep 8 completed: SHORT bracket orders placed")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-9...")
        time.sleep(2)
        return cmd_step_9(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-9")

    return 0


def cmd_step_9(args, state=None, client=None):
    """Step 9: Verify SHORT positions and tag persistence."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 9: Verify SHORT Positions & Tag Persistence")
    print_progress_bar(9)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 9, "in_progress")

    # Query positions
    pos_resp = client.api.position_search_open(account_id)
    save_response("step9_positions", pos_resp)

    if not pos_resp.get("success"):
        print_error(f"Position query failed: {pos_resp}")
        return 1

    positions = pos_resp.get("positions", [])
    print_info(f"Found {len(positions)} position(s)")

    position_count = 0
    for pos in positions:
        contract_id = pos.get("contractId", "")
        qty = pos.get("size", 0)
        avg_price = pos.get("averagePrice", 0)
        pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT

        symbol = extract_symbol_from_contract(contract_id)
        if symbol in state["instruments"] and pos_type == 2:  # SHORT position
            state["instruments"][symbol]["short"]["entry_price"] = avg_price
            state["instruments"][symbol]["short"]["position_qty"] = -qty  # Store as negative for consistency
            position_count += 1
            print_success(f"  {symbol}: SHORT -{qty} @ {avg_price:.2f}")

    update_step(state, 9, "completed")
    save_state(state)

    print_success(f"\nStep 9 completed: {position_count} SHORT positions verified")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-10...")
        time.sleep(2)
        return cmd_step_10(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-10")

    return 0


def cmd_step_10(args, state=None, client=None):
    """Step 10: Validate SHORT P&L calculations."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 10: Validate SHORT P&L Calculations")
    print_progress_bar(10)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 10, "in_progress")

    pos_resp = client.api.position_search_open(account_id)
    if not pos_resp.get("success"):
        print_error(f"Position query failed: {pos_resp}")
        return 1

    positions = pos_resp.get("positions", [])
    pnl_records = []

    # Note: ProjectX API does not provide P&L in position data
    # We calculate P&L ourselves and record for verification
    print_info("Note: API does not provide P&L - showing calculated values\n")

    for pos in positions:
        contract_id = pos.get("contractId", "")
        qty = pos.get("size", 0)
        avg_price = pos.get("averagePrice", 0)
        pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT

        symbol = extract_symbol_from_contract(contract_id)
        direction = "LONG" if pos_type == 1 else "SHORT"

        bars = fetch_historical_bars(client, contract_id, bars_needed=5)
        current_price = get_last_price(bars) if bars else avg_price

        calc = calculate_pnl(avg_price, current_price, abs(qty), direction, symbol)
        calculated_pnl = calc["calculated_pnl"]
        price_move = current_price - avg_price
        tick_value = calc.get("tick_value", 0)
        tick_size = calc.get("tick_size", 0)

        print_info(f"\n{symbol} ({direction}):")
        print_info(f"  Entry: {avg_price:.2f}, Current: {current_price:.2f}")
        print_info(f"  Price Move: {price_move:+.2f} ({price_move/tick_size:.1f} ticks)")
        print_info(f"  Tick Value: ${tick_value:.2f}, Tick Size: {tick_size}")
        print_success(f"  Calculated P&L: ${calculated_pnl:+.2f}")

        pnl_records.append(
            {
                "symbol": symbol,
                "direction": direction,
                "entry_price": avg_price,
                "current_price": current_price,
                "calculated_pnl": calculated_pnl,
                "price_move": price_move,
            }
        )

    state["test_results"]["short_pnl_records"] = pnl_records
    save_response(
        "step10_pnl_validation",
        {
            "positions": len(positions),
            "pnl_records": pnl_records,
        },
    )

    update_step(state, 10, "completed")
    save_state(state)

    print_success(f"\nStep 10 completed: {len(positions)} P&L calculations recorded")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-11...")
        time.sleep(2)
        return cmd_step_11(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-11")

    return 0


def cmd_step_11(args, state=None, client=None):
    """Step 11: Self-healing test (SHORT) - Cancel TP, detect, recreate."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 11: Self-Healing Test (SHORT)")
    print_progress_bar(11)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 11, "in_progress")

    # Find first instrument with TP order (test TP for SHORT phase)
    target_symbol = None
    target_tp_id = None
    for symbol in TEST_INSTRUMENTS:
        tp_id = state["instruments"][symbol]["short"].get("tp_order_id")
        if tp_id:
            target_symbol = symbol
            target_tp_id = tp_id
            break

    if not target_symbol or not target_tp_id:
        print_warning("No TP order found to test self-healing")
        state["test_results"]["short_self_heal"] = "SKIPPED"
        update_step(state, 11, "completed")
        save_state(state)
        return 0

    print_info(f"Target: {target_symbol} TP order ID={target_tp_id}")
    print_warning(f"\nCANCELLING TP ORDER {target_tp_id} for {target_symbol}...")

    cancel_resp = client.api.order_cancel(account_id, target_tp_id)
    save_response(f"step11_cancel_tp_{target_symbol}", cancel_resp)

    if not cancel_resp.get("success"):
        error_code = cancel_resp.get("errorCode")
        # errorCode 5 = order doesn't exist (already filled or cancelled)
        if error_code == 5:
            print_warning(f"TP order {target_tp_id} no longer exists (already filled or cancelled)")
            print_info("This is expected if the position was closed by SL fill or manual action.")
            state["test_results"]["short_self_heal"] = "SKIPPED_ORDER_GONE"
            update_step(state, 11, "completed")
            save_state(state)
            if state.get("autonomous"):
                print_info("Autonomous mode: proceeding to step-12...")
                time.sleep(2)
                return cmd_step_12(args, state, client)
            else:
                print_next_command("python tools/pnl_validation_workflow.py step-12")
            return 0
        else:
            print_error(f"Cancel failed: {cancel_resp}")
            state["test_results"]["short_self_heal"] = "CANCEL_FAILED"
            update_step(state, 11, "completed")
            save_state(state)
            return 1

    print_success(f"TP order {target_tp_id} cancel request accepted")

    # Poll to confirm cancellation propagated (0.5s intervals, up to 5 attempts)
    print_info("\nPolling to confirm TP order cancellation...")
    max_attempts = 5
    poll_interval = 0.5
    cancellation_confirmed = False

    for attempt in range(1, max_attempts + 1):
        time.sleep(poll_interval)
        start_date = (datetime.now() - timedelta(hours=1)).isoformat()
        orders_resp = client.api.order_search(account_id, start_date)

        open_tp_orders = []
        if orders_resp.get("success"):
            orders = orders_resp.get("orders", [])
            for order in orders:
                if order.get("status") == 1:  # Open
                    tag = order.get("customTag") or ""
                    # Use startswith to avoid "ES" matching "MES"
                    if tag.startswith(f"{target_symbol}_SHORT_TP"):
                        open_tp_orders.append(order)

        if not open_tp_orders:
            print_success(f"  Attempt {attempt}/{max_attempts}: Confirmed cancelled")
            cancellation_confirmed = True
            break
        else:
            print_info(f"  Attempt {attempt}/{max_attempts}: Order still open, waiting...")

    if not cancellation_confirmed:
        print_error(f"\nTP order {target_tp_id} still exists after {max_attempts} polling attempts!")
        print_warning("The cancel API returned success, but the order remains open.")
        print_info("\nPossible causes:")
        print_info("  1. API latency - cancellation still propagating")
        print_info("  2. Order already filled - check if position changed")
        print_info("  3. Order locked by exchange - check TopStep UI")
        print_info("\nRecommended actions:")
        print_info("  - Check TopStep UI to verify order status")
        print_info("  - If order is gone in UI, re-run step-11")
        print_info("  - If order persists, manually cancel via TopStep UI")
        state["test_results"]["short_self_heal"] = "CANCEL_NOT_CONFIRMED"
        update_step(state, 11, "completed")
        save_state(state)
        return 1

    # Only recreate if cancellation was confirmed
    print_info("\nRecreating TP order...")
    inst = state["instruments"][target_symbol]
    contract_id = inst.get("contract_id")
    tp_price = inst["short"].get("tp_price")

    # Use unique tag for recreated order (add HEAL suffix)
    heal_suffix = datetime.now().strftime("%H%M%S")
    tp_tag = f"{target_symbol}_SHORT_TPHEAL_{STRATEGY_NAME}_{heal_suffix}"
    tp_resp = client.api.order_place(
        account_id=account_id,
        contract_id=contract_id,
        type=1,  # Limit
        side=0,  # Buy
        size=1,
        limit_price=tp_price,
        custom_tag=tp_tag,
    )
    save_response(f"step11_recreate_tp_{target_symbol}", tp_resp)

    if tp_resp.get("success"):
        new_tp_id = tp_resp.get("orderId")
        inst["short"]["tp_order_id"] = new_tp_id
        print_success(f"TP order recreated: ID={new_tp_id}")
        state["test_results"]["short_self_heal"] = "PASS"
    else:
        print_error(f"Failed to recreate TP: {tp_resp}")
        state["test_results"]["short_self_heal"] = "RECREATE_FAILED"

    update_step(state, 11, "completed")
    save_state(state)

    print_success("\nStep 11 completed: Self-healing test done")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-12...")
        time.sleep(2)
        return cmd_step_12(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-12")

    return 0


def cmd_step_12(args, state=None, client=None):
    """Step 12: Wait for natural SL/TP fills (SHORT)."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 12: Wait for Natural Fills (SHORT)")
    print_progress_bar(12)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 12, "in_progress")

    poll_interval = DEFAULT_POLL_INTERVAL
    sl_fill_required = 1
    tp_fill_required = 1

    print_info(f"Waiting for at least {sl_fill_required} SL and {tp_fill_required} TP fills...")
    print_info(f"Polling every {poll_interval} seconds (Ctrl+C to stop)")

    try:
        while True:
            sl_fills = 0
            tp_fills = 0

            start_date = (datetime.now() - timedelta(hours=2)).isoformat()
            orders_resp = client.api.order_search(account_id, start_date)

            if orders_resp.get("success"):
                orders = orders_resp.get("orders", [])
                for order in orders:
                    tag = order.get("customTag") or ""
                    status = order.get("status")

                    if STRATEGY_NAME not in tag:
                        continue

                    if "_SHORT_SL_" in tag and status == 2:
                        sl_fills += 1
                    elif "_SHORT_TP_" in tag and status == 2:
                        tp_fills += 1

            state["test_results"]["short_sl_fill_count"] = sl_fills
            state["test_results"]["short_tp_fill_count"] = tp_fills
            save_state(state)

            sl_ok = "\u2705" if sl_fills >= sl_fill_required else "\u23f3"
            tp_ok = "\u2705" if tp_fills >= tp_fill_required else "\u23f3"

            # Display phase and fill status
            print_info("  Phase: SHORT TESTS")
            print_info(
                f"  SL Fills: {sl_fills}/{sl_fill_required} {sl_ok}  |  "
                f"TP Fills: {tp_fills}/{tp_fill_required} {tp_ok}"
            )

            # Show position status
            pos_resp = client.api.position_search_open(account_id)
            if pos_resp.get("success"):
                positions = pos_resp.get("positions", [])
                if positions:
                    print_info("  Open Positions:")
                    for pos in positions:
                        contract_id = pos.get("contractId", "")
                        qty = pos.get("size", 0)
                        avg_price = pos.get("averagePrice", 0)
                        sym = extract_symbol_from_contract(contract_id)
                        print_info(f"    {sym}: SHORT {qty} @ {avg_price:.2f}")

            if sl_fills >= sl_fill_required and tp_fills >= tp_fill_required:
                print_success("Gate passed: Required fills achieved!")
                break

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print_warning("\nPolling interrupted by user")
        print_info("Resume with: python tools/pnl_validation_workflow.py step-12")
        return 0

    update_step(state, 12, "completed")
    save_state(state)

    print_success("\nStep 12 completed: Natural fills detected")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-13...")
        time.sleep(2)
        return cmd_step_13(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-13")

    return 0


def cmd_step_13(args, state=None, client=None):
    """Step 13: Close remaining SHORT positions.

    IMPORTANT: Cancels bracket orders FIRST to prevent race condition where
    a bracket fills while we're sending close orders, which would cause our
    BUY order to create a new LONG instead of closing the SHORT.
    """
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 13: Close Remaining SHORT Positions")
    print_progress_bar(13)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    update_step(state, 13, "in_progress")

    # CRITICAL: Cancel bracket orders FIRST to prevent race condition
    # If we don't do this, a bracket can fill between position query and close order,
    # causing our BUY to create a new LONG instead of closing the SHORT
    print_info("Cancelling bracket orders FIRST (prevent race condition)...")
    start_date = (datetime.now() - timedelta(hours=2)).isoformat()
    orders_resp = client.api.order_search(account_id, start_date)
    cancelled_count = 0
    if orders_resp.get("success"):
        for order in orders_resp.get("orders", []):
            tag = order.get("customTag") or ""
            status = order.get("status")
            order_id = order.get("id")
            # Cancel any open SHORT bracket orders
            if ("_SHORT_SL_" in tag or "_SHORT_TP_" in tag) and status == 1:
                cancel_resp = client.api.order_cancel(account_id, order_id)
                if cancel_resp.get("success"):
                    print_success(f"  Cancelled: {tag}")
                    cancelled_count += 1
                elif cancel_resp.get("errorCode") == 5:
                    print_info(f"  Already gone: {tag}")
                else:
                    print_warning(f"  Failed to cancel {tag}: {cancel_resp}")

    print_info(f"Cancelled {cancelled_count} bracket orders")
    time.sleep(0.5)  # Wait for cancels to propagate

    # NOW query positions (after brackets are cancelled)
    pos_resp = client.api.position_search_open(account_id)
    if not pos_resp.get("success"):
        print_error(f"Position query failed: {pos_resp}")
        return 1

    positions = pos_resp.get("positions", [])
    print_info(f"\nFound {len(positions)} open position(s)")

    closed_count = 0
    close_counter = 0  # Unique counter for close order tags
    for pos in positions:
        contract_id = pos.get("contractId", "")
        qty = pos.get("size", 0)
        pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT
        symbol = extract_symbol_from_contract(contract_id)

        if pos_type == 2:  # SHORT position
            print_info(f"\nClosing {symbol} SHORT -{qty} @ market...")

            # Market BUY to close
            workflow_id = state.get("workflow_id")
            close_counter += 1
            # Add counter to ensure unique tag even for same-symbol positions
            close_tag = f"{symbol}_SHORT_CLOSE{close_counter}_{STRATEGY_NAME}_{workflow_id}"
            close_resp = client.api.order_place(
                account_id=account_id,
                contract_id=contract_id,
                type=2,  # Market
                side=0,  # Buy
                size=abs(qty),
                custom_tag=close_tag,
            )
            save_response(f"step13_close_{symbol}_{close_counter}", close_resp)

            if close_resp.get("success"):
                print_success(f"  Close order placed: ID={close_resp.get('orderId')}")
                closed_count += 1
                if symbol in state["instruments"]:
                    state["instruments"][symbol]["short"]["closed"] = True
            else:
                print_error(f"  Close failed: {close_resp}")

            time.sleep(0.2)

    time.sleep(2)

    pos_resp = client.api.position_search_open(account_id)
    remaining = len(pos_resp.get("positions", [])) if pos_resp.get("success") else -1

    # Verify all bracket orders are now gone
    print_info("\nVerifying bracket order cleanup...")
    orders_resp = client.api.order_search(account_id, start_date)
    open_bracket_orders = 0
    if orders_resp.get("success"):
        for order in orders_resp.get("orders", []):
            tag = order.get("customTag") or ""
            if ("_SHORT_SL_" in tag or "_SHORT_TP_" in tag) and order.get("status") == 1:
                open_bracket_orders += 1
                print_warning(f"  Still open after cleanup: {tag}")

    if remaining == 0 and open_bracket_orders == 0:
        print_success(f"Account is FLAT, {cancelled_count} orphan orders cleaned up")
        state["test_results"]["short_bracket_cleanup"] = "PASS"
    elif remaining == 0:
        print_warning(f"Account FLAT but {open_bracket_orders} bracket orders still open")
        state["test_results"]["short_bracket_cleanup"] = "PARTIAL"
    else:
        print_warning(f"Still have {remaining} position(s)")
        state["test_results"]["short_bracket_cleanup"] = "PARTIAL"

    update_step(state, 13, "completed")
    save_state(state)

    print_success(f"\nStep 13 completed: {closed_count} positions closed")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-14...")
        time.sleep(2)
        return cmd_step_14(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-14")

    return 0


def cmd_step_14(args, state=None, client=None):
    """Step 14: Generate final report."""
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 14: Generate Final Report", style="bright_green")
    print_progress_bar(14)

    update_step(state, 14, "in_progress")

    results = state.get("test_results", {})

    # Build summary
    print_header("FINAL TEST RESULTS")

    # Tag persistence
    tag_result = results.get("tag_persistence", "N/A")
    tag_icon = "\u2705" if tag_result == "PASS" else "\u274c"
    print_info(f"Tag Persistence: {tag_icon} {tag_result}")

    # Self-healing
    long_heal = results.get("long_self_heal", "N/A")
    short_heal = results.get("short_self_heal", "N/A")
    heal_icon_l = "\u2705" if long_heal == "PASS" else "\u274c" if "FAIL" in str(long_heal) else "\u2754"
    heal_icon_s = "\u2705" if short_heal == "PASS" else "\u274c" if "FAIL" in str(short_heal) else "\u2754"
    print_info(f"Self-Healing (LONG): {heal_icon_l} {long_heal}")
    print_info(f"Self-Healing (SHORT): {heal_icon_s} {short_heal}")

    # Bracket cleanup
    long_cleanup = results.get("long_bracket_cleanup", "N/A")
    short_cleanup = results.get("short_bracket_cleanup", "N/A")
    cleanup_icon_l = "\u2705" if long_cleanup == "PASS" else "\u274c"
    cleanup_icon_s = "\u2705" if short_cleanup == "PASS" else "\u274c"
    print_info(f"Bracket Cleanup (LONG): {cleanup_icon_l} {long_cleanup}")
    print_info(f"Bracket Cleanup (SHORT): {cleanup_icon_s} {short_cleanup}")

    # P&L discrepancies
    long_disc = results.get("long_pnl_discrepancies", [])
    short_disc = results.get("short_pnl_discrepancies", [])
    disc_icon_l = "\u2705" if len(long_disc) == 0 else "\u26a0\ufe0f"
    disc_icon_s = "\u2705" if len(short_disc) == 0 else "\u26a0\ufe0f"
    print_info(f"P&L Discrepancies (LONG): {disc_icon_l} {len(long_disc)} issues")
    print_info(f"P&L Discrepancies (SHORT): {disc_icon_s} {len(short_disc)} issues")

    # BracketOrderManager Test (Step 15)
    bracket_result = results.get("bracket_manager_test", "N/A")
    bracket_icon = "\u2705" if bracket_result == "PASS" else "\u274c" if "FAIL" in str(bracket_result) else "\u2754"
    print_info(f"\nBracketOrderManager Test: {bracket_icon} {bracket_result}")
    bracket_details = results.get("bracket_details", {})
    if bracket_details:
        print_info(f"  Symbol: {bracket_details.get('symbol', 'N/A')}")
    bracket_stats = results.get("bracket_manager_stats", {})
    if bracket_stats:
        print_info(f"  Orphans Cancelled: {bracket_stats.get('orphans_cancelled', 0)}")

    # Fill counts
    print_info("\nFill Counts:")
    print_info(f"  LONG SL fills: {results.get('long_sl_fill_count', 0)}")
    print_info(f"  LONG TP fills: {results.get('long_tp_fill_count', 0)}")
    print_info(f"  SHORT SL fills: {results.get('short_sl_fill_count', 0)}")
    print_info(f"  SHORT TP fills: {results.get('short_tp_fill_count', 0)}")

    # Rate limiting observations
    rate_events = results.get("rate_limit_events", []) + RATE_LIMIT_EVENTS
    print_info("\nRate Limiting Observations:")
    if rate_events:
        print_warning(f"  {len(rate_events)} rate limit event(s) detected")
        for event in rate_events[:5]:  # Show first 5
            print_info(f"    - {event.get('operation')}: {event.get('error', 'N/A')}")
    else:
        print_success("  No rate limit events detected")

    # Overall result
    all_pass = (
        tag_result == "PASS"
        and long_heal in ["PASS", "SKIPPED"]
        and short_heal in ["PASS", "SKIPPED"]
        and long_cleanup == "PASS"
        and short_cleanup == "PASS"
        and len(long_disc) == 0
        and len(short_disc) == 0
    )

    print_info("")
    if all_pass:
        print_success("OVERALL: PASS - All tests successful!")
    else:
        print_error("OVERALL: ISSUES FOUND - Review results above")

    # Save final report (include rate limit events)
    results["rate_limit_events"] = rate_events
    report = {
        "workflow_id": state.get("workflow_id"),
        "completed_at": datetime.now().isoformat(),
        "overall_result": "PASS" if all_pass else "ISSUES",
        "test_results": results,
        "step_history": state.get("step_history", []),
        "rate_limit_events": rate_events,
    }
    filepath = save_response("final_report", report)
    print_info(f"\nFinal report saved to: {filepath}")

    update_step(state, 14, "completed")
    save_state(state)

    print_success("\nStep 14 completed!")

    if state.get("autonomous"):
        print_info("Autonomous mode: proceeding to step-15...")
        time.sleep(2)
        return cmd_step_15(args, state, client)
    else:
        print_next_command("python tools/pnl_validation_workflow.py step-15")

    return 0


def cmd_step_15(args, state=None, client=None):
    """Step 15: BracketOrderManager Integration Test.

    Tests the REST-based bracket order manager that handles orphan cleanup
    since ProjectX's native OCO (linkedOrderId) doesn't work.

    Uses MGC (Micro Gold) - cheapest instrument ($1/tick).
    """
    if state is None:
        state = load_state()
        if not state:
            print_error("No workflow state found. Run 'start' first.")
            return 1

    print_header("STEP 15: BracketOrderManager Integration Test")
    print_progress_bar(15)

    if client is None:
        client = ProjectXClient(PROJECTX_CONFIG)

    account_id = state.get("account_id")
    if not account_id:
        account_id = client.get_preferred_account_id()
        state["account_id"] = account_id

    update_step(state, 15, "in_progress")

    # Import BracketOrderManager
    try:
        from tools.bracket_order_manager import BracketOrderManager
    except ImportError:
        print_error("BracketOrderManager not found. Skipping test.")
        state["test_results"]["bracket_manager_test"] = "SKIPPED_IMPORT_ERROR"
        update_step(state, 15, "completed")
        save_state(state)
        return 0

    # Use MGC for test (cheapest instrument - $1/tick)
    test_symbol = "MGC"
    inst = state["instruments"][test_symbol]
    contract_id = inst.get("contract_id")

    if not contract_id:
        contract_id = resolve_contract_id(client, test_symbol)
        if not contract_id:
            print_error(f"Could not resolve contract for {test_symbol}")
            state["test_results"]["bracket_manager_test"] = "FAILED_NO_CONTRACT"
            update_step(state, 15, "completed")
            save_state(state)
            return 1
        inst["contract_id"] = contract_id

    # Fetch current price and ATR
    bars = fetch_historical_bars(client, contract_id, bars_needed=20)
    if not bars:
        print_error("Could not fetch price data")
        state["test_results"]["bracket_manager_test"] = "FAILED_NO_DATA"
        update_step(state, 15, "completed")
        save_state(state)
        return 1

    last_price = get_last_price(bars)
    atr = calculate_atr(bars, period=14) or 1.0

    print_info(f"Test instrument: {test_symbol} (cheapest - $1/tick)")
    print_info(f"Current price: {last_price:.2f}, ATR: {atr:.4f}")

    # Use TIGHT bracket (0.25 ATR) for quick fill
    atr_mult = 0.25
    sl_price = round_to_tick(last_price - (atr * atr_mult), test_symbol)
    tp_price = round_to_tick(last_price + (atr * atr_mult), test_symbol)

    print_info("\nPlacing TIGHT bracket for quick fill:")
    print_info(f"  SL: {sl_price:.2f} (0.25 ATR below)")
    print_info(f"  TP: {tp_price:.2f} (0.25 ATR above)")

    # Initialize BracketOrderManager
    manager = BracketOrderManager(client, account_id)
    print_success("BracketOrderManager initialized")

    # Place market entry
    workflow_id = state.get("workflow_id")
    base_tag = f"{test_symbol}_BRACKET_TEST_{workflow_id}"
    entry_tag = f"{base_tag}_ENTRY"

    entry_resp = client.api.order_place(
        account_id=account_id,
        contract_id=contract_id,
        type=2,  # Market
        side=0,  # Buy (LONG)
        size=1,
        custom_tag=entry_tag,
    )
    save_response(f"step15_bracket_entry_{test_symbol}", entry_resp)

    if not entry_resp.get("success"):
        print_error(f"Entry order failed: {entry_resp}")
        state["test_results"]["bracket_manager_test"] = "FAILED_ENTRY"
        update_step(state, 15, "completed")
        save_state(state)
        return 1

    entry_order_id = entry_resp.get("orderId")
    print_success(f"Entry placed: ID={entry_order_id}")

    time.sleep(0.5)

    # Place SL order
    sl_tag = f"{base_tag}_SL"
    sl_resp = client.api.order_place(
        account_id=account_id,
        contract_id=contract_id,
        type=4,  # Stop
        side=1,  # Sell
        size=1,
        stop_price=sl_price,
        custom_tag=sl_tag,
    )
    save_response(f"step15_bracket_sl_{test_symbol}", sl_resp)

    sl_order_id = None
    if sl_resp.get("success"):
        sl_order_id = sl_resp.get("orderId")
        print_success(f"SL placed: ID={sl_order_id}")
    else:
        print_error(f"SL failed: {sl_resp}")

    # Place TP order
    tp_tag = f"{base_tag}_TP"
    tp_resp = client.api.order_place(
        account_id=account_id,
        contract_id=contract_id,
        type=1,  # Limit
        side=1,  # Sell
        size=1,
        limit_price=tp_price,
        custom_tag=tp_tag,
    )
    save_response(f"step15_bracket_tp_{test_symbol}", tp_resp)

    tp_order_id = None
    if tp_resp.get("success"):
        tp_order_id = tp_resp.get("orderId")
        print_success(f"TP placed: ID={tp_order_id}")
    else:
        print_error(f"TP failed: {tp_resp}")

    if not sl_order_id or not tp_order_id:
        print_error("Could not create complete bracket")
        state["test_results"]["bracket_manager_test"] = "FAILED_BRACKET"
        update_step(state, 15, "completed")
        save_state(state)
        return 1

    # Register bracket with manager
    print_info("\nRegistering bracket with BracketOrderManager...")
    manager.register_bracket(base_tag, sl_order_id=sl_order_id, tp_order_id=tp_order_id, symbol=test_symbol)
    print_success(f"Bracket registered: {base_tag}")

    # Store for tracking
    bracket_details = {
        "symbol": test_symbol,
        "base_tag": base_tag,
        "entry_order_id": entry_order_id,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "sl_price": sl_price,
        "tp_price": tp_price,
    }
    state["test_results"]["bracket_details"] = bracket_details

    # Poll using BracketOrderManager
    print_info("\nPolling with BracketOrderManager (5 min max)...")
    print_info("Manager will auto-cancel orphan when one leg fills.")
    print_info("(Ctrl+C to skip)")

    poll_interval = 2  # Fast polling
    max_wait = 300
    waited = 0
    orphan_cancelled = False

    try:
        while waited < max_wait:
            # Use manager's poll_and_cleanup
            result = manager.poll_and_cleanup()

            if result["cancelled"]:
                orphan_cancelled = True
                for item in result["cancelled"]:
                    print_success(f"ORPHAN CANCELLED: {item['type']} ID={item['order_id']}")
                break

            # Check if bracket is done
            bracket = manager.brackets.get(base_tag)
            if bracket and not bracket.active:
                print_success("Bracket deactivated - both legs terminal")
                orphan_cancelled = True
                break

            # Show status
            sl_status = "TERMINAL" if bracket and bracket.sl_terminal else "OPEN"
            tp_status = "TERMINAL" if bracket and bracket.tp_terminal else "OPEN"
            print_info(f"  {waited}s | SL: {sl_status} | TP: {tp_status}")

            time.sleep(poll_interval)
            waited += poll_interval

    except KeyboardInterrupt:
        print_warning("\nPolling interrupted")

    # Record result
    if orphan_cancelled:
        print_success("\nBRACKET MANAGER TEST: PASS")
        print_success("Manager successfully detected fill and cancelled orphan!")
        state["test_results"]["bracket_manager_test"] = "PASS"
        state["test_results"]["bracket_manager_stats"] = manager.stats
    else:
        print_warning("\nBRACKET MANAGER TEST: TIMEOUT")
        print_info("No fills detected within timeout period")
        state["test_results"]["bracket_manager_test"] = "TIMEOUT"

    # Clean up - cancel any remaining orders and close position
    print_info("\nCleaning up...")

    # Cancel any remaining bracket orders
    manager.cancel_bracket(base_tag)

    # Close any remaining position
    pos_resp = client.api.position_search_open(account_id)
    if pos_resp.get("success"):
        for pos in pos_resp.get("positions", []):
            pos_symbol = extract_symbol_from_contract(pos.get("contractId", ""))
            if pos_symbol == test_symbol:
                qty = pos.get("size", 0)
                pos_type = pos.get("type", 1)
                if qty != 0:
                    close_side = 1 if pos_type == 1 else 0
                    close_resp = client.api.order_place(
                        account_id=account_id,
                        contract_id=contract_id,
                        type=2,
                        side=close_side,
                        size=abs(qty),
                        custom_tag=f"{base_tag}_CLEANUP",
                    )
                    if close_resp.get("success"):
                        print_success(f"Position closed: {test_symbol}")

    # Final stats
    print_info("\nBracketOrderManager Stats:")
    print_info(f"  Brackets registered: {manager.stats['brackets_registered']}")
    print_info(f"  Orphans cancelled: {manager.stats['orphans_cancelled']}")
    print_info(f"  Polls executed: {manager.stats['polls_executed']}")

    update_step(state, 15, "completed")
    state["phase"] = "COMPLETE"
    save_state(state)

    print_success("\nStep 15 completed: BracketOrderManager test done")
    print_info("\nWorkflow complete! Run 'report' for final summary.")
    print_next_command("python tools/pnl_validation_workflow.py report")

    return 0


def cmd_status(args):
    """Show current workflow status."""
    state = load_state()
    if not state:
        print_error("No workflow state found. Run 'start' first.")
        return 1

    current_step = state.get("current_step", 0)
    phase = state.get("phase", "UNKNOWN")

    print_header(f"Workflow Status: Step {current_step} of {TOTAL_STEPS}")
    print_progress_bar(current_step)

    print_info(f"Workflow ID: {state.get('workflow_id')}")
    print_info(f"Phase: {phase}")
    print_info(f"Started: {state.get('started_at')}")
    print_info(f"Autonomous: {state.get('autonomous')}")

    if current_step < TOTAL_STEPS:
        print_next_command(f"python tools/pnl_validation_workflow.py step-{current_step + 1}")

    return 0


def cmd_report(args):
    """Generate report from current state."""
    return cmd_step_14(args)


# ============================================================================
# Main CLI
# ============================================================================


def cmd_run_to(args):
    """Run all steps from start up to (and including) the specified step."""
    target_step = args.target_step
    if target_step is None:
        print_error("run-to requires a step number. Example: run-to 5")
        return 1

    if target_step < 1 or target_step > 15:
        print_error(f"Invalid step number: {target_step}. Must be 1-15.")
        return 1

    print_header(f"RUNNING STEPS: start → step-{target_step}")
    print_info(f"Will execute start + steps 1-{target_step}, then pause.\n")

    # Step functions in order
    step_funcs = [
        ("start", cmd_start),
        ("step-1", cmd_step_1),
        ("step-2", cmd_step_2),
        ("step-3", cmd_step_3),
        ("step-4", cmd_step_4),
        ("step-5", cmd_step_5),
        ("step-6", cmd_step_6),
        ("step-7", cmd_step_7),
        ("step-8", cmd_step_8),
        ("step-9", cmd_step_9),
        ("step-10", cmd_step_10),
        ("step-11", cmd_step_11),
        ("step-12", cmd_step_12),
        ("step-13", cmd_step_13),
        ("step-14", cmd_step_14),
        ("step-15", cmd_step_15),
    ]

    # Run start + steps 1 through target_step
    steps_to_run = step_funcs[: target_step + 1]  # +1 because start is index 0

    for i, (step_name, step_func) in enumerate(steps_to_run):
        print_info(f"\n{'='*60}")
        print_info(f"  Running: {step_name} ({i+1}/{len(steps_to_run)})")
        print_info(f"{'='*60}\n")

        result = step_func(args)
        if result != 0:
            print_error(f"\n{step_name} failed with code {result}. Stopping.")
            return result

        # Small delay between steps for readability
        if i < len(steps_to_run) - 1:
            time.sleep(1)

    print_success(f"\n{'='*60}")
    print_success(f"  Completed: start → step-{target_step}")
    print_success(f"{'='*60}")
    print_info(f"\nReady for manual testing. Next step would be: step-{target_step + 1}")
    print_next_command(f"python tools/pnl_validation_workflow.py step-{target_step + 1}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="P&L Validation Workflow CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  start           Start workflow (clears data, checks flat)
  step-1..15      Execute individual steps
  run-to N        Run start + steps 1-N, then pause (e.g., run-to 5)
  status          Show current progress
  report          Generate final summary

Steps:
  1-7             LONG phase tests
  8-13            SHORT phase tests
  14              Generate interim report
  15              BracketOrderManager integration test (MGC)

Examples:
  python tools/pnl_validation_workflow.py start
  python tools/pnl_validation_workflow.py run-to 5      # Run start → step-5
  python tools/pnl_validation_workflow.py run-to 15     # Run full workflow
  python tools/pnl_validation_workflow.py step-6        # Run single step
  python tools/pnl_validation_workflow.py status
        """,
    )

    parser.add_argument("command", help="Command to execute")
    parser.add_argument("target_step", nargs="?", type=int, help="Target step for run-to command")
    parser.add_argument("--autonomous", "-a", action="store_true", help="Auto-progress through all steps")

    args = parser.parse_args()

    cmd = args.command.lower().replace("_", "-")

    commands = {
        "start": cmd_start,
        "run-to": cmd_run_to,
        "step-1": lambda a: cmd_step_1(a),
        "step-2": lambda a: cmd_step_2(a),
        "step-3": lambda a: cmd_step_3(a),
        "step-4": lambda a: cmd_step_4(a),
        "step-5": lambda a: cmd_step_5(a),
        "step-6": lambda a: cmd_step_6(a),
        "step-7": lambda a: cmd_step_7(a),
        "step-8": lambda a: cmd_step_8(a),
        "step-9": lambda a: cmd_step_9(a),
        "step-10": lambda a: cmd_step_10(a),
        "step-11": lambda a: cmd_step_11(a),
        "step-12": lambda a: cmd_step_12(a),
        "step-13": lambda a: cmd_step_13(a),
        "step-14": lambda a: cmd_step_14(a),
        "step-15": lambda a: cmd_step_15(a),
        "status": cmd_status,
        "report": cmd_report,
    }

    if cmd not in commands:
        print_error(f"Unknown command: {cmd}")
        parser.print_help()
        return 1

    try:
        return commands[cmd](args)
    except KeyboardInterrupt:
        print_warning("\nInterrupted by user")
        return 130
    except Exception as e:
        print_error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
