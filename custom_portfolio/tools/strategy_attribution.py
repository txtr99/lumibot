"""
StrategyAttribution - Performance Tracking for Multi-Strategy Trading

This module provides comprehensive performance attribution and reporting for
individual strategies within a multi-strategy trading system.

Key Features:
- Per-strategy P&L tracking
- Win rate, Sharpe ratio, max drawdown calculations
- Trade count and average P&L metrics
- Report generation (DataFrame output)
- Identify best/worst performers

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import logging
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd


class StrategyAttribution:
    """
    Tracks and calculates performance metrics for multiple strategies.

    This class maintains performance data for each strategy and provides
    methods to calculate metrics, generate reports, and identify top performers.

    Attributes:
        strategy_metrics: Dictionary mapping strategy_id to metrics
        logger: Logger instance for debugging

    Example:
        >>> attribution = StrategyAttribution()
        >>>
        >>> # Register strategies
        >>> attribution.register_strategy('strat_1', initial_capital=10000)
        >>> attribution.register_strategy('strat_2', initial_capital=10000)
        >>>
        >>> # Record trades
        >>> attribution.record_trade('strat_1', 4500.0, 4510.0, 1.0, datetime.now())
        >>> attribution.record_trade('strat_2', 4500.0, 4505.0, 1.0, datetime.now())
        >>>
        >>> # Generate report
        >>> report = attribution.generate_report()
        >>> print(report)
    """

    def __init__(self):
        """Initialize the StrategyAttribution tracker."""
        self.strategy_metrics: Dict[str, dict] = {}
        self.logger = logging.getLogger(__name__)

    def register_strategy(self, strategy_id: str, initial_capital: float = 0.0) -> None:
        """
        Register a new strategy for attribution tracking.

        Args:
            strategy_id: Unique identifier for the strategy
            initial_capital: Initial capital allocated to strategy

        Example:
            >>> attribution.register_strategy('strat_1', initial_capital=10000.0)
        """
        if strategy_id in self.strategy_metrics:
            self.logger.warning(f"Strategy {strategy_id} already registered, resetting metrics")

        self.strategy_metrics[strategy_id] = {
            "initial_capital": initial_capital,
            "trades": [],
            "pnl_history": [],
            "total_pnl": 0.0,
            "win_count": 0,
            "loss_count": 0,
            "registered_at": datetime.now(),
        }

        self.logger.info(f"Registered strategy {strategy_id} with capital ${initial_capital:.2f}")

    def record_trade(
        self, strategy_id: str, entry_price: float, exit_price: float, quantity: float, timestamp: datetime = None
    ) -> None:
        """
        Record a completed trade for attribution.

        Args:
            strategy_id: Strategy identifier
            entry_price: Entry price per contract
            exit_price: Exit price per contract
            quantity: Number of contracts (signed)
            timestamp: Trade timestamp (defaults to now)

        Example:
            >>> attribution.record_trade('strat_1', 4500.0, 4510.0, 1.0)
        """
        if strategy_id not in self.strategy_metrics:
            self.logger.warning(f"Strategy {strategy_id} not registered, auto-registering")
            self.register_strategy(strategy_id)

        if timestamp is None:
            timestamp = datetime.now()

        # Calculate P&L
        pnl = (exit_price - entry_price) * quantity

        # Create trade record
        trade = {
            "timestamp": timestamp,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl": pnl,
        }

        # Update metrics
        metrics = self.strategy_metrics[strategy_id]
        metrics["trades"].append(trade)
        metrics["pnl_history"].append(pnl)
        metrics["total_pnl"] += pnl

        if pnl > 0:
            metrics["win_count"] += 1
        elif pnl < 0:
            metrics["loss_count"] += 1

        self.logger.debug(
            f"Recorded trade for {strategy_id}: "
            f"entry=${entry_price:.2f}, exit=${exit_price:.2f}, "
            f"qty={quantity:.1f}, pnl=${pnl:+.2f}"
        )

    def calculate_metrics(self, strategy_id: str) -> dict:
        """
        Calculate comprehensive metrics for a strategy.

        Args:
            strategy_id: Strategy identifier

        Returns:
            Dictionary with metrics including:
                - total_pnl: Cumulative P&L
                - trade_count: Total number of trades
                - win_rate: Percentage of winning trades
                - avg_pnl: Average P&L per trade
                - sharpe_ratio: Risk-adjusted return measure
                - max_drawdown: Maximum peak-to-trough decline
                - win_avg: Average winning trade
                - loss_avg: Average losing trade
                - profit_factor: Ratio of gross profit to gross loss

        Example:
            >>> metrics = attribution.calculate_metrics('strat_1')
            >>> print(f"Win rate: {metrics['win_rate']:.1%}")
            >>> print(f"Sharpe ratio: {metrics['sharpe_ratio']:.2f}")
        """
        if strategy_id not in self.strategy_metrics:
            self.logger.error(f"Strategy {strategy_id} not found")
            return {}

        metrics = self.strategy_metrics[strategy_id]
        trades = metrics["trades"]
        pnl_history = metrics["pnl_history"]

        if len(trades) == 0:
            # No trades yet
            return {
                "strategy_id": strategy_id,
                "total_pnl": 0.0,
                "trade_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_avg": 0.0,
                "loss_avg": 0.0,
                "profit_factor": 0.0,
                "initial_capital": metrics["initial_capital"],
            }

        # Basic metrics
        total_pnl = metrics["total_pnl"]
        trade_count = len(trades)
        win_count = metrics["win_count"]
        loss_count = metrics["loss_count"]
        win_rate = win_count / trade_count if trade_count > 0 else 0.0
        avg_pnl = total_pnl / trade_count if trade_count > 0 else 0.0

        # Sharpe ratio calculation
        if len(pnl_history) > 1:
            pnl_std = np.std(pnl_history, ddof=1)
            sharpe_ratio = (np.mean(pnl_history) / pnl_std) if pnl_std > 0 else 0.0
        else:
            sharpe_ratio = 0.0

        # Max drawdown calculation
        cumulative_pnl = np.cumsum(pnl_history)
        running_max = np.maximum.accumulate(cumulative_pnl)
        drawdown = running_max - cumulative_pnl
        max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0.0

        # Win/loss averages
        winning_trades = [t["pnl"] for t in trades if t["pnl"] > 0]
        losing_trades = [t["pnl"] for t in trades if t["pnl"] < 0]

        win_avg = np.mean(winning_trades) if winning_trades else 0.0
        loss_avg = np.mean(losing_trades) if losing_trades else 0.0

        # Profit factor
        gross_profit = sum(winning_trades) if winning_trades else 0.0
        gross_loss = abs(sum(losing_trades)) if losing_trades else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

        return {
            "strategy_id": strategy_id,
            "total_pnl": total_pnl,
            "trade_count": trade_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "win_avg": win_avg,
            "loss_avg": loss_avg,
            "profit_factor": profit_factor,
            "initial_capital": metrics["initial_capital"],
        }

    def generate_report(self, sort_by: str = "total_pnl", ascending: bool = False) -> pd.DataFrame:
        """
        Generate a performance attribution report for all strategies.

        Args:
            sort_by: Column to sort by (default: 'total_pnl')
            ascending: Sort in ascending order (default: False for descending)

        Returns:
            DataFrame with metrics for all strategies

        Example:
            >>> report = attribution.generate_report(sort_by='sharpe_ratio')
            >>> print(report)
        """
        if not self.strategy_metrics:
            self.logger.warning("No strategies registered")
            return pd.DataFrame()

        # Calculate metrics for all strategies
        all_metrics = []
        for strategy_id in self.strategy_metrics.keys():
            metrics = self.calculate_metrics(strategy_id)
            all_metrics.append(metrics)

        # Create DataFrame
        df = pd.DataFrame(all_metrics)

        # Sort if requested
        if sort_by in df.columns:
            df = df.sort_values(by=sort_by, ascending=ascending)

        return df

    def get_total_pnl(self) -> float:
        """
        Calculate total P&L across all strategies.

        Returns:
            Total P&L for all strategies combined
        """
        total = sum(metrics["total_pnl"] for metrics in self.strategy_metrics.values())
        return total

    def get_best_strategy(self, metric: str = "total_pnl") -> Optional[str]:
        """
        Identify the best performing strategy by a specific metric.

        Args:
            metric: Metric to compare (default: 'total_pnl')

        Returns:
            Strategy ID of best performer, or None if no strategies
        """
        if not self.strategy_metrics:
            return None

        best_id = None
        best_value = float("-inf")

        for strategy_id in self.strategy_metrics.keys():
            metrics = self.calculate_metrics(strategy_id)
            value = metrics.get(metric, float("-inf"))

            if value > best_value:
                best_value = value
                best_id = strategy_id

        return best_id

    def get_worst_strategy(self, metric: str = "total_pnl") -> Optional[str]:
        """
        Identify the worst performing strategy by a specific metric.

        Args:
            metric: Metric to compare (default: 'total_pnl')

        Returns:
            Strategy ID of worst performer, or None if no strategies
        """
        if not self.strategy_metrics:
            return None

        worst_id = None
        worst_value = float("inf")

        for strategy_id in self.strategy_metrics.keys():
            metrics = self.calculate_metrics(strategy_id)
            value = metrics.get(metric, float("inf"))

            if value < worst_value:
                worst_value = value
                worst_id = strategy_id

        return worst_id

    def get_strategy_count(self) -> int:
        """
        Get the number of registered strategies.

        Returns:
            Count of strategies
        """
        return len(self.strategy_metrics)

    def reset_strategy(self, strategy_id: str) -> None:
        """
        Reset metrics for a specific strategy.

        Args:
            strategy_id: Strategy to reset
        """
        if strategy_id in self.strategy_metrics:
            initial_capital = self.strategy_metrics[strategy_id]["initial_capital"]
            self.register_strategy(strategy_id, initial_capital)
            self.logger.info(f"Reset metrics for strategy {strategy_id}")

    def reset_all(self) -> None:
        """Reset metrics for all strategies."""
        strategy_ids = list(self.strategy_metrics.keys())
        for strategy_id in strategy_ids:
            self.reset_strategy(strategy_id)

    def __repr__(self) -> str:
        """String representation of attribution tracker."""
        total_pnl = self.get_total_pnl()
        strategy_count = self.get_strategy_count()
        return f"StrategyAttribution(" f"strategies={strategy_count}, " f"total_pnl=${total_pnl:+.2f})"
