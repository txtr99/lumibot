"""
Trading Calendar - Reusable calendar-based trading window manager with visual progress tracking.

Features:
- Two-layer restriction system (Platform + Session)
- Countdown timers to next trading windows
- Visual progress bars for window transitions
- Compact, color-coded status display
- Easy symbol-to-session mapping

Integration:
    from lumibot.tools.trading_calendar import TradingCalendar

    # In initialize():
    self.calendar = TradingCalendar(timezone, platform_config)
    self.calendar.register_sessions(sessions_config)
    self.calendar.map_symbols(symbol_defaults)

    # In on_trading_iteration():
    status = self.calendar.get_status(symbol, current_time, position_qty, position_id)
    self.calendar.log_status(status, self.log_message)
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import pytz

_logger = logging.getLogger(__name__)


@dataclass
class CalendarStatus:
    """Complete calendar status for a symbol at a specific time."""

    # Current state
    current_time: datetime
    symbol: str
    minute: int
    is_even_minute: bool

    # Position info
    position_qty: int
    position_status: str  # "FLAT", "LONG", "SHORT"
    position_id: Optional[str]

    # Platform layer (Layer 1)
    platform_open: bool
    platform_reason: str
    platform_next_event: Optional[str]  # "Opens at 4:05 PM" or "Closes at 2:05 PM"
    platform_countdown_seconds: Optional[int]
    platform_progress_pct: Optional[float]

    # Session layer (Layer 2)
    active_sessions: List[str]
    can_enter_orders: bool
    session_reason: str
    session_next_event: Optional[str]
    session_countdown_seconds: Optional[int]
    session_progress_pct: Optional[float]

    # Position requirements
    must_be_flat: bool
    close_reason: str
    close_deadline: Optional[datetime]
    close_countdown_seconds: Optional[int]

    # Order entry cutoff (when can_enter_orders becomes False)
    stop_orders_deadline: Optional[datetime] = None
    stop_orders_countdown_seconds: Optional[int] = None


class TradingCalendar:
    """
    Manages calendar-based trading windows with visual progress tracking.

    Two-layer architecture:
    - Layer 1 (Platform): Broker-specific restrictions (e.g., TopStepX maintenance)
    - Layer 2 (Session): Instrument-specific trading windows (e.g., NY session)
    """

    def __init__(self, timezone: pytz.timezone, platform_config: dict):
        """
        Initialize trading calendar.

        Args:
            timezone: Timezone for all time calculations (e.g., pytz.timezone("America/Denver"))
            platform_config: Platform-level restrictions dict with keys:
                - daily_force_flat: "14:05" (time to force close positions)
                - daily_stop_new_orders: "13:30" (time to stop accepting new orders)
                - daily_resume: "16:05" (time platform reopens)
                - weekend_close: {"day": 4, "time": "14:05"} (Friday close)
                - weekend_open: {"day": 6, "time": "16:05"} (Sunday open)
        """
        self.timezone = timezone
        self.platform_config = platform_config
        self.sessions: Dict[str, dict] = {}
        self.symbol_sessions: Dict[str, List[str]] = {}

    def register_sessions(self, sessions: Dict[str, dict]):
        """
        Register trading sessions.

        Args:
            sessions: Dict mapping session names to config dicts with keys:
                - start: "17:00" (session start time in timezone)
                - stop_new_orders: "01:30" (stop accepting new orders)
                - force_flat: "02:00" (force close time)
                - description: Human-readable description

        Example:
            calendar.register_sessions({
                "New_York": {
                    "start": "07:30",
                    "stop_new_orders": "13:30",
                    "force_flat": "14:05",
                    "description": "CME/NYMEX peak hours"
                }
            })
        """
        self.sessions = sessions

    def map_symbols(self, symbol_mapping: Dict[str, List[str]]):
        """
        Map symbol roots to their allowed sessions.

        Args:
            symbol_mapping: Dict mapping symbol roots to list of allowed session names

        Example:
            calendar.map_symbols({
                "ES": ["New_York"],
                "MGC": ["Australia", "Asia", "London", "New_York"],
                "6E": ["London", "New_York"]
            })
        """
        self.symbol_sessions = symbol_mapping

    def get_status(
        self,
        symbol: str,
        current_time: datetime,
        position_qty: int = 0,
        position_id: Optional[str] = None,
        allowed_sessions: Optional[List[str]] = None,
    ) -> CalendarStatus:
        """
        Get complete calendar status for a symbol at current time.

        Args:
            symbol: Trading symbol (will extract root, e.g., "MGCZ24" -> "MGC")
            current_time: Current datetime (will be converted to calendar timezone)
            position_qty: Current position quantity
            position_id: Current position identifier
            allowed_sessions: Optional override for allowed sessions (e.g., ["24/7"])
                            If None, uses symbol_sessions mapping

        Returns:
            CalendarStatus object with all timing information
        """
        # Convert to calendar timezone
        current_dt = current_time.astimezone(self.timezone)
        minute = current_dt.minute
        is_even = minute % 2 == 0

        # Extract symbol root and get allowed sessions
        symbol_root = self._get_symbol_root(symbol)
        if allowed_sessions is None:
            allowed_sessions = self.symbol_sessions.get(symbol_root, ["New_York"])

        # Position status
        if position_qty == 0:
            pos_status = "FLAT"
        elif position_qty > 0:
            pos_status = "LONG"
        else:
            pos_status = "SHORT"

        # Platform layer (Layer 1)
        platform_open, platform_reason = self._is_platform_allowed(current_dt)
        platform_next, platform_countdown, platform_progress = self._calculate_platform_timing(current_dt)

        # Session layer (Layer 2)
        active_sessions = self._get_current_session(current_dt, allowed_sessions)
        can_enter, session_reason = self._can_enter_new_orders(current_dt, allowed_sessions, platform_open)
        session_next, session_countdown, session_progress = self._calculate_session_timing(
            current_dt, allowed_sessions, active_sessions
        )

        # Position requirements
        must_close, close_reason = self._must_be_flat(current_dt, allowed_sessions, platform_open)
        close_deadline, close_countdown = self._calculate_close_timing(current_dt, allowed_sessions)

        # Order entry cutoff timing
        stop_orders_deadline, stop_orders_countdown = self._calculate_stop_orders_timing(current_dt, allowed_sessions)

        return CalendarStatus(
            current_time=current_dt,
            symbol=symbol,
            minute=minute,
            is_even_minute=is_even,
            position_qty=position_qty,
            position_status=pos_status,
            position_id=position_id,
            platform_open=platform_open,
            platform_reason=platform_reason,
            platform_next_event=platform_next,
            platform_countdown_seconds=platform_countdown,
            platform_progress_pct=platform_progress,
            active_sessions=active_sessions,
            can_enter_orders=can_enter,
            session_reason=session_reason,
            session_next_event=session_next,
            session_countdown_seconds=session_countdown,
            session_progress_pct=session_progress,
            must_be_flat=must_close,
            close_reason=close_reason,
            close_deadline=close_deadline,
            close_countdown_seconds=close_countdown,
            stop_orders_deadline=stop_orders_deadline,
            stop_orders_countdown_seconds=stop_orders_countdown,
        )

    def log_status(self, status: CalendarStatus, logger_func: Optional[Callable] = None):
        """
        Display compact visual status with countdown timers and progress bars.

        Args:
            status: CalendarStatus object from get_status()
            logger_func: Optional logging function (e.g., self.log_message)
                        If None, prints to stdout

        Output format (4 lines):
            +------------------------------------------------------------------------------+
            | 🕐 EVEN Min 24 | 15:24:00 MT | MGC | FLAT (0)                            |
            +------------------------------------------------------------------------------+
            | 🔴 Platform: Maintenance window   | Opens in 1h 38m                       |
            | 🔴 Session: None                  | Australia in 1h 4m                    |
            +------------------------------------------------------------------------------+
        """
        minute_type = "EVEN" if status.is_even_minute else "ODD"
        time_str = status.current_time.strftime("%H:%M:%S %Z")

        # Header line
        pos_id_str = f", ID={status.position_id}" if status.position_id else ""
        # Calculate padding for header line
        header_content_len = (
            len(time_str)
            + len(status.symbol)
            + len(status.position_status)
            + len(str(status.position_qty))
            + len(pos_id_str)
            + len(minute_type)
            + 25
        )
        header_padding = " " * (78 - header_content_len)
        header = (
            f"+{'-' * 78}+\n"
            f"| 🕐 {minute_type} Min {status.minute:02d} | {time_str} | "
            f"{status.symbol} | {status.position_status} ({status.position_qty}{pos_id_str})"
            f"{header_padding}|\n"
            f"+{'-' * 78}+"
        )

        # Platform status line
        platform_icon = "🟢" if status.platform_open else "🔴"
        platform_status = f"Platform: {status.platform_reason}"
        platform_countdown_str = self._format_countdown(status.platform_countdown_seconds)
        platform_next_str = status.platform_next_event or platform_countdown_str
        platform_padding_len = 78 - 4 - len(platform_status) - len(platform_next_str) - 4
        platform_padding = " " * max(0, platform_padding_len)

        platform_line = f"| {platform_icon} {platform_status:<30} | {platform_next_str}{platform_padding} |"

        # Session status line
        session_icon = "🟢" if status.active_sessions else "🔴"
        sessions_str = ", ".join(status.active_sessions) if status.active_sessions else "None"
        session_status = f"Session: {sessions_str}"
        session_countdown_str = self._format_countdown(status.session_countdown_seconds)
        session_next_str = status.session_next_event or session_countdown_str
        session_padding_len = 78 - 4 - len(session_status) - len(session_next_str) - 4
        session_padding = " " * max(0, session_padding_len)

        session_line = f"| {session_icon} {session_status:<30} | {session_next_str}{session_padding} |"

        # Footer
        footer = f"+{'-' * 78}+"

        # Combine all lines
        output = f"\n{header}\n{platform_line}\n{session_line}\n{footer}"

        if logger_func:
            logger_func(output, color="cyan")
        else:
            print(output)

    # ========== Private Helper Methods ==========

    def _get_symbol_root(self, symbol: str) -> str:
        """Extract root symbol (e.g., 'MGC' from 'MGCZ24')."""
        for i, char in enumerate(symbol):
            if char.isdigit():
                return symbol[:i]
        return symbol

    def _is_weekend_blackout(self, current_dt: datetime) -> bool:
        """Check if we're in weekend blackout period."""
        weekday = current_dt.weekday()  # 0=Monday, 6=Sunday
        current_time_str = current_dt.strftime("%H:%M")

        if weekday == 5:  # Saturday
            return True
        if weekday == 4 and current_time_str >= self.platform_config["weekend_close"]["time"]:
            return True  # Friday after close
        if weekday == 6 and current_time_str < self.platform_config["weekend_open"]["time"]:
            return True  # Sunday before open
        return False

    def _is_platform_allowed(self, current_dt: datetime) -> tuple[bool, str]:
        """Check if platform allows trading (Layer 1)."""
        if self._is_weekend_blackout(current_dt):
            return False, "Weekend blackout"

        current_time_str = current_dt.strftime("%H:%M")
        if (
            current_time_str >= self.platform_config["daily_force_flat"]
            and current_time_str < self.platform_config["daily_resume"]
        ):
            return False, "Maintenance window"

        return True, "OPEN"

    def _get_current_session(self, current_dt: datetime, allowed_sessions: List[str]) -> List[str]:
        """Determine which session(s) are currently active."""
        current_time_str = current_dt.strftime("%H:%M")
        active = []

        for session_name in allowed_sessions:
            # Special case: 24/7 is always active (platform constraints handled separately)
            if session_name == "24/7":
                active.append(session_name)
                continue

            if session_name not in self.sessions:
                continue

            session = self.sessions[session_name]
            start = session["start"]
            end = session["force_flat"]

            # Handle midnight crossover
            if start > end:
                if current_time_str >= start or current_time_str < end:
                    active.append(session_name)
            else:
                if start <= current_time_str < end:
                    active.append(session_name)

        return active

    def _can_enter_new_orders(
        self, current_dt: datetime, allowed_sessions: List[str], platform_open: bool
    ) -> tuple[bool, str]:
        """Check if we can enter new orders (Layer 1 + Layer 2)."""
        if not platform_open:
            return False, "Platform closed"

        current_time_str = current_dt.strftime("%H:%M")

        # Check for stop_new_orders bypass (TESTING ONLY)
        # This allows trades up until force_flat time, bypassing the 30-min buffer
        bypass_enabled = os.environ.get("ALLOW_TRADES_UNTIL_FORCE_FLAT", "").lower() == "true"

        # Platform-level restriction
        if bypass_enabled:
            # Bypass: use force_flat instead of stop_new_orders
            if (
                current_time_str >= self.platform_config["daily_force_flat"]
                and current_time_str < self.platform_config["daily_resume"]
            ):
                return False, "Platform closed"

            # Log warning when bypass is actually triggered (between stop_new_orders and force_flat)
            if (
                current_time_str >= self.platform_config["daily_stop_new_orders"]
                and current_time_str < self.platform_config["daily_force_flat"]
            ):
                _logger.warning(
                    f"ALLOW_TRADES_UNTIL_FORCE_FLAT active at {current_time_str} - "
                    f"normally blocked from {self.platform_config['daily_stop_new_orders']}"
                )
        else:
            # Normal behavior: block after daily_stop_new_orders
            if (
                current_time_str >= self.platform_config["daily_stop_new_orders"]
                and current_time_str < self.platform_config["daily_resume"]
            ):
                return False, "No new orders (platform)"

        # Special case: 24/7 sessions are always open (platform constraints already checked above)
        if "24/7" in allowed_sessions:
            return True, "24/7 open"

        # Session-level check
        for session_name in allowed_sessions:
            if session_name not in self.sessions:
                continue

            session = self.sessions[session_name]
            start = session["start"]

            # Use force_flat as cutoff if bypass enabled, otherwise use stop_new_orders
            cutoff = session["force_flat"] if bypass_enabled else session["stop_new_orders"]

            # Handle midnight crossover
            if start > cutoff:
                # Session crosses midnight (e.g., 17:00 - 01:30)
                if current_time_str >= start or current_time_str < cutoff:
                    reason = f"{session_name} open (bypass)" if bypass_enabled else f"{session_name} open"
                    return True, reason
            else:
                # Normal session (e.g., 07:30 - 13:45)
                # Must be >= start AND < cutoff
                if start <= current_time_str < cutoff:
                    reason = f"{session_name} open (bypass)" if bypass_enabled else f"{session_name} open"
                    return True, reason

        return False, "No active session"

    def _must_be_flat(self, current_dt: datetime, allowed_sessions: List[str], platform_open: bool) -> tuple[bool, str]:
        """Check if we must close all positions now."""
        if not platform_open:
            return True, "Platform closed"

        current_time_str = current_dt.strftime("%H:%M")

        # Platform-level force flat
        if (
            current_time_str >= self.platform_config["daily_force_flat"]
            and current_time_str < self.platform_config["daily_resume"]
        ):
            return True, "Platform maintenance"

        # Special case: 24/7 sessions have no session-level force flat
        # Only platform-level constraints apply (checked above)
        if "24/7" in allowed_sessions:
            return False, "24/7 session"

        # Check if we're in any allowed session
        active = self._get_current_session(current_dt, allowed_sessions)
        if not active:
            return True, "Outside session hours"

        return False, "In session window"

    def _calculate_platform_timing(self, current_dt: datetime) -> tuple[Optional[str], Optional[int], Optional[float]]:
        """Calculate platform next event, countdown, and progress."""
        is_open, _ = self._is_platform_allowed(current_dt)
        current_time_str = current_dt.strftime("%H:%M")
        weekday = current_dt.weekday()

        if not is_open:
            # Calculate when platform opens next
            if weekday == 5:  # Saturday
                # Opens Sunday at resume time
                days_ahead = 1
                next_open = current_dt + timedelta(days=days_ahead)
                next_open = next_open.replace(
                    hour=int(self.platform_config["daily_resume"].split(":")[0]),
                    minute=int(self.platform_config["daily_resume"].split(":")[1]),
                    second=0,
                    microsecond=0,
                )
            elif weekday == 4 and current_time_str >= self.platform_config["weekend_close"]["time"]:
                # Friday after close - opens Sunday
                days_ahead = 2
                next_open = current_dt + timedelta(days=days_ahead)
                next_open = next_open.replace(
                    hour=int(self.platform_config["daily_resume"].split(":")[0]),
                    minute=int(self.platform_config["daily_resume"].split(":")[1]),
                    second=0,
                    microsecond=0,
                )
            elif weekday == 6 and current_time_str < self.platform_config["weekend_open"]["time"]:
                # Sunday before open
                next_open = current_dt.replace(
                    hour=int(self.platform_config["daily_resume"].split(":")[0]),
                    minute=int(self.platform_config["daily_resume"].split(":")[1]),
                    second=0,
                    microsecond=0,
                )
            else:
                # Daily maintenance - opens today at resume time
                next_open = current_dt.replace(
                    hour=int(self.platform_config["daily_resume"].split(":")[0]),
                    minute=int(self.platform_config["daily_resume"].split(":")[1]),
                    second=0,
                    microsecond=0,
                )

            countdown = int((next_open - current_dt).total_seconds())
            next_event = f"Opens {self._format_countdown(countdown)}"
            progress = 0.0
        else:
            # Calculate when platform closes
            close_time = current_dt.replace(
                hour=int(self.platform_config["daily_force_flat"].split(":")[0]),
                minute=int(self.platform_config["daily_force_flat"].split(":")[1]),
                second=0,
                microsecond=0,
            )

            # If close time already passed today, it's tomorrow
            if current_time_str >= self.platform_config["daily_force_flat"]:
                close_time += timedelta(days=1)

            countdown = int((close_time - current_dt).total_seconds())
            next_event = f"Closes {self._format_countdown(countdown)}"

            # Calculate progress through trading day
            open_time = current_dt.replace(
                hour=int(self.platform_config["daily_resume"].split(":")[0]),
                minute=int(self.platform_config["daily_resume"].split(":")[1]),
                second=0,
                microsecond=0,
            )

            # If we're before resume time, the open was yesterday
            if current_time_str < self.platform_config["daily_resume"]:
                open_time -= timedelta(days=1)

            total_seconds = (close_time - open_time).total_seconds()
            elapsed_seconds = (current_dt - open_time).total_seconds()
            progress = min(100.0, max(0.0, (elapsed_seconds / total_seconds) * 100))

        return next_event, countdown, progress

    def _calculate_session_timing(
        self, current_dt: datetime, allowed_sessions: List[str], active_sessions: List[str]
    ) -> tuple[Optional[str], Optional[int], Optional[float]]:
        """Calculate session next event, countdown, and progress."""
        current_time_str = current_dt.strftime("%H:%M")

        # Special case: 24/7 sessions have no session-level timing
        # Return None to indicate no session-level countdown (platform countdown used instead)
        if "24/7" in allowed_sessions:
            return None, None, 100.0  # 100% progress = always in session

        if not active_sessions:
            # Find next session opening
            next_openings = []
            for session_name in allowed_sessions:
                if session_name not in self.sessions:
                    continue

                session = self.sessions[session_name]
                start_time = current_dt.replace(
                    hour=int(session["start"].split(":")[0]),
                    minute=int(session["start"].split(":")[1]),
                    second=0,
                    microsecond=0,
                )

                # If start time already passed, it's tomorrow
                if current_time_str >= session["start"]:
                    start_time += timedelta(days=1)

                next_openings.append((session_name, start_time))

            if next_openings:
                next_session_name, next_open = min(next_openings, key=lambda x: x[1])
                countdown = int((next_open - current_dt).total_seconds())
                next_event = f"{next_session_name} {self._format_countdown(countdown)}"
                progress = 0.0
            else:
                next_event = None
                countdown = None
                progress = 0.0
        else:
            # Calculate when current session closes
            session_name = active_sessions[0]  # Use first active session
            session = self.sessions[session_name]

            end_time = current_dt.replace(
                hour=int(session["force_flat"].split(":")[0]),
                minute=int(session["force_flat"].split(":")[1]),
                second=0,
                microsecond=0,
            )

            start_time = current_dt.replace(
                hour=int(session["start"].split(":")[0]),
                minute=int(session["start"].split(":")[1]),
                second=0,
                microsecond=0,
            )

            # Handle midnight crossover
            if session["start"] > session["force_flat"]:
                if current_time_str < session["force_flat"]:
                    # We're in the early morning part
                    start_time -= timedelta(days=1)
                else:
                    # We're in the evening part
                    end_time += timedelta(days=1)

            countdown = int((end_time - current_dt).total_seconds())
            next_event = f"Closes {self._format_countdown(countdown)}"

            # Calculate progress through session
            total_seconds = (end_time - start_time).total_seconds()
            elapsed_seconds = (current_dt - start_time).total_seconds()
            progress = min(100.0, max(0.0, (elapsed_seconds / total_seconds) * 100))

        return next_event, countdown, progress

    def _calculate_close_timing(
        self, current_dt: datetime, allowed_sessions: List[str]
    ) -> tuple[Optional[datetime], Optional[int]]:
        """Calculate when position must be closed."""
        current_time_str = current_dt.strftime("%H:%M")

        # Platform close time
        platform_close = current_dt.replace(
            hour=int(self.platform_config["daily_force_flat"].split(":")[0]),
            minute=int(self.platform_config["daily_force_flat"].split(":")[1]),
            second=0,
            microsecond=0,
        )

        if current_time_str >= self.platform_config["daily_force_flat"]:
            platform_close += timedelta(days=1)

        # Special case: 24/7 sessions only use platform close (no session-level close)
        if "24/7" in allowed_sessions:
            countdown = int((platform_close - current_dt).total_seconds())
            return platform_close, countdown

        # Session close times
        session_closes = [platform_close]
        for session_name in self._get_current_session(current_dt, allowed_sessions):
            if session_name == "24/7":
                continue  # Skip 24/7, already handled platform close above

            if session_name not in self.sessions:
                continue

            session = self.sessions[session_name]
            session_close = current_dt.replace(
                hour=int(session["force_flat"].split(":")[0]),
                minute=int(session["force_flat"].split(":")[1]),
                second=0,
                microsecond=0,
            )

            if session["start"] > session["force_flat"] and current_time_str < session["force_flat"]:
                # Already in the next day part of midnight-crossing session
                pass
            elif current_time_str >= session["force_flat"]:
                session_close += timedelta(days=1)

            session_closes.append(session_close)

        earliest_close = min(session_closes)
        countdown = int((earliest_close - current_dt).total_seconds())

        return earliest_close, countdown

    def _calculate_stop_orders_timing(
        self, current_dt: datetime, allowed_sessions: List[str]
    ) -> tuple[Optional[datetime], Optional[int]]:
        """Calculate when new order entry will be blocked (stop_new_orders cutoff)."""
        current_time_str = current_dt.strftime("%H:%M")

        # Platform stop_new_orders time
        platform_stop = current_dt.replace(
            hour=int(self.platform_config["daily_stop_new_orders"].split(":")[0]),
            minute=int(self.platform_config["daily_stop_new_orders"].split(":")[1]),
            second=0,
            microsecond=0,
        )

        if current_time_str >= self.platform_config["daily_stop_new_orders"]:
            platform_stop += timedelta(days=1)

        # Special case: 24/7 sessions only use platform stop (no session-level stop)
        if "24/7" in allowed_sessions:
            countdown = int((platform_stop - current_dt).total_seconds())
            return platform_stop, countdown

        # Session stop_new_orders times
        session_stops = [platform_stop]
        for session_name in self._get_current_session(current_dt, allowed_sessions):
            if session_name == "24/7":
                continue

            if session_name not in self.sessions:
                continue

            session = self.sessions[session_name]
            session_stop = current_dt.replace(
                hour=int(session["stop_new_orders"].split(":")[0]),
                minute=int(session["stop_new_orders"].split(":")[1]),
                second=0,
                microsecond=0,
            )

            if session["start"] > session["stop_new_orders"] and current_time_str < session["stop_new_orders"]:
                # Already in the next day part of midnight-crossing session
                pass
            elif current_time_str >= session["stop_new_orders"]:
                session_stop += timedelta(days=1)

            session_stops.append(session_stop)

        earliest_stop = min(session_stops)
        countdown = int((earliest_stop - current_dt).total_seconds())

        return earliest_stop, countdown

    def _format_countdown(self, seconds: Optional[int]) -> str:
        """Format countdown seconds into human-readable string."""
        if seconds is None:
            return "N/A"

        if seconds < 0:
            return "now"

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"in {hours}h {minutes}m"
        elif minutes > 0:
            return f"in {minutes}m"
        else:
            return f"in {secs}s"

    def _render_progress_bar(self, progress_pct: Optional[float], width: int = 20) -> str:
        """Render progress bar using ASCII characters."""
        if progress_pct is None:
            progress_pct = 0.0

        filled_blocks = int((progress_pct / 100) * width)
        empty_blocks = width - filled_blocks

        return "[" + "#" * filled_blocks + "." * empty_blocks + "]"
