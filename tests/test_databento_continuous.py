"""Tests for Databento native continuous futures functionality."""

import os
from datetime import datetime

import pytest

from lumibot.entities import Asset
from lumibot.tools.databento_helper import (
    NATIVE_CONTINUOUS_CONFIG,
    ContinuousRollMethod,
    DatabentoNativeContinuousError,
    _build_cache_filename,
    build_native_continuous_symbol,
    get_continuous_config,
)


class TestContinuousRollMethodEnum:
    """Test the ContinuousRollMethod enum."""

    def test_calendar_value(self):
        """Test CALENDAR roll method has correct value."""
        assert ContinuousRollMethod.CALENDAR.value == "c"

    def test_volume_value(self):
        """Test VOLUME roll method has correct value."""
        assert ContinuousRollMethod.VOLUME.value == "v"

    def test_open_interest_value(self):
        """Test OPEN_INTEREST roll method has correct value."""
        assert ContinuousRollMethod.OPEN_INTEREST.value == "n"


class TestNativeContinuousConfig:
    """Test the NATIVE_CONTINUOUS_CONFIG dictionary."""

    def test_gc_uses_open_interest(self):
        """Test GC is configured for open interest."""
        assert NATIVE_CONTINUOUS_CONFIG.get("GC") == ContinuousRollMethod.OPEN_INTEREST

    def test_mgc_uses_open_interest(self):
        """Test MGC is configured for open interest."""
        assert NATIVE_CONTINUOUS_CONFIG.get("MGC") == ContinuousRollMethod.OPEN_INTEREST

    def test_es_uses_calendar(self):
        """Test ES is configured for calendar roll."""
        assert NATIVE_CONTINUOUS_CONFIG.get("ES") == ContinuousRollMethod.CALENDAR

    def test_mes_uses_calendar(self):
        """Test MES is configured for calendar roll."""
        assert NATIVE_CONTINUOUS_CONFIG.get("MES") == ContinuousRollMethod.CALENDAR


class TestGetContinuousConfig:
    """Test the get_continuous_config helper function."""

    def test_gc_returns_open_interest(self):
        """Test GC returns open interest config."""
        result = get_continuous_config("GC")
        assert result == ContinuousRollMethod.OPEN_INTEREST

    def test_case_insensitive(self):
        """Test lookup is case insensitive."""
        result_lower = get_continuous_config("gc")
        result_upper = get_continuous_config("GC")
        assert result_lower == result_upper == ContinuousRollMethod.OPEN_INTEREST

    def test_unknown_symbol_returns_none(self):
        """Test unknown symbol returns None (fallback to manual stitching)."""
        result = get_continuous_config("UNKNOWN_SYMBOL")
        assert result is None

    def test_cl_not_configured(self):
        """Test CL (crude oil) is not configured (uses manual stitching)."""
        result = get_continuous_config("CL")
        assert result is None


class TestBuildNativeContinuousSymbol:
    """Test the build_native_continuous_symbol helper function."""

    def test_gc_open_interest(self):
        """Test building GC.n.0 symbol."""
        result = build_native_continuous_symbol("GC", ContinuousRollMethod.OPEN_INTEREST)
        assert result == "GC.n.0"

    def test_es_calendar(self):
        """Test building ES.c.0 symbol."""
        result = build_native_continuous_symbol("ES", ContinuousRollMethod.CALENDAR)
        assert result == "ES.c.0"

    def test_lowercase_uppercased(self):
        """Test lowercase symbol is uppercased."""
        result = build_native_continuous_symbol("gc", ContinuousRollMethod.OPEN_INTEREST)
        assert result == "GC.n.0"

    def test_volume_roll(self):
        """Test volume roll method."""
        result = build_native_continuous_symbol("NQ", ContinuousRollMethod.VOLUME)
        assert result == "NQ.v.0"


class TestCacheFilenameWithRollTag:
    """Test cache filename generation with roll method tag."""

    def test_native_continuous_includes_roll_tag(self):
        """Test native continuous creates filename with roll tag."""
        asset = Asset("MGC", Asset.AssetType.CONT_FUTURE)
        start = datetime(2025, 10, 1, 0, 0)
        end = datetime(2025, 10, 5, 0, 0)

        cache_path = _build_cache_filename(asset, start, end, "1m", symbol_override="MGC", roll_method_tag="n")

        # Should include _roll-n_ in filename
        assert "_roll-n_" in cache_path.name
        assert cache_path.name.startswith("MGC_roll-n_")

    def test_manual_stitching_no_roll_tag(self):
        """Test manual stitching creates filename without roll tag."""
        asset = Asset("MGC", Asset.AssetType.CONT_FUTURE)
        start = datetime(2025, 10, 1, 0, 0)
        end = datetime(2025, 10, 5, 0, 0)

        cache_path = _build_cache_filename(asset, start, end, "1m", symbol_override="MGCZ5", roll_method_tag=None)

        # Should NOT include _roll- in filename
        assert "_roll-" not in cache_path.name
        assert cache_path.name.startswith("MGCZ5_")

    def test_different_roll_methods_different_filenames(self):
        """Test different roll methods create different cache files."""
        asset = Asset("GC", Asset.AssetType.CONT_FUTURE)
        start = datetime(2025, 10, 1, 0, 0)
        end = datetime(2025, 10, 5, 0, 0)

        cache_oi = _build_cache_filename(asset, start, end, "1m", symbol_override="GC", roll_method_tag="n")
        cache_cal = _build_cache_filename(asset, start, end, "1m", symbol_override="GC", roll_method_tag="c")

        assert cache_oi != cache_cal
        assert "_roll-n_" in cache_oi.name
        assert "_roll-c_" in cache_cal.name


class TestDatabentoNativeContinuousError:
    """Test the DatabentoNativeContinuousError exception."""

    def test_exception_inherits_from_runtime_error(self):
        """Test exception is a RuntimeError."""
        assert issubclass(DatabentoNativeContinuousError, RuntimeError)

    def test_exception_with_message(self):
        """Test exception can be raised with message."""
        with pytest.raises(DatabentoNativeContinuousError) as exc_info:
            raise DatabentoNativeContinuousError("HARD STOP: Test error")

        assert "HARD STOP" in str(exc_info.value)


class TestIntegrationWithDatabento:
    """Integration tests that require API key - skipped in CI."""

    @pytest.mark.skipif(not os.getenv("DATABENTO_API_KEY"), reason="No DATABENTO_API_KEY set")
    def test_native_continuous_gc_api_call(self):
        """Test actual API call with GC.n.0 symbol."""
        # This test actually calls Databento API
        # Only runs when DATABENTO_API_KEY is set
        from lumibot.tools.databento_helper import get_price_data_from_databento

        api_key = os.getenv("DATABENTO_API_KEY")
        asset = Asset("GC", Asset.AssetType.CONT_FUTURE)
        start = datetime(2025, 10, 1, 0, 0)
        end = datetime(2025, 10, 2, 0, 0)

        df = get_price_data_from_databento(
            api_key=api_key,
            asset=asset,
            start=start,
            end=end,
            timestep="minute",
            force_cache_update=True,
        )

        assert df is not None
        assert len(df) > 0
        assert "open" in df.columns
        assert "close" in df.columns

    @pytest.mark.skipif(not os.getenv("DATABENTO_API_KEY"), reason="No DATABENTO_API_KEY set")
    def test_gc_native_continuous_coverage_90_percent(self):
        """
        Test that GC continuous futures via open interest (GC.n.0) achieves 90%+ data coverage.

        This is a regression test for the bug where reference_date wasn't passed to
        get_price_data_from_databento(), causing fallback to broken manual stitching
        which yielded ~2.5% coverage instead of ~100%.
        """
        from lumibot.tools.databento_helper import get_price_data_from_databento

        api_key = os.getenv("DATABENTO_API_KEY")
        asset = Asset("GC", Asset.AssetType.CONT_FUTURE)

        # Use a 3-day window for a reasonable test duration
        start = datetime(2025, 12, 1, 0, 0)
        end = datetime(2025, 12, 4, 0, 0)
        reference_date = start  # Key: this enables native continuous path

        df = get_price_data_from_databento(
            api_key=api_key,
            asset=asset,
            start=start,
            end=end,
            timestep="minute",
            reference_date=reference_date,  # CRITICAL - enables GC.n.0 path
        )

        assert df is not None, "DataFrame should not be None"
        assert len(df) > 0, "DataFrame should have rows"

        # Calculate coverage
        # ETH for metals is ~23 hours/day (CME Globex), so ~1380 minutes/day
        # 3 days = ~4140 expected minutes (some variation for weekends/holidays)
        actual_minutes = len(df)

        # Sun 12/1 partial + Mon 12/2 + Tue 12/3 = roughly 2.5 full trading days
        # Conservative: expect at least 2 days worth = 2760 minutes
        min_expected_minutes = 2760
        coverage_pct = (actual_minutes / min_expected_minutes) * 100

        assert coverage_pct >= 90, (
            f"Coverage should be >= 90% but got {coverage_pct:.1f}% "
            f"({actual_minutes} rows, expected >= {min_expected_minutes}). "
            f"This may indicate native continuous (GC.n.0) is not being used."
        )

        # Verify it's using open interest roll method by checking for continuous data
        # If manual stitching was used (broken), we'd see huge gaps
        time_diffs = df.index.to_series().diff().dropna()
        max_gap_minutes = time_diffs.max().total_seconds() / 60

        # Max gap should be <= 120 minutes (maintenance window)
        # Manual stitching would show gaps of hours/days
        assert max_gap_minutes <= 180, (
            f"Max gap should be <= 180 minutes but got {max_gap_minutes:.0f} minutes. "
            f"Large gaps suggest native continuous (GC.n.0) is not being used."
        )
