"""
Strategy 001: MGC 3 Lows RSI Mean Reversion

This strategy implements the original logic from Python_from_EL_plot_fixed.py:
- Entry: 3 descending lows + RSI cross below 70 + SMA200 rising (long only)
- Exit: ATR-based bracket orders (TP=5x ATR, SL=2x ATR)
- Time exit: 180 bars maximum

Symbol: MGC (Micro Gold Futures)
Direction: Long only

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import numpy as np
import pandas as pd

# ==================== STRATEGY CONFIGURATION ====================

STRATEGY_CONFIG = {
    "strategy_id": "mgc_3lows_rsi_001",
    "symbol": "MGC",  # Micro Gold Futures
    "contracts": 1,
    "params": {
        # RSI parameters
        "rsi_period": 14,
        "rsi_threshold": 70,  # Cross below this level
        # Moving average parameters
        "sma_period": 200,
        # Pattern parameters
        "descending_lows_count": 3,
    },
    "bracket_config": {
        "use_bracket_orders": True,
        "atr_period": 20,
        "profit_target_mult": 5.0,  # 5x ATR for profit target
        "stop_loss_mult": 2.0,  # 2x ATR for stop loss
        "use_atr_profit": True,
        "use_atr_stop": True,
    },
    "exit_config": {
        "max_bars_in_trade": 180,  # 180-minute time exit
        "exit_on_session_end": True,
    },
    "allowed_sessions": ["New_York"],
    "initial_capital": 10000.0,
}


# ==================== TECHNICAL INDICATORS ====================


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Wilder's RSI."""
    delta = close.diff()
    gains = delta.where(delta > 0, 0)
    losses = -delta.where(delta < 0, 0)

    avg_gains = gains.ewm(alpha=1 / period, adjust=False).mean()
    avg_losses = losses.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gains / avg_losses
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)

    return rsi


def has_descending_lows(low: pd.Series, count: int = 3) -> bool:
    """Check if we have N descending lows."""
    if len(low) < count:
        return False

    # Check last N lows
    recent_lows = low.iloc[-count:].values

    # Check if each low is lower than the previous
    for i in range(1, len(recent_lows)):
        if recent_lows[i] >= recent_lows[i - 1]:
            return False

    return True


# ==================== SIGNAL GENERATION FUNCTION ====================


def generate_signal(strategy_state, market_data):
    """
    Generate trading signal for MGC 3 Lows RSI strategy.

    Entry conditions (all must be true):
    1. Three consecutive descending lows
    2. RSI crosses below 70
    3. SMA 200 is rising

    This is a long-only strategy.
    """

    # Get parameters
    params = strategy_state.params
    rsi_period = params.get("rsi_period", 14)
    rsi_threshold = params.get("rsi_threshold", 70)
    sma_period = params.get("sma_period", 200)
    descending_count = params.get("descending_lows_count", 3)

    # Check if we have enough data
    min_required = max(sma_period + 1, rsi_period + 1, descending_count)
    if len(market_data) < min_required:
        return "HOLD"

    # Calculate indicators
    rsi = wilder_rsi(market_data["close"], period=rsi_period)
    sma200 = market_data["close"].rolling(window=sma_period).mean()

    # Get current and previous values
    rsi_now = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-2]
    sma_now = sma200.iloc[-1]
    sma_prev = sma200.iloc[-2]

    # Check for NaN values
    if pd.isna(rsi_now) or pd.isna(rsi_prev) or pd.isna(sma_now) or pd.isna(sma_prev):
        return "HOLD"

    # Check entry conditions

    # 1. Three descending lows
    descending_lows = has_descending_lows(market_data["low"], count=descending_count)

    # 2. RSI crosses below 70
    rsi_cross_below = (rsi_prev >= rsi_threshold) and (rsi_now < rsi_threshold)

    # 3. SMA 200 is rising
    sma_rising = sma_now > sma_prev

    # Generate signal (long only)
    if descending_lows and rsi_cross_below and sma_rising:
        return "BUY"
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

    if params.get("rsi_period", 0) <= 0:
        return False, "rsi_period must be positive"

    if params.get("sma_period", 0) <= 0:
        return False, "sma_period must be positive"

    if params.get("descending_lows_count", 0) <= 0:
        return False, "descending_lows_count must be positive"

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
    "name": "MGC 3 Lows RSI Mean Reversion",
    "description": "Mean reversion strategy using 3 descending lows pattern with RSI confirmation",
    "author": "LumiBot Team",
    "version": "1.0.0",
    "created_date": "2025-11-18",
    "strategy_type": "mean_reversion",
    "timeframe": "1M",
    "markets": ["MGC"],
    "risk_level": "medium",
    "notes": "Long-only strategy adapted from Python_from_EL_plot_fixed.py",
}


# ==================== TESTING ====================

if __name__ == "__main__":
    """Test the strategy configuration."""

    import sys
    from datetime import datetime, timedelta

    print("=" * 60)
    print("MGC 3 LOWS RSI STRATEGY TEST")
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

    # Test signal generation
    print("\n3. Testing signal generation...")

    # Create sample data with pattern
    dates = pd.date_range(start=datetime.now() - timedelta(hours=5), periods=250, freq="1min")

    # Create price data with descending lows pattern
    base_price = 2000.0
    prices = []
    lows = []

    for i in range(250):
        if i < 240:
            # Normal price movement
            price = base_price + np.random.randn() * 5
        else:
            # Create descending lows in last 10 bars
            price = base_price - (250 - i) * 0.5 + np.random.randn() * 2

        prices.append(price)
        lows.append(price - abs(np.random.randn() * 2))

    sample_data = pd.DataFrame(
        {
            "open": prices,
            "high": [p + abs(np.random.randn() * 1) for p in prices],
            "low": lows,
            "close": prices,
            "volume": np.random.randint(1000, 10000, 250),
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

        # Check RSI
        rsi = wilder_rsi(sample_data["close"], period=14)
        print(f"   Current RSI: {rsi.iloc[-1]:.2f}")

        # Check descending lows
        has_pattern = has_descending_lows(sample_data["low"], count=3)
        print(f"   Has 3 descending lows: {has_pattern}")

        # Check SMA
        sma = sample_data["close"].rolling(window=200).mean()
        sma_rising = sma.iloc[-1] > sma.iloc[-2] if not pd.isna(sma.iloc[-1]) else False
        print(f"   SMA200 rising: {sma_rising}")

        print("   ✓ Signal generation successful")

    except Exception as e:
        print(f"   ✗ Signal generation failed: {e}")
        import traceback

        print(traceback.format_exc())

    print("\n" + "=" * 60)
    print("Strategy test complete!")
    print("=" * 60)
