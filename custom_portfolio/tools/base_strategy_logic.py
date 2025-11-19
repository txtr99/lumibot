"""
Base Strategy Logic - Technical Indicators and Helper Functions

This module provides common technical indicators and helper functions used across
multiple trading strategies. All indicators are optimized for performance and
designed to work with pandas DataFrames.

Key Features:
- Wilder's RSI and ATR calculations
- Moving averages (SMA, EMA)
- MACD indicator
- Bollinger Bands
- Price pattern detection
- Helper functions for rounding and comparisons

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd


class TechnicalIndicators:
    """Collection of technical indicators for trading strategies."""

    @staticmethod
    def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate Wilder's Average True Range (ATR).

        ATR measures market volatility by decomposing the entire range of an asset
        price for that period. Uses Wilder's smoothing (EMA with alpha=1/period).

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            period: ATR period (default: 20)

        Returns:
            Series with ATR values
        """
        prev_close = close.shift(1)

        # True Range = max of:
        # 1. High - Low
        # 2. abs(High - Previous Close)
        # 3. abs(Low - Previous Close)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

        # Wilder's smoothing (exponential moving average with alpha = 1/period)
        atr = tr.ewm(alpha=1 / period, adjust=False).mean()

        return atr

    @staticmethod
    def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Wilder's Relative Strength Index (RSI).

        RSI measures momentum - whether a stock is overbought or oversold.
        Uses Wilder's smoothing method for the average gains and losses.

        Args:
            close: Close price series
            period: RSI period (default: 14)

        Returns:
            Series with RSI values (0-100)
        """
        # Calculate price changes
        delta = close.diff()

        # Separate gains and losses
        gains = delta.where(delta > 0, 0)
        losses = -delta.where(delta < 0, 0)

        # Apply Wilder's smoothing (EMA with alpha = 1/period)
        avg_gains = gains.ewm(alpha=1 / period, adjust=False).mean()
        avg_losses = losses.ewm(alpha=1 / period, adjust=False).mean()

        # Calculate RS and RSI
        rs = avg_gains / avg_losses
        rsi = 100 - (100 / (1 + rs))

        # Handle edge cases (divide by zero)
        rsi = rsi.fillna(50)  # Neutral RSI when no data

        return rsi

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """
        Simple Moving Average.

        Args:
            series: Price series
            period: MA period

        Returns:
            Series with SMA values
        """
        return series.rolling(window=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """
        Exponential Moving Average.

        Args:
            series: Price series
            period: EMA period

        Returns:
            Series with EMA values
        """
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def macd(
        close: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD (Moving Average Convergence Divergence) indicator.

        Args:
            close: Close price series
            fast_period: Fast EMA period (default: 12)
            slow_period: Slow EMA period (default: 26)
            signal_period: Signal line EMA period (default: 9)

        Returns:
            Tuple of (macd_line, signal_line, histogram)
        """
        fast_ema = close.ewm(span=fast_period, adjust=False).mean()
        slow_ema = close.ewm(span=slow_period, adjust=False).mean()

        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(
        close: pd.Series, period: int = 20, num_std: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands indicator.

        Args:
            close: Close price series
            period: MA period (default: 20)
            num_std: Number of standard deviations (default: 2.0)

        Returns:
            Tuple of (upper_band, middle_band, lower_band)
        """
        middle_band = close.rolling(window=period).mean()
        std_dev = close.rolling(window=period).std()

        upper_band = middle_band + (std_dev * num_std)
        lower_band = middle_band - (std_dev * num_std)

        return upper_band, middle_band, lower_band

    @staticmethod
    def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        Williams %R indicator (Williams Percent Range).

        Momentum indicator that measures overbought/oversold levels.
        Scale is -100 to 0 (inverse of typical oscillators).

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            period: Lookback period (default: 14)

        Returns:
            Series with Williams %R values (-100 to 0)
        """
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()

        williams_r = -100 * ((highest_high - close) / (highest_high - lowest_low))

        return williams_r

    @staticmethod
    def stochastic(
        high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Stochastic Oscillator.

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            k_period: %K period (default: 14)
            d_period: %D period (smoothing) (default: 3)

        Returns:
            Tuple of (%K, %D)
        """
        lowest_low = low.rolling(window=k_period).min()
        highest_high = high.rolling(window=k_period).max()

        k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
        d_percent = k_percent.rolling(window=d_period).mean()

        return k_percent, d_percent


class PricePatterns:
    """Detection of price patterns used in trading strategies."""

    @staticmethod
    def descending_lows(low: pd.Series, lookback: int = 3) -> pd.Series:
        """
        Check for pattern of descending lows.

        Args:
            low: Low price series
            lookback: Number of bars to check (default: 3)

        Returns:
            Boolean series indicating where pattern exists
        """
        # Check if each low is lower than the previous
        conditions = []
        for i in range(1, lookback):
            conditions.append(low < low.shift(i))

        # All conditions must be true
        if conditions:
            result = conditions[0]
            for condition in conditions[1:]:
                result = result & condition
            return result
        else:
            return pd.Series([False] * len(low), index=low.index)

    @staticmethod
    def ascending_highs(high: pd.Series, lookback: int = 3) -> pd.Series:
        """
        Check for pattern of ascending highs.

        Args:
            high: High price series
            lookback: Number of bars to check (default: 3)

        Returns:
            Boolean series indicating where pattern exists
        """
        # Check if each high is higher than the previous
        conditions = []
        for i in range(1, lookback):
            conditions.append(high > high.shift(i))

        # All conditions must be true
        if conditions:
            result = conditions[0]
            for condition in conditions[1:]:
                result = result & condition
            return result
        else:
            return pd.Series([False] * len(high), index=high.index)

    @staticmethod
    def is_rising(series: pd.Series, lookback: int = 1) -> pd.Series:
        """
        Check if series is rising (current value > previous value).

        Args:
            series: Price series
            lookback: Bars to look back (default: 1)

        Returns:
            Boolean series
        """
        return series > series.shift(lookback)

    @staticmethod
    def is_falling(series: pd.Series, lookback: int = 1) -> pd.Series:
        """
        Check if series is falling (current value < previous value).

        Args:
            series: Price series
            lookback: Bars to look back (default: 1)

        Returns:
            Boolean series
        """
        return series < series.shift(lookback)

    @staticmethod
    def crosses_above(series1: pd.Series, series2: pd.Series) -> pd.Series:
        """
        Check if series1 crosses above series2.

        Args:
            series1: First series
            series2: Second series

        Returns:
            Boolean series indicating crossover points
        """
        return (series1 > series2) & (series1.shift(1) <= series2.shift(1))

    @staticmethod
    def crosses_below(series1: pd.Series, series2: pd.Series) -> pd.Series:
        """
        Check if series1 crosses below series2.

        Args:
            series1: First series
            series2: Second series

        Returns:
            Boolean series indicating crossunder points
        """
        return (series1 < series2) & (series1.shift(1) >= series2.shift(1))


class HelperFunctions:
    """Helper functions for strategy implementation."""

    @staticmethod
    def round_to_tick(price: Optional[float], tick_size: float) -> Optional[float]:
        """
        Round price to nearest tick size.

        Args:
            price: Price to round
            tick_size: Minimum price increment

        Returns:
            Rounded price or None
        """
        if price is None or tick_size <= 0:
            return price
        # Round to nearest tick with appropriate precision to avoid FP noise
        return round(round(price / tick_size) * tick_size, 10)

    @staticmethod
    def calculate_position_size(
        account_balance: float,
        risk_percent: float,
        stop_loss_points: float,
        point_value: float = 1.0,
        max_contracts: int = None,
    ) -> int:
        """
        Calculate position size based on risk management.

        Args:
            account_balance: Account balance in dollars
            risk_percent: Percentage of account to risk (e.g., 0.02 for 2%)
            stop_loss_points: Stop loss distance in points
            point_value: Dollar value per point (default: 1.0)
            max_contracts: Maximum contracts allowed (optional)

        Returns:
            Number of contracts to trade
        """
        if stop_loss_points <= 0:
            return 0

        # Calculate risk amount
        risk_amount = account_balance * risk_percent

        # Calculate contracts based on risk
        contracts = risk_amount / (stop_loss_points * point_value)

        # Round down to integer
        contracts = int(contracts)

        # Apply maximum if specified
        if max_contracts is not None:
            contracts = min(contracts, max_contracts)

        return max(0, contracts)

    @staticmethod
    def is_market_hours(
        current_time: datetime, market_open: str = "09:30", market_close: str = "16:00", timezone: str = "US/Eastern"
    ) -> bool:
        """
        Check if current time is within market hours.

        Args:
            current_time: Current datetime
            market_open: Market open time (HH:MM format)
            market_close: Market close time (HH:MM format)
            timezone: Market timezone

        Returns:
            True if within market hours
        """
        # This is a simplified check - real implementation would use
        # proper timezone handling and market calendar
        hour = current_time.hour
        minute = current_time.minute

        open_hour, open_min = map(int, market_open.split(":"))
        close_hour, close_min = map(int, market_close.split(":"))

        current_minutes = hour * 60 + minute
        open_minutes = open_hour * 60 + open_min
        close_minutes = close_hour * 60 + close_min

        return open_minutes <= current_minutes < close_minutes

    @staticmethod
    def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02, periods_per_year: int = 252) -> float:
        """
        Calculate Sharpe ratio for a returns series.

        Args:
            returns: Returns series
            risk_free_rate: Annual risk-free rate (default: 2%)
            periods_per_year: Trading periods per year (default: 252)

        Returns:
            Sharpe ratio
        """
        if len(returns) < 2:
            return 0.0

        # Calculate excess returns
        risk_free_per_period = risk_free_rate / periods_per_year
        excess_returns = returns - risk_free_per_period

        # Calculate Sharpe ratio
        if excess_returns.std() == 0:
            return 0.0

        sharpe = (excess_returns.mean() / excess_returns.std()) * np.sqrt(periods_per_year)

        return sharpe

    @staticmethod
    def calculate_max_drawdown(equity_curve: pd.Series) -> Tuple[float, datetime, datetime]:
        """
        Calculate maximum drawdown from an equity curve.

        Args:
            equity_curve: Series of account values over time

        Returns:
            Tuple of (max_drawdown_percent, peak_date, trough_date)
        """
        if len(equity_curve) < 2:
            return 0.0, None, None

        # Calculate running maximum
        running_max = equity_curve.expanding().max()

        # Calculate drawdown
        drawdown = (equity_curve - running_max) / running_max

        # Find maximum drawdown
        max_dd = drawdown.min()

        if max_dd >= 0:
            return 0.0, None, None

        # Find the peak and trough dates
        trough_idx = drawdown.idxmin()
        peak_idx = equity_curve[:trough_idx].idxmax()

        return abs(max_dd), peak_idx, trough_idx


# Convenience functions for common use cases
def calculate_atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Calculate ATR from a DataFrame with OHLC columns."""
    return TechnicalIndicators.wilder_atr(df["high"], df["low"], df["close"], period)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate RSI from a DataFrame with close column."""
    return TechnicalIndicators.wilder_rsi(df["close"], period)


def calculate_sma(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
    """Calculate SMA from a DataFrame."""
    return TechnicalIndicators.sma(df[column], period)


def calculate_ema(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
    """Calculate EMA from a DataFrame."""
    return TechnicalIndicators.ema(df[column], period)


def calculate_bollinger_bands(
    df: pd.DataFrame, period: int = 20, num_std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands from a DataFrame."""
    return TechnicalIndicators.bollinger_bands(df["close"], period, num_std)


def calculate_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate MACD from a DataFrame."""
    return TechnicalIndicators.macd(df["close"], fast, slow, signal)
