# Position Sync/Repair Edge Cases - Quick Reference Card

## The Problem
- Sync check runs **every ~30 seconds** (15 poll iterations)
- Current system: unstructured print/logger output + no deduplication
- 30 strategies × multiple symbols = potential for hundreds of log lines in minutes
- No audit trail → can't answer "why was this position zeroed?" post-incident

---

## Fire Risk (Implement Immediately)

### 🔥 TIER 1: Disk Fills with Logs

| Edge Case | Problem | Impact | Quick Fix |
|-----------|---------|--------|-----------|
| **1.1: Log Explosion** | 50+ lines per 30s when repeated desync | Disk full in hours | Suppress repeat desyncs in 5min window, log only first occurrence |
| **1.2: Logger Contention** | Main loop + logger both writing = deadlock | Freezes bot during bracket operations | Use single-threaded QueueHandler, batch repair logs into 1-2 lines |
| **1.3: Uninitialized Logger** | No logger configured = silent failures | Can't see if repairs happened | Explicitly initialize logger in bracket_manager.__init__() |

**Action Items:**
1. Add `DESYNC_SUPPRESSION_WINDOW = 300` (5 minutes)
2. Track `last_alert_signature` per symbol
3. Log only first + last occurrence in window

---

## Alert Fatigue (Implement Week 1)

### ⚠️ TIER 2: Alerts Fire Constantly

| Edge Case | Problem | Impact | Quick Fix |
|-----------|---------|--------|-----------|
| **2.1: Duplicate Alerts** | Same desync triggers alert 6x in 5min | Alert system ignored by team | Deduplicate: `alert_sig = f"{symbol}_{exchange_qty}_{virtual_qty}"` |
| **2.2: False Positives** | Expected desyncs at session close = noise | Can't distinguish bugs from expected | Tag desyncs with "expected: true" during 15:10-17:00 CT maintenance |
| **2.3: Floating Point Noise** | 0.000000001 difference treated as alert | Hundreds of useless alerts | Only alert if `abs(diff) > 0.001` (1 contract granularity) |

**Action Items:**
1. Maintain `recent_alerts_5min` dict with signatures + timestamps
2. Define `EXPECTED_DESYNC_WINDOWS` for session end, maintenance
3. Use epsilon thresholds: `ALERT_THRESHOLD = 0.001`

---

## Missing Context (Implement Week 2)

### 🔍 TIER 3: Can't Debug After the Fact

| Edge Case | Problem | Impact | Quick Fix |
|-----------|---------|--------|-----------|
| **3.1: Lost Context** | "Why was position zeroed?" not in logs | Can't diagnose root cause | Log as JSON: `{"action": "zero", "reason": "phantom_long", "exchange_qty": 3, "virtual_qty": 5}` |
| **3.2: Silent Failures** | Bracket cancel partially fails, repair says "success" | Orphaned brackets exist | Return detailed result: `{success: [id1], failed: [{id2: "already_filled"}]}` |
| **3.3: Retry Count Lost** | Multiple repair attempts not tracked | Can't detect runaway loops | Track `repair_attempt_count` per desync signature, escalate at 5 attempts |
| **3.4: Black Box Repair** | "Which strategy had phantom position?" unknown | Can't improve strategy logic | Log `per_strategy_virtual: {ES_1M_05: 3, ES_1M_08: 1}` before repair |
| **3.5: API Failures Hidden** | Position check API fails, no one knows | Trading with blind positions | Track `sync_check_health = {failures: 0, status: "OK"|"FAILED"}` |
| **3.6: Partial State Corruption** | Repair fails midway, leaves bad state | Subsequent repairs have wrong reference | Implement snapshot + rollback transaction per repair |

**Action Items:**
1. Create `position_repairs_audit.jsonl` (one JSON per repair)
2. Track `DesyncRepairAttempt(attempt_number, timestamp, result)`
3. Log pre-repair position snapshot + post-repair state

---

## Low Priority (Month 2+)

### 📊 TIER 4: Operational Intelligence

| Edge Case | Recommendation |
|-----------|---|
| **4.1: No Metrics** | Create desync counter by symbol/hour, track repair success rate |
| **4.2: No Market Context** | Tag desyncs with volume/volatility/time-to-close |

---

## Critical Scenarios (Real Examples)

### Scenario A: Election Day (High Volatility)
```
14:30:00 - ES desync detected (exchange=5, virtual=7)
14:30:30 - ALERT #1: "ES DESYNC DETECTED"
14:31:00 - Repair attempt #1: reduces from 7 to 5 (success)
14:32:00 - Another ES trade happens, new desync (exchange=5, virtual=8)
14:32:30 - ALERT #2: "ES DESYNC DETECTED"
14:33:00 - Repair attempt #2...

WITHOUT FIXES:
- Operator sees 5 identical "ES DESYNC" alerts in 5 minutes
- Silences all desync alerts
- Real bug at 14:45 goes unnoticed

WITH FIXES:
- First alert: "ES DESYNC: phantom position, repair success"
- Retry alert (at 14:35): "ES DESYNC PERSISTING - attempt #2, needs manual review"
- Audit log: "2 desyncs, both phantom, 100% repair success"
```

### Scenario B: Overnight Session End (Expected Behavior)
```
15:12:00 - Session ending in 3 minutes, force_flat() called
15:12:30 - Sync check: "DESYNC: ES exchange=2, virtual=0"
         - Current system: ALERT! "POSITION DESYNC DETECTED"
         - Operator: "WTF, why is this broken??"

WITH FIXES:
- Sync check: "EXPECTED DESYNC: session_end window"
- Log: "Temporary desync during force_flat, expected recovery in 30s"
- No alert generated
- Operator: "This is normal, no action needed"
```

### Scenario C: Debugging 3 Days Later
```
Trade review: "Why did GC_1M_05 get zeroed on Nov 26 at 14:32?"

Current system:
- Grep logs: "Zeroed GC_1M_05: 2 -> 0"
- That's it. No context.

WITH FIXES:
- Query audit log:
  jq 'select(.strategy == "GC_1M_05" and .timestamp > "2025-11-26T14:00Z")' \
    position_repairs_audit.jsonl
- Result:
  {
    "timestamp": "2025-11-26T14:32:15Z",
    "strategy": "GC_1M_05",
    "action": "zero_phantom_position",
    "reason": "virtual_qty=2 > exchange_qty=0",
    "why": "Phantom long position detected during repair",
    "before": {"qty": 2, "avg_entry": 2045.50},
    "after": {"qty": 0},
    "success": true
  }
- Full context immediately visible
```

---

## Implementation Priority

### Week 1 (Critical Path)
- [ ] Desync window suppression (1.1)
- [ ] Alert deduplication (2.1)
- [ ] Add repair attempt tracking (3.3)
- [ ] JSON logging for repairs (3.1)

### Week 2-3 (Important)
- [ ] Sync check health tracking (3.5)
- [ ] Per-strategy position snapshots (3.4)
- [ ] Expected desync annotations (2.2)
- [ ] Bracket cancel result details (3.2)

### Week 4+ (Nice to Have)
- [ ] Transaction rollback (3.6)
- [ ] Desync metrics/dashboard (4.1, 4.2)

---

## Code Snippets to Add

### Desync Suppression Template
```python
DESYNC_SUPPRESSION_WINDOW = 300  # 5 minutes
recent_desyncs = {}  # {signature: timestamp}

def should_alert_on_desync(symbol, exchange_qty, virtual_qty):
    sig = f"{symbol}_{exchange_qty}_{virtual_qty}"
    if sig in recent_desyncs:
        age = time.time() - recent_desyncs[sig]
        if age < DESYNC_SUPPRESSION_WINDOW:
            return False  # Already alerted recently

    recent_desyncs[sig] = time.time()
    return True
```

### JSON Repair Logging
```python
repair_record = {
    "timestamp": datetime.now().isoformat(),
    "strategy": strategy_id,
    "symbol": symbol,
    "action": "zero_phantom_position",
    "reason_code": "excess_long_zeroed",
    "exchange_qty": exchange_qty,
    "virtual_qty": virtual_qty,
    "before": {"position": qty, "avg_entry": pos.avg_entry_price},
    "after": {"position": 0},
    "success": True,
    "duration_ms": elapsed_time
}
logger.warning(f"[REPAIR] {json.dumps(repair_record)}")
```

### Attempt Tracking
```python
class RepairAttempt:
    desync_signature: str
    attempt_number: int = 1
    first_detected: datetime

    def increment(self):
        self.attempt_number += 1
        if self.attempt_number > 5:
            logger.critical(f"[REPAIR] {self.desync_signature} attempt #{self.attempt_number}")

active_repairs = {}  # {signature: RepairAttempt}
```

---

## Questions for Team

1. **Disk Usage:** How big can logs get before disk fills? What's max log size in production?
2. **Alert Targets:** Who gets paged on "POSITION DESYNC"? Email? Slack? PagerDuty?
3. **Audit Requirements:** Need SOC2/compliance audit trail? How long to keep?
4. **Trading Window:** Does 15:10-17:00 CT maintenance window always cause desyncs?
5. **Acceptable Desync Rate:** What's "normal" per day? 0? 5? 20?

---

## Files to Review

- `/Users/marvin/repos/lumibot_fork/tools/bracket_order_manager.py:1953-2256` - Sync/repair logic
- `/Users/marvin/repos/lumibot_fork/custom_portfolio/strategies/run_portfolio.py:2008-2055` - Sync check loop
- `/Users/marvin/repos/lumibot_fork/lumibot/tools/virtual_position_tracker.py` - Virtual position tracking

---

## Full Documentation

See `/Users/marvin/repos/lumibot_fork/EDGE_CASES_POSITION_SYNC_LOGGING.md` for detailed scenarios, code examples, and implementation roadmap.
