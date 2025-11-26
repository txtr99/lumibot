#!/usr/bin/env python3
"""
Signal Frequency Analyzer - Tests how often strategies generate signals on historical data.

Usage:
    python tools/signal_frequency_analyzer.py [--hours N] [--debug]

Examples:
    python tools/signal_frequency_analyzer.py                # Default 24h
    python tools/signal_frequency_analyzer.py --hours 6      # Last 6 hours
    python tools/signal_frequency_analyzer.py --hours 1 --debug  # Debug mode with indicator values
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from importlib import util as importlib_util
from pathlib import Path

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from lumibot.credentials import PROJECTX_CONFIG  # noqa: E402
from lumibot.tools.projectx_helpers import ProjectXClient  # noqa: E402


class MockState:
    """Mock state object for strategy signal generation"""

    def __init__(self, params: dict):
        self.params = params


def load_strategy_module(filepath: Path):
    """Dynamically load a strategy module"""
    spec = importlib_util.spec_from_file_location(filepath.stem, filepath)
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_historical_bars(client, symbol: str, hours: int = 24) -> pd.DataFrame:
    """Fetch historical 1-minute bars"""
    # Map trading symbol to contract symbol
    symbol_map = {"MES": "MES", "MNQ": "MNQ", "MGC": "MGC"}
    contract_symbol = symbol_map.get(symbol, symbol)

    # Get contract ID
    response = client.api.contract_search(contract_symbol)
    contracts = response.get("contracts", []) if isinstance(response, dict) else response
    if not contracts:
        return pd.DataFrame()

    contract_id = contracts[0].get("id")

    # Fetch bars - history_retrieve_bars returns DataFrame directly
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    df = client.api.history_retrieve_bars(
        contract_id=contract_id,
        start_datetime=start_time,
        end_datetime=end_time,
        unit=1,  # 1=Minute
        unit_number=1,
        limit=hours * 60 + 100,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # Rename datetime to timestamp for consistency
    df = df.rename(columns={"datetime": "timestamp"})
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def analyze_strategy_signals(module, df: pd.DataFrame, min_bars: int = 50, debug: bool = False) -> dict:
    """Run strategy signal generation across historical data"""
    config = getattr(module, "STRATEGY_CONFIG", {})
    params = config.get("params", {})
    generate_signal = getattr(module, "generate_signal", None)
    populate_indicators = getattr(module, "populate_indicators", None)

    if generate_signal is None:
        return {"error": "No generate_signal function"}

    state = MockState(params)

    signals = {"BUY": 0, "SELL": 0, "HOLD": 0, "ERROR": 0}
    signal_times = []
    debug_samples = []

    # Need enough bars for indicators
    for i in range(min_bars, len(df)):
        window = df.iloc[: i + 1].copy()
        try:
            signal = generate_signal(state, window)
            signals[signal] = signals.get(signal, 0) + 1

            if signal in ("BUY", "SELL"):
                signal_info = {
                    "time": df.iloc[i]["timestamp"],
                    "signal": signal,
                    "price": df.iloc[i]["close"],
                }
                signal_times.append(signal_info)

                # Capture debug info for signals
                if debug and populate_indicators:
                    try:
                        indicators = populate_indicators(window, params)
                        signal_info["indicators"] = {
                            k: round(v, 4) if isinstance(v, float) else v for k, v in indicators.items()
                        }
                    except Exception:
                        pass

            # Sample debug info periodically
            if debug and i == min_bars:
                if populate_indicators:
                    try:
                        indicators = populate_indicators(window, params)
                        debug_samples.append(
                            {
                                "bar": i,
                                "time": str(df.iloc[i]["timestamp"]),
                                "close": df.iloc[i]["close"],
                                "indicators": {
                                    k: round(v, 4) if isinstance(v, float) else v for k, v in indicators.items()
                                },
                            }
                        )
                    except Exception as e:
                        debug_samples.append({"bar": i, "error": str(e)})

        except Exception as e:
            signals["ERROR"] += 1
            if debug and signals["ERROR"] <= 3:
                debug_samples.append({"bar": i, "error": str(e)})

    total_bars = len(df) - min_bars
    hours_analyzed = total_bars / 60 if total_bars > 0 else 0

    result = {
        "total_bars": total_bars,
        "hours_analyzed": round(hours_analyzed, 2),
        "signals": signals,
        "buy_per_hour": round(signals["BUY"] / hours_analyzed, 2) if hours_analyzed > 0 else 0,
        "sell_per_hour": round(signals["SELL"] / hours_analyzed, 2) if hours_analyzed > 0 else 0,
        "signal_rate_pct": round((signals["BUY"] + signals["SELL"]) / total_bars * 100, 2) if total_bars > 0 else 0,
        "recent_signals": signal_times[-5:] if signal_times else [],
    }

    if debug:
        result["debug_samples"] = debug_samples

    return result


def main():
    parser = argparse.ArgumentParser(description="Analyze signal frequency for strategies")
    parser.add_argument("--hours", type=int, default=24, help="Hours of history to analyze")
    parser.add_argument("--debug", action="store_true", help="Include debug indicator values")
    parser.add_argument("--json", action="store_true", help="Output only JSON (no table)")
    args = parser.parse_args()

    results = {"success": False, "data": {}}

    try:
        # Initialize client
        client = ProjectXClient(PROJECTX_CONFIG)

        # Find active strategies
        strategies_dir = Path("custom_portfolio/strategies/active_strategies")
        strategy_files = sorted(strategies_dir.glob("*.py"))

        if not strategy_files:
            results["error"] = "No strategy files found"
            print(json.dumps(results, indent=2, default=str))
            return

        # Group by symbol
        symbol_data = {}
        symbol_strategies = defaultdict(list)

        for filepath in strategy_files:
            module = load_strategy_module(filepath)
            config = getattr(module, "STRATEGY_CONFIG", {})
            symbol = config.get("symbol", "UNKNOWN")
            symbol_strategies[symbol].append((filepath, module, config))

        # Fetch data once per symbol
        if not args.json:
            print(f"Fetching {args.hours}h of data per symbol...", file=sys.stderr)

        for symbol in symbol_strategies:
            if not args.json:
                print(f"  Fetching {symbol}...", file=sys.stderr)
            symbol_data[symbol] = get_historical_bars(client, symbol, hours=args.hours)
            if not args.json:
                print(f"    Got {len(symbol_data[symbol])} bars", file=sys.stderr)

        # Analyze each strategy
        strategy_results = {}

        for symbol, strategies in symbol_strategies.items():
            df = symbol_data.get(symbol)
            if df is None or df.empty:
                for filepath, _module, _config in strategies:
                    strategy_results[filepath.stem] = {"error": f"No data for {symbol}"}
                continue

            for filepath, module, config in strategies:
                if not args.json:
                    print(f"  Analyzing {filepath.stem}...", file=sys.stderr)
                analysis = analyze_strategy_signals(module, df, debug=args.debug)
                analysis["symbol"] = symbol
                analysis["strategy_type"] = config.get("metadata", {}).get("strategy_type", "unknown")
                analysis["direction"] = config.get("metadata", {}).get("direction", "unknown")
                strategy_results[filepath.stem] = analysis

        # Summary
        total_buys = sum(r.get("signals", {}).get("BUY", 0) for r in strategy_results.values() if "error" not in r)
        total_sells = sum(r.get("signals", {}).get("SELL", 0) for r in strategy_results.values() if "error" not in r)

        results["success"] = True
        results["data"] = {
            "hours_analyzed": args.hours,
            "strategies_analyzed": len(strategy_results),
            "total_buy_signals": total_buys,
            "total_sell_signals": total_sells,
            "strategies": strategy_results,
        }

        # Print summary table to stderr (unless json-only mode)
        if not args.json:
            print("\n" + "=" * 90, file=sys.stderr)
            print(f"SIGNAL FREQUENCY ANALYSIS ({args.hours}h)", file=sys.stderr)
            print("=" * 90, file=sys.stderr)
            header = (
                f"{'Strategy':<12} {'Symbol':<6} {'Type':<16} {'Bars':<6} "
                f"{'BUY':<5} {'SELL':<5} {'BUY/h':<6} {'Rate%':<6} {'Last Signal'}"
            )
            print(header, file=sys.stderr)
            print("-" * 90, file=sys.stderr)

            for name, data in sorted(strategy_results.items()):
                if "error" in data:
                    print(f"{name:<12} ERROR: {data['error']}", file=sys.stderr)
                else:
                    last_sig = ""
                    if data.get("recent_signals"):
                        last = data["recent_signals"][-1]
                        t = str(last["time"])
                        last_sig = f"{last['signal']} @ {last['price']} ({t[-8:-6]}:{t[-5:-3]})"
                    print(
                        f"{name:<12} {data['symbol']:<6} {data['strategy_type']:<16} {data['total_bars']:<6} "
                        f"{data['signals']['BUY']:<5} {data['signals']['SELL']:<5} "
                        f"{data['buy_per_hour']:<6} {data['signal_rate_pct']:<6} {last_sig}",
                        file=sys.stderr,
                    )

            print("-" * 90, file=sys.stderr)
            print(f"TOTALS: {total_buys} BUY signals, {total_sells} SELL signals across {args.hours}h", file=sys.stderr)

            # Show strategies with signals
            if total_buys > 0 or total_sells > 0:
                print("\nStrategies that SHOULD have traded:", file=sys.stderr)
                for name, data in sorted(strategy_results.items()):
                    if "error" not in data and (data["signals"]["BUY"] > 0 or data["signals"]["SELL"] > 0):
                        print(
                            f"  - {name}: {data['signals']['BUY']} BUY, {data['signals']['SELL']} SELL", file=sys.stderr
                        )

    except Exception as e:
        results["success"] = False
        results["error"] = str(e)
        import traceback

        results["traceback"] = traceback.format_exc()

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
