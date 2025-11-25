#!/usr/bin/env python3
"""
TopStepX Metadata Verification Script

This script verifies and updates local futures metadata against ProjectX API data.
Run quarterly to ensure tick_size, tick_value, and multiplier data is accurate.

Usage:
    python tools/verify_topstep_metadata.py

Author: LumiBot Multi-Strategy Team
Date: 2025-11-24
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lumibot.tools.projectx_helpers import ProjectXClient  # noqa: E402

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.table import Table
except ImportError:
    print("ERROR: 'rich' library required. Install with: pip install rich")
    sys.exit(1)

# ==================== CONFIGURATION ====================

# Path to the metadata file we're verifying/updating
METADATA_FILE = PROJECT_ROOT / "custom_portfolio" / "data" / "futures_metadata.py"

# Path to write verification timestamp
VERIFICATION_FILE = PROJECT_ROOT / ".metadata_last_verified"

# TopStepX permitted symbols (all 50 symbols allowed on TopStepX platform)
TOPSTEP_SYMBOLS = [
    # CME Equity Futures
    "ES",
    "MES",
    "NQ",
    "MNQ",
    "RTY",
    "M2K",
    "NKD",
    "MBT",
    "MET",
    # CME NYMEX Futures
    "CL",
    "MCL",
    "QM",
    "PL",
    "QG",
    "RB",
    "HO",
    "NG",
    "MNG",
    # CME CBOT Equity Futures
    "YM",
    "MYM",
    # CME Foreign Exchange Futures
    "6A",
    "M6A",
    "6B",
    "6C",
    "6E",
    "M6E",
    "6J",
    "6S",
    "E7",
    "6M",
    "6N",
    "M6B",
    # CME CBOT Financial/Interest Rate Futures
    "ZT",
    "ZF",
    "ZN",
    "ZB",
    "UB",
    "TN",
    # CME COMEX Futures
    "GC",
    "MGC",
    "SI",
    "SIL",
    "HG",
    "MHG",
    # CME Agricultural Futures
    "HE",
    "LE",
    # CME CBOT Commodity Futures
    "ZC",
    "ZW",
    "ZS",
    "ZM",
    "ZL",
]

# TopStepX round-turn fees (for merging into metadata)
TOPSTEP_FEES = {
    "ES": 2.80,
    "MES": 0.74,
    "NQ": 2.80,
    "MNQ": 0.74,
    "RTY": 2.80,
    "M2K": 0.74,
    "NKD": 4.34,
    "MBT": 2.34,
    "MET": 0.24,
    "CL": 3.04,
    "MCL": 1.04,
    "QM": 2.44,
    "PL": 3.24,
    "QG": 1.04,
    "RB": 3.04,
    "HO": 3.04,
    "NG": 3.20,
    "MNG": 1.24,
    "YM": 2.80,
    "MYM": 0.74,
    "6A": 3.24,
    "M6A": 0.52,
    "6B": 3.24,
    "6C": 3.24,
    "6E": 3.24,
    "M6E": 0.52,
    "6J": 3.24,
    "6S": 3.24,
    "E7": 1.74,
    "6M": 3.24,
    "6N": 3.24,
    "M6B": 0.52,
    "ZT": 1.34,
    "ZF": 1.34,
    "ZN": 1.60,
    "ZB": 1.78,
    "UB": 1.94,
    "TN": 1.64,
    "GC": 3.24,
    "MGC": 1.24,
    "SI": 3.24,
    "SIL": 2.04,
    "HG": 3.24,
    "MHG": 1.24,
    "HE": 4.24,
    "LE": 4.24,
    "ZC": 4.30,
    "ZW": 4.30,
    "ZS": 4.30,
    "ZM": 4.30,
    "ZL": 4.30,
}


console = Console()


def get_local_metadata():
    """Load current local metadata from futures_metadata.py."""
    try:
        # Import the module dynamically
        import importlib.util

        spec = importlib.util.spec_from_file_location("futures_metadata", METADATA_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.FUTURES_METADATA.copy()
    except Exception as e:
        console.print(f"[bright_red]Error loading local metadata: {e}[/]")
        return {}


def query_api_metadata(client: ProjectXClient, symbols: list) -> dict:
    """Query ProjectX API for contract specifications."""
    api_data = {}
    errors = []

    console.print("\n[bright_cyan]Querying ProjectX API for contract specifications...[/]\n")

    with console.status("[bright_yellow]Fetching contract data...") as status:
        for i, symbol in enumerate(symbols):
            status.update(f"[bright_yellow]Fetching {symbol} ({i+1}/{len(symbols)})...")

            try:
                # Search for the contract
                result = client.api.contract_search(symbol, live=False)

                if result.get("success") and result.get("contracts"):
                    # Find the active contract
                    contracts = result["contracts"]
                    active = [c for c in contracts if c.get("activeContract", False)]
                    contract = active[0] if active else contracts[0]

                    tick_size = contract.get("tickSize")
                    tick_value = contract.get("tickValue")

                    if tick_size is not None and tick_value is not None:
                        api_data[symbol] = {
                            "tick_size": float(tick_size),
                            "tick_value": float(tick_value),
                            "contract_id": contract.get("id", ""),
                            "description": contract.get("description", ""),
                        }
                    else:
                        errors.append(f"{symbol}: Missing tick data in response")
                else:
                    errors.append(f"{symbol}: No contracts found")

            except Exception as e:
                errors.append(f"{symbol}: {str(e)}")

            # Small delay to avoid rate limiting
            # Note: For 50 symbols, this adds ~5s total - acceptable for a quarterly script
            # ProjectX API does not support batch contract queries
            import time

            time.sleep(0.1)

    if errors:
        console.print("\n[bright_yellow]API Query Warnings:[/]")
        for err in errors[:10]:  # Show first 10 errors
            console.print(f"  [dim]{err}[/]")
        if len(errors) > 10:
            console.print(f"  [dim]... and {len(errors) - 10} more[/]")

    return api_data


def compare_metadata(local: dict, api: dict, symbols: list) -> tuple:
    """
    Compare local metadata against API data.

    Returns:
        (matches, mismatches, missing_from_local, api_failures)
    """
    matches = []
    mismatches = []
    missing_from_local = []
    api_failures = []

    for symbol in symbols:
        if symbol not in api:
            api_failures.append(symbol)
            continue

        api_info = api[symbol]

        if symbol not in local:
            missing_from_local.append(
                {
                    "symbol": symbol,
                    "api_tick_size": api_info["tick_size"],
                    "api_tick_value": api_info["tick_value"],
                    "description": api_info.get("description", ""),
                }
            )
            continue

        local_info = local[symbol]
        local_tick_size = local_info.get("tick_size")
        local_tick_value = local_info.get("tick_value")
        api_tick_size = api_info["tick_size"]
        api_tick_value = api_info["tick_value"]

        # Use tolerance for float comparison (10 decimal places as per plan)
        tick_size_match = abs(local_tick_size - api_tick_size) < 1e-10 if local_tick_size else False
        tick_value_match = abs(local_tick_value - api_tick_value) < 1e-10 if local_tick_value else False

        if tick_size_match and tick_value_match:
            matches.append(symbol)
        else:
            mismatches.append(
                {
                    "symbol": symbol,
                    "local_tick_size": local_tick_size,
                    "api_tick_size": api_tick_size,
                    "local_tick_value": local_tick_value,
                    "api_tick_value": api_tick_value,
                    "tick_size_diff": tick_size_match,
                    "tick_value_diff": tick_value_match,
                }
            )

    return matches, mismatches, missing_from_local, api_failures


def display_results(matches: list, mismatches: list, missing: list, failures: list):
    """Display comparison results in Rich table format."""

    # Summary panel
    summary_text = (
        f"[bright_green]Matches:[/] {len(matches)}  "
        f"[bright_yellow]Mismatches:[/] {len(mismatches)}  "
        f"[bright_red]Missing:[/] {len(missing)}  "
        f"[dim]API Failures:[/] {len(failures)}"
    )
    console.print(Panel(summary_text, title="Verification Summary", border_style="bright_cyan"))

    # Matches table (collapsed if many)
    if matches:
        console.print(f"\n[bright_green]Matches ({len(matches)} symbols):[/]")
        if len(matches) <= 20:
            match_str = ", ".join(sorted(matches))
        else:
            match_str = ", ".join(sorted(matches)[:20]) + f" ... (+{len(matches)-20} more)"
        console.print(f"  [green]{match_str}[/]")

    # Mismatches table
    if mismatches:
        console.print(f"\n[bright_yellow]Mismatches ({len(mismatches)} symbols):[/]")
        table = Table(show_header=True, header_style="bright_yellow")
        table.add_column("Symbol", style="bright_white")
        table.add_column("Field", style="dim")
        table.add_column("Local", style="bright_red")
        table.add_column("API", style="bright_green")

        for m in mismatches:
            if not m["tick_size_diff"]:
                table.add_row(m["symbol"], "tick_size", str(m["local_tick_size"]), str(m["api_tick_size"]))
            if not m["tick_value_diff"]:
                table.add_row(m["symbol"], "tick_value", str(m["local_tick_value"]), str(m["api_tick_value"]))
        console.print(table)

    # Missing from local
    if missing:
        console.print(f"\n[bright_red]Missing from local metadata ({len(missing)} symbols):[/]")
        table = Table(show_header=True, header_style="bright_red")
        table.add_column("Symbol", style="bright_white")
        table.add_column("Tick Size", style="bright_cyan")
        table.add_column("Tick Value", style="bright_cyan")
        table.add_column("Description", style="dim")

        for m in missing:
            table.add_row(m["symbol"], str(m["api_tick_size"]), str(m["api_tick_value"]), m.get("description", "")[:40])
        console.print(table)

    # API failures
    if failures:
        console.print(f"\n[dim]API query failed for: {', '.join(failures)}[/]")


def generate_update_code(mismatches: list, missing: list, api_data: dict) -> str:
    """Generate Python code updates for the metadata file."""
    updates = []

    # Existing symbol updates (mismatches)
    for m in mismatches:
        symbol = m["symbol"]
        updates.append(
            f"# {symbol}: tick_size {m['local_tick_size']} -> {m['api_tick_size']}, "
            f"tick_value {m['local_tick_value']} -> {m['api_tick_value']}"
        )

    # New symbol entries
    for m in missing:
        symbol = m["symbol"]
        tick_size = m["api_tick_size"]
        tick_value = m["api_tick_value"]
        fee = TOPSTEP_FEES.get(symbol, 0.85)

        # Determine category based on symbol
        category = "other"
        if symbol in ["ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "MYM", "NKD"]:
            category = "equity_index"
        elif symbol in ["6A", "6B", "6C", "6E", "6J", "6S", "6M", "6N", "M6A", "M6B", "M6E", "E7"]:
            category = "currency"
        elif symbol in ["GC", "MGC", "SI", "SIL", "HG", "MHG", "PL"]:
            category = "metals"
        elif symbol in ["CL", "MCL", "QM", "NG", "QG", "MNG", "RB", "HO"]:
            category = "energy"
        elif symbol in ["ZB", "ZN", "ZF", "ZT", "UB", "TN"]:
            category = "treasury"
        elif symbol in ["ZC", "ZW", "ZS", "ZM", "ZL", "HE", "LE"]:
            category = "agriculture"
        elif symbol in ["MBT", "MET"]:
            category = "crypto"

        desc = api_data.get(symbol, {}).get("description", f"{symbol} Futures")

        updates.append(
            f"""
    "{symbol}": {{
        "name": "{desc.split(':')[0] if ':' in desc else symbol}",
        "description": "{desc}",
        "tick_size": {tick_size},
        "tick_value": {tick_value},
        "trading_fee": {fee},
        "exchange": "CME",
        "currency": "USD",
        "category": "{category}",
        "topstep_round_turn_fee": {TOPSTEP_FEES.get(symbol, 0.0)},
        "last_verified": "{datetime.now().strftime('%Y-%m-%d')}",
        "data_source": "projectx_api",
    }},"""
        )

    return "\n".join(updates)


def apply_updates(mismatches: list, missing: list, api_data: dict):
    """Apply updates to the metadata file."""
    try:
        # Read the current file
        with open(METADATA_FILE) as f:
            content = f.read()

        # Apply mismatch updates (tick_size/tick_value changes)
        for m in mismatches:
            symbol = m["symbol"]

            # Update tick_size
            if not m["tick_size_diff"]:
                import re

                # Find and replace tick_size for this symbol
                pattern = rf'("{symbol}":\s*\{{[^}}]*"tick_size":\s*)[0-9.e\-/]+([^}}]*\}})'
                replacement = rf'\g<1>{m["api_tick_size"]}\2'
                content = re.sub(pattern, replacement, content, flags=re.DOTALL)

            # Update tick_value
            if not m["tick_value_diff"]:
                import re

                pattern = rf'("{symbol}":\s*\{{[^}}]*"tick_value":\s*)[0-9.e\-]+([^}}]*\}})'
                replacement = rf'\g<1>{m["api_tick_value"]}\2'
                content = re.sub(pattern, replacement, content, flags=re.DOTALL)

        # For missing symbols, we'll add them at the end of the FUTURES_METADATA dict
        # This is complex to do programmatically; show manual instructions instead
        if missing:
            console.print("\n[bright_yellow]New symbols need to be added manually:[/]")
            code = generate_update_code([], missing, api_data)
            console.print(f"[dim]{code}[/]")
            console.print("\n[bright_cyan]Add these entries to FUTURES_METADATA in futures_metadata.py[/]")

        # Write the updated content
        if mismatches:
            with open(METADATA_FILE, "w") as f:
                f.write(content)
            console.print(f"\n[bright_green]Updated {len(mismatches)} mismatched entries in {METADATA_FILE}[/]")

        return True
    except Exception as e:
        console.print(f"[bright_red]Error applying updates: {e}[/]")
        return False


def write_verification_timestamp(symbols_verified: int, mismatches: list, missing: list, applied: bool):
    """Write verification timestamp file."""
    data = {
        "timestamp": datetime.now().isoformat(),
        "symbols_verified": symbols_verified,
        "mismatches": [m["symbol"] for m in mismatches],
        "missing": [m["symbol"] for m in missing],
        "applied": applied,
    }

    try:
        with open(VERIFICATION_FILE, "w") as f:
            json.dump(data, f, indent=2)
        console.print(f"\n[bright_green]Wrote verification timestamp to {VERIFICATION_FILE}[/]")
    except Exception as e:
        console.print(f"[bright_yellow]Warning: Could not write timestamp file: {e}[/]")


def main():
    """Main entry point."""
    # Import PROJECTX_CONFIG here to ensure credentials module is loaded after path setup
    from lumibot.credentials import PROJECTX_CONFIG

    console.print(
        Panel(
            "[bright_cyan]TopStepX Metadata Verification Script[/]\n"
            "[dim]Compares local futures_metadata.py against ProjectX API data[/]",
            border_style="bright_cyan",
        )
    )

    # Validate configuration (using same pattern as projectx_api_explorer.py)
    if not PROJECTX_CONFIG.get("api_key") or not PROJECTX_CONFIG.get("username"):
        console.print("[bright_red]Missing ProjectX credentials![/]")
        console.print(
            "[bright_yellow]Set PROJECTX_TOPSTEPX_USERNAME and PROJECTX_TOPSTEPX_API_KEY in your environment.[/]"
        )
        console.print("[dim]See env.example for reference.[/]")
        return 1

    console.print(f"[dim]Firm: {PROJECTX_CONFIG.get('firm', 'unknown')}[/]")
    console.print(f"[dim]Username: {PROJECTX_CONFIG.get('username', 'unknown')}[/]")

    # Initialize client
    try:
        console.print("\n[bright_cyan]Authenticating with ProjectX API...[/]")
        client = ProjectXClient(PROJECTX_CONFIG)
        console.print("[bright_green]Authentication successful![/]")
    except Exception as e:
        console.print(f"[bright_red]Failed to authenticate: {e}[/]")
        return 1

    # Load local metadata
    local_metadata = get_local_metadata()
    console.print(f"[dim]Loaded {len(local_metadata)} symbols from local metadata[/]")

    # Query API for all TopStep symbols
    api_metadata = query_api_metadata(client, TOPSTEP_SYMBOLS)
    console.print(f"[dim]Retrieved {len(api_metadata)} symbols from API[/]")

    # Compare
    matches, mismatches, missing, failures = compare_metadata(local_metadata, api_metadata, TOPSTEP_SYMBOLS)

    # Display results
    display_results(matches, mismatches, missing, failures)

    # Check if updates are needed
    needs_update = len(mismatches) > 0 or len(missing) > 0

    if needs_update:
        console.print("\n")

        # Show what will be updated
        if mismatches:
            console.print(f"[bright_yellow]Will update {len(mismatches)} symbol(s) with corrected values[/]")
        if missing:
            console.print(f"[bright_yellow]Will show code for {len(missing)} new symbol(s) to add[/]")

        # Prompt for confirmation
        apply = Confirm.ask("\n[bright_cyan]Apply changes to futures_metadata.py?[/]", default=False)

        if apply:
            success = apply_updates(mismatches, missing, api_metadata)
            write_verification_timestamp(len(api_metadata), mismatches, missing, success)
        else:
            console.print("[dim]No changes applied. Generating diff for review...[/]")
            code = generate_update_code(mismatches, missing, api_metadata)
            console.print(f"\n[bright_cyan]Proposed updates:[/]\n{code}")
            write_verification_timestamp(len(api_metadata), mismatches, missing, False)
    else:
        console.print("\n[bright_green]All metadata is up to date![/]")
        write_verification_timestamp(len(api_metadata), [], [], True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
