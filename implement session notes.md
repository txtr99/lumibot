# Implementing Trading Sessions – Notes & Plan

## Current State
- Strategies declare `allowed_sessions` (e.g., `"New_York"`), but session windows are never defined globally. The executor merely checks `if not self.ignore_calendar and self.calendar is not None`, but no calendar is actually created in backtests, and the existing `create_calendar()` stub in `run_portfolio.py` returns `None`.
- Result: sessions have no effect. Strategies can set `allowed_sessions`, but the calendar is never instantiated; calendar logic in the executor is effectively bypassed.
- We previously disabled Topstep session enforcement to debug signal flow, so the platform-specific rules (stop new orders 30 minutes before close, force-flat at 2:05 PM MT, nightly maintenance windows) were removed. We need to reintroduce them cleanly.

## Requirements
1. **Session Definitions**
   - Each session (Australia, Asia, London, New_York, 24/7) must have explicit start/stop windows in Mountain Time (America/Denver) with logic for cross-midnight sessions.
   - Example Topstep windows (from previous implementation / Topstep docs):
     - `Australia`: 17:00–02:00 MT (next day)
     - `Asia`: 17:00–02:00 MT (overlap; we may differentiate if needed)
     - `London`: 02:00–09:00 MT
     - `New_York`: stop new orders 30 minutes before close (13:35 MT) and force flat at 14:05 MT.
     - `24/7`: open all day but still subject to platform maintenance (typically 14:15–15:00 MT Topstep daily shutdown, plus weekend close/start).
   - Each session definition should include:
     - `start`: HH:MM
     - `stop_new_orders`: HH:MM (optional, default same as force flat)
     - `force_flat`: HH:MM
     - `description`

2. **Platform Rules**
   - Topstep imposes daily maintenance window (close at ~14:05 MT, reopen ~15:00 MT) and weekend shutdown (Friday afternoon to Sunday afternoon). Need a `PLATFORM_CONFIG` to capture these (e.g., `daily_force_flat`, `daily_stop_new_orders`, `daily_resume`, `weekend_close`, `weekend_open`).

3. **Calendar Construction**
   - Use `lumibot.tools.trading_calendar.TradingCalendar` (exists in repo). Need to import `pytz` for timezone handling.
   - Build once (via `create_calendar()`), register session definitions, attach platform-level rules, and hand to `PortfolioManager`. This calendar is then consumed by the executor per strategy (already coded).

4. **Backtest Switch**
   - Provide env var `ENFORCE_SESSIONS_IN_BACKTEST` (default False) to toggle session enforcement in backtests. Allows debugging without gating, but can be enabled for full realism.

5. **Template & Docs**
   - Update `strategy_template.py` docstring to note where session definitions live (`TRADING_SESSIONS`), not just abbreviations.
   - Mention the new env var in `env.example`.

6. **Validation/Testing**
   - Ensure the calendar is created in both live/backtest modes.
   - Confirm crossing-midnight sessions (Australia/Asia) handle windows that pass midnight (e.g., start 17:00 today, force flat 02:00 next day).
   - Confirm `allowed_sessions` only restrict entry (if desired) while still allowing forced exits when `force_flat` hits.

## File-by-File Plan

### 1. `custom_portfolio/strategies/portfolio_manager.py`
- Define `TRADING_SESSIONS` near `SESSION_ABBREVIATIONS`. Example structure:
  ```python
  TRADING_SESSIONS = {
      "New_York": {
          "start": "07:00",
          "stop_new_orders": "13:35",
          "force_flat": "14:05",
          "description": "Topstep NY session (MT).",
      },
      "London": {...},
      "Australia": {...},
      "Asia": {...},
      "24/7": {
          "start": "00:00",
          "stop_new_orders": "23:59",
          "force_flat": "23:59",
          "description": "Always on; platform maintenance only.",
      },
  }
  ```
- Add `PLATFORM_CONFIG` capturing Topstep platform windows:
  ```python
  PLATFORM_CONFIG = {
      "daily_stop_new_orders": "13:35",
      "daily_force_flat": "14:05",
      "daily_resume": "15:00",
      "weekend_close": "14:05 Fri",
      "weekend_open": "15:00 Sun",
  }
  ```
- Export these so `run_portfolio.py` can import them.

### 2. `custom_portfolio/strategies/run_portfolio.py`
- Replace `create_calendar()` stub with a function that imports the session definitions and builds `TradingCalendar` (set timezone to America/Denver). Example logic:
  ```python
  from lumibot.tools.trading_calendar import TradingCalendar
  import pytz
  from custom_portfolio.strategies.portfolio_manager import TRADING_SESSIONS, PLATFORM_CONFIG

  def create_calendar():
      try:
          tz = pytz.timezone("America/Denver")
          calendar = TradingCalendar(timezone=tz, platform_config=PLATFORM_CONFIG)
          for name, sess in TRADING_SESSIONS.items():
              calendar.register_session(name, sess)
          return calendar
      except Exception as exc:
          print(f"[CALENDAR] Failed to create calendar: {exc}")
          return None
  ```
- In `PortfolioStrategy.initialize()`, call `create_calendar()` and set `self.trading_calendar`. Pass to `PortfolioManager`.
- Add env flag `ENFORCE_SESSIONS_IN_BACKTEST` (default False). When constructing `PortfolioManager`, set `ignore_calendar = not enforce_flag` for backtests.
- Update live mode to gracefully handle `create_calendar()` failure (warn if None, but try to continue).

### 3. `custom_portfolio/multi_strategy_executor_enhanced.py`
- No structural change; just confirm existing calendar logic continues to use `status.must_be_flat`, `status.can_enter_orders`, etc. Possibly re-check cross-midnight logic once calendar is implemented.

### 4. `custom_portfolio/strategies/templates/strategy_template.py`
- Update docstring line to: “Sessions: see `custom_portfolio/strategies/portfolio_manager.py` (`TRADING_SESSIONS` for time windows, `SESSION_ABBREVIATIONS` for display names).”

### 5. `env.example`
- Add `ENFORCE_SESSIONS_IN_BACKTEST=false` under existing backtest settings.

## Additional Notes / Research
- Lumibot `TradingCalendar` already has methods for `register_session`, `get_status`, etc., so we don’t need to reinvent gating logic.
- Topstep’s actual windows (per docs, prior code, and broker behavior):
  - Daily close: 13:55 CT / 12:55 MT depending on product; Topstep typically forces flatten at 14:05 MT.
  - Maintenance window: ~14:15–15:00 MT (No orders allowed).
  - Weekend: closes Friday 14:05 MT, reopens Sunday 15:00 MT.
- Use Mountain time consistently for all session definitions and conversions.
- For 24/7 strategies (e.g., crypto), bracket orders/time exit still apply, but platform config ensures they get flattened before daily maintenance.

## Pending Decisions
- Exact time windows for Asia/Australia sessions: Topstep lumps Asian hours 17:00–02:00 MT; we can map both to the same windows unless you prefer distinct boundaries.
- Whether to enforce `stop_new_orders` for 24/7 session (maybe only daily force flat). Can keep permissive (stop/resume only at platform level).
- If Topstep adds special holidays, we may need future calendar hooks; for now, daily/weekly windows should suffice.

## Next Steps
- Implement the code changes above in order.
- Test with `ENFORCE_SESSIONS_IN_BACKTEST=true` over a small date range to ensure session gating works (verify trade counts drop outside sessions).
- Confirm daily maintenance/force-flat triggers by inspecting logs.
- Once verified, re-enable the session settings for production runs.
