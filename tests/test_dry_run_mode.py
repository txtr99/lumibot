"""
Tests for dry-run mode functionality.

Verifies that:
- _live_dry_run_config() correctly parses DRY_RUN env var
- Default for live mode is real trading (False)
- Various truthy/falsy values work correctly
"""

from unittest.mock import patch


class TestLiveDryRunConfig:
    """Test the live dry-run config function."""

    def test_default_is_real_trading(self):
        """By default (no env var), live mode should be real trading."""
        from custom_portfolio.strategies.run_portfolio import _live_dry_run_config

        with patch.dict("os.environ", {}, clear=True):
            # Remove DRY_RUN if it exists
            import os

            os.environ.pop("DRY_RUN", None)
            result = _live_dry_run_config()
            assert result is False, "Default should be real trading (False)"

    def test_explicit_true_enables_dry_run(self):
        """DRY_RUN=true should enable dry-run mode."""
        from custom_portfolio.strategies.run_portfolio import _live_dry_run_config

        with patch.dict("os.environ", {"DRY_RUN": "true"}):
            result = _live_dry_run_config()
            assert result is True

    def test_explicit_false_disables_dry_run(self):
        """DRY_RUN=false should disable dry-run mode."""
        from custom_portfolio.strategies.run_portfolio import _live_dry_run_config

        with patch.dict("os.environ", {"DRY_RUN": "false"}):
            result = _live_dry_run_config()
            assert result is False

    def test_various_truthy_values(self):
        """Various truthy values should enable dry-run."""
        from custom_portfolio.strategies.run_portfolio import _live_dry_run_config

        truthy_values = ["true", "True", "TRUE", "1", "yes", "Yes", "y", "Y", "on", "ON"]
        for val in truthy_values:
            with patch.dict("os.environ", {"DRY_RUN": val}):
                result = _live_dry_run_config()
                assert result is True, f"'{val}' should enable dry-run"

    def test_various_falsy_values(self):
        """Various falsy values should disable dry-run."""
        from custom_portfolio.strategies.run_portfolio import _live_dry_run_config

        falsy_values = ["false", "False", "0", "no", "No", "off", "anything", ""]
        for val in falsy_values:
            with patch.dict("os.environ", {"DRY_RUN": val}):
                result = _live_dry_run_config()
                assert result is False, f"'{val}' should disable dry-run"


class TestSimulateFillsConfig:
    """Test the simulate_fills config function (for backtest)."""

    def test_default_is_simulate(self):
        """By default, backtest should simulate fills."""
        from custom_portfolio.strategies.run_portfolio import _simulate_fills_config

        with patch.dict("os.environ", {}, clear=True):
            import os

            os.environ.pop("DRY_RUN", None)
            # Default for backtest is True (simulate)
            result = _simulate_fills_config()
            assert result is True, "Default for backtest should be simulate (True)"

    def test_explicit_false_disables_simulate(self):
        """DRY_RUN=false should disable simulation in backtest too."""
        from custom_portfolio.strategies.run_portfolio import _simulate_fills_config

        with patch.dict("os.environ", {"DRY_RUN": "false"}):
            result = _simulate_fills_config()
            assert result is False


class TestExecutorSimulateFillsFlag:
    """Test that executor respects simulate_fills flag."""

    def test_executor_stores_simulate_fills_flag(self):
        """Executor should store the simulate_fills flag."""
        from unittest.mock import MagicMock

        from custom_portfolio.multi_strategy_executor_enhanced import (
            MultiStrategyExecutorEnhanced,
        )

        mock_broker = MagicMock()
        mock_broker.data_source = MagicMock()
        mock_calendar = MagicMock()

        strategy_config = {
            "strategy_id": "test_strategy",
            "symbol": "ES",
            "contracts": 1,
            "params": {},
            "allowed_sessions": ["New_York"],
        }

        # Test with simulate_fills=True
        executor_dry = MultiStrategyExecutorEnhanced(
            broker=mock_broker,
            data_source=mock_broker.data_source,
            calendar=mock_calendar,
            strategy_configs=[strategy_config],
            simulate_fills=True,
            ignore_calendar=True,
        )
        assert executor_dry.simulate_fills is True

        # Test with simulate_fills=False
        executor_live = MultiStrategyExecutorEnhanced(
            broker=mock_broker,
            data_source=mock_broker.data_source,
            calendar=mock_calendar,
            strategy_configs=[strategy_config],
            simulate_fills=False,
            ignore_calendar=True,
        )
        assert executor_live.simulate_fills is False
