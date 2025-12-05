#!/usr/bin/env python3
"""
Diagnostic script to trace phantom P&L at backtest start.
Instruments the equity curve calculation to see what's happening.
"""

import json
import sys
from pathlib import Path

import pandas as pd

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def analyze_equity_curve():
    """Analyze the equity curve CSV to find phantom P&L."""

    # Find most recent equity curve
    snapshot_dir = project_root / "snapshots"
    equity_files = sorted(snapshot_dir.glob("*/equity_curve.csv"), reverse=True)

    if not equity_files:
        return {"success": False, "error": "No equity curve files found"}

    latest = equity_files[0]
    print(f"Analyzing: {latest}")

    df = pd.read_csv(latest)

    # Find first rows where portfolio_value != account_balance
    df["unrealized_pnl"] = df["portfolio_value"] - df["account_balance"]

    # First few rows
    first_rows = df.head(20).to_dict("records")

    # Find first non-zero unrealized P&L
    nonzero = df[df["unrealized_pnl"] != 0]
    first_nonzero_idx = nonzero.index[0] if len(nonzero) > 0 else None

    # Summary stats
    result = {
        "success": True,
        "file": str(latest),
        "total_rows": len(df),
        "first_timestamp": df["timestamp"].iloc[0],
        "first_account_balance": df["account_balance"].iloc[0],
        "first_portfolio_value": df["portfolio_value"].iloc[0],
        "first_unrealized_pnl": df["unrealized_pnl"].iloc[0],
        "first_nonzero_unrealized_idx": int(first_nonzero_idx) if first_nonzero_idx is not None else None,
        "first_20_rows": first_rows,
        "unique_unrealized_values": sorted(df["unrealized_pnl"].unique().tolist())[:20],
    }

    # Check if phantom P&L exists at start
    if df["unrealized_pnl"].iloc[0] != 0:
        result["phantom_pnl_at_start"] = True
        result["phantom_amount"] = df["unrealized_pnl"].iloc[0]
    else:
        result["phantom_pnl_at_start"] = False

    return result


if __name__ == "__main__":
    result = analyze_equity_curve()
    print(json.dumps(result, indent=2, default=str))
