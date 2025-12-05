# Roadmap: Fix Backtest Data Lookahead Bugs

## Phase Overview

| Phase | Name | Status | Plans |
|-------|------|--------|-------|
| 01 | Validation & Instrumentation | **ready** | 01-01, 01-02 |
| 02 | Core Fixes | pending | 02-01, 02-02, 02-03 |
| 03 | Verification | pending | 03-01 |

---

## Phase 01: Validation & Instrumentation
**Goal:** Confirm each bug with isolated tests before changing any code

### 01-01: Create Diagnostic Test Script
- Build minimal test that captures lookahead behavior
- Log: `current_time`, `df.index[-1]`, `df["close"].iloc[-1]`, expected vs actual

### 01-02: Confirm Market Calendar Bug
- Test that backtest only iterates NYSE hours
- Confirm broker.market is being set correctly for futures

---

## Phase 02: Core Fixes
**Goal:** Fix each bug with minimal, non-breaking changes

### 02-01: Add `get_data_at_time()` Method
- Add method to SharedDataManager that filters to current_time
- Keep `get_cached_data()` unchanged for backward compatibility
- New signature: `get_data_at_time(symbol, current_time, length, timestep)`

### 02-02: Fix Equity Curve Calculation
- Update lines 1107-1113 to use filtered data
- Pass `current_time` to data retrieval
- Fix fallback logic in lines 1544-1548

### 02-03: Fix Market Calendar
- Add `self.set_market("us_futures")` in PortfolioStrategy.initialize()
- Verify broker auto-detection is working

---

## Phase 03: Verification
**Goal:** Confirm all bugs are fixed

### 03-01: Run Full Backtest Validation
- Run same backtest that showed $3,353 constant P&L
- Verify P&L now varies
- Verify no phantom P&L at t=0
- Verify futures hours are covered

---

## Notes
- Bug #5 (snapshot timestamps) is a symptom of Bug #4
- Bug #6 (phantom unrealized P&L) is a symptom of Bug #1 + #2
- Fixing root causes (Bugs 1-4) should resolve symptoms (Bugs 5-6)
