#!/usr/bin/env python3
"""
Strategy Validation Utility

Validates converted EasyLanguage strategies for:
- Python syntax correctness
- Required imports
- STRATEGY_CONFIG structure
- Required functions (populate_indicators, go_long, go_short, generate_signal)
- Execution with synthetic data

Usage:
    venv/bin/python validate_strategy.py /path/to/strategy.py
"""

import importlib.util
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# Check if pandas and numpy are available in venv
try:
    import numpy as np
    import pandas as pd
except ImportError:
    print("❌ FAIL: pandas and numpy required. Ensure lumibot venv is activated or use venv/bin/python")
    sys.exit(1)


def generate_synthetic_ohlcv(bars=500, base_price=5000.0, volatility=0.02):
    """
    Generate synthetic OHLCV data resembling gold futures.

    Args:
        bars: Number of bars to generate
        base_price: Starting price (default ~$5000 for gold)
        volatility: Price volatility (default 2%)

    Returns:
        pandas DataFrame with columns: open, high, low, close, volume
    """
    np.random.seed(42)  # Reproducible data

    # Generate random walk for close prices
    returns = np.random.normal(0, volatility, bars)
    close_prices = base_price * np.exp(np.cumsum(returns))

    # Generate OHLC from close
    data = []
    for close in close_prices:
        # Random intrabar volatility
        bar_range = close * np.random.uniform(0.001, 0.01)
        open_price = close + np.random.uniform(-bar_range / 2, bar_range / 2)
        high_price = max(open_price, close) + np.random.uniform(0, bar_range / 2)
        low_price = min(open_price, close) - np.random.uniform(0, bar_range / 2)
        volume = np.random.randint(1000, 10000)

        data.append(
            {
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close, 2),
                "volume": volume,
            }
        )

    df = pd.DataFrame(data)

    # Add timestamp index (1-minute bars)
    start_time = datetime.now() - timedelta(minutes=bars)
    df.index = pd.date_range(start=start_time, periods=bars, freq="1min")

    return df


def validate_syntax(file_path):
    """Check if Python syntax is valid."""
    try:
        with open(file_path) as f:
            code = f.read()
        compile(code, file_path, "exec")
        return True, None
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


def load_strategy_module(file_path):
    """Dynamically load strategy module."""
    try:
        spec = importlib.util.spec_from_file_location("strategy_module", file_path)
        if spec is None or spec.loader is None:
            return None, "Failed to create module spec"

        module = importlib.util.module_from_spec(spec)
        sys.modules["strategy_module"] = module
        spec.loader.exec_module(module)
        return module, None
    except ImportError as e:
        return None, f"Import error: {str(e)}"
    except Exception as e:
        return None, f"Failed to load module: {str(e)}\n{traceback.format_exc()}"


def validate_strategy_config(module):
    """Validate STRATEGY_CONFIG structure."""
    if not hasattr(module, "STRATEGY_CONFIG"):
        return False, "Missing STRATEGY_CONFIG"

    config = module.STRATEGY_CONFIG

    # Check required keys
    required_keys = ["symbol", "contracts", "params", "bracket_orders", "time_exit", "metadata"]
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        return False, f"Missing keys in STRATEGY_CONFIG: {missing_keys}"

    # Validate bracket_orders
    if not isinstance(config.get("bracket_orders"), dict):
        return False, "bracket_orders must be a dict"

    bracket_required = ["atr_period", "pt_mult", "sl_mult"]
    bracket_missing = [key for key in bracket_required if key not in config["bracket_orders"]]
    if bracket_missing:
        return False, f"Missing keys in bracket_orders: {bracket_missing}"

    # Validate time_exit
    if not isinstance(config.get("time_exit"), dict):
        return False, "time_exit must be a dict"

    if "max_bars" not in config["time_exit"]:
        return False, "Missing max_bars in time_exit"

    # Validate metadata
    if not isinstance(config.get("metadata"), dict):
        return False, "metadata must be a dict"

    metadata_required = ["strategy_type", "direction"]
    metadata_missing = [key for key in metadata_required if key not in config["metadata"]]
    if metadata_missing:
        return False, f"Missing keys in metadata: {metadata_missing}"

    # Validate direction value
    valid_directions = ["long", "short", "both"]
    if config["metadata"]["direction"] not in valid_directions:
        return False, f"Invalid direction: {config['metadata']['direction']} (must be: {valid_directions})"

    return True, None


def validate_required_functions(module):
    """Check if all required functions exist."""
    required_functions = [
        "populate_indicators",
        "go_long",
        "go_short",
        "generate_signal",
    ]

    missing_functions = []
    for func_name in required_functions:
        if not hasattr(module, func_name):
            missing_functions.append(func_name)
        elif not callable(getattr(module, func_name)):
            missing_functions.append(f"{func_name} (not callable)")

    if missing_functions:
        return False, f"Missing functions: {missing_functions}"

    return True, None


def test_with_synthetic_data(module):
    """Test strategy execution with synthetic data."""
    try:
        # Generate synthetic data
        df = generate_synthetic_ohlcv(bars=500, base_price=5000.0)

        # Create mock state object
        class MockState:
            def __init__(self, params):
                self.params = params

        config = module.STRATEGY_CONFIG
        state = MockState(config.get("params", {}))

        # Test populate_indicators
        try:
            indicators = module.populate_indicators(df, state.params)
            if not isinstance(indicators, dict):
                return False, "populate_indicators must return a dict"
        except Exception as e:
            return False, f"populate_indicators failed: {str(e)}\n{traceback.format_exc()}"

        # Test generate_signal
        signal_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}

        # Test on multiple windows to ensure robustness
        test_windows = [50, 100, 200, 300, 400, 500]
        for window_size in test_windows:
            if window_size > len(df):
                continue

            df_window = df.iloc[:window_size]

            try:
                signal = module.generate_signal(state, df_window)

                if signal not in ["BUY", "SELL", "HOLD"]:
                    return False, f"Invalid signal: {signal} (must be BUY, SELL, or HOLD)"

                signal_counts[signal] += 1

            except Exception as e:
                return False, f"generate_signal failed on window {window_size}: {str(e)}\n{traceback.format_exc()}"

        # Verify signals were generated
        total_signals = sum(signal_counts.values())
        if total_signals == 0:
            return False, "No signals generated"

        return True, signal_counts

    except Exception as e:
        return False, f"Synthetic data test failed: {str(e)}\n{traceback.format_exc()}"


def validate_strategy(file_path):
    """
    Perform complete validation of a strategy file.

    Returns:
        (bool, dict): (passed, results_dict)
    """
    file_path = Path(file_path)

    if not file_path.exists():
        return False, {"error": f"File not found: {file_path}"}

    results = {
        "file": str(file_path),
        "timestamp": datetime.now().isoformat(),
        "checks": {},
    }

    print(f"\n{'='*60}")
    print(f"Validating: {file_path.name}")
    print(f"{'='*60}\n")

    # 1. Syntax check
    print("⏳ Checking Python syntax...")
    syntax_ok, syntax_error = validate_syntax(file_path)
    results["checks"]["syntax"] = {"passed": syntax_ok, "error": syntax_error}

    if syntax_ok:
        print("✓ Syntax valid")
    else:
        print(f"✗ Syntax error: {syntax_error}")
        return False, results

    # 2. Load module
    print("⏳ Loading module and checking imports...")
    module, load_error = load_strategy_module(file_path)
    results["checks"]["imports"] = {"passed": module is not None, "error": load_error}

    if module is None:
        print(f"✗ Import failed: {load_error}")
        return False, results
    else:
        print("✓ Imports successful")

    # 3. Validate STRATEGY_CONFIG
    print("⏳ Validating STRATEGY_CONFIG structure...")
    config_ok, config_error = validate_strategy_config(module)
    results["checks"]["config"] = {"passed": config_ok, "error": config_error}

    if config_ok:
        print("✓ STRATEGY_CONFIG structure valid")
    else:
        print(f"✗ Config error: {config_error}")
        return False, results

    # 4. Validate required functions
    print("⏳ Checking required functions...")
    functions_ok, functions_error = validate_required_functions(module)
    results["checks"]["functions"] = {"passed": functions_ok, "error": functions_error}

    if functions_ok:
        print("✓ Required functions present")
    else:
        print(f"✗ Functions error: {functions_error}")
        return False, results

    # 5. Test with synthetic data
    print("⏳ Testing with synthetic data (500 bars)...")
    data_ok, data_result = test_with_synthetic_data(module)

    if isinstance(data_result, dict):
        # Success, got signal counts
        results["checks"]["synthetic_data"] = {
            "passed": True,
            "signal_counts": data_result,
        }
        print("✓ Synthetic data test passed")
        print(f"  Signals generated: {data_result['BUY']} BUY, {data_result['SELL']} SELL, {data_result['HOLD']} HOLD")
    else:
        # Failure, got error message
        results["checks"]["synthetic_data"] = {
            "passed": False,
            "error": data_result,
        }
        print(f"✗ Synthetic data test failed: {data_result}")
        return False, results

    # All checks passed
    return True, results


def main():
    if len(sys.argv) < 2:
        print("Usage: venv/bin/python validate_strategy.py /path/to/strategy.py")
        sys.exit(1)

    strategy_file = sys.argv[1]
    passed, results = validate_strategy(strategy_file)

    # Print summary
    print(f"\n{'='*60}")
    if passed:
        print("✅ PASS: Strategy validation successful")
    else:
        print("❌ FAIL: Strategy validation failed")
    print(f"{'='*60}\n")

    # Optionally save results to JSON
    if len(sys.argv) > 2 and sys.argv[2] == "--json":
        output_file = Path(strategy_file).with_suffix(".validation.json")
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {output_file}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
