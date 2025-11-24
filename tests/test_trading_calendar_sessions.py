"""
Trading Calendar Sessions Unit Tests

Tests for the TradingCalendar system that enforces:
1. Platform layer (TopStepX rules) - maintenance windows, weekend blackouts
2. Session layer (per-instrument) - trading hour restrictions

Architecture:
- Layer 1 (Platform): Global restrictions that ALWAYS apply
- Layer 2 (Session): Per-symbol trading windows (optional)

Key Business Rules:
- Platform maintenance: 15:10-17:00 CT daily (must be flat)
- Weekend blackout: Friday 15:10 CT - Sunday 17:00 CT
- Sessions: Define when specific symbols can trade
- Platform layer ALWAYS overrides session layer
"""

from datetime import datetime, timedelta

import pytest
import pytz

# Correct imports from actual implementation
from lumibot.tools.trading_calendar import CalendarStatus, TradingCalendar

# =============================================================================
# TEST FIXTURES - Reusable configuration
# =============================================================================


@pytest.fixture
def ct_tz():
    """Central Time timezone (source of truth)."""
    return pytz.timezone("America/Chicago")


@pytest.fixture
def platform_config():
    """TopStepX platform configuration."""
    return {
        "daily_stop_new_orders": "15:08",  # 3:08 PM CT
        "daily_force_flat": "15:10",  # 3:10 PM CT
        "daily_resume": "17:00",  # 5:00 PM CT
        "weekend_close": {"day": 4, "time": "15:10"},  # Friday
        "weekend_open": {"day": 6, "time": "17:00"},  # Sunday
    }


@pytest.fixture
def trading_sessions():
    """Trading session definitions (all times in CT)."""
    return {
        "24/7": {
            "start": "00:00",
            "stop_new_orders": "23:59",
            "force_flat": "23:59",
            "description": "24/7 trading (no session restrictions)",
        },
        "Australia": {
            "start": "17:00",  # 5:00 PM CT
            "stop_new_orders": "01:30",  # 1:30 AM CT next day
            "force_flat": "02:00",  # 2:00 AM CT next day
            "description": "Australia/NZ session (crosses midnight)",
        },
        "Asia": {
            "start": "18:00",  # 6:00 PM CT
            "stop_new_orders": "02:30",  # 2:30 AM CT next day
            "force_flat": "03:00",  # 3:00 AM CT next day
            "description": "Asia session (crosses midnight)",
        },
        "London": {
            "start": "02:00",  # 2:00 AM CT
            "stop_new_orders": "10:30",  # 10:30 AM CT
            "force_flat": "11:00",  # 11:00 AM CT
            "description": "European session",
        },
        "New_York": {
            "start": "07:30",  # 7:30 AM CT
            "stop_new_orders": "13:45",  # 1:45 PM CT
            "force_flat": "14:00",  # 2:00 PM CT
            "description": "CME regular trading hours",
        },
    }


@pytest.fixture
def calendar(ct_tz, platform_config, trading_sessions):
    """Fully configured TradingCalendar."""
    cal = TradingCalendar(timezone=ct_tz, platform_config=platform_config)
    cal.register_sessions(trading_sessions)
    cal.map_symbols(
        {
            "ES": ["New_York"],
            "MGC": ["Australia", "Asia", "London", "New_York"],
            "6E": ["London", "New_York"],
        }
    )
    return cal


# =============================================================================
# TEST CLASS - Trading Calendar Sessions
# =============================================================================


class TestTradingCalendarSessions:
    """
    Test suite for trading calendar session functionality.

    All tests use FIXED datetime values (no time-dependent logic) to ensure
    reproducibility and prevent test flakiness.

    Timezone conventions:
    - Internal calculations: Central Time (America/Chicago)
    - All times in test fixtures are CT
    """

    # =========================================================================
    # Test 1: Cross-midnight session handling
    # =========================================================================
    def test_cross_midnight_session_australia_active(self, calendar, ct_tz):
        """
        Test cross-midnight session (Australia: 17:00 CT → 02:00 CT next day).

        Validates that sessions spanning midnight are handled correctly:
        - 23:59 CT Jan 15 → session ACTIVE (past start, before midnight)
        - 00:01 CT Jan 16 → session ACTIVE (past midnight, before end)
        - 02:01 CT Jan 16 → session INACTIVE (past end time)
        """
        # Before midnight on Jan 15 (within Australia session 17:00-02:00)
        time_before_midnight = ct_tz.localize(datetime(2025, 1, 15, 23, 59, 0))
        status_before = calendar.get_status("MGC", time_before_midnight, allowed_sessions=["Australia"])
        assert "Australia" in status_before.active_sessions, "23:59 CT should be within Australia session"

        # After midnight on Jan 16 (still within session until 02:00)
        time_after_midnight = ct_tz.localize(datetime(2025, 1, 16, 0, 1, 0))
        status_after = calendar.get_status("MGC", time_after_midnight, allowed_sessions=["Australia"])
        assert "Australia" in status_after.active_sessions, "00:01 CT should still be within Australia session"

        # After session close (02:01 CT - session ended at 02:00)
        time_after_close = ct_tz.localize(datetime(2025, 1, 16, 2, 1, 0))
        status_closed = calendar.get_status("MGC", time_after_close, allowed_sessions=["Australia"])
        assert "Australia" not in status_closed.active_sessions, "02:01 CT should be outside Australia session"

    # =========================================================================
    # Test 2: Weekend blackout enforcement
    # =========================================================================
    def test_weekend_blackout_enforcement(self, calendar, ct_tz):
        """
        Test weekend blackout (Friday 15:10 CT → Sunday 17:00 CT).

        TopStepX rules: No trading Friday 3:10 PM CT through Sunday 5:00 PM CT.

        Test checkpoints (Jan 2025: Fri 10th, Sat 11th, Sun 12th):
        - Fri 15:09 CT → platform OPEN
        - Fri 15:11 CT → platform CLOSED (weekend blackout)
        - Sat 12:00 CT → platform CLOSED
        - Sun 16:59 CT → platform CLOSED
        - Sun 17:01 CT → platform OPEN
        """
        # Friday 15:09 CT - just before weekend close
        fri_before_close = ct_tz.localize(datetime(2025, 1, 10, 15, 9, 0))
        status = calendar.get_status("ES", fri_before_close)
        assert status.platform_open is True, "Should allow trading Fri 15:09 CT"

        # Friday 15:11 CT - just after weekend close
        fri_after_close = ct_tz.localize(datetime(2025, 1, 10, 15, 11, 0))
        status = calendar.get_status("ES", fri_after_close)
        assert status.platform_open is False, "Should block trading Fri 15:11 CT"
        assert "Weekend blackout" in status.platform_reason or "Maintenance" in status.platform_reason

        # Saturday midday - definitely blocked
        sat_midday = ct_tz.localize(datetime(2025, 1, 11, 12, 0, 0))
        status = calendar.get_status("ES", sat_midday)
        assert status.platform_open is False, "Should block trading Saturday"
        assert "Weekend" in status.platform_reason

        # Sunday 16:59 CT - just before weekend open
        sun_before_open = ct_tz.localize(datetime(2025, 1, 12, 16, 59, 0))
        status = calendar.get_status("ES", sun_before_open)
        assert status.platform_open is False, "Should block trading Sun 16:59 CT"

        # Sunday 17:01 CT - just after weekend open
        sun_after_open = ct_tz.localize(datetime(2025, 1, 12, 17, 1, 0))
        status = calendar.get_status("ES", sun_after_open)
        assert status.platform_open is True, "Should allow trading Sun 17:01 CT"

    # =========================================================================
    # Test 3: Platform maintenance overrides session
    # =========================================================================
    def test_platform_maintenance_overrides_session(self, calendar, ct_tz):
        """
        Test platform maintenance window overrides active sessions.

        Platform maintenance: 15:10-17:00 CT daily
        24/7 session: Always "active" but platform rules still apply

        During overlap, platform layer MUST WIN:
        - platform_open = False
        - must_be_flat = True
        """
        # Wednesday 15:30 CT - during platform maintenance
        # Using 24/7 session to ensure session is "active" but platform blocks
        maintenance_time = ct_tz.localize(datetime(2025, 1, 15, 15, 30, 0))
        status = calendar.get_status("ES", maintenance_time, allowed_sessions=["24/7"])

        # Session is active (24/7 covers all times)
        assert "24/7" in status.active_sessions, "24/7 session should be active"

        # But platform is NOT open (maintenance window)
        assert status.platform_open is False, "Platform maintenance blocks trading"
        assert "Maintenance" in status.platform_reason

        # Must be flat during maintenance
        assert status.must_be_flat is True, "Must close positions during maintenance"

    # =========================================================================
    # Test 4: DST spring forward transition
    # =========================================================================
    def test_dst_transition_spring_forward(self, ct_tz):
        """
        Test DST spring forward (2:00 AM → 3:00 AM).

        March 9, 2025 at 2:00 AM CT: clocks spring forward to 3:00 AM CT.
        The hour 2:00-3:00 AM DOES NOT EXIST.

        Test that:
        - 01:59 CT → valid time
        - 02:00 CT → raises NonExistentTimeError (strict mode)
        - 03:00 CT → valid time
        """
        # Valid time before transition
        before_dst = ct_tz.localize(datetime(2025, 3, 9, 1, 59, 0))
        assert before_dst is not None

        # Non-existent time during transition (is_dst=None for strict mode)
        with pytest.raises(pytz.exceptions.NonExistentTimeError):
            ct_tz.localize(datetime(2025, 3, 9, 2, 0, 0), is_dst=None)

        # Valid time after transition
        after_dst = ct_tz.localize(datetime(2025, 3, 9, 3, 0, 0))
        assert after_dst is not None

    # =========================================================================
    # Test 5: DST fall back transition
    # =========================================================================
    def test_dst_transition_fall_back(self, ct_tz):
        """
        Test DST fall back (2:00 AM → 1:00 AM).

        November 2, 2025 at 2:00 AM CDT: clocks fall back to 1:00 AM CST.
        The hour 1:00-2:00 AM occurs TWICE.

        Use is_dst flag to disambiguate:
        - 01:59 CDT (first occurrence, is_dst=True)
        - 01:59 CST (second occurrence, is_dst=False)
        """
        # First occurrence (is_dst=True, CDT, UTC-5)
        first_occurrence = ct_tz.localize(datetime(2025, 11, 2, 1, 59, 0), is_dst=True)

        # Second occurrence (is_dst=False, CST, UTC-6)
        second_occurrence = ct_tz.localize(datetime(2025, 11, 2, 1, 59, 0), is_dst=False)

        # They represent different moments in time
        assert first_occurrence != second_occurrence

        # First occurrence should be 1 hour earlier in UTC
        time_diff = second_occurrence - first_occurrence
        assert time_diff == timedelta(hours=1)

    # =========================================================================
    # Test 6: Session countdown calculations
    # =========================================================================
    def test_session_countdown_calculations(self, calendar, ct_tz):
        """
        Test countdown calculation accuracy.

        NY session force_flat: 14:00 CT
        Current time: 10:00 CT
        Expected countdown: ~14,400 seconds (4 hours)
        """
        # Wednesday 10:00 CT - 4 hours before NY session force_flat
        current_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))
        status = calendar.get_status("ES", current_time, allowed_sessions=["New_York"])

        # Verify we're in NY session
        assert "New_York" in status.active_sessions

        # Check close countdown exists and is reasonable
        assert status.close_countdown_seconds is not None

        # Should be approximately 4 hours (14,400 seconds) to NY force_flat at 14:00
        # Allow some tolerance for session vs platform close timing
        expected_seconds = 4 * 60 * 60  # 4 hours
        assert status.close_countdown_seconds <= expected_seconds + 3600  # Max 5 hours
        assert status.close_countdown_seconds >= expected_seconds - 3600  # Min 3 hours

    # =========================================================================
    # Test 7: UTC to Central timezone conversion
    # =========================================================================
    def test_utc_to_central_with_dst(self):
        """
        Test UTC → Central Time conversion with DST handling.

        Summer (CDT): UTC-5
        Winter (CST): UTC-6

        Test cases:
        - Summer: 2024-07-15 12:00 UTC → 2024-07-15 07:00 CDT
        - Winter: 2024-01-15 12:00 UTC → 2024-01-15 06:00 CST
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

    # =========================================================================
    # Test 8: Default session (backward compatibility)
    # =========================================================================
    def test_default_session_when_symbol_not_mapped(self, ct_tz, platform_config, trading_sessions):
        """
        Test backward compatibility: unmapped symbols get default sessions.

        When a symbol is not in the symbol_sessions mapping, get_status
        should fall back to default ["New_York"] behavior.
        """
        # Create calendar WITHOUT symbol mapping
        cal = TradingCalendar(timezone=ct_tz, platform_config=platform_config)
        cal.register_sessions(trading_sessions)
        # Deliberately NOT calling map_symbols()

        # 10:00 AM CT Wednesday - within NY session hours
        ny_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))

        # Get status for unmapped symbol - should use default ["New_York"]
        status = cal.get_status("UNKNOWN", ny_time)

        # Default should be NY session, which is active at 10 AM
        assert "New_York" in status.active_sessions, "Default should be New_York session"
        assert status.can_enter_orders is True, "Should be able to trade during NY hours"

        # 3:00 AM CT - outside NY session
        early_time = ct_tz.localize(datetime(2025, 1, 15, 3, 0, 0))
        status_early = cal.get_status("UNKNOWN", early_time)

        # NY session not active at 3 AM
        assert "New_York" not in status_early.active_sessions
        assert status_early.can_enter_orders is False, "Should not trade outside NY hours"

    # =========================================================================
    # Test 9: Multiple symbols with different sessions
    # =========================================================================
    def test_multiple_symbols_different_sessions(self, calendar, ct_tz):
        """
        Test per-symbol session enforcement with different rules.

        ES (E-mini S&P 500): NY session only (07:30-14:00 CT)
        MGC (Micro Gold): Multi-session (Australia, Asia, London, NY)

        Test times:
        - 05:00 CT Wed: ES blocked (no session), MGC blocked (between Australia end and London start)
        - 10:00 CT Wed: ES allowed (NY), MGC allowed (NY)
        - 15:30 CT Wed: Both blocked (platform maintenance)
        """
        # 05:00 AM CT Wednesday - between sessions
        between_sessions = ct_tz.localize(datetime(2025, 1, 15, 5, 0, 0))

        # ES - only has NY session (07:30-14:00)
        es_status = calendar.get_status("ES", between_sessions)
        assert es_status.active_sessions == [], "ES should have no active session at 05:00"
        assert es_status.can_enter_orders is False, "ES blocked outside NY session"

        # MGC - check if any of its sessions are active
        # At 05:00 CT: Australia ended at 02:00, London doesn't start until 02:00 but 05:00 is within London
        # Wait - London starts at 02:00 and ends at 11:00, so 05:00 IS within London
        mgc_status = calendar.get_status("MGC", between_sessions)
        # Actually London 02:00-11:00 includes 05:00
        assert "London" in mgc_status.active_sessions, "MGC London session active at 05:00"
        assert mgc_status.can_enter_orders is True, "MGC allowed during London"

        # 10:00 AM CT - NY session active for both
        ny_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))

        es_status = calendar.get_status("ES", ny_time)
        assert "New_York" in es_status.active_sessions
        assert es_status.can_enter_orders is True, "ES allowed during NY"

        mgc_status = calendar.get_status("MGC", ny_time)
        assert "New_York" in mgc_status.active_sessions
        assert mgc_status.can_enter_orders is True, "MGC allowed during NY"

        # 15:30 CT - Platform maintenance (overrides all sessions)
        maintenance_time = ct_tz.localize(datetime(2025, 1, 15, 15, 30, 0))

        es_status = calendar.get_status("ES", maintenance_time)
        assert es_status.platform_open is False, "ES blocked during maintenance"

        mgc_status = calendar.get_status("MGC", maintenance_time)
        assert mgc_status.platform_open is False, "MGC blocked during maintenance"

    # =========================================================================
    # Test 10: Calendar creation and factory function
    # =========================================================================
    def test_calendar_creation_from_factory(self, ct_tz):
        """
        Test calendar creation via factory function in run_portfolio.

        Verifies:
        - Factory function returns valid TradingCalendar
        - Sessions are properly registered
        - Platform config is applied
        """
        from custom_portfolio.strategies.run_portfolio import create_calendar

        # Create calendar via factory
        calendar = create_calendar(is_live=False)

        # Should return a TradingCalendar instance
        assert calendar is not None
        assert isinstance(calendar, TradingCalendar)

        # Test that it works with a real query
        test_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))
        status = calendar.get_status("ES", test_time)

        # Should have valid status
        assert isinstance(status, CalendarStatus)
        assert status.platform_open is True  # 10 AM CT is trading hours
        assert "New_York" in status.active_sessions

    # =========================================================================
    # Test 11: CalendarStatus dataclass fields
    # =========================================================================
    def test_calendar_status_fields(self, calendar, ct_tz):
        """
        Verify CalendarStatus dataclass has all expected fields.
        """
        test_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))
        status = calendar.get_status("ES", test_time, position_qty=1, position_id="TEST001")

        # Current state fields
        assert hasattr(status, "current_time")
        assert hasattr(status, "symbol")
        assert hasattr(status, "minute")
        assert hasattr(status, "is_even_minute")

        # Position info fields
        assert hasattr(status, "position_qty")
        assert hasattr(status, "position_status")
        assert hasattr(status, "position_id")
        assert status.position_qty == 1
        assert status.position_status == "LONG"
        assert status.position_id == "TEST001"

        # Platform layer fields
        assert hasattr(status, "platform_open")
        assert hasattr(status, "platform_reason")
        assert hasattr(status, "platform_countdown_seconds")

        # Session layer fields
        assert hasattr(status, "active_sessions")
        assert hasattr(status, "can_enter_orders")
        assert hasattr(status, "session_reason")

        # Position requirements fields
        assert hasattr(status, "must_be_flat")
        assert hasattr(status, "close_reason")
        assert hasattr(status, "close_deadline")

    # =========================================================================
    # Test 12: 24/7 session still respects platform layer
    # =========================================================================
    def test_24_7_session_respects_platform_layer(self, calendar, ct_tz):
        """
        Test that 24/7 session ONLY bypasses session restrictions,
        NOT platform restrictions (maintenance, weekends).

        24/7 session = no session-level restrictions
        Platform layer = ALWAYS enforced
        """
        # During platform hours - 24/7 works
        normal_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))
        status = calendar.get_status("ES", normal_time, allowed_sessions=["24/7"])
        assert status.platform_open is True
        assert "24/7" in status.active_sessions
        assert status.can_enter_orders is True

        # During platform maintenance - 24/7 still blocked
        maintenance_time = ct_tz.localize(datetime(2025, 1, 15, 15, 30, 0))
        status = calendar.get_status("ES", maintenance_time, allowed_sessions=["24/7"])
        assert status.platform_open is False, "24/7 doesn't bypass platform maintenance"
        assert "24/7" in status.active_sessions, "Session is active but platform isn't"

        # During weekend - 24/7 still blocked
        saturday = ct_tz.localize(datetime(2025, 1, 11, 12, 0, 0))
        status = calendar.get_status("ES", saturday, allowed_sessions=["24/7"])
        assert status.platform_open is False, "24/7 doesn't bypass weekend blackout"

    # =========================================================================
    # Test 13: must_be_flat enforcement
    # =========================================================================
    def test_must_be_flat_enforcement(self, calendar, ct_tz):
        """
        Test must_be_flat flag is set correctly:
        - During platform maintenance: must_be_flat = True
        - Outside allowed sessions: must_be_flat = True
        - During normal trading hours: must_be_flat = False
        """
        # Normal trading - no force flat
        normal_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))
        status = calendar.get_status("ES", normal_time, position_qty=1)
        assert status.must_be_flat is False, "Should NOT require flat during normal hours"

        # Platform maintenance - must be flat
        maintenance_time = ct_tz.localize(datetime(2025, 1, 15, 15, 30, 0))
        status = calendar.get_status("ES", maintenance_time, position_qty=1)
        assert status.must_be_flat is True, "MUST be flat during maintenance"
        assert "Maintenance" in status.close_reason or "Platform" in status.close_reason

        # Outside session hours - must be flat
        outside_session = ct_tz.localize(datetime(2025, 1, 15, 3, 0, 0))
        status = calendar.get_status("ES", outside_session, position_qty=1, allowed_sessions=["New_York"])
        assert status.must_be_flat is True, "MUST be flat outside session hours"

    # =========================================================================
    # Test 14: Position status reporting
    # =========================================================================
    def test_position_status_reporting(self, calendar, ct_tz):
        """
        Test position_status field correctly reports FLAT/LONG/SHORT.
        """
        test_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))

        # Flat position
        status = calendar.get_status("ES", test_time, position_qty=0)
        assert status.position_status == "FLAT"

        # Long position
        status = calendar.get_status("ES", test_time, position_qty=5)
        assert status.position_status == "LONG"

        # Short position
        status = calendar.get_status("ES", test_time, position_qty=-3)
        assert status.position_status == "SHORT"


# =============================================================================
# TEST CLASS - ALLOW_TRADES_UNTIL_FORCE_FLAT Override
# =============================================================================


class TestAllowTradesUntilForceFlat:
    """
    Test suite for ALLOW_TRADES_UNTIL_FORCE_FLAT environment variable.

    This override bypasses ONLY the stop_new_orders check (30-min buffer)
    while still enforcing:
    - force_flat (hard close deadline)
    - Platform maintenance windows
    - Weekend blackouts
    """

    # =========================================================================
    # Test 1: Bypass disabled by default
    # =========================================================================
    def test_bypass_disabled_by_default(self, calendar, ct_tz, monkeypatch):
        """Verify stop_new_orders is enforced when bypass env var is not set."""
        monkeypatch.delenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", raising=False)

        # 13:50 CT - after stop_new_orders (13:45) but before force_flat (14:00)
        after_stop = ct_tz.localize(datetime(2025, 1, 15, 13, 50, 0))
        status = calendar.get_status("ES", after_stop, allowed_sessions=["New_York"])

        assert status.can_enter_orders is False, "Should block orders after stop_new_orders"

    # =========================================================================
    # Test 2: Bypass allows orders in buffer zone
    # =========================================================================
    def test_bypass_allows_orders_in_buffer_zone(self, calendar, ct_tz, monkeypatch):
        """With bypass, can enter orders between stop_new_orders and force_flat."""
        monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", "true")

        # 13:50 CT - after stop_new_orders but before force_flat
        after_stop = ct_tz.localize(datetime(2025, 1, 15, 13, 50, 0))
        status = calendar.get_status("ES", after_stop, allowed_sessions=["New_York"])

        assert status.can_enter_orders is True, "Bypass should allow orders"
        assert "bypass" in status.session_reason.lower()

    # =========================================================================
    # Test 3: CRITICAL - Bypass still enforces force_flat
    # =========================================================================
    def test_bypass_still_enforces_force_flat(self, calendar, ct_tz, monkeypatch):
        """CRITICAL: Bypass must NOT override force_flat deadline."""
        monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", "true")

        # 14:05 CT - AFTER force_flat (14:00)
        after_force_flat = ct_tz.localize(datetime(2025, 1, 15, 14, 5, 0))
        status = calendar.get_status("ES", after_force_flat, allowed_sessions=["New_York"])

        assert status.can_enter_orders is False, "force_flat MUST still be enforced"

    # =========================================================================
    # Test 4: CRITICAL - Bypass still enforces platform maintenance
    # =========================================================================
    def test_bypass_still_enforces_platform_maintenance(self, calendar, ct_tz, monkeypatch):
        """CRITICAL: Bypass must NOT override platform maintenance."""
        monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", "true")

        # 15:30 CT - during platform maintenance
        maintenance = ct_tz.localize(datetime(2025, 1, 15, 15, 30, 0))
        status = calendar.get_status("ES", maintenance, allowed_sessions=["24/7"])

        assert status.platform_open is False
        assert status.can_enter_orders is False, "Bypass must NOT override maintenance"

    # =========================================================================
    # Test 5: CRITICAL - Bypass still enforces weekend blackout
    # =========================================================================
    def test_bypass_still_enforces_weekend_blackout(self, calendar, ct_tz, monkeypatch):
        """CRITICAL: Bypass must NOT override weekend blackout."""
        monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", "true")

        # Saturday noon
        saturday = ct_tz.localize(datetime(2025, 1, 11, 12, 0, 0))
        status = calendar.get_status("ES", saturday, allowed_sessions=["24/7"])

        assert status.platform_open is False
        assert status.can_enter_orders is False, "Bypass must NOT override weekend"

    # =========================================================================
    # Test 6: Bypass works for platform-level stop_new_orders (15:08-15:10)
    # =========================================================================
    def test_bypass_platform_level_buffer(self, calendar, ct_tz, monkeypatch):
        """With bypass, can enter orders between platform stop (15:08) and force_flat (15:10)."""
        monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", "true")

        # 15:09 CT - between platform stop_new_orders and force_flat
        after_platform_stop = ct_tz.localize(datetime(2025, 1, 15, 15, 9, 0))
        status = calendar.get_status("ES", after_platform_stop, allowed_sessions=["24/7"])

        assert status.can_enter_orders is True, "Bypass should allow orders before force_flat"

    # =========================================================================
    # Test 7: Bypass works for cross-midnight sessions
    # =========================================================================
    def test_bypass_cross_midnight_session(self, calendar, ct_tz, monkeypatch):
        """Bypass should work with sessions crossing midnight."""
        monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", "true")

        # Australia: stop_new_orders=01:30, force_flat=02:00
        # 01:45 CT - after Australia stop_new_orders but before force_flat
        after_stop = ct_tz.localize(datetime(2025, 1, 16, 1, 45, 0))
        status = calendar.get_status("MGC", after_stop, allowed_sessions=["Australia"])

        assert status.can_enter_orders is True, "Bypass should work for cross-midnight"

    # =========================================================================
    # Test 8: Env var is case insensitive
    # =========================================================================
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("", False),
        ],
    )
    def test_bypass_env_var_values(self, ct_tz, platform_config, trading_sessions, monkeypatch, value, expected):
        """Bypass should only activate when env var equals 'true' (case-insensitive)."""
        if value:
            monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", value)
        else:
            monkeypatch.delenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", raising=False)

        # Create fresh calendar (env var is read on each get_status call)
        cal = TradingCalendar(timezone=ct_tz, platform_config=platform_config)
        cal.register_sessions(trading_sessions)

        # 13:50 CT - after stop_new_orders
        after_stop = ct_tz.localize(datetime(2025, 1, 15, 13, 50, 0))
        status = cal.get_status("ES", after_stop, allowed_sessions=["New_York"])

        assert status.can_enter_orders is expected

    # =========================================================================
    # Test 9: Normal hours unaffected by bypass
    # =========================================================================
    def test_normal_hours_unaffected(self, ct_tz, platform_config, trading_sessions, monkeypatch):
        """During normal trading hours, behavior identical with/without bypass."""
        normal_time = ct_tz.localize(datetime(2025, 1, 15, 10, 0, 0))

        # Without bypass
        monkeypatch.delenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", raising=False)
        cal = TradingCalendar(timezone=ct_tz, platform_config=platform_config)
        cal.register_sessions(trading_sessions)
        status_normal = cal.get_status("ES", normal_time, allowed_sessions=["New_York"])

        # With bypass
        monkeypatch.setenv("ALLOW_TRADES_UNTIL_FORCE_FLAT", "true")
        cal2 = TradingCalendar(timezone=ct_tz, platform_config=platform_config)
        cal2.register_sessions(trading_sessions)
        status_bypass = cal2.get_status("ES", normal_time, allowed_sessions=["New_York"])

        assert status_normal.can_enter_orders is True
        assert status_bypass.can_enter_orders is True


if __name__ == "__main__":
    # Run tests with: python -m pytest tests/test_trading_calendar_sessions.py -v
    pytest.main([__file__, "-v", "--tb=short"])
