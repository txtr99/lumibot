# Root Cause Analysis Map: Position Desync Scenarios

This document maps observable symptoms to root causes and shows how edge cases cascade.

---

## Symptom → Root Cause → Edge Case Matrix

### Symptom: "Disk filled with logs overnight"

**Possible Root Causes:**
1. **Rapid repeat desyncs** → Edge Case 1.1
   - Same symbol (ES) desyncs every 30 seconds
   - Each desync generates 30-50 log lines
   - Over 8 hours: 1,000+ desyncs × 40 lines = 40,000 log lines
   - Solution: Suppress repeats in 5min window

2. **Logger not rotating** → Edge Case 1.3
   - Logger not configured with rotation
   - All logs append to single file indefinitely
   - Solution: Explicitly initialize with RotatingFileHandler

3. **Debug mode left enabled** → Related to 1.1 + 1.2
   - Logger set to DEBUG level instead of WARNING
   - Every sync check generates debug line (synced, no discrepancies)
   - Over 8 hours: 480 sync checks × 5 lines = 2,400 debug lines alone
   - Solution: Use proper log level bucketing (CRITICAL/ERROR/WARNING only)

---

### Symptom: "On-call team ignores all 'POSITION DESYNC' alerts"

**Possible Root Causes:**
1. **Alert fatigue from duplicates** → Edge Case 2.1
   - Same desync signature fires alert 6 times in 5 minutes
   - Team learns: "DESYNC alert = noise, I'll check in morning"
   - Real bug at 3pm goes unnoticed
   - Solution: Deduplicate with signature matching + state machine

2. **Expected desyncs creating false alarms** → Edge Case 2.2
   - Every session end (15:13) triggers 2-3 desyncs during force_flat()
   - These are EXPECTED and always repair cleanly
   - But system alerts every day at 15:13
   - Team adds "mute alerts 15:10-17:00" rule (dangerous!)
   - Solution: Tag expected desyncs, different alert logic

3. **Floating point noise generating junk alerts** → Edge Case 2.3
   - Position: exchange=5.0000000001, virtual=5.0
   - Alert system sees "diff != 0" → fires alert
   - But difference is < 1 contract (noise)
   - Solution: Use epsilon-aware thresholds, round for display

**Cascading Failure:**
- 1.1 (log explosion) + 2.1 (duplicate alerts) + 2.2 (false positives)
- Result: Operators disable alert system entirely
- New real bug doesn't get noticed for hours

---

### Symptom: "Why was GC_1M_05 zeroed at 14:32?"

**Possible Root Causes:**
1. **Lost context during repair** → Edge Case 3.1
   - Logs show: "Zeroed GC_1M_05: 2 -> 0"
   - But WHY? Phantom position? Direction conflict? Manual intervention?
   - 3 days later, can't answer
   - Solution: Log reason_code + context in JSON

2. **No visibility into phantom positions** → Edge Case 3.4
   - GC had: GC_1M_05 (2L) + GC_1M_08 (1L) = 3L virtual
   - Exchange showed: 1L
   - Which strategy had phantom? Both? Just one?
   - Logs only show: "zeroed both"
   - Solution: Log per-strategy breakdown before repair

3. **Repair attempt not tracked** → Edge Case 3.3
   - Was this first repair attempt or retry #4?
   - If retry: why didn't previous attempts work?
   - Can't tell from logs
   - Solution: Track attempt_number in audit log

**Cascading Failure:**
- Multiple repairs to same desync happen over 10 minutes
- 3.1 (no context) + 3.3 (no attempt tracking) + 3.4 (no strategy visibility)
- Result: Post-incident analysis takes 3 hours instead of 5 minutes

---

### Symptom: "Bot suddenly stopped trading at 2pm"

**Possible Root Causes:**
1. **Sync check fails, trading halts** → Edge Case 3.5
   - API timeout during position_search_open()
   - catch-all exception swallows error
   - Subsequent repairs can't run (bad state)
   - Bot stops placing orders (safety mechanism)
   - But no one knows WHY (exception was logged as debug)
   - Solution: Track sync health, expose status to main loop

2. **Partial repair leaves bad state** → Edge Case 3.6
   - Repair runs for 200ms
   - Bracket cancel fails with network error
   - Virtual position already cleared
   - But filled stop/limit order still active on exchange
   - Next repair sees "unexpected direction position"
   - Escalates to manual intervention (bot halts)
   - Solution: Implement rollback on exception

3. **Uninitialized logger crashes during repair** → Edge Case 1.3
   - logger.warning() called, but logger is None
   - Throws AttributeError
   - Repair exception caught silently
   - Next sync check fails (state corrupted)
   - Bot halts to prevent losses
   - Solution: Validate logger initialization

**Cascading Failure:**
- 3.5 (sync fails) + 3.6 (state corruption) + 1.3 (logger crash)
- All three in sequence → bot completely offline
- Error logs show: "Non-fatal exception" (misleading!)
- Real root cause is buried in stack trace

---

### Symptom: "System repaired 15 times for ES, still desynced"

**Possible Root Causes:**
1. **Repair attempts not tracked** → Edge Case 3.3
   - No way to know it's attempt 15 (should have escalated at 5)
   - System keeps trying indefinitely
   - Solution: Escalate after N retries

2. **Direction conflict not fixed** → Edge Case 3.4
   - ES virtual: 5L (ES_1M_05)
   - ES exchange: 3S (short position!)
   - Repair logic: "excess = 5 - 3 = 8, need to reduce LONG exposure"
   - Zeros the LONG position
   - But exchange still has SHORT (opposite direction!)
   - Next repair: "Still desync: virtual=0 exchange=3S"
   - Repair can't reduce further (no LONG to zero)
   - Solution: Fix "opposite direction" case explicitly

3. **Root cause never diagnosed** → Edge Case 3.1
   - Was the desync caused by:
     - API missing a fill?
     - Trading logic placing order twice?
     - Exchange returning stale position?
   - Without reason_code, can't tell
   - Solution: Log reason_code for each repair

**Cascading Failure:**
- Desync actually caused by trading logic bug (duplicate order)
- 3.3 (no attempt tracking) + 3.4 (direction conflict) + 3.1 (no root cause)
- System keeps repairing symptom, never fixes cause
- Trading logic bug never gets fixed
- On-call engineer spends 4 hours debugging audit trail

---

### Symptom: "Alert system spammed me with 100 'ES DESYNC' messages"

**Possible Root Causes:**
1. **No alert deduplication** → Edge Case 2.1
   - Desync at 14:32:00, alert fires
   - Desync at 14:32:30, same signature → second alert (could be suppressed!)
   - Desync at 14:33:00, same signature → third alert
   - By 14:35:00: 6 alerts for same underlying issue
   - Solution: Deduplicate by signature + time window

2. **Different desync magnitudes treated same** → Related 2.1
   - Desync: exchange=5, virtual=7 (diff=2)
   - Alert fires with full details
   - Desync: exchange=5, virtual=7.00001 (diff=0.00001, floating point)
   - Alert fires again (same signature!)
   - Solution: Use epsilon for signature matching

3. **No alert state machine** → Edge Case 2.1 + 2.2
   - Desync happens
   - Alert fires every 30 seconds (one per sync check)
   - But repair never completes (partial repair)
   - Alert system doesn't know "still same issue" vs. "new issue"
   - Treats each as separate incident
   - Solution: Implement DesyncState.UNRESOLVED_5MIN → escalate to CRITICAL

---

## Failure Cascade Examples

### Cascade A: Trading Logic Bug → Infinite Repair Loop → System Shutdown
```
1. Bug in strategy causes duplicate order
   - Market order placed twice (same exchange order ID)
   - One fill is attributed to strategy, second fill dropped

2. Next sync check: virtual=2, exchange=1 (phantom position detected)
   - Repair #1: zeros one phantom
   - Sync check still shows desync (because bug keeps placing doubles)

3. Repair attempts keep happening
   - Edge Case 3.3 (no attempt tracking): system doesn't know this is retry #8
   - Should escalate to manual review at retry #5

4. Alert system spammed
   - Edge Case 2.1 (no deduplication): operator gets 20 identical alerts in 2 hours
   - Operator disables alert system

5. Different bug happens at 4pm
   - No one notices (alerts disabled)
   - Losses accumulate

ROOT CAUSE: Bug in trading logic (duplicate orders)
EDGE CASES THAT ENABLED: 3.1 (no root cause logging) + 3.3 (no attempt tracking) + 2.1 (alert fatigue)
FIX: Better signal detection in trading logic, not better repair logging
BUT SYMPTOMS MASKED BY: Inadequate logging/alerting
```

### Cascade B: Session End Maintenance → Expected Desync → False Alarm → Alert Silenced → Real Bug Missed
```
1. Session ends at 15:13, force_flat() called
   - Virtual positions zeroed immediately
   - But exchange still has some positions (race condition)
   - Sync check detects "virtual=0, exchange=2"

2. System alerts: "POSITION DESYNC DETECTED"
   - Edge Case 2.2 (no expected window handling)
   - Alert fires every day at 15:13

3. Operator adds alert rule: "Mute 15:00-16:00"
   - "This false alarm is annoying"

4. Real bug happens at 15:30 (within mute window)
   - Desync caused by API timeout (not maintenance)
   - Should escalate to manual review
   - But alert system muted → no notification

5. Bot trades all afternoon with wrong position state
   - Losses accumulate

ROOT CAUSE: API timeout (real bug)
BUT MISSED BECAUSE: Expected desyncs weren't distinguished from real bugs
FIX: Tag desyncs with context (session_end, api_error, etc.)
```

### Cascade C: Bracket Cancel Failure → Silent Partial Repair → Orphaned Position → Next Trade Fails
```
1. Desync detected: virtual=4, exchange=3 (phantom long)

2. Repair starts:
   - Zeros virtual position (success)
   - Tries to cancel 2 bracket orders (SL + TP)
   - Edge Case 3.2 (silent partial failure):
     - SL cancel succeeds
     - TP cancel fails (order already filled)
     - But repair doesn't distinguish → "success"

3. Repair audit log shows: "success: true, brackets_cancelled: 1"
   - Actually cancelled only 1 of 2 brackets!

4. Orphaned TP order still active on exchange
   - Waiting to fill at +25 points

5. Next trade on ES happens
   - Position fills at 4495
   - TP order still active at 4520
   - Gets hit immediately
   - Position closed for zero profit
   - Trader confused: "Why did my 1-point profit become zero?"

ROOT CAUSE: TP order didn't cancel during repair
EDGE CASES THAT MASKED: 3.2 (no detailed cancel result) + 3.1 (no full context logging)
FIX: Return detailed cancel result, expose which orders actually cancelled
```

---

## How Edge Cases Interact

### Multiplicative Effect: 1.1 × 2.1 × 3.3
```
1.1: Log explosion (50 lines per desync)
2.1: Duplicate alerts (same alert fires 6x)
3.3: No retry tracking (can't escalate)

COMBINED EFFECT:
- Hour 1: ES desyncs 4 times, generates 200 log lines
- Alerts system fires 24 times (4 desyncs × 6 repeats = 24 alerts)
- No escalation happens (unknown retry count)
- Hour 2: Same pattern repeats
- By hour 4: Team has received 96 identical alerts, 800 log lines
- Team disables alerts entirely
- Real bug at hour 5: Goes unnoticed

INDIVIDUAL FIX TIME: 2 hours each = 6 hours total
COMBINED FIX TIME: 10 hours (because fixes interact)
COST OF NOT FIXING: 5-6 hours of unmonitored trading with wrong positions
```

### Defensive Layers
```
Layer 1: Don't let desync happen in first place
  - Better fill detection
  - Better API error handling
  - BUT: Perfection is impossible

Layer 2: Detect desync quickly + suppress noise
  - Edge Case 1.1: Suppress repeat logs
  - Edge Case 2.1: Deduplicate alerts
  - Edge Case 2.2: Tag expected desyncs
  - Goal: Signal-to-noise ratio stays high

Layer 3: Repair desync automatically
  - Edge Case 3.1: Log context for diagnosis
  - Edge Case 3.3: Track retry attempts, escalate
  - Edge Case 3.4: Show per-strategy breakdown
  - Goal: Fix 90%+ of desyncs without manual intervention

Layer 4: Detect when repair fails
  - Edge Case 3.5: Track sync check health
  - Edge Case 3.6: Rollback on exception
  - Edge Case 3.2: Detailed cancel results
  - Goal: Stop trading if repairs can't keep up

Layer 5: Post-incident analysis
  - Edge Case 3.1: JSON audit logs
  - Edge Case 4.1: Desync metrics
  - Edge Case 4.2: Market context
  - Goal: Answer "why did this happen?" in 10 minutes instead of 10 hours
```

---

## Testing Scenarios

### Test A: Simulate 10 Desyncs in 2 Minutes
```python
# Inject artificial desyncs
for i in range(10):
    sync_result = {
        "synced": False,
        "discrepancies": [{
            "symbol": "ES",
            "exchange_qty": 5,
            "virtual_qty": 5 + i  # Increases each time
        }]
    }
    bracket_manager.repair_position_desync(sync_result)
    time.sleep(12)  # 30s sync check interval

# Check results:
# 1. Logs: Should have ~20 lines, not 500+
# 2. Alerts: Should fire once, not 10 times
# 3. Audit trail: Should show 10 repair attempts with retry counts
```

### Test B: Simulate Expected Desync + Real Desync
```python
# 1. Expected desync during session end
#    - Set clock to 15:12:00
#    - Trigger force_flat()
#    - Sync check at 15:12:30 shows desync
#    - Should NOT alert

# 2. Real desync (API timeout)
#    - Simulate API timeout
#    - Sync check returns error
#    - Should escalate to CRITICAL (health degraded)

# Check results:
# 1. Expected: No alert, just debug log
# 2. Real: Health status = DEGRADED, bot stops trading
```

### Test C: Simulate Partial Repair (Bracket Cancel Fails)
```python
# 1. Desync detected, repair triggered
# 2. Virtual position zeroed (success)
# 3. Bracket cancel returns: {success: [id1], failed: [{id2: "already_filled"}]}
# 4. Repair completes

# Check results:
# 1. Audit log should show: "brackets_cancelled_partial: true"
# 2. Next sync check should know that orphaned bracket still exists
# 3. Future repair attempts should handle this gracefully
```

---

## Prevention vs. Handling

| Approach | Cost | Benefit | Downside |
|----------|------|---------|----------|
| **Prevent Desync (Layer 1)** | High | Eliminates problem | Takes months, hard to test |
| **Suppress Noise (Layer 2)** | Low | Keeps signal-to-noise high | Doesn't fix root cause |
| **Auto-Repair (Layer 3)** | Medium | 90% of issues handled | Complex state management |
| **Detect Repair Failure (Layer 4)** | Low | Prevents cascading failures | Reduces trading availability |
| **Good Logging (Layer 5)** | Low | Fast diagnosis | Doesn't prevent incident |

**Recommendation:** Focus on Layers 2-3-5 first (quick wins, low cost, high ROI).
- Layer 1 (prevent) requires trading logic overhaul (month-long effort)
- Layer 4 (detect failure) needs health monitoring (requires infrastructure)

---

## Summary: Why Logging/Alerting Matters

The position sync/repair system is **fundamentally complex** because it must balance:
1. **Safety** - Stop trading if positions unclear
2. **Availability** - Keep bot running even with small desyncs
3. **Auditability** - Explain what happened 3 days later
4. **Operability** - Don't spam alerts, distinguish signal from noise

**Current state:** Focuses on safety + availability, neglects auditability + operability.
- Result: System works but operators can't understand what it's doing
- Root cause: Inadequate logging, alerting, and debugging infrastructure

**With fixes:** All four goals balanced.
- Safety: Still stops trading if repairs fail (Layer 4)
- Availability: Repairs handle 90%+ of desyncs without trading halt
- Auditability: JSON logs explain every decision
- Operability: Alerts only for real issues, no noise
