# Lumibot Fork - Development Guide

## Virtual Environment

This project uses a standard Python virtual environment (not Poetry).

**Activate the virtual environment:**
```bash
source venv/bin/activate
```

**If packages are missing, install with pip after activating:**
```bash
source venv/bin/activate
pip install <package-name>
```

## Running Tests

**Always activate the venv first, then run pytest:**
```bash
source venv/bin/activate && pytest tests/ -v
```

**Run a specific test file:**
```bash
source venv/bin/activate && pytest tests/test_trading_calendar_sessions.py -v
```

**Run a specific test class:**
```bash
source venv/bin/activate && pytest tests/test_trading_calendar_sessions.py::TestTradingCalendarSessions -v
```

**Run a specific test method:**
```bash
source venv/bin/activate && pytest tests/test_trading_calendar_sessions.py::TestTradingCalendarSessions::test_weekend_blackout_enforcement -v
```

## Environment Variables

### Trading Calendar Overrides

- **`ALLOW_TRADES_UNTIL_FORCE_FLAT`**: Bypass the 30-minute `stop_new_orders` buffer before session end
  - `true`: Allow trades up until `force_flat` time (bypasses `stop_new_orders`)
  - Unset or any other value: Normal behavior (blocks at `stop_new_orders`)
  - **Still enforces**: `force_flat`, platform maintenance (15:10-17:00 CT), weekend blackout
  - **Usage**: `export ALLOW_TRADES_UNTIL_FORCE_FLAT=true`

- **`CALENDAR_EMERGENCY_DISABLE`**: Completely disable the calendar system (NOT RECOMMENDED)

- **`ENFORCE_SESSIONS_IN_BACKTEST`**: Control session enforcement in backtest mode
  - `true` (default): Enforce sessions realistically
  - `false`: Skip session enforcement for signal exploration

## Key Files

- `lumibot/tools/trading_calendar.py` - Two-layer trading window manager
- `custom_portfolio/strategies/portfolio_manager.py` - TopStepX platform config
- `tests/test_trading_calendar_sessions.py` - Calendar session tests
