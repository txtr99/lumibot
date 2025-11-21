"""
Strategy Template - Standard Format for All Trading Strategies

This file serves as the template for creating new trading strategies that are
compatible with the dynamic multi-strategy portfolio system.

To create a new strategy:
1. Copy this file and rename it (e.g., strategy_001_es_ma_cross.py)
2. Update STRATEGY_CONFIG with your strategy parameters
3. Implement the generate_signal() function with your entry logic
4. Optionally implement validate_config() for custom validation
5. Drop the file in active_strategies/ folder - it will auto-load!

Key Requirements:
- Must have STRATEGY_CONFIG dictionary
- Must have generate_signal() function
- Must return "BUY", "SELL", or "HOLD" from generate_signal
- All parameters must be in STRATEGY_CONFIG
- No plotting or visualization code
- No direct broker/order management (handled by executor)

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

# ==================== STRATEGY CONFIGURATION ====================
# This configuration MUST be present in every strategy file

STRATEGY_CONFIG = {
    # Unique identifier for this strategy instance
    "strategy_id": "template_strategy_001",
    # Symbol to trade (must be exact symbol as used by broker)
    "symbol": "ES",  # ES, NQ, YM, GC, CL, etc.
    # Number of contracts per trade (fixed position sizing)
    "contracts": 1,
    # Strategy-specific parameters for entry logic
    "params": {
        # Example parameters - replace with your strategy's needs
        "fast_sma_period": 10,
        "slow_sma_period": 20,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        # Add any parameters your strategy needs
        # These will be available in generate_signal function
    },
    # Bracket order configuration (ATR-based TP/SL)
    "bracket_config": {
        "use_bracket_orders": True,  # Enable bracket orders
        "atr_period": 20,  # Period for ATR calculation
        "profit_target_mult": 3.0,  # Take profit as multiple of ATR
        "stop_loss_mult": 1.5,  # Stop loss as multiple of ATR
        "use_atr_profit": True,  # Use ATR for profit target
        "use_atr_stop": True,  # Use ATR for stop loss
    },
    # Exit configuration
    "exit_config": {
        "max_bars_in_trade": 180,  # Maximum bars before time exit (None to disable)
        "exit_on_session_end": True,  # Exit before session close
    },
    # Optional: Trading session restrictions
    "allowed_sessions": ["New_York"],  # Which sessions to trade in
    # Optional: Initial capital for attribution (backtesting)
    "initial_capital": 10000.0,
}


# ==================== SIGNAL GENERATION FUNCTION ====================
# This function MUST be present in every strategy file


def generate_signal(strategy_state, market_data):
    """
    Generate trading signal based on strategy logic.

    This function is called on every bar to determine if the strategy
    should enter a position. It should only generate entry signals when
    the strategy is flat (no position).

    Args:
        strategy_state: EnhancedStrategyState object containing:
            - strategy_id: Unique identifier
            - symbol: Symbol being traded
            - params: Dictionary of strategy parameters
            - tracker: VirtualPositionTracker for position info
            - last_signal: Previous signal generated
            - entry_time: When current position was entered
            - bars_in_trade: Bars since entry

        market_data: pandas DataFrame with columns:
            - open: Opening prices
            - high: High prices
            - low: Low prices
            - close: Closing prices
            - volume: Volume data
            Index is datetime timestamps

    Returns:
        str: One of:
            - "BUY": Enter long position
            - "SELL": Enter short position
            - "HOLD": No action (stay flat or in position)

    Example Implementation:
        >>> # Simple moving average crossover
        >>> fast_sma = market_data['close'].rolling(window=10).mean()
        >>> slow_sma = market_data['close'].rolling(window=20).mean()
        >>>
        >>> # Get latest values
        >>> fast_now = fast_sma.iloc[-1]
        >>> slow_now = slow_sma.iloc[-1]
        >>> fast_prev = fast_sma.iloc[-2]
        >>> slow_prev = slow_sma.iloc[-2]
        >>>
        >>> # Check for crossover
        >>> if fast_now > slow_now and fast_prev <= slow_prev:
        >>>     return "BUY"
        >>> elif fast_now < slow_now and fast_prev >= slow_prev:
        >>>     return "SELL"
        >>> else:
        >>>     return "HOLD"
    """

    # ============ REPLACE THIS WITH YOUR STRATEGY LOGIC ============

    # Example: Simple SMA crossover strategy
    # This is just a template - implement your own logic!

    # Get parameters
    params = strategy_state.params
    fast_period = params.get("fast_sma_period", 10)
    slow_period = params.get("slow_sma_period", 20)

    # Check if we have enough data
    if len(market_data) < slow_period + 1:
        return "HOLD"

    # Calculate indicators
    fast_sma = market_data["close"].rolling(window=fast_period).mean()
    slow_sma = market_data["close"].rolling(window=slow_period).mean()

    # Get current and previous values
    fast_now = fast_sma.iloc[-1]
    slow_now = slow_sma.iloc[-1]
    fast_prev = fast_sma.iloc[-2]
    slow_prev = slow_sma.iloc[-2]

    # Check for NaN values
    if any(pd.isna([fast_now, slow_now, fast_prev, slow_prev])):
        return "HOLD"

    # Generate signals based on crossover
    if fast_now > slow_now and fast_prev <= slow_prev:
        # Fast SMA crossed above slow SMA - BUY signal
        return "BUY"
    elif fast_now < slow_now and fast_prev >= slow_prev:
        # Fast SMA crossed below slow SMA - SELL signal
        return "SELL"
    else:
        # No crossover - hold position
        return "HOLD"


# ==================== OPTIONAL VALIDATION FUNCTION ====================
# This function is optional but recommended for parameter validation


def validate_config():
    """
    Validate strategy configuration before loading.

    This optional function is called when the strategy is loaded to ensure
    all parameters are valid. Use this to check parameter ranges, required
    fields, and logical consistency.

    Returns:
        tuple: (is_valid: bool, error_message: str)
            - is_valid: True if configuration is valid
            - error_message: Description of validation error (if any)

    Example:
        >>> # Check that SMA periods make sense
        >>> if STRATEGY_CONFIG["params"]["fast_sma_period"] >= \
        ...    STRATEGY_CONFIG["params"]["slow_sma_period"]:
        ...     return False, "Fast SMA period must be less than slow SMA period"
        >>> return True, ""
    """

    # Basic validation
    config = STRATEGY_CONFIG

    # Check required fields
    if "strategy_id" not in config or not config["strategy_id"]:
        return False, "strategy_id is required"

    if "symbol" not in config or not config["symbol"]:
        return False, "symbol is required"

    if "contracts" not in config or config["contracts"] < 1:
        return False, "contracts must be >= 1"

    # Check parameters exist
    if "params" not in config or not isinstance(config["params"], dict):
        return False, "params dictionary is required"

    # Strategy-specific validation
    params = config["params"]

    # Example: Check SMA periods
    fast_period = params.get("fast_sma_period", 0)
    slow_period = params.get("slow_sma_period", 0)

    if fast_period <= 0 or slow_period <= 0:
        return False, "SMA periods must be positive"

    if fast_period >= slow_period:
        return False, "Fast SMA period must be less than slow SMA period"

    # Check bracket configuration if enabled
    if config.get("bracket_config", {}).get("use_bracket_orders", False):
        bracket = config["bracket_config"]

        if bracket.get("profit_target_mult", 0) <= 0:
            return False, "profit_target_mult must be positive"

        if bracket.get("stop_loss_mult", 0) <= 0:
            return False, "stop_loss_mult must be positive"

        if bracket.get("atr_period", 0) <= 0:
            return False, "atr_period must be positive"

    # All validation passed
    return True, ""


# ==================== OPTIONAL HELPER FUNCTIONS ====================
# You can add strategy-specific helper functions here


def calculate_custom_indicator(data):
    """
    Example of a custom indicator calculation.

    Add any helper functions your strategy needs here.
    Keep them focused and well-documented.
    """
    # Your custom logic here
    pass


# ==================== STRATEGY METADATA (OPTIONAL) ====================
# Additional information about the strategy for documentation

STRATEGY_METADATA = {
    "name": "Template Strategy",
    "description": "Template for creating new trading strategies",
    "author": "Your Name",
    "version": "1.0.0",
    "created_date": "2025-11-18",
    "strategy_type": "trend_following",  # or "mean_reversion", "breakout", etc.
    "timeframe": "1M",  # 1-minute bars
    "markets": ["ES"],  # List of compatible markets
    "risk_level": "medium",  # low, medium, high
    "notes": "This is a template - replace with your actual strategy logic",
}


# ==================== TESTING THE STRATEGY ====================
# This section only runs when the file is executed directly

if __name__ == "__main__":
    """
    Test the strategy configuration and signal generation.

    Run this file directly to test your strategy:
        python strategy_template.py
    """

    from datetime import datetime, timedelta

    import numpy as np
    import pandas as pd

    print("=" * 60)
    print("STRATEGY TEMPLATE TEST")
    print("=" * 60)

    # Validate configuration
    print("\n1. Validating configuration...")
    is_valid, error_msg = validate_config()
    if is_valid:
        print("   ✓ Configuration is valid")
    else:
        print(f"   ✗ Configuration error: {error_msg}")
        exit(1)

    # Display configuration
    print("\n2. Strategy Configuration:")
    print(f"   ID: {STRATEGY_CONFIG['strategy_id']}")
    print(f"   Symbol: {STRATEGY_CONFIG['symbol']}")
    print(f"   Contracts: {STRATEGY_CONFIG['contracts']}")
    print(f"   Parameters: {STRATEGY_CONFIG['params']}")

    # Test signal generation with sample data
    print("\n3. Testing signal generation...")

    # Create sample market data
    dates = pd.date_range(start=datetime.now() - timedelta(days=5), periods=100, freq="1min")
    prices = 4500 + np.cumsum(np.random.randn(100) * 2)  # Random walk

    sample_data = pd.DataFrame(
        {
            "open": prices + np.random.randn(100) * 0.5,
            "high": prices + abs(np.random.randn(100) * 1),
            "low": prices - abs(np.random.randn(100) * 1),
            "close": prices,
            "volume": np.random.randint(1000, 10000, 100),
        },
        index=dates,
    )

    # Create mock strategy state
    class MockStrategyState:
        def __init__(self):
            self.strategy_id = STRATEGY_CONFIG["strategy_id"]
            self.symbol = STRATEGY_CONFIG["symbol"]
            self.params = STRATEGY_CONFIG["params"]
            self.last_signal = None
            self.entry_time = None
            self.bars_in_trade = 0

    mock_state = MockStrategyState()

    # Test signal generation
    try:
        signal = generate_signal(mock_state, sample_data)
        print(f"   Generated signal: {signal}")

        if signal in ["BUY", "SELL", "HOLD"]:
            print("   ✓ Signal generation successful")
        else:
            print(f"   ✗ Invalid signal: {signal}")

    except Exception as e:
        print(f"   ✗ Signal generation failed: {e}")

    print("\n" + "=" * 60)
    print("Strategy template test complete!")
    print("=" * 60)
