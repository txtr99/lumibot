"""
Strategy 003: NQ RSI Mean Reversion

RSI-based mean reversion strategy for Nasdaq E-mini futures.
- Entry: RSI oversold (<30) for longs, overbought (>70) for shorts, with trend filter
- Exit: ATR-based bracket orders (TP=2.5x ATR, SL=1x ATR)
- Time exit: 90 bars maximum

Symbol: NQ (E-mini Nasdaq 100 Futures)
Direction: Both long and short

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import numpy as np
import pandas as pd

# ==================== STRATEGY CONFIGURATION ====================

STRATEGY_CONFIG = {
    "strategy_id": "nq_rsi_reversion_003",
    "symbol": "NQ",  # E-mini Nasdaq 100 Futures
    "contracts": 1,
    "params": {
        # RSI parameters
        "rsi_period": 14,
        "rsi_oversold": 30,  # Long entry below this
        "rsi_overbought": 70,  # Short entry above this
        # Trend filter for mean reversion
        "use_trend_filter": True,
        "trend_sma_period": 100,  # Trade against short-term moves within trend
        # Minimum RSI divergence from extreme
        "min_rsi_divergence": 5,  # RSI must be at least 5 points from extreme
    },
    "bracket_config": {
        "use_bracket_orders": True,
        "atr_period": 20,
        "profit_target_mult": 2.5,  # 2.5x ATR for profit target (tighter for mean reversion)
        "stop_loss_mult": 1.0,  # 1x ATR for stop loss (tight stop)
        "use_atr_profit": True,
        "use_atr_stop": True,
    },
    "exit_config": {
        "max_bars_in_trade": 90,  # 90-minute time exit (faster for mean reversion)
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


# ==================== SIGNAL GENERATION FUNCTION ====================


def generate_signal(strategy_state, market_data):
    """
    Generate trading signal for NQ RSI Mean Reversion strategy.

    Entry conditions:
    - LONG: RSI < 30 (oversold) and price above trend SMA
    - SHORT: RSI > 70 (overbought) and price below trend SMA

    The trend filter ensures we're trading mean reversion within the trend,
    not against the major trend.
    """

    # Get parameters
    params = strategy_state.params
    rsi_period = params.get("rsi_period", 14)
    rsi_oversold = params.get("rsi_oversold", 30)
    rsi_overbought = params.get("rsi_overbought", 70)
    use_trend_filter = params.get("use_trend_filter", True)
    trend_period = params.get("trend_sma_period", 100)
    min_divergence = params.get("min_rsi_divergence", 5)

    # Check if we have enough data
    min_required = max(rsi_period + 2, trend_period + 2 if use_trend_filter else 0)
    if len(market_data) < min_required:
        return "HOLD"

    # Calculate RSI
    rsi = wilder_rsi(market_data["close"], period=rsi_period)
    rsi_now = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-2]

    # Check for NaN
    if pd.isna(rsi_now) or pd.isna(rsi_prev):
        return "HOLD"

    # Trend filter
    in_uptrend = True
    in_downtrend = True

    if use_trend_filter:
        trend_sma = market_data["close"].rolling(window=trend_period).mean()
        trend_now = trend_sma.iloc[-1]

        if not pd.isna(trend_now):
            close_now = market_data["close"].iloc[-1]
            # In uptrend if price above trend SMA
            in_uptrend = close_now > trend_now
            # In downtrend if price below trend SMA
            in_downtrend = close_now < trend_now
        else:
            # If trend data not ready, don't trade
            return "HOLD"

    # Check RSI extremes with minimum divergence
    # For oversold: RSI must be below threshold and recovering slightly
    is_oversold = rsi_now < rsi_oversold
    oversold_recovering = (rsi_now > rsi_prev) and (rsi_now < (rsi_oversold + min_divergence))

    # For overbought: RSI must be above threshold and declining slightly
    is_overbought = rsi_now > rsi_overbought
    overbought_declining = (rsi_now < rsi_prev) and (rsi_now > (rsi_overbought - min_divergence))

    # Generate signals
    # Long: Oversold in uptrend (mean reversion within uptrend)
    if is_oversold and oversold_recovering and in_uptrend:
        return "BUY"

    # Short: Overbought in downtrend (mean reversion within downtrend)
    elif is_overbought and overbought_declining and in_downtrend:
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

    rsi_period = params.get("rsi_period", 0)
    rsi_oversold = params.get("rsi_oversold", 0)
    rsi_overbought = params.get("rsi_overbought", 0)

    if rsi_period <= 0:
        return False, "rsi_period must be positive"

    if rsi_oversold <= 0 or rsi_oversold >= 50:
        return False, "rsi_oversold must be between 0 and 50"

    if rsi_overbought <= 50 or rsi_overbought >= 100:
        return False, "rsi_overbought must be between 50 and 100"

    if rsi_oversold >= rsi_overbought:
        return False, "rsi_oversold must be less than rsi_overbought"

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
    "name": "NQ RSI Mean Reversion",
    "description": "Mean reversion strategy using RSI extremes with trend filter",
    "author": "LumiBot Team",
    "version": "1.0.0",
    "created_date": "2025-11-18",
    "strategy_type": "mean_reversion",
    "timeframe": "1M",
    "markets": ["NQ"],
    "risk_level": "medium",
    "notes": "Trades mean reversion within the trend using RSI extremes",
}


# ==================== TESTING ====================

if __name__ == "__main__":
    """Test the strategy configuration."""

    import sys
    from datetime import datetime, timedelta

    print("=" * 60)
    print("NQ RSI MEAN REVERSION STRATEGY TEST")
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
    print(f"   RSI Period: {STRATEGY_CONFIG['params']['rsi_period']}")
    print(f"   RSI Oversold: {STRATEGY_CONFIG['params']['rsi_oversold']}")
    print(f"   RSI Overbought: {STRATEGY_CONFIG['params']['rsi_overbought']}")

    # Test signal generation
    print("\n3. Testing signal generation...")

    # Create sample data with mean reverting pattern
    dates = pd.date_range(start=datetime.now() - timedelta(hours=3), periods=150, freq="1min")

    # Create base trend
    base_price = 15000.0
    trend = np.linspace(0, 100, 150)  # Uptrend

    # Add mean-reverting oscillations
    oscillation = np.sin(np.linspace(0, 6 * np.pi, 150)) * 50
    noise = np.random.randn(150) * 10

    prices = base_price + trend + oscillation + noise

    sample_data = pd.DataFrame(
        {
            "open": prices + np.random.randn(150) * 2,
            "high": prices + abs(np.random.randn(150) * 5),
            "low": prices - abs(np.random.randn(150) * 5),
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

        # Show current indicators
        rsi = wilder_rsi(sample_data["close"], period=14)
        trend_sma = sample_data["close"].rolling(window=100).mean()

        print(f"   Current RSI: {rsi.iloc[-1]:.2f}")
        print(f"   Previous RSI: {rsi.iloc[-2]:.2f}")
        print(
            f"   Trend SMA (100): {trend_sma.iloc[-1]:.2f}"
            if not pd.isna(trend_sma.iloc[-1])
            else "   Trend SMA (100): N/A"
        )
        print(f"   Current Close: {sample_data['close'].iloc[-1]:.2f}")

        # Check conditions
        if not pd.isna(trend_sma.iloc[-1]):
            above_trend = sample_data["close"].iloc[-1] > trend_sma.iloc[-1]
            print(f"   Price above trend: {above_trend}")

        if rsi.iloc[-1] < 30:
            print("   RSI Status: OVERSOLD")
        elif rsi.iloc[-1] > 70:
            print("   RSI Status: OVERBOUGHT")
        else:
            print("   RSI Status: NEUTRAL")

        print("   ✓ Signal generation successful")

    except Exception as e:
        print(f"   ✗ Signal generation failed: {e}")
        import traceback

        print(traceback.format_exc())

    print("\n" + "=" * 60)
    print("Strategy test complete!")
    print("=" * 60)
