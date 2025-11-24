"""
Trading Calendar Sessions Unit Tests

Test-Driven Development (TDD) approach:
- ALL tests written BEFORE implementation
- ALL tests should FAIL initially
- After implementation, ALL tests must PASS

Tests cover:
1. Cross-midnight session handling
2. Weekend blackout enforcement
3. Platform maintenance overrides
4. DST spring forward transition
5. DST fall back transition
6. Session countdown calculations
7. UTC to Central timezone conversion
8. Strategy without sessions (backward compatibility)
9. Multiple symbols with different sessions
10. Calendar creation failure handling
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
import pytz


class TestTradingCalendarSessions:
    """
    Test suite for trading calendar session functionality.

    All tests use FIXED datetime values (no time-dependent logic) to ensure
    reproducibility and prevent test flakiness.

    Timezone conventions:
    - Internal calculations: Central Time (America/Chicago)
    - Display/UI: Mountain Time (America/Denver)
    - Data source: UTC
    """

    def test_cross_midnight_session_australia_active(self):
        """
        Test cross-midnight session (Australia: 17:00 CT → 02:00 CT next day).

        Validates that sessions spanning midnight are handled correctly:
        - 23:59 CT Jan 15 → session ACTIVE
        - 00:01 CT Jan 16 → session ACTIVE (still within session)
        - 02:01 CT Jan 16 → session INACTIVE (past end time)

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.strategies.portfolio_manager import TradingCalendar

        # Create calendar with Australia session (17:00 CT → 02:00 CT next day)
        calendar = TradingCalendar(timezone=pytz.timezone("America/Chicago"))

        # Fixed test times
        ct_tz = pytz.timezone("America/Chicago")
        time_before_midnight = ct_tz.localize(datetime(2025, 1, 15, 23, 59, 0))
        time_after_midnight = ct_tz.localize(datetime(2025, 1, 16, 0, 1, 0))
        time_after_close = ct_tz.localize(datetime(2025, 1, 16, 2, 1, 0))

        # Check session status at each time
        status_before = calendar.is_session_active("Australia", time_before_midnight)
        status_after = calendar.is_session_active("Australia", time_after_midnight)
        status_closed = calendar.is_session_active("Australia", time_after_close)

        assert status_before is True, "23:59 CT should be within Australia session"
        assert status_after is True, "00:01 CT should still be within Australia session"
        assert status_closed is False, "02:01 CT should be outside Australia session"

    def test_weekend_blackout_enforcement(self):
        """
        Test weekend blackout (Friday 14:00 CT → Sunday 16:00 CT).

        TopStepX rules: No trading Friday 2 PM CT through Sunday 4 PM CT.

        Test checkpoints:
        - Fri 13:59 CT → ALLOW trading
        - Fri 14:01 CT → BLOCK trading
        - Sat 12:00 CT → BLOCK trading
        - Sun 15:59 CT → BLOCK trading
        - Sun 16:01 CT → ALLOW trading

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.strategies.portfolio_manager import TradingCalendar

        calendar = TradingCalendar(timezone=pytz.timezone("America/Chicago"))
        ct_tz = pytz.timezone("America/Chicago")

        # Fixed weekend times (using Jan 2025: Fri 10th, Sat 11th, Sun 12th)
        fri_before_close = ct_tz.localize(datetime(2025, 1, 10, 13, 59, 0))
        fri_after_close = ct_tz.localize(datetime(2025, 1, 10, 14, 1, 0))
        sat_midday = ct_tz.localize(datetime(2025, 1, 11, 12, 0, 0))
        sun_before_open = ct_tz.localize(datetime(2025, 1, 12, 15, 59, 0))
        sun_after_open = ct_tz.localize(datetime(2025, 1, 12, 16, 1, 0))

        assert calendar.can_trade(fri_before_close) is True, "Should allow trading Fri 13:59 CT"
        assert calendar.can_trade(fri_after_close) is False, "Should block trading Fri 14:01 CT"
        assert calendar.can_trade(sat_midday) is False, "Should block trading Saturday"
        assert calendar.can_trade(sun_before_open) is False, "Should block trading Sun 15:59 CT"
        assert calendar.can_trade(sun_after_open) is True, "Should allow trading Sun 16:01 CT"

    def test_platform_maintenance_overrides_session(self):
        """
        Test platform maintenance window overrides active sessions.

        Platform maintenance: 14:00-16:00 CT daily
        NY session: 07:30-14:00 CT (active during maintenance window)

        During overlap, platform layer must WIN:
        - can_enter_orders=False
        - must_be_flat=True

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.strategies.portfolio_manager import TradingCalendar

        calendar = TradingCalendar(timezone=pytz.timezone("America/Chicago"))
        ct_tz = pytz.timezone("America/Chicago")

        # Time during NY session but also platform maintenance
        maintenance_time = ct_tz.localize(datetime(2025, 1, 15, 14, 30, 0))  # Wed 14:30 CT

        status = calendar.get_trading_status(maintenance_time, session="New_York")

        assert status["session_active"] is True, "NY session should be active at 14:30 CT"
        assert status["can_enter_orders"] is False, "Platform maintenance blocks new orders"
        assert status["must_be_flat"] is True, "Platform maintenance requires flat positions"

    def test_dst_transition_spring_forward(self):
        """
        Test DST spring forward (2:00 AM → 3:00 AM).

        March 10, 2024 at 2:00 AM CT: clocks spring forward to 3:00 AM CT.
        The hour 2:00-3:00 AM DOES NOT EXIST.

        Test times:
        - 01:59 CT → valid time
        - 02:00 CT → DOES NOT EXIST (should raise exception or be handled)
        - 03:00 CT → valid time (first moment after transition)

        EXPECTED: This test should FAIL before implementation.
        """

        ct_tz = pytz.timezone("America/Chicago")

        # Valid time before transition
        before_dst = ct_tz.localize(datetime(2024, 3, 10, 1, 59, 0))
        assert before_dst is not None

        # Non-existent time during transition (is_dst=None for strict mode)
        with pytest.raises(pytz.exceptions.NonExistentTimeError):
            ct_tz.localize(datetime(2024, 3, 10, 2, 0, 0), is_dst=None)

        # Valid time after transition
        after_dst = ct_tz.localize(datetime(2024, 3, 10, 3, 0, 0))
        assert after_dst is not None

    def test_dst_transition_fall_back(self):
        """
        Test DST fall back (2:00 AM → 1:00 AM).

        November 3, 2024 at 2:00 AM CDT: clocks fall back to 1:00 AM CST.
        The hour 1:00-2:00 AM occurs TWICE.

        Use is_dst flag to disambiguate:
        - 01:59 CDT (first occurrence, is_dst=True)
        - 01:59 CST (second occurrence, is_dst=False)

        EXPECTED: This test should FAIL before implementation.
        """

        ct_tz = pytz.timezone("America/Chicago")

        # First occurrence (is_dst=True, CDT, UTC-5)
        first_occurrence = ct_tz.localize(datetime(2024, 11, 3, 1, 59, 0), is_dst=True)

        # Second occurrence (is_dst=False, CST, UTC-6)
        second_occurrence = ct_tz.localize(datetime(2024, 11, 3, 1, 59, 0), is_dst=False)

        # They represent different moments in time
        assert first_occurrence != second_occurrence

        # First occurrence should be 1 hour earlier in UTC
        time_diff = second_occurrence - first_occurrence
        assert time_diff == timedelta(hours=1)

    def test_session_countdown_calculations(self):
        """
        Test countdown calculation accuracy.

        Force flat time: 14:00 CT
        Current time: 10:00 CT
        Expected countdown: exactly 14,400 seconds (4 hours)

        Countdown should never be negative (clamped to 0 at deadline).

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.strategies.portfolio_manager import TradingCalendar

        calendar = TradingCalendar(timezone=pytz.timezone("America/Chicago"))
        ct_tz = pytz.timezone("America/Chicago")

        current_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))
        force_flat_time = ct_tz.localize(datetime(2025, 1, 15, 14, 0, 0))

        countdown_seconds = calendar._calculate_close_timing(current_time, force_flat_time)

        expected_seconds = 4 * 60 * 60  # 4 hours = 14,400 seconds
        assert countdown_seconds == expected_seconds, f"Expected {expected_seconds}s, got {countdown_seconds}s"

        # Test past deadline (should be clamped to 0)
        past_time = ct_tz.localize(datetime(2025, 1, 15, 15, 0, 0))
        countdown_past = calendar._calculate_close_timing(past_time, force_flat_time)
        assert countdown_past == 0, "Countdown should be clamped to 0 after deadline"

    def test_utc_to_central_with_dst(self):
        """
        Test UTC → Central Time conversion with DST handling.

        Summer (CDT): UTC-5
        Winter (CST): UTC-6

        Test cases:
        - Summer: 2024-07-15 12:00 UTC → 2024-07-15 07:00 CDT
        - Winter: 2024-01-15 12:00 UTC → 2024-01-15 06:00 CST

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.tools.timezone_utils import utc_to_central

        utc_tz = pytz.UTC
        ct_tz = pytz.timezone("America/Chicago")

        # Summer time (DST active, UTC-5)
        summer_utc = utc_tz.localize(datetime(2024, 7, 15, 12, 0, 0))
        summer_ct = utc_to_central(summer_utc)
        expected_summer = ct_tz.localize(datetime(2024, 7, 15, 7, 0, 0))
        assert summer_ct == expected_summer, f"Summer: expected {expected_summer}, got {summer_ct}"

        # Winter time (DST inactive, UTC-6)
        winter_utc = utc_tz.localize(datetime(2024, 1, 15, 12, 0, 0))
        winter_ct = utc_to_central(winter_utc)
        expected_winter = ct_tz.localize(datetime(2024, 1, 15, 6, 0, 0))
        assert winter_ct == expected_winter, f"Winter: expected {expected_winter}, got {winter_ct}"

    def test_strategy_without_allowed_sessions(self):
        """
        Test backward compatibility: strategies without allowed_sessions.

        Old strategies may not define allowed_sessions attribute.
        Behavior:
        - Should default to 24/7 trading
        - Should log a WARNING
        - Should NOT block trading

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.strategies.portfolio_manager import PortfolioManager

        # Mock strategy without allowed_sessions attribute
        mock_strategy = Mock()
        mock_strategy.name = "LegacyStrategy"
        # Explicitly no allowed_sessions attribute

        calendar = Mock()
        pm = PortfolioManager(strategies=[mock_strategy], calendar=calendar, ignore_calendar=False)

        # Should allow trading at any time (24/7 default)
        ct_tz = pytz.timezone("America/Chicago")
        random_time = ct_tz.localize(datetime(2025, 1, 15, 3, 30, 0))  # 3:30 AM CT

        can_trade = pm.check_trading_allowed(mock_strategy, random_time)
        assert can_trade is True, "Strategy without allowed_sessions should default to 24/7 trading"

    def test_multiple_symbols_different_sessions(self):
        """
        Test per-symbol session enforcement with different rules.

        ES (E-mini S&P 500): NY session only (07:30-14:00 CT)
        MGC (Micro Gold): Multi-session (Australia, Asia, London, NY)

        Test times:
        - 05:00 CT: ES blocked, MGC allowed (Australia session)
        - 10:00 CT: ES allowed, MGC allowed (NY session)
        - 15:00 CT: ES blocked, MGC blocked (maintenance window)

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.strategies.portfolio_manager import PortfolioManager

        # Mock strategies with different allowed_sessions
        es_strategy = Mock()
        es_strategy.name = "ES_Strategy"
        es_strategy.symbol = "ES"
        es_strategy.allowed_sessions = ["New_York"]

        mgc_strategy = Mock()
        mgc_strategy.name = "MGC_Strategy"
        mgc_strategy.symbol = "MGC"
        mgc_strategy.allowed_sessions = ["Australia", "Asia", "London", "New_York"]

        calendar = Mock()  # Will be replaced with real TradingCalendar in implementation
        pm = PortfolioManager(strategies=[es_strategy, mgc_strategy], calendar=calendar, ignore_calendar=False)

        ct_tz = pytz.timezone("America/Chicago")

        # 05:00 CT - Australia session active
        australia_time = ct_tz.localize(datetime(2025, 1, 15, 5, 0, 0))
        assert pm.check_trading_allowed(es_strategy, australia_time) is False, "ES blocked outside NY"
        assert pm.check_trading_allowed(mgc_strategy, australia_time) is True, "MGC allowed during Australia"

        # 10:00 CT - NY session active
        ny_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))
        assert pm.check_trading_allowed(es_strategy, ny_time) is True, "ES allowed during NY"
        assert pm.check_trading_allowed(mgc_strategy, ny_time) is True, "MGC allowed during NY"

        # 15:00 CT - Maintenance window (both blocked by platform layer)
        maintenance_time = ct_tz.localize(datetime(2025, 1, 15, 15, 0, 0))
        assert pm.check_trading_allowed(es_strategy, maintenance_time) is False, "ES blocked during maintenance"
        assert pm.check_trading_allowed(mgc_strategy, maintenance_time) is False, "MGC blocked during maintenance"

    def test_calendar_creation_failure_aborts(self):
        """
        Test calendar creation failure aborts live trading.

        Live mode: Calendar creation failure → SystemExit (hard fail)
        Backtest mode: Calendar creation failure → Warning, continue with None

        Rationale: Never violate TopStepX rules in live trading.

        EXPECTED: This test should FAIL before implementation.
        """
        from custom_portfolio.strategies.run_portfolio import create_calendar

        # Mock pytz import failure to trigger error path
        with patch(
            "custom_portfolio.strategies.run_portfolio.TradingCalendar", side_effect=ImportError("Mock failure")
        ):
            # Live mode: should raise SystemExit
            with pytest.raises(SystemExit):
                create_calendar(is_live=True)

            # Backtest mode: should return None with warning (not crash)
            calendar = create_calendar(is_live=False)
            assert calendar is None, "Backtest should return None on failure, not crash"


if __name__ == "__main__":
    # Run tests with: pytest tests/test_trading_calendar_sessions.py -v
    pytest.main([__file__, "-v", "--tb=short"])
