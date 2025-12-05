#!/usr/bin/env python3
"""Check what prices the data shows for MGC around Oct 1, 2025."""

import json
import sys
from pathlib import Path

from custom_portfolio.tools.shared_data_manager import SharedDataManager

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def check_prices():
    """Check MGC prices at different times."""
    sdm = SharedDataManager(["MGC"], timestep="minute", logger=None)

    # Get the full cached data
    full_data = sdm.get_cached_data("MGC", 1000, "minute")
    if full_data is None:
        return {"success": False, "error": "No data cached"}

    df = full_data.df if hasattr(full_data, "df") else full_data

    result = {
        "success": True,
        "total_rows": len(df),
        "first_timestamp": str(df.index[0]),
        "last_timestamp": str(df.index[-1]),
        "first_close": float(df["close"].iloc[0]),
        "last_close": float(df["close"].iloc[-1]),
    }

    # Check what get_data_at_time returns for first few times
    if len(df) > 0:
        first_time = df.index[0]
        filtered = sdm.get_data_at_time("MGC", first_time, 1000, "minute")
        if filtered is not None:
            result["at_first_time"] = {
                "filtered_rows": len(filtered),
                "filtered_last_close": float(filtered["close"].iloc[-1]),
                "filtered_last_timestamp": str(filtered.index[-1]),
            }

    # Sample of first 5 rows
    result["first_5_rows"] = [
        {"timestamp": str(idx), "close": float(row["close"])} for idx, row in df.head(5).iterrows()
    ]

    # Sample of last 5 rows
    result["last_5_rows"] = [
        {"timestamp": str(idx), "close": float(row["close"])} for idx, row in df.tail(5).iterrows()
    ]

    return result


if __name__ == "__main__":
    result = check_prices()
    print(json.dumps(result, indent=2, default=str))
