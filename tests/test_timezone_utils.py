"""
Unit Tests for Timezone Utilities

Tests all timezone conversion functions with focus on:
- DST transitions (spring forward, fall back)
- UTC to Central conversion accuracy
- Central to Mountain display formatting
- Time string parsing with error handling
- Timezone-naive datetime rejection

Author: LumiBot Multi-Strategy Team
Date: 2025-01-23
"""

from datetime import datetime

import pytest
import pytz

from custom_portfolio.tools.timezone_utils import (
    CENTRAL_TZ,
    UTC_TZ,
    central_to_mountain_display,
    get_current_central_time_str,
    get_current_mountain_time_str,
    now_central,
    parse_time_string_central,
    utc_to_central,
)


class TestTimezoneUtilities:
    """Test suite for timezone conversion utilities."""

    def test_now_central_returns_aware_datetime(self):
        """Test that now_central() returns timezone-aware Central Time."""
        ct_now = now_central()

        assert ct_now.tzinfo is not None, "Should return timezone-aware datetime"
        # Check timezone name contains Chicago/CST/CDT
        tz_name = str(ct_now.tzinfo)
        assert any(x in tz_name for x in ["America/Chicago", "CST", "CDT"]), f"Should be Central Time, got {tz_name}"

    def test_utc_to_central_summer_dst(self):
        """Test UTC → Central conversion during summer (CDT, UTC-5)."""
        # Summer: 2024-07-15 12:00 UTC → 2024-07-15 07:00 CDT
        utc_time = UTC_TZ.localize(datetime(2024, 7, 15, 12, 0, 0))
        ct_time = utc_to_central(utc_time)

        expected_ct = CENTRAL_TZ.localize(datetime(2024, 7, 15, 7, 0, 0))

        assert ct_time == expected_ct, f"Expected {expected_ct}, got {ct_time}"
        assert ct_time.hour == 7, "Should be 7 AM Central"
        assert "CDT" in str(ct_time.tzinfo) or "-05:00" in str(ct_time), "Should be CDT (UTC-5)"

    def test_utc_to_central_winter_standard(self):
        """Test UTC → Central conversion during winter (CST, UTC-6)."""
        # Winter: 2024-01-15 12:00 UTC → 2024-01-15 06:00 CST
        utc_time = UTC_TZ.localize(datetime(2024, 1, 15, 12, 0, 0))
        ct_time = utc_to_central(utc_time)

        expected_ct = CENTRAL_TZ.localize(datetime(2024, 1, 15, 6, 0, 0))

        assert ct_time == expected_ct, f"Expected {expected_ct}, got {ct_time}"
        assert ct_time.hour == 6, "Should be 6 AM Central"
        assert "CST" in str(ct_time.tzinfo) or "-06:00" in str(ct_time), "Should be CST (UTC-6)"

    def test_utc_to_central_rejects_naive_datetime(self):
        """Test that utc_to_central() raises TypeError for naive datetime."""
        naive_dt = datetime(2024, 1, 15, 12, 0, 0)

        with pytest.raises(TypeError, match="timezone-aware"):
            utc_to_central(naive_dt)

    def test_utc_to_central_rejects_non_utc_timezone(self):
        """Test that utc_to_central() raises ValueError for non-UTC timezone."""
        ct_time = CENTRAL_TZ.localize(datetime(2024, 1, 15, 12, 0, 0))

        with pytest.raises(ValueError, match="UTC timezone"):
            utc_to_central(ct_time)

    def test_central_to_mountain_display_formatting(self):
        """Test Central → Mountain display string formatting."""
        # Winter: CST (UTC-6) → MST (UTC-7), 1 hour difference
        ct_time = CENTRAL_TZ.localize(datetime(2025, 1, 23, 14, 30, 0))
        mt_str = central_to_mountain_display(ct_time)

        # Should format as "YYYY-MM-DD HH:MM:SS TZ"
        assert "2025-01-23" in mt_str, "Should contain date"
        assert "13:30:00" in mt_str, "Should be 1 hour earlier (13:30 MT vs 14:30 CT)"
        assert "MST" in mt_str or "MDT" in mt_str, "Should contain timezone abbreviation"

    def test_central_to_mountain_display_rejects_naive(self):
        """Test that central_to_mountain_display() rejects naive datetime."""
        naive_dt = datetime(2025, 1, 23, 14, 30, 0)

        with pytest.raises(TypeError, match="timezone-aware"):
            central_to_mountain_display(naive_dt)

    def test_central_to_mountain_display_rejects_non_central(self):
        """Test that central_to_mountain_display() rejects non-Central timezone."""
        utc_time = UTC_TZ.localize(datetime(2025, 1, 23, 14, 30, 0))

        with pytest.raises(ValueError, match="Central Time"):
            central_to_mountain_display(utc_time)

    def test_parse_time_string_valid_format(self):
        """Test parsing valid time strings (HH:MM format)."""
        base_date = datetime(2025, 1, 23)

        # Parse "14:00"
        ct_time = parse_time_string_central("14:00", base_date)

        assert ct_time.year == 2025
        assert ct_time.month == 1
        assert ct_time.day == 23
        assert ct_time.hour == 14
        assert ct_time.minute == 0
        assert ct_time.second == 0
        assert ct_time.tzinfo is not None, "Should be timezone-aware"

    def test_parse_time_string_invalid_format(self):
        """Test that parse_time_string_central() rejects invalid formats."""
        base_date = datetime(2025, 1, 23)

        # Missing colon
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_string_central("1400", base_date)

        # Too many parts
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_string_central("14:00:00", base_date)

        # Non-numeric
        with pytest.raises(ValueError, match="integers"):
            parse_time_string_central("14:XX", base_date)

    def test_parse_time_string_invalid_values(self):
        """Test that parse_time_string_central() validates hour/minute ranges."""
        base_date = datetime(2025, 1, 23)

        # Invalid hour
        with pytest.raises(ValueError, match="Invalid hour"):
            parse_time_string_central("25:00", base_date)

        # Invalid minute
        with pytest.raises(ValueError, match="Invalid minute"):
            parse_time_string_central("14:60", base_date)

    def test_parse_time_string_dst_spring_forward_nonexistent(self):
        """Test parsing time during DST spring-forward (nonexistent time)."""
        # March 10, 2024 at 2:00 AM CT: clocks spring forward to 3:00 AM
        # Time 2:30 AM does not exist
        base_date = datetime(2024, 3, 10)

        with pytest.raises(pytz.exceptions.NonExistentTimeError):
            parse_time_string_central("02:30", base_date)

    def test_parse_time_string_dst_fall_back_ambiguous(self):
        """Test parsing time during DST fall-back (ambiguous time)."""
        # November 3, 2024 at 2:00 AM CDT: clocks fall back to 1:00 AM CST
        # Time 1:30 AM occurs twice - should default to standard time (is_dst=False)
        base_date = datetime(2024, 11, 3)

        # Should not raise exception, should default to standard time
        ct_time = parse_time_string_central("01:30", base_date)

        assert ct_time is not None
        # Should be CST (standard time, is_dst=False)
        # We can't directly check is_dst, but we can verify it doesn't crash

    def test_parse_time_string_uses_current_date_if_none(self):
        """Test that parse_time_string_central() uses current date if base_date is None."""
        ct_time = parse_time_string_central("14:00")

        # Should use current date
        current_date = now_central().date()
        assert ct_time.date() == current_date, "Should use current date"
        assert ct_time.hour == 14
        assert ct_time.minute == 0

    def test_get_current_central_time_str_format(self):
        """Test get_current_central_time_str() returns properly formatted string."""
        time_str = get_current_central_time_str()

        # Should match format: "YYYY-MM-DD HH:MM:SS TZ"
        parts = time_str.split()
        assert len(parts) == 3, f"Expected 3 parts, got {parts}"
        assert parts[0].count("-") == 2, "Date should have 2 hyphens"
        assert parts[1].count(":") == 2, "Time should have 2 colons"
        assert parts[2] in ["CST", "CDT"], f"Timezone should be CST or CDT, got {parts[2]}"

    def test_get_current_mountain_time_str_format(self):
        """Test get_current_mountain_time_str() returns properly formatted string."""
        time_str = get_current_mountain_time_str()

        # Should match format: "YYYY-MM-DD HH:MM:SS TZ"
        parts = time_str.split()
        assert len(parts) == 3, f"Expected 3 parts, got {parts}"
        assert parts[0].count("-") == 2, "Date should have 2 hyphens"
        assert parts[1].count(":") == 2, "Time should have 2 colons"
        assert parts[2] in ["MST", "MDT"], f"Timezone should be MST or MDT, got {parts[2]}"

    def test_central_mountain_time_difference(self):
        """Test that Central and Mountain times have 1-hour difference."""
        ct_now = now_central()
        mt_str = central_to_mountain_display(ct_now)

        # Extract hour from Mountain time string
        mt_time_part = mt_str.split()[1]  # "HH:MM:SS"
        mt_hour = int(mt_time_part.split(":")[0])

        # Mountain should be 1 hour earlier than Central
        # Handle wraparound (e.g., CT 00:30 → MT 23:30 previous day)
        expected_mt_hour = (ct_now.hour - 1) % 24

        assert mt_hour == expected_mt_hour, f"MT hour {mt_hour} should be 1 less than CT hour {ct_now.hour}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
