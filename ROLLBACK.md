# Emergency Rollback Procedures
## Trading Sessions & Calendar Integration Feature

**Feature Branch**: `feature/session-calendar-integration`
**Main Branch**: `dev`
**Implementation Date**: 2025-11-24
**Phases Completed**: 0-8 (Core implementation + validation)

---

## ⚠️ CRITICAL: When to Rollback

### Immediate Rollback Required

Trigger rollback IMMEDIATELY if:
- ❌ **Live trading bot fails to start** due to calendar errors
- ❌ **Positions stuck open** after 3:10 PM CT deadline (TopStepX violation risk)
- ❌ **Trading during platform maintenance** (14:00-16:00 CT window violations)
- ❌ **Weekend trading occurs** (Friday 15:10 CT - Sunday 17:00 CT violations)
- ❌ **Calendar creation fails** in live mode causing startup failure

### Monitored Rollback (Within 24 hours)

Consider rollback if:
- ⚠️ Excessive strategy errors related to calendar/session enforcement
- ⚠️ Unexpected session blocking (strategies not trading when they should)
- ⚠️ Performance degradation (>10% PnL drop unexplained by market)
- ⚠️ Timezone conversion errors causing wrong trading hours

---

## Rollback Levels

### Level 1: Emergency Disable (Fastest - 30 seconds)

**Use When**: Critical production failure, need immediate fix
**Downtime**: None (hot fix via environment variable)
**Risk**: Calendar enforcement disabled, TopStepX compliance at risk

**Steps**:
```bash
# 1. Set emergency disable flag
echo "CALENDAR_EMERGENCY_DISABLE=true" >> .env

# 2. Restart trading bot
# No code changes needed, just restart

# 3. Verify trading bot starts successfully
# Check logs for: "⚠️  CALENDAR EMERGENCY OVERRIDE ACTIVE ⚠️"
```

**What This Does**:
- ✅ Disables calendar creation
- ✅ Allows bot to start normally
- ✅ No session enforcement (all signals processed)
- ❌ **TopStepX rules NOT enforced** (risk of violations)

**Important**:
- This is **EMERGENCY ONLY**
- **DO NOT use in live trading** except as last resort
- Must fix underlying issue and re-enable ASAP
- Monitor for TopStepX rule violations manually

---

### Level 2: Disable Enforcement (Quick - 2 minutes)

**Use When**: Calendar working but enforcement causing issues
**Downtime**: None (hot fix via environment variable)
**Risk**: Sessions ignored, but calendar still loads

**Steps**:
```bash
# 1. Disable enforcement in backtest (if testing)
sed -i '' 's/ENFORCE_SESSIONS_IN_BACKTEST=true/ENFORCE_SESSIONS_IN_BACKTEST=false/' .env

# 2. For live mode - This requires code change (see Level 3)
# Live mode always enforces by design - cannot disable via env var

# 3. Restart if in backtest mode
```

**What This Does**:
- ✅ Calendar still creates successfully
- ✅ Session definitions loaded
- ✅ Calendar validation still runs
- ❌ Session restrictions ignored (all signals processed)

**Limitations**:
- Only works in backtest mode
- Live mode ALWAYS enforces (by design for safety)
- To disable in live, must proceed to Level 3

---

### Level 3: Code Revert (Medium - 10-15 minutes)

**Use When**: Need to revert code changes while keeping history
**Downtime**: 10-15 minutes (bot restart required)
**Risk**: Low (clean revert to known working state)

**Steps**:

#### 3A: Revert via Git
```bash
# 1. Find the commit before calendar integration
git log --oneline dev

# 2. Create revert branch
git checkout dev
git checkout -b hotfix/revert-calendar-integration

# 3. Revert all calendar commits (most recent first)
git revert d7a9e50e  # Task 30: TopStepX time fixes
git revert 6c229270  # Task 29: Backtest comparison
git revert <commit>  # Task 27-28: Validation integration
# ... continue reverting back to last stable commit

# 4. Or revert entire feature branch merge (if already merged)
git revert -m 1 <merge-commit-hash>

# 5. Test revert
python custom_portfolio/strategies/run_portfolio.py --mode validate

# 6. Deploy
git push origin hotfix/revert-calendar-integration

# 7. Restart bot
```

#### 3B: Stub Out Calendar Functions
```python
# Alternative: Quickly stub out calendar in run_portfolio.py

def create_calendar(is_live: bool = False):
    """
    ROLLBACK STUB: Calendar creation disabled
    """
    print("⚠️  Calendar system temporarily disabled (rollback stub)")
    return None

# In PortfolioStrategy.initialize():
calendar = None  # Force None instead of calling create_calendar()
ignore_calendar = True  # Force ignore

# In run_live():
calendar = None  # Force None
ignore_calendar = True  # Force ignore
```

**What This Does**:
- ✅ Reverts to pre-calendar code state
- ✅ No calendar enforcement
- ✅ System works as before implementation
- ✅ Clean git history maintained

**After Revert**:
- Test thoroughly before re-deploying
- Monitor for 24-48 hours
- Fix underlying issue before attempting re-deployment

---

### Level 4: Branch Delete (Extreme - 30 minutes)

**Use When**: Feature fundamentally flawed, complete removal needed
**Downtime**: 30+ minutes (thorough testing required)
**Risk**: Medium (loses all calendar work, must re-implement from scratch)

**Steps**:
```bash
# 1. Switch to main branch
git checkout dev

# 2. Delete local feature branch
git branch -D feature/session-calendar-integration

# 3. Delete remote feature branch (if pushed)
git push origin --delete feature/session-calendar-integration

# 4. Verify clean state
git log --oneline dev
# Should not show any calendar integration commits

# 5. Remove any calendar-related files manually
rm -f BACKTEST_COMPARISON_TASK29.md
rm -f TOPSTEPX_VERIFICATION_TASK30.md
rm -f TEST_STATUS_TASK31.md
rm -f IMPLEMENTATION_STATUS.md
rm -f ROLLBACK.md
rm -f custom_portfolio/tools/timezone_utils.py
rm -f tests/test_timezone_utils.py
rm -f tests/test_trading_calendar_sessions.py

# 6. Revert changes to existing files
git checkout dev -- custom_portfolio/strategies/portfolio_manager.py
git checkout dev -- custom_portfolio/strategies/run_portfolio.py
git checkout dev -- custom_portfolio/strategies/templates/strategy_template.py
git checkout dev -- env.example

# 7. Full validation
python custom_portfolio/strategies/run_portfolio.py --mode validate

# 8. Test backtest
python custom_portfolio/strategies/run_portfolio.py --mode backtest

# 9. Deploy with extreme caution
```

**What This Does**:
- ✅ Complete removal of calendar feature
- ✅ System returns to original state
- ❌ Loses all calendar development work
- ❌ Must re-implement if feature needed later

---

## Rollback Verification Checklist

After any rollback level, verify:

### Startup Verification
- [ ] Bot starts without errors
- [ ] All strategies load successfully
- [ ] No calendar-related errors in logs
- [ ] Validate mode runs successfully

### Functional Verification
- [ ] Backtest runs to completion
- [ ] Strategies generate signals normally
- [ ] Position management working
- [ ] No unexpected trading restrictions

### Safety Verification
- [ ] Check for position closure by 3:10 PM CT (manual monitoring if calendar disabled)
- [ ] No weekend trading occurs (manual verification required)
- [ ] Platform maintenance windows respected (manual verification required)

---

## Post-Rollback Actions

### Immediate (Within 1 hour)
1. Document rollback reason and level used
2. Notify team/stakeholders of calendar system status
3. Set up manual TopStepX compliance monitoring
4. Monitor first few trading sessions closely

### Short-term (Within 24 hours)
1. Analyze root cause of failure
2. Determine if fix is possible
3. Create incident report
4. Plan for either:
   - Fix and re-deploy calendar system
   - Permanent removal of calendar feature
   - Alternative implementation approach

### Long-term (Within 1 week)
1. If re-deploying: Fix root cause, add additional tests, staged rollout
2. If removing: Document decision, update roadmap
3. Review rollback procedures effectiveness
4. Update emergency contact procedures if needed

---

## Rollback Decision Matrix

| Symptom | Severity | Rollback Level | Timeframe |
|---------|----------|----------------|-----------|
| Bot won't start | 🔴 Critical | Level 1 | Immediate |
| Live TopStepX violation | 🔴 Critical | Level 1 | Immediate |
| Position stuck open after 3:10 PM | 🔴 Critical | Level 1 | Immediate |
| Weekend trading occurred | 🔴 Critical | Level 1 | Immediate |
| Excessive strategy errors | 🟡 High | Level 2 | Within 1 hour |
| Session blocking incorrect | 🟡 High | Level 2 | Within 4 hours |
| Performance drop >10% | 🟡 High | Level 3 | Within 24 hours |
| Timezone errors | 🟡 High | Level 3 | Within 24 hours |
| Tests failing | 🟢 Medium | None | Investigate |
| Minor bugs | 🟢 Low | None | Fix forward |

---

## Emergency Contacts

**Before Rolling Back**:
1. Check this document for appropriate rollback level
2. Follow rollback procedures exactly
3. Verify rollback success using checklist
4. Document actions taken

**If Unsure**:
- Default to Level 1 (Emergency Disable) for safety
- Can escalate to higher levels if Level 1 doesn't resolve
- Better to disable and investigate than risk TopStepX violations

---

## Code Restoration (After Rollback Fixed)

If calendar system is rolled back but fix is identified:

```bash
# 1. Create new fix branch
git checkout dev
git checkout -b hotfix/calendar-system-fix

# 2. Cherry-pick working commits from feature branch
git cherry-pick <commit-hash>

# 3. Apply fix
# ... make necessary code changes ...

# 4. Test extensively
python -m pytest tests/
python custom_portfolio/strategies/run_portfolio.py --mode validate
python custom_portfolio/strategies/run_portfolio.py --mode backtest

# 5. Compare with known working state
# Run backtest comparison again

# 6. Gradual rollout
# - Start with paper trading
# - Monitor for 48 hours
# - Then enable in small live account
# - Finally full deployment

# 7. Merge to dev
git checkout dev
git merge hotfix/calendar-system-fix
```

---

## Prevention Measures

### To Avoid Future Rollbacks

1. **Always test in paper trading first** (minimum 48 hours)
2. **Monitor first 3 trading days closely** after deployment
3. **Have manual TopStepX compliance checks** as backup
4. **Set up alerts** for:
   - Calendar creation failures
   - Session enforcement errors
   - Trading outside allowed hours
   - Position closure delays
5. **Regular verification** against TopStepX documentation (quarterly)

### Testing Checklist Before Deployment

- [ ] All timezone utility tests pass
- [ ] Backtest comparison shows session enforcement working
- [ ] TopStepX rules match official documentation
- [ ] Validate mode runs successfully
- [ ] Paper trading 48+ hours successful
- [ ] No errors in logs
- [ ] Performance metrics acceptable
- [ ] Rollback procedures tested and ready

---

## Appendix: File Restoration

### Key Files Modified by Calendar Integration

```
Modified:
- custom_portfolio/strategies/portfolio_manager.py
- custom_portfolio/strategies/run_portfolio.py
- custom_portfolio/strategies/templates/strategy_template.py
- env.example

Created:
- custom_portfolio/tools/timezone_utils.py
- tests/test_timezone_utils.py
- tests/test_trading_calendar_sessions.py
- IMPLEMENTATION_STATUS.md
- BACKTEST_COMPARISON_TASK29.md
- TOPSTEPX_VERIFICATION_TASK30.md
- TEST_STATUS_TASK31.md
- ROLLBACK.md (this file)
```

### Backup Before Deployment

```bash
# Create backup before deploying calendar system
tar -czf calendar_implementation_backup_$(date +%Y%m%d).tar.gz \
  custom_portfolio/strategies/portfolio_manager.py \
  custom_portfolio/strategies/run_portfolio.py \
  custom_portfolio/strategies/templates/strategy_template.py \
  env.example

# Restore from backup if needed
tar -xzf calendar_implementation_backup_YYYYMMDD.tar.gz
```

---

**Document Version**: 1.0
**Last Updated**: 2025-11-24
**Branch**: feature/session-calendar-integration
**Status**: Ready for production use
