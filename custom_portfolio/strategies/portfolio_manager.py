"""
Portfolio Manager - Automatic Strategy Discovery and Loading System

This module provides automatic discovery and loading of trading strategies from
a designated folder. It enables hot-reloading and dynamic portfolio management
without any manual configuration.

Key Features:
- Automatic discovery of .py files in active_strategies/ folder
- Dynamic module loading with importlib
- Validation before loading (checks required fields)
- No manual mapping - fully automatic
- Hot-reload capability for updating strategies
- Integration with MultiStrategyExecutor
- Comprehensive error handling and reporting

Usage:
    Drop strategy files in active_strategies/ folder and they will be
    automatically discovered and loaded. No code changes required!

Author: LumiBot Multi-Strategy Team
Date: 2025-11-18
"""

import importlib.util
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from custom_portfolio.multi_strategy_executor_enhanced import (
    MultiStrategyExecutorEnhanced,
)

# Import broker configuration
try:
    from lumibot.credentials import IS_BACKTESTING
except ImportError:
    IS_BACKTESTING = True  # Default to backtesting if import fails


# Session abbreviation mapping
SESSION_ABBREVIATIONS = {
    "Australia": "AUS",
    "Asia": "ASIA",
    "London": "LON",
    "New_York": "NY",
    "24/7": "247",
}


class StrategyLoader:
    """Handles loading and validation of individual strategy modules."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        """Initialize the StrategyLoader."""
        self.logger = logger or logging.getLogger(__name__)
        self.loaded_modules = {}
        self.validation_errors = []

    def load_strategy_file(self, file_path: Path) -> Tuple[Optional[Any], Optional[str]]:
        """
        Load a strategy module from a Python file.

        Args:
            file_path: Path to the strategy Python file

        Returns:
            Tuple of (module, error_message)
            - module: Loaded Python module or None if failed
            - error_message: Error message if loading failed
        """
        try:
            # Get module name from file
            module_name = file_path.stem

            # Create module spec
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None:
                return None, f"Could not create spec for {file_path}"

            # Load module
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            self.logger.info(f"Successfully loaded module: {module_name}")
            return module, None

        except Exception as e:
            error_msg = f"Failed to load {file_path.name}: {str(e)}"
            self.logger.error(error_msg)
            self.logger.debug(traceback.format_exc())
            return None, error_msg

    def validate_strategy_module(self, module: Any, file_name: str) -> Tuple[bool, str]:
        """
        Validate that a loaded module has all required components.

        Args:
            module: Loaded Python module
            file_name: Name of the file for error reporting

        Returns:
            Tuple of (is_valid, error_message)
        """
        errors = []

        # Check for STRATEGY_CONFIG
        if not hasattr(module, "STRATEGY_CONFIG"):
            errors.append("Missing STRATEGY_CONFIG dictionary")
        else:
            config = module.STRATEGY_CONFIG

            # Validate required fields in config
            required_fields = ["strategy_id", "symbol", "contracts", "params"]
            for field in required_fields:
                if field not in config:
                    errors.append(f"Missing required field in STRATEGY_CONFIG: {field}")

            # Validate field types and values
            if "strategy_id" in config and not config["strategy_id"]:
                errors.append("strategy_id cannot be empty")

            if "symbol" in config and not config["symbol"]:
                errors.append("symbol cannot be empty")

            if "contracts" in config and config["contracts"] < 1:
                errors.append("contracts must be >= 1")

            if "params" in config and not isinstance(config["params"], dict):
                errors.append("params must be a dictionary")

        # Check for generate_signal function
        if not hasattr(module, "generate_signal"):
            errors.append("Missing generate_signal() function")
        else:
            # Check function signature
            import inspect

            sig = inspect.signature(module.generate_signal)
            if len(sig.parameters) != 2:
                errors.append("generate_signal() must accept exactly 2 parameters")

        # Run custom validation if provided
        if hasattr(module, "validate_config"):
            try:
                is_valid, error_msg = module.validate_config()
                if not is_valid:
                    errors.append(f"Custom validation failed: {error_msg}")
            except Exception as e:
                errors.append(f"validate_config() raised exception: {str(e)}")

        if errors:
            error_message = f"{file_name}: " + "; ".join(errors)
            return False, error_message

        return True, ""

    def extract_strategy_config(self, module: Any) -> Dict[str, Any]:
        """
        Extract strategy configuration from a validated module.

        Args:
            module: Validated strategy module

        Returns:
            Strategy configuration dictionary
        """
        config = module.STRATEGY_CONFIG.copy()

        # Standardized internal key for signal generation
        config["_generate_signal_func"] = module.generate_signal

        # Add module reference for reloading
        config["_module"] = module

        # Add metadata if available
        if hasattr(module, "STRATEGY_METADATA"):
            config["metadata"] = module.STRATEGY_METADATA

        return config


class PortfolioManager:
    """
    Manages a portfolio of trading strategies with automatic discovery and loading.

    This class provides the main interface for managing multiple trading strategies
    dynamically. It discovers strategy files, loads them, validates them, and
    integrates with the MultiStrategyExecutor for execution.

    Attributes:
        strategies_folder: Path to folder containing strategy files
        broker: Broker instance for order execution
        data_source: Data source for market data
        calendar: Trading calendar for session management
        executor: MultiStrategyExecutor instance
        loader: StrategyLoader instance
        loaded_strategies: Dictionary of loaded strategy configurations
        logger: Logger instance
    """

    def __init__(
        self,
        strategies_folder: str,
        broker,
        calendar,
        data_source=None,
        auto_load: bool = True,
        cache_ttl_seconds: int = 60,
        min_order_delay_seconds: float = 2.0,
        default_atr_period: int = 20,
        enable_snapshots: bool = True,
        simulate_fills: bool = True,
        max_snapshots: int = 200000,
        timestep: str = "minute",
        shared_initial_capital: float = 150000.0,
        ignore_calendar: bool = False,
        broker_strategy_name: Optional[str] = None,
        deep_portfolio_debug: bool = False,
    ):
        """
        Initialize the PortfolioManager.

        Args:
            strategies_folder: Path to folder containing strategy files
            broker: Broker instance
            calendar: Trading calendar instance
            data_source: Data source instance (optional - will use broker.data_source if not provided)
            auto_load: Automatically load strategies on init (default: True)
            cache_ttl_seconds: Data cache TTL (default: 60)
            min_order_delay_seconds: Minimum delay between orders (default: 2.0)
            default_atr_period: Default ATR period (default: 20)
            ignore_calendar: If True, skip calendar/session gating (useful for backtests)
            broker_strategy_name: Optional wrapper strategy name used when submitting real orders
            deep_portfolio_debug: Emit verbose executor logs when True

        Note:
            Tick sizes are automatically looked up per symbol from futures_metadata
        """
        self.strategies_folder = Path(strategies_folder)
        self.broker = broker
        # Get data_source from broker if not explicitly provided
        self.data_source = data_source if data_source is not None else (broker.data_source if broker else None)
        self.calendar = calendar
        self.broker_strategy_name = broker_strategy_name
        self.deep_portfolio_debug = deep_portfolio_debug

        # Create strategies folder if it doesn't exist
        self.strategies_folder.mkdir(parents=True, exist_ok=True)

        # Logger - set to ERROR level to suppress INFO messages during validation
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.ERROR)

        # Initialize components
        self.loader = StrategyLoader(logger=self.logger)
        self.loaded_strategies = {}
        self.load_errors = []

        # Initialize executor (will be created after loading strategies)
        self.executor = None
        self.executor_params = {
            "cache_ttl_seconds": cache_ttl_seconds,
            "min_order_delay_seconds": min_order_delay_seconds,
            "default_atr_period": default_atr_period,
            "enable_snapshots": enable_snapshots,
            "simulate_fills": simulate_fills,
            "max_snapshots": max_snapshots,
            "timestep": timestep,
            "shared_initial_capital": shared_initial_capital,
            "ignore_calendar": ignore_calendar,
            "broker_strategy_name": broker_strategy_name,
            "deep_portfolio_debug": deep_portfolio_debug,
        }

        # Auto-load strategies if requested
        if auto_load:
            self.load_all_strategies()
            self._create_executor()

    def discover_strategy_files(self) -> List[Path]:
        """
        Discover all Python files in the strategies folder.

        Returns:
            List of Path objects for strategy files
        """
        # Find all .py files
        strategy_files = list(self.strategies_folder.glob("*.py"))

        # Filter out __pycache__ and other non-strategy files
        strategy_files = [f for f in strategy_files if not f.name.startswith("__") and not f.name.startswith(".")]

        self.logger.info(f"Discovered {len(strategy_files)} potential strategy files " f"in {self.strategies_folder}")

        return sorted(strategy_files)

    def load_all_strategies(self) -> Dict[str, Any]:
        """
        Load all strategies from the strategies folder.

        Returns:
            Dictionary of loaded strategies with statistics
        """
        self.logger.info("=" * 60)
        self.logger.info("LOADING PORTFOLIO STRATEGIES")
        self.logger.info("=" * 60)

        # Clear previous state
        self.loaded_strategies.clear()
        self.load_errors.clear()

        # Discover strategy files
        strategy_files = self.discover_strategy_files()

        if not strategy_files:
            self.logger.warning(f"No strategy files found in {self.strategies_folder}")
            return self._get_load_summary()

        # Load and validate each file
        for file_path in strategy_files:
            self._load_single_strategy(file_path)

        # Report results
        self.logger.info("=" * 60)
        self.logger.info(f"Successfully loaded {len(self.loaded_strategies)} strategies")
        if self.load_errors:
            self.logger.warning(f"Failed to load {len(self.load_errors)} strategies")
        self.logger.info("=" * 60)

        return self._get_load_summary()

    def _load_single_strategy(self, file_path: Path) -> bool:
        """
        Load a single strategy file.

        Args:
            file_path: Path to strategy file

        Returns:
            True if successfully loaded
        """
        file_name = file_path.name
        self.logger.info(f"Loading {file_name}...")

        # Load the module
        module, load_error = self.loader.load_strategy_file(file_path)
        if module is None:
            self.load_errors.append({"file": file_name, "error": load_error})
            return False

        # Validate the module
        is_valid, validation_error = self.loader.validate_strategy_module(module, file_name)
        if not is_valid:
            self.load_errors.append({"file": file_name, "error": validation_error})
            return False

        # Extract configuration
        config = self.loader.extract_strategy_config(module)

        # Check for duplicate strategy IDs
        strategy_id = config["strategy_id"]
        if strategy_id in self.loaded_strategies:
            error = f"Duplicate strategy_id: {strategy_id}"
            self.load_errors.append({"file": file_name, "error": error})
            self.logger.error(f"{file_name}: {error}")
            return False

        # Store loaded strategy
        config["file_name"] = file_name
        config["file_path"] = str(file_path)
        self.loaded_strategies[strategy_id] = config

        self.logger.info(f"  ✓ Loaded {strategy_id} ({config['symbol']}, " f"{config['contracts']} contracts)")

        return True

    def reload_strategy(self, strategy_id: str) -> bool:
        """
        Reload a specific strategy from file.

        Args:
            strategy_id: ID of strategy to reload

        Returns:
            True if successfully reloaded
        """
        if strategy_id not in self.loaded_strategies:
            self.logger.error(f"Strategy {strategy_id} not found")
            return False

        # Get file path
        file_path = Path(self.loaded_strategies[strategy_id]["file_path"])

        if not file_path.exists():
            self.logger.error(f"Strategy file {file_path} no longer exists")
            return False

        # Reload the strategy
        self.logger.info(f"Reloading strategy {strategy_id}...")

        # Remove old version
        del self.loaded_strategies[strategy_id]

        # Load new version
        success = self._load_single_strategy(file_path)

        if success:
            # Recreate executor with updated strategies
            self._create_executor()

        return success

    def reload_all_strategies(self) -> Dict[str, Any]:
        """
        Reload all strategies (useful for hot-reloading).

        Returns:
            Dictionary of loaded strategies with statistics
        """
        self.logger.info("Reloading all strategies...")
        result = self.load_all_strategies()
        self._create_executor()
        return result

    def _create_executor(self) -> None:
        """Create or recreate the MultiStrategyExecutor with loaded strategies."""
        if not self.loaded_strategies:
            self.logger.warning("No strategies loaded, executor not created")
            self.executor = None
            return

        # Convert loaded strategies to executor format
        strategy_configs = []
        for strategy_id, config in self.loaded_strategies.items():
            sig_func = config.get("_generate_signal_func") or config.get("generate_signal_func")
            executor_config = {
                "strategy_id": strategy_id,
                "strategy_type": config.get("strategy_type", "DynamicStrategy"),
                "symbol": config["symbol"],
                "contracts": config["contracts"],
                "params": config["params"],
                "allowed_sessions": config.get("allowed_sessions", ["New_York"]),
                "initial_capital": config.get("initial_capital", 0.0),
                # Store generate_signal function reference
                "_generate_signal_func": sig_func,
                # Store bracket and exit configs
                "_bracket_config": config.get("bracket_config", {}),
                "_exit_config": config.get("exit_config", {}),
            }

            # Merge bracket config into params for executor
            if "_bracket_config" in executor_config:
                executor_config["params"].update(executor_config["_bracket_config"])

            # Merge exit config into params
            if "_exit_config" in executor_config:
                max_bars = executor_config["_exit_config"].get("max_bars_in_trade")
                if max_bars is not None:
                    executor_config["params"]["max_bars"] = max_bars

            strategy_configs.append(executor_config)

        # Create executor
        self.executor = MultiStrategyExecutorEnhanced(
            broker=self.broker,
            data_source=self.data_source,
            calendar=self.calendar,
            strategy_configs=strategy_configs,
            **self.executor_params,
        )

        # Override the executor's signal generation with our dynamic functions
        self._inject_signal_functions()

        self.logger.info(f"Created executor with {len(strategy_configs)} strategies")

    def _inject_signal_functions(self) -> None:
        """Inject the loaded signal generation functions into executor strategies."""
        if self.executor is None:
            return

        for strategy_state in self.executor.strategies:
            # Find the config for this strategy
            config = None
            for loaded_config in self.loaded_strategies.values():
                if loaded_config["strategy_id"] == strategy_state.strategy_id:
                    config = loaded_config
                    break

            if config:
                sig_func = config.get("_generate_signal_func") or config.get("generate_signal_func")
                if sig_func:
                    if "_generate_signal_func" not in config:
                        self.logger.debug(
                            f"Using legacy generate_signal_func for {strategy_state.strategy_id}; "
                            f"consider updating to _generate_signal_func."
                        )
                    strategy_state.generate_signal_func = sig_func

    def run_iteration(self, current_time: datetime = None) -> Dict[str, Any]:
        """
        Run one trading iteration for all strategies.

        Args:
            current_time: Current datetime (defaults to now)

        Returns:
            Dictionary with iteration summary
        """
        if self.executor is None:
            self.logger.warning("No executor available, loading strategies first...")
            self.load_all_strategies()
            self._create_executor()

            if self.executor is None:
                return {"error": "No strategies loaded"}

        # Override executor's signal generation to use our loaded functions
        original_generate = self.executor._generate_signal_for_strategy

        def dynamic_generate(strategy_state, market_data):
            """Use the dynamically loaded signal generation function."""
            if hasattr(strategy_state, "generate_signal_func"):
                return strategy_state.generate_signal_func(strategy_state, market_data)

            # Warn once per strategy about missing signal function
            if not getattr(strategy_state, "_missing_signal_warned", False):
                self.logger.warning(
                    f"No generate_signal_func found for {strategy_state.strategy_id}; returning HOLD"
                )
                strategy_state._missing_signal_warned = True
            return "HOLD"

        # Temporarily replace the method
        self.executor._generate_signal_for_strategy = dynamic_generate

        try:
            # Run the iteration
            result = self.executor.on_trading_iteration(current_time)
        finally:
            # Restore original method
            self.executor._generate_signal_for_strategy = original_generate

        return result

    def get_validation_report(self) -> str:
        """
        Generate a scientifically formatted validation report for all strategies.

        Returns:
            Terminal-formatted validation report string
        """
        from custom_portfolio.tools.terminal_formatter import Symbol
        from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

        lines = []

        # Header (Level 1)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(TF.horizontal_rule())
        lines.append("")
        lines.append(TF.section_header("Portfolio Validation Report"))
        lines.append(TF.metadata(timestamp))
        lines.append(TF.metadata(f"Location: {self.strategies_folder}"))
        lines.append("")

        # Broker Configuration (Subsection)
        lines.append(TF.subsection_header("Broker Configuration"))
        lines.extend(self._get_broker_config_lines())
        lines.append("")

        # Successfully loaded strategies
        if self.loaded_strategies:
            # Sort strategies by symbol
            sorted_strategies = sorted(
                self.loaded_strategies.items(), key=lambda x: (x[1]["symbol"], x[1].get("strategy_id", x[0]))
            )

            lines.append(TF.subsection_header(f"Loaded Strategies ({len(self.loaded_strategies)} total)"))
            lines.append("")

            for strategy_id, config in sorted_strategies:
                # Symbol and filename line
                symbol = TF.label(config["symbol"])
                line = f"{TF.INDENT}{Symbol.BULLET.value} {symbol} — {config['file_name']} ({strategy_id})"
                lines.append(line)

                # Description (indented, wrapped at 80 chars)
                if "metadata" in config and "description" in config["metadata"]:
                    desc = config["metadata"]["description"]
                    wrapped = TF.wrap_text(desc, max_width=TF.MAX_LINE_LENGTH - 4, indent=2, hanging=0)
                    lines.append(wrapped)

            lines.append("")

        # Failed strategies
        if self.load_errors:
            lines.append(TF.error(f"Failed to load: {len(self.load_errors)} strategies", symbol=False))
            lines.append("")

            for error in self.load_errors:
                lines.append(f"{TF.INDENT}{Symbol.BULLET.value} {error['file']}")
                lines.append(TF.metadata(f"{TF.INDENT * 2}Error: {error['error']}"))

            lines.append("")

        # Summary statistics
        total_files = len(self.discover_strategy_files())
        loaded_count = len(self.loaded_strategies)
        failed_count = len(self.load_errors)

        lines.append(TF.subsection_header("Summary"))
        lines.append(TF.key_value("Total files", str(total_files)))
        lines.append(TF.key_value("Loaded", str(loaded_count)))
        lines.append(TF.key_value("Failed", str(failed_count)))

        if self.loaded_strategies:
            # Group by symbol
            symbols = {}
            sessions = set()
            for config in self.loaded_strategies.values():
                symbol = config["symbol"]
                symbols[symbol] = symbols.get(symbol, 0) + 1

                # Collect unique sessions
                allowed_sessions = config.get("allowed_sessions", [])
                if allowed_sessions:
                    for session in allowed_sessions:
                        abbrev = SESSION_ABBREVIATIONS.get(session, session)
                        sessions.add(abbrev)

            symbols_list = ", ".join(sorted(symbols.keys()))
            lines.append(TF.key_value("Symbols", symbols_list))

            if sessions:
                sessions_list = ", ".join(sorted(sessions))
                lines.append(TF.key_value("Sessions", sessions_list))

        lines.append("")

        # Final status
        if failed_count == 0 and loaded_count > 0:
            lines.append(TF.success("All strategies validated successfully"))
        elif failed_count > 0:
            lines.append(TF.warning("Some strategies failed validation"))

        lines.append("")
        lines.append(TF.horizontal_rule())

        return "\n".join(lines)

    def _get_broker_config_lines(self) -> List[str]:
        """
        Get broker configuration lines.

        Returns:
            List of formatted configuration lines
        """
        from custom_portfolio.tools.terminal_formatter import TerminalFormatter as TF

        lines = []

        # Try to get broker info from credentials
        try:
            from lumibot.credentials import get_broker_config, get_data_source_config

            broker_cfg = get_broker_config()
            data_cfg = get_data_source_config()

            if broker_cfg:
                broker_name = broker_cfg.__class__.__name__ if hasattr(broker_cfg, "__class__") else "Unknown"
                lines.append(TF.key_value("Broker", broker_name))
            else:
                lines.append(TF.key_value("Broker", "Not configured"))

            # Backtesting mode
            mode_text = "Backtesting" if IS_BACKTESTING else "Live Trading"
            if IS_BACKTESTING:
                lines.append(TF.key_value("Mode", mode_text))
            else:
                lines.append(TF.key_value("Mode", TF.colorize(mode_text, TF.Color.WARNING)))

            if data_cfg:
                data_name = data_cfg.__class__.__name__ if hasattr(data_cfg, "__class__") else "Unknown"
                lines.append(TF.key_value("Data source", data_name))
            else:
                lines.append(TF.key_value("Data source", "Not configured"))

        except Exception:
            # Fallback if credentials module doesn't have these functions
            lines.append(TF.key_value("Broker", "See .env configuration"))

            mode_text = "Backtesting" if IS_BACKTESTING else "Live Trading"
            if IS_BACKTESTING:
                lines.append(TF.key_value("Mode", mode_text))
            else:
                lines.append(TF.key_value("Mode", TF.colorize(mode_text, TF.Color.WARNING)))

            lines.append(TF.key_value("Data source", "See .env configuration"))

        return lines

    def get_loaded_strategies_summary(self) -> pd.DataFrame:
        """
        Get a summary DataFrame of all loaded strategies.

        Returns:
            DataFrame with strategy information sorted by symbol, then strategy_id
        """
        if not self.loaded_strategies:
            return pd.DataFrame()

        data = []
        for strategy_id, config in self.loaded_strategies.items():
            # Format sessions
            allowed_sessions = config.get("allowed_sessions", [])
            if allowed_sessions:
                # Convert to abbreviations
                session_abbrevs = [SESSION_ABBREVIATIONS.get(s, s) for s in allowed_sessions]
                sessions_str = ", ".join(session_abbrevs)
            else:
                sessions_str = "247"  # Default to 24/7 if not specified

            row = {
                "symbol": config["symbol"],
                "strategy_id": strategy_id,
                "sessions": sessions_str,
                "qty": config["contracts"],
                "params": len(config["params"]),
                "file_name": config["file_name"],
            }

            # Add metadata if available (excluding author and risk_level)
            if "metadata" in config:
                meta = config["metadata"]
                row["type"] = meta.get("strategy_type", "unknown")

            # Add bracket config info
            bracket = config.get("bracket_config", {})
            if bracket.get("use_bracket_orders"):
                row["tp_mult"] = bracket.get("profit_target_mult", 0)
                row["sl_mult"] = bracket.get("stop_loss_mult", 0)

            # Add exit config info (fallback to params if exit config missing)
            exit_cfg = config.get("exit_config", {})
            row["max_bars"] = exit_cfg.get("max_bars_in_trade") or config["params"].get("max_bars")

            data.append(row)

        df = pd.DataFrame(data)
        # Sort by symbol, then by strategy_id
        df = df.sort_values(["symbol", "strategy_id"])
        # Reset index and don't include it in the output
        df = df.reset_index(drop=True)
        return df

    def get_performance_report(self) -> Any:
        """Get performance report from executor."""
        if self.executor:
            return self.executor.get_performance_report()
        return None

    def export_snapshots(self, folder: Path) -> Optional[Path]:
        """
        Export per-bar snapshots to CSV if enabled and available.

        Args:
            folder: Destination folder

        Returns:
            Path to CSV if written, else None
        """
        if self.executor is None or not getattr(self.executor, "enable_snapshots", False):
            return None
        df = self.executor.attribution.get_snapshots_df()
        if df.empty:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        out_path = folder / "snapshots.csv"
        # Write in chunks to reduce peak memory usage on very large snapshots
        chunk_size = 50000
        if len(df) <= chunk_size:
            df.to_csv(out_path, index=False)
        else:
            with out_path.open("w") as f:
                start = 0
                end = chunk_size
                df.iloc[start:end].to_csv(f, index=False, header=True)
                while end < len(df):
                    start = end
                    end = min(len(df), start + chunk_size)
                    df.iloc[start:end].to_csv(f, index=False, header=False)
        return out_path

    def _get_load_summary(self) -> Dict[str, Any]:
        """Get summary of loading results."""
        return {
            "loaded_count": len(self.loaded_strategies),
            "error_count": len(self.load_errors),
            "loaded_strategies": list(self.loaded_strategies.keys()),
            "errors": self.load_errors,
        }

    def archive_strategies(self, archive_folder: str) -> int:
        """
        Archive current strategies to a specified folder.

        Useful for weekly rotation - move current strategies to archive
        before loading new ones.

        Args:
            archive_folder: Path to archive folder

        Returns:
            Number of files archived
        """
        archive_path = Path(archive_folder)
        archive_path.mkdir(parents=True, exist_ok=True)

        count = 0
        strategy_files = self.discover_strategy_files()

        for file_path in strategy_files:
            # Move file to archive
            archive_file = archive_path / file_path.name
            file_path.rename(archive_file)
            count += 1
            self.logger.info(f"Archived {file_path.name} to {archive_folder}")

        return count

    def __repr__(self) -> str:
        """String representation of PortfolioManager."""
        return f"PortfolioManager(" f"strategies={len(self.loaded_strategies)}, " f"folder='{self.strategies_folder}')"
