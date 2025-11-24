# TopStepX Documentation Verification - Task 30
## Phase 8: Manual Verification Against Official TopStepX Rules

**Date**: 2025-11-24
**Source**: https://help.topstep.com/en/articles/8284206
**Status**: ⚠️ **CRITICAL DISCREPANCIES FOUND**

---

## CRITICAL FINDINGS

### ❌ Our Configuration is INCORRECT

Our current implementation uses **incorrect times** for TopStepX platform rules:

| Rule | Our Config | TopStepX Actual | Discrepancy |
|------|-----------|-----------------|-------------|
| **Daily close time** | 14:00 CT (2:00 PM) | **15:10 CT (3:10 PM)** | ❌ 70 minutes early |
| **Resume trading** | 16:00 CT (4:00 PM) | **17:00 CT (5:00 PM)** | ❌ 60 minutes early |
| **Weekend open** | Sunday 16:00 CT | **Sunday 17:00 CT** | ❌ 60 minutes early |
| **Weekend close** | Friday 14:00 CT | **Friday 15:10 CT** | ❌ 70 minutes early |

### Impact of Incorrect Configuration

**SEVERE**: Our overly restrictive configuration would:
- ✅ Keep account safe (closes positions earlier than required)
- ❌ Miss trading opportunities in the 14:00-15:10 window (70 minutes daily)
- ❌ Close positions at 2:00 PM when trading until 3:10 PM is allowed
- ❌ Miss Sunday 4:00-5:00 PM window (60 minutes)
- ❌ Not optimal for profitability

---

## Official TopStepX Rules (Verified)

### Daily Trading Schedule

**Position Closure Requirement** (Monday-Friday):
- **Hard deadline**: 3:10:00 PM CT (15:10:00)
- **Risk manager starts flattening**: 3:08 PM CT (15:08:00) as courtesy
- **Trader responsibility**: Must close all positions by 3:10 PM CT

**Resume Trading**:
- **Same day**: 5:00 PM CT (17:00)
- **Sunday**: 5:00 PM CT (17:00) for upcoming week

### Day-Trading Model
- **No overnight positions**: All positions must close before 3:10 PM CT
- **No weekend positions**: Positions cannot carry over between sessions
- **Product-specific**: Futures closing before 3:10 PM must exit before their close time

### CBOT Commodity Market Pause
- **Time window**: 7:45 AM CST - 8:30 AM CST (Monday-Friday)
- **Restriction**: No orders accepted during this window
- **Affected**: Only accounts holding open CBOT commodity positions
- **Note**: Manual lock-out feature unavailable during this window

### Holiday Schedule
- CME holidays apply
- Weekend trading resumes Sunday 5:00 PM CT (unless holiday)

---

## Current Configuration (INCORRECT)

**File**: `custom_portfolio/strategies/portfolio_manager.py`
**Lines**: 125-138

```python
TOPSTEPX_PLATFORM_CONFIG = {
    "daily_stop_new_orders": "14:00",  # 2:00 PM CT - start of daily maintenance
    "daily_force_flat": "14:00",  # 2:00 PM CT - must be flat during maintenance
    "daily_resume": "16:00",  # 4:00 PM CT - maintenance ends, trading resumes
    "weekend_close": {
        "day": 4,  # Friday (0=Monday, 4=Friday)
        "time": "14:00",  # 2:00 PM CT Friday
    },
    "weekend_open": {
        "day": 6,  # Sunday (0=Monday, 6=Sunday)
        "time": "16:00",  # 4:00 PM CT Sunday
    },
    "description": "TopStepX platform rules - NEVER violate these in live trading",
}
```

---

## Corrected Configuration (REQUIRED)

**Based on official TopStepX documentation**:

```python
TOPSTEPX_PLATFORM_CONFIG = {
    "daily_stop_new_orders": "15:08",  # 3:08 PM CT - risk managers start flattening
    "daily_force_flat": "15:10",  # 3:10 PM CT - hard deadline for position closure
    "daily_resume": "17:00",  # 5:00 PM CT - trading resumes same day
    "weekend_close": {
        "day": 4,  # Friday (0=Monday, 4=Friday)
        "time": "15:10",  # 3:10 PM CT Friday - must be flat by this time
    },
    "weekend_open": {
        "day": 6,  # Sunday (0=Monday, 6=Sunday)
        "time": "17:00",  # 5:00 PM CT Sunday - trading resumes for new week
    },
    "cbot_commodity_pause": {
        "start": "07:45",  # 7:45 AM CST - CBOT pause starts
        "end": "08:30",  # 8:30 AM CST - CBOT pause ends
        "description": "No orders accepted for CBOT commodities during this window",
    },
    "description": "TopStepX platform rules - verified 2025-11-24 from official documentation",
}
```

---

## Additional Rules Not Yet Implemented

### CBOT Commodity Market Pause (7:45-8:30 AM CST)

**Current Status**: Not implemented
**Priority**: Medium (only affects CBOT commodity positions)
**Action Required**:
- Add `cbot_commodity_pause` configuration
- Implement in TradingCalendar to block CBOT orders during pause
- Only applies to: Corn, Soybeans, Wheat, Oats, etc. (CBOT commodities)

### Product-Specific Close Times

**Current Status**: Not implemented
**Priority**: Low (most futures close after 3:10 PM CT)
**Examples**:
- Some futures close before 3:10 PM CT
- Traders must exit those markets before their close time
**Action Required**:
- Document early-closing products
- Add product-specific close time validation

---

## Session Times Verification

Our session definitions appear reasonable but should be cross-checked:

### New York Session (CME Regular Trading Hours)
- **Our Config**: 07:30-14:00 CT (ends at 2:00 PM, 70 min before TopStepX deadline)
- **Assessment**: ✅ Safe - closes positions well before 3:10 PM deadline
- **Optimal**: Could extend to 15:00 CT (3:00 PM) to capture more trading time

### Australia, Asia, London Sessions
- **Assessment**: ✅ All end before TopStepX 3:10 PM deadline
- **Weekend Consideration**: Must respect Sunday 5:00 PM open time

### 24/7 Session
- **Our Config**: No restrictions (00:00-23:59)
- **Assessment**: ⚠️ Should still respect TopStepX daily 3:10 PM closure
- **Fix Needed**: Even 24/7 session must close by 3:10 PM CT daily

---

## Recommended Actions

### 1. IMMEDIATE (Critical)
- [x] Document discrepancies (this file)
- [ ] Update TOPSTEPX_PLATFORM_CONFIG with correct times
- [ ] Re-run backtest comparison with corrected times
- [ ] Verify New_York session end time is optimal

### 2. IMPORTANT (High Priority)
- [ ] Implement CBOT commodity pause (7:45-8:30 AM CST)
- [ ] Add validation that 24/7 session respects daily 3:10 PM closure
- [ ] Update documentation in strategy_template.py

### 3. NICE TO HAVE (Medium Priority)
- [ ] Research product-specific close times
- [ ] Add CME holiday calendar integration
- [ ] Document DST handling for 7:45-8:30 AM CST window

---

## Task 30 Status

**Verification Complete**: ✅ YES
**Configuration Correct**: ❌ NO - Critical discrepancies found
**Action Required**: Fix TOPSTEPX_PLATFORM_CONFIG immediately

**Critical Finding**: Our configuration closes positions 70 minutes too early (2:00 PM vs 3:10 PM), potentially reducing profitability by missing the afternoon trading window.

**Safety Assessment**: Current config is overly conservative (closes early), so no risk of TopStepX violations, but not optimal for trading performance.

---

## Next Steps

1. Update TOPSTEPX_PLATFORM_CONFIG in portfolio_manager.py
2. Consider extending New_York session to 15:00 CT (3:00 PM)
3. Update strategy_template.py documentation
4. Re-run Task 29 backtest comparison (optional)
5. Proceed to Phase 9: Final Validation

---

**Verified By**: Claude Code
**Verification Date**: 2025-11-24
**Source Documentation**: https://help.topstep.com/en/articles/8284206
