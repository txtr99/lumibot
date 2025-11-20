"""
Strategy 002: ES SMA Crossover

Simple moving average crossover strategy for E-mini S&P 500 futures.
- Entry: Fast SMA crosses above/below slow SMA
- Exit: ATR-based bracket orders (TP=3x ATR, SL=1.5x ATR)
- Time exit: 120 bars maximum

Symbol: ES (E-mini S&P 500 Futures)
Direction: Both long and short

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import numpy as np
import pandas as pd

# ==================== STRATEGY CONFIGURATION ====================

STRATEGY_CONFIG = {
    "strategy_id": "es_sma_cross_002",
    "symbol": "ES",  # E-mini S&P 500 Futures
    "contracts": 1,
    "params": {
        # Moving average parameters
        "fast_sma_period": 10,
        "slow_sma_period": 20,
        # Trend filter (optional)
        "use_trend_filter": True,
        "trend_sma_period": 50,  # Only trade in direction of longer trend
    },
    "bracket_config": {
        "use_bracket_orders": True,
        "atr_period": 20,
        "profit_target_mult": 3.0,  # 3x ATR for profit target
        "stop_loss_mult": 1.5,  # 1.5x ATR for stop loss
        "use_atr_profit": True,
        "use_atr_stop": True,
    },
    "exit_config": {
        "max_bars_in_trade": 120,  # 120-minute time exit
        "exit_on_session_end": True,
    },
    "allowed_sessions": ["New_York"],
}


# ==================== SIGNAL GENERATION FUNCTION ====================


def generate_signal(strategy_state, market_data):
    """
    Generate trading signal for ES SMA Crossover strategy.

    Entry conditions:
    - BUY: Fast SMA crosses above Slow SMA (and trend filter if enabled)
    - SELL: Fast SMA crosses below Slow SMA (and trend filter if enabled)

    This strategy can go both long and short.
    """

    # Get parameters
    params = strategy_state.params
    fast_period = params.get("fast_sma_period", 10)
    slow_period = params.get("slow_sma_period", 20)
    use_trend_filter = params.get("use_trend_filter", True)
    trend_period = params.get("trend_sma_period", 50)

    # Check if we have enough data
    min_required = max(slow_period + 2, trend_period + 2 if use_trend_filter else 0)
    if len(market_data) < min_required:
        return "HOLD"

    # Calculate moving averages
    fast_sma = market_data["close"].rolling(window=fast_period).mean()
    slow_sma = market_data["close"].rolling(window=slow_period).mean()

    # Get current and previous values
    fast_now = fast_sma.iloc[-1]
    slow_now = slow_sma.iloc[-1]
    fast_prev = fast_sma.iloc[-2]
    slow_prev = slow_sma.iloc[-2]

    # Check for NaN values
    if pd.isna(fast_now) or pd.isna(slow_now) or pd.isna(fast_prev) or pd.isna(slow_prev):
        return "HOLD"

    # Optional trend filter
    trend_up = True
    trend_down = True

    if use_trend_filter:
        trend_sma = market_data["close"].rolling(window=trend_period).mean()
        trend_now = trend_sma.iloc[-1]
        trend_prev = trend_sma.iloc[-2]

        if not pd.isna(trend_now) and not pd.isna(trend_prev):
            # Trend is up if price above trend SMA and trend SMA rising
            close_now = market_data["close"].iloc[-1]
            trend_up = (close_now > trend_now) and (trend_now > trend_prev)
            trend_down = (close_now < trend_now) and (trend_now < trend_prev)
        else:
            # If trend filter data not ready, don't trade
            return "HOLD"

    # Check for crossovers
    fast_cross_above = (fast_prev <= slow_prev) and (fast_now > slow_now)
    fast_cross_below = (fast_prev >= slow_prev) and (fast_now < slow_now)

    # Generate signals with optional trend filter
    if fast_cross_above and trend_up:
        return "BUY"
    elif fast_cross_below and trend_down:
        return "SELL"
    else:
        return "HOLD"


# ==================== VALIDATION FUNCTION ====================


def validate_config():
    """Validate strategy configuration."""

    config = STRATEGY_CONFIG

    # Check required fields
    if "strategy_id" not in config or not config["strategy_id"]:
        return False, "strategy_id is required"

    if "symbol" not in config or not config["symbol"]:
        return False, "symbol is required"

    if "contracts" not in config or config["contracts"] < 1:
        return False, "contracts must be >= 1"

    # Check parameters
    params = config.get("params", {})

    fast_period = params.get("fast_sma_period", 0)
    slow_period = params.get("slow_sma_period", 0)

    if fast_period <= 0:
        return False, "fast_sma_period must be positive"

    if slow_period <= 0:
        return False, "slow_sma_period must be positive"

    if fast_period >= slow_period:
        return False, "fast_sma_period must be less than slow_sma_period"

    # Check trend filter period if enabled
    if params.get("use_trend_filter", False):
        trend_period = params.get("trend_sma_period", 0)
        if trend_period <= 0:
            return False, "trend_sma_period must be positive when trend filter is enabled"

    # Check bracket configuration
    bracket = config.get("bracket_config", {})
    if bracket.get("use_bracket_orders"):
        if bracket.get("profit_target_mult", 0) <= 0:
            return False, "profit_target_mult must be positive"

        if bracket.get("stop_loss_mult", 0) <= 0:
            return False, "stop_loss_mult must be positive"

    return True, ""


# ==================== STRATEGY METADATA ====================

STRATEGY_METADATA = {
    "name": "ES SMA Crossover",
    "description": "Simple moving average crossover strategy with trend filter",
    "author": "LumiBot Team",
    "version": "1.0.0",
    "created_date": "2025-11-18",
    "strategy_type": "trend_following",
    "timeframe": "1M",
    "markets": ["ES"],
    "risk_level": "low",
    "notes": "Classic SMA crossover with optional trend filter for ES futures",
}


# ==================== TESTING ====================

if __name__ == "__main__":
    """Test the strategy configuration."""

    import sys
    from datetime import datetime, timedelta

    print("=" * 60)
    print("ES SMA CROSSOVER STRATEGY TEST")
    print("=" * 60)

    # Validate configuration
    print("\n1. Validating configuration...")
    is_valid, error_msg = validate_config()
    if is_valid:
        print("   ✓ Configuration is valid")
    else:
        print(f"   ✗ Configuration error: {error_msg}")
        sys.exit(1)

    # Display configuration
    print("\n2. Strategy Configuration:")
    print(f"   ID: {STRATEGY_CONFIG['strategy_id']}")
    print(f"   Symbol: {STRATEGY_CONFIG['symbol']}")
    print(f"   Contracts: {STRATEGY_CONFIG['contracts']}")
    print(f"   Fast SMA: {STRATEGY_CONFIG['params']['fast_sma_period']}")
    print(f"   Slow SMA: {STRATEGY_CONFIG['params']['slow_sma_period']}")

    # Test signal generation
    print("\n3. Testing signal generation...")

    # Create sample data with trending pattern
    dates = pd.date_range(start=datetime.now() - timedelta(hours=3), periods=150, freq="1min")

    # Create trending price data
    base_price = 4500.0
    trend = np.linspace(0, 50, 150)  # Uptrend
    noise = np.random.randn(150) * 2
    prices = base_price + trend + noise

    # Add some oscillation for crossover
    fast_cycle = np.sin(np.linspace(0, 8 * np.pi, 150)) * 5
    prices = prices + fast_cycle

    sample_data = pd.DataFrame(
        {
            "open": prices + np.random.randn(150) * 0.5,
            "high": prices + abs(np.random.randn(150) * 1),
            "low": prices - abs(np.random.randn(150) * 1),
            "close": prices,
            "volume": np.random.randint(1000, 10000, 150),
        },
        index=dates,
    )

    # Create mock strategy state
    class MockStrategyState:
        def __init__(self):
            self.strategy_id = STRATEGY_CONFIG["strategy_id"]
            self.symbol = STRATEGY_CONFIG["symbol"]
            self.params = STRATEGY_CONFIG["params"]

    mock_state = MockStrategyState()

    # Test signal
    try:
        signal = generate_signal(mock_state, sample_data)
        print(f"   Generated signal: {signal}")

        # Show current SMA values
        fast_sma = sample_data["close"].rolling(window=10).mean()
        slow_sma = sample_data["close"].rolling(window=20).mean()
        trend_sma = sample_data["close"].rolling(window=50).mean()

        print(f"   Fast SMA (10): {fast_sma.iloc[-1]:.2f}")
        print(f"   Slow SMA (20): {slow_sma.iloc[-1]:.2f}")
        print(
            f"   Trend SMA (50): {trend_sma.iloc[-1]:.2f}"
            if not pd.isna(trend_sma.iloc[-1])
            else "   Trend SMA (50): N/A"
        )
        print(f"   Current Close: {sample_data['close'].iloc[-1]:.2f}")

        # Check crossover
        fast_above = fast_sma.iloc[-1] > slow_sma.iloc[-1]
        print(f"   Fast > Slow: {fast_above}")

        print("   ✓ Signal generation successful")

    except Exception as e:
        print(f"   ✗ Signal generation failed: {e}")
        import traceback

        print(traceback.format_exc())

    print("\n" + "=" * 60)
    print("Strategy test complete!")
    print("=" * 60)
