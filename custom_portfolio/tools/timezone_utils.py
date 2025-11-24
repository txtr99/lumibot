"""
Timezone Utilities for Trading Calendar System

This module provides timezone conversion utilities for the trading calendar system.

Architecture:
- **Source of Truth**: Central Time (America/Chicago) for ALL calculations
- **Display/UI**: Mountain Time (America/Denver) for user-facing output
- **Data Source**: UTC (from Databento and other providers)

Conversion Flow:
    UTC (data) → Central Time (calculations) → Mountain Time (display)

DST Handling:
- Both Central and Mountain Time observe DST on the same schedule
- CT↔MT conversions are safe (1-hour offset year-round)
- UTC→CT conversions MUST handle DST correctly (UTC-5 summer, UTC-6 winter)

Usage:
    from custom_portfolio.tools.timezone_utils import (
        now_central,
        utc_to_central,
        central_to_mountain_display,
        parse_time_string_central
    )

    # Get current time in Central
    current_ct = now_central()

    # Convert UTC timestamp to Central
    ct_time = utc_to_central(utc_timestamp)

    # Display Central time in Mountain for user
    mt_str = central_to_mountain_display(ct_time)

Author: LumiBot Multi-Strategy Team
Date: 2025-01-23
"""

from datetime import datetime
from typing import Optional

import pytz

# Timezone objects
CENTRAL_TZ = pytz.timezone("America/Chicago")
MOUNTAIN_TZ = pytz.timezone("America/Denver")
UTC_TZ = pytz.UTC


def now_central() -> datetime:
    """
    Get current datetime in Central Time (timezone-aware).

    Returns:
        datetime: Current time in America/Chicago timezone

    Example:
        >>> ct_now = now_central()
        >>> print(ct_now.tzinfo)
        America/Chicago
    """
    return datetime.now(CENTRAL_TZ)


def utc_to_central(utc_dt: datetime) -> datetime:
    """
    Convert UTC datetime to Central Time with DST handling.

    CRITICAL: This function handles DST transitions correctly:
    - Summer (CDT): UTC-5
    - Winter (CST): UTC-6

    Args:
        utc_dt: Timezone-aware UTC datetime

    Returns:
        datetime: Timezone-aware Central Time datetime

    Raises:
        TypeError: If utc_dt is timezone-naive
        ValueError: If utc_dt is not in UTC timezone

    Example:
        >>> from datetime import datetime
        >>> import pytz
        >>> utc_time = pytz.UTC.localize(datetime(2024, 7, 15, 12, 0, 0))
        >>> ct_time = utc_to_central(utc_time)
        >>> print(ct_time)  # 2024-07-15 07:00:00-05:00 (CDT)
    """
    # Validate input
    if utc_dt.tzinfo is None:
        raise TypeError(
            "utc_to_central() requires timezone-aware datetime. "
            "Use pytz.UTC.localize() to create timezone-aware UTC datetime."
        )

    if utc_dt.tzinfo != UTC_TZ:
        raise ValueError(
            f"utc_to_central() requires UTC timezone, got {utc_dt.tzinfo}. "
            "Convert to UTC first using .astimezone(pytz.UTC)."
        )

    # Convert UTC → Central Time (pytz handles DST automatically)
    central_dt = utc_dt.astimezone(CENTRAL_TZ)

    return central_dt


def central_to_mountain_display(ct_dt: datetime) -> str:
    """
    Convert Central Time to Mountain Time display string.

    This is for USER-FACING display only. All internal calculations
    should remain in Central Time.

    Args:
        ct_dt: Timezone-aware Central Time datetime

    Returns:
        str: Formatted Mountain Time string (e.g., "2025-01-23 10:30:00 MST")

    Raises:
        TypeError: If ct_dt is timezone-naive
        ValueError: If ct_dt is not in Central timezone

    Example:
        >>> ct_time = CENTRAL_TZ.localize(datetime(2025, 1, 23, 11, 30, 0))
        >>> mt_str = central_to_mountain_display(ct_time)
        >>> print(mt_str)  # "2025-01-23 10:30:00 MST"
    """
    # Validate input
    if ct_dt.tzinfo is None:
        raise TypeError(
            "central_to_mountain_display() requires timezone-aware datetime. "
            "Use CENTRAL_TZ.localize() to create timezone-aware Central datetime."
        )

    # Note: We use str(ct_dt.tzinfo) instead of direct comparison because
    # pytz creates different timezone instances for different DST states
    tz_name = str(ct_dt.tzinfo)
    if "America/Chicago" not in tz_name and "CST" not in tz_name and "CDT" not in tz_name:
        raise ValueError(
            f"central_to_mountain_display() requires Central Time, got {ct_dt.tzinfo}. "
            "Convert to Central first using .astimezone(CENTRAL_TZ)."
        )

    # Convert Central → Mountain (1 hour offset year-round)
    mt_dt = ct_dt.astimezone(MOUNTAIN_TZ)

    # Format for display
    # Format: "YYYY-MM-DD HH:MM:SS TZ"
    # Example: "2025-01-23 10:30:00 MST"
    return mt_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def parse_time_string_central(time_str: str, base_date: Optional[datetime] = None) -> datetime:
    """
    Parse time string (HH:MM format) to Central Time datetime.

    Used for parsing session times like "14:00" into timezone-aware
    Central Time datetime objects.

    Args:
        time_str: Time string in HH:MM format (e.g., "14:00", "09:30")
        base_date: Optional base date to use. If None, uses current date in CT.

    Returns:
        datetime: Timezone-aware Central Time datetime

    Raises:
        ValueError: If time_str is not in HH:MM format
        pytz.exceptions.NonExistentTimeError: If time falls in DST spring-forward gap
        pytz.exceptions.AmbiguousTimeError: If time is ambiguous during DST fall-back

    Example:
        >>> ct_time = parse_time_string_central("14:00")
        >>> print(ct_time)  # Today at 2:00 PM Central Time

        >>> from datetime import datetime
        >>> base = datetime(2025, 1, 23)
        >>> ct_time = parse_time_string_central("14:00", base)
        >>> print(ct_time)  # 2025-01-23 14:00:00-06:00 CST
    """
    # Validate format
    if ":" not in time_str or len(time_str.split(":")) != 2:
        raise ValueError(f"Invalid time format: '{time_str}'. Expected HH:MM format (e.g., '14:00').")

    try:
        hour, minute = map(int, time_str.split(":"))
    except ValueError as e:
        raise ValueError(f"Invalid time format: '{time_str}'. Hour and minute must be integers.") from e

    if not (0 <= hour <= 23):
        raise ValueError(f"Invalid hour: {hour}. Must be 0-23.")

    if not (0 <= minute <= 59):
        raise ValueError(f"Invalid minute: {minute}. Must be 0-59.")

    # Use base date or current date in Central Time
    if base_date is None:
        base_date = now_central()

    # Create naive datetime
    naive_dt = datetime(
        year=base_date.year,
        month=base_date.month,
        day=base_date.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    # Localize to Central Time with strict DST handling
    # is_dst=None means raise exception on ambiguous/nonexistent times
    try:
        ct_dt = CENTRAL_TZ.localize(naive_dt, is_dst=None)
    except pytz.exceptions.NonExistentTimeError as e:
        # This happens during DST spring-forward (e.g., 2:00 AM doesn't exist)
        raise pytz.exceptions.NonExistentTimeError(
            f"Time '{time_str}' on {base_date.date()} does not exist due to DST transition. " f"Original error: {e}"
        ) from e
    except pytz.exceptions.AmbiguousTimeError:
        # This happens during DST fall-back (e.g., 1:00 AM occurs twice)
        # For session times, we default to is_dst=False (standard time)
        ct_dt = CENTRAL_TZ.localize(naive_dt, is_dst=False)

    return ct_dt


# Convenience functions for common operations


def get_current_central_time_str() -> str:
    """
    Get current Central Time as formatted string.

    Returns:
        str: Current time in Central Time (e.g., "2025-01-23 14:30:00 CST")

    Example:
        >>> time_str = get_current_central_time_str()
        >>> print(time_str)  # "2025-01-23 14:30:00 CST"
    """
    ct_now = now_central()
    return ct_now.strftime("%Y-%m-%d %H:%M:%S %Z")


def get_current_mountain_time_str() -> str:
    """
    Get current Mountain Time as formatted string (for display).

    Returns:
        str: Current time in Mountain Time (e.g., "2025-01-23 13:30:00 MST")

    Example:
        >>> time_str = get_current_mountain_time_str()
        >>> print(time_str)  # "2025-01-23 13:30:00 MST"
    """
    ct_now = now_central()
    return central_to_mountain_display(ct_now)


# Export public API
__all__ = [
    "CENTRAL_TZ",
    "MOUNTAIN_TZ",
    "UTC_TZ",
    "now_central",
    "utc_to_central",
    "central_to_mountain_display",
    "parse_time_string_central",
    "get_current_central_time_str",
    "get_current_mountain_time_str",
]
