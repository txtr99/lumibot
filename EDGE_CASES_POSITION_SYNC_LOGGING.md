# Edge Cases: Position Sync/Repair Logging, Alerting, and Debugging

## Executive Summary

The position sync/repair system (`bracket_order_manager.py::check_position_sync()` and `repair_position_desync()`) runs every ~30 seconds in the main trading loop. This document identifies **18 edge cases** focused on logging, alerting, and debugging across three severity tiers, with specific scenarios and recommendations.

**Current System:**
- Sync check: Every 15 poll iterations (~30 seconds) in `run_portfolio.py:2010`
- Repair: Auto-triggered on desync detection (logs to bracket_manager.logger)
- Logs: `[POSITION-SYNC]`, `[POSITION-REPAIR]` prefixes
- Visibility: Print statements + Python logger (no structured audit trail)

---

## TIER 1: Disk & Performance Impact (High Risk)

### Edge Case 1.1: Rapid Repeat Desyncs Create Log Explosion
**Scenario:** Same symbol (e.g., "ES") desyncs repeatedly in 5-minute window.
- Sync check runs every 30s → up to 10 checks in 5 minutes
- Each desync logs: 3+ warning lines + multiple discrepancy lines + repair lines
- Result: 30-50+ log lines per desync event
- **With 3 concurrent desyncs (ES, NQ, GC):** 150+ lines per 30 seconds

**Impact:**
- Log files grow 100MB/day with verbose POSITION-REPAIR logging
- Disk space fills on small VPS (TopStepX platform)
- Log rotation delays slow down the main event loop
- Old desyncs become unreadable in 100,000+ line files

**Recommendations:**
```
1. Implement "desync window" suppression:
   - Only log full details first occurrence in 5-minute window
   - Subsequent repeats: single-line aggregate "Same ES desync repeated 5x since 14:30:00"
   - Reset window on successful repair or 15 min elapsed

2. Use log level bucketing:
   - First desync in window: WARNING level (always logged)
   - Repeat #2-5: DEBUG level (verbose logging off)
   - Repeat #6+: TRACE level (only if debug enabled)

3. Implement log size caps per event:
   - Desync summary: max 200 bytes
   - Repair details: max 400 bytes
   - Truncate strategy lists if > 10 affected

4. Rotate/archive sync logs separately:
   - Keep sync logs in dedicated file: position_sync.log (max 50MB)
   - Daily rotation + gzip
   - Main bot.log stays clean for other events
```

---

### Edge Case 1.2: Logging During High-Frequency Repair Loops
**Scenario:** Multiple strategies trigger repairs in succession.
- Repair loop calls: `cancel_brackets_for_strategy()` for each broken strategy
- Each cancel attempt logs: bracket IDs, statuses, errors
- Multiple strategies: 5+ brackets × 3+ log lines = 15-20 lines per repair cycle
- In 30 seconds with 3 cycles: 60+ lines

**Impact:**
- Logger lock contention with main event loop (both writing simultaneously)
- Buffered log writes cause delayed flushing to disk
- `print()` statements flush immediately but bypass logger buffering
- Interleaved output becomes unreadable (log message A split by message B)

**Recommendations:**
```
1. Batch repair logging:
   - Collect all repairs/cancellations in a single summary
   - Single logger.warning() call with structured format:
     [POSITION-REPAIR] Fixed 3 symbols: ES(1 repair, 2 brackets),
                       NQ(1 repair, 1 bracket), GC(no repair needed)

2. Use structured logging for parsing:
   - Log JSON: {"event": "position_repair", "symbol": "ES", "repairs": 1,
                "brackets_cancelled": 2, "duration_ms": 145}
   - Easier to parse for alerts than text parsing

3. Defer heavy logging to post-repair:
   - Repair happens in ~200ms, log happens in ~50ms
   - Split: minimal logging during repair, detailed logging after

4. Implement logger queue for thread-safe async writes:
   - QueueHandler with separate logging thread
   - Prevents main loop blocking on disk I/O
```

---

### Edge Case 1.3: Uninitialized Logger Crashes Bot
**Scenario:** `bracket_order_manager` instantiated without logger parameter.
- Constructor: `self.logger = logger or logging.getLogger(__name__)`
- If no logger + root logger not configured: logs go to stderr silently
- If multiple instances created: each gets own logger, harder to filter
- Root logger in test mode might have verbose config from another module

**Impact:**
- Repair logs disappear silently during testing
- Production logs go to stderr instead of rotating file
- Alert thresholds can't fire (logs never exist)

**Recommendations:**
```
1. Explicit logger initialization:
   def __init__(self, ..., logger=None, log_prefix="BRACKET_MANAGER"):
       if logger is None:
           logger = logging.getLogger(f"lumibot.{log_prefix}")
           if not logger.handlers:  # Only add if not already configured
               handler = logging.FileHandler("position_sync.log")
               handler.setFormatter(logging.Formatter(
                   '%(asctime)s - %(levelname)s - [%(name)s] %(message)s'
               ))
               logger.addHandler(handler)
       self.logger = logger

2. Use dependency injection:
   - Pass logger from run_portfolio.py explicitly
   - Ensures consistent logging config across system
   - Easier to swap loggers in tests

3. Validate logger on every critical operation:
   if not self.logger or not self.logger.handlers:
       raise RuntimeError("BracketOrderManager logger not properly configured")
```

---

## TIER 2: Alert Fatigue & False Positives (Medium Risk)

### Edge Case 2.1: Alerting the Same Desync Multiple Times
**Scenario:** ES desyncs at 14:32:15, repair fails partially (remaining_to_zero > 0).
- Sync check at 14:32:15: "ES DESYNC, virtual=5 exchange=3"
- Repair attempt: "Partial repair: reduced 2 of 3 excess. 1 remain."
- Alert fires: "ES DESYNC DETECTED"
- Sync check at 14:32:45: Same ES desync still exists
- Alert fires again: "ES DESYNC DETECTED" (same issue!)
- By 14:35:00: 6 identical alerts, on-call engineer silences alert system

**Impact:**
- Alert fatigue: humans learn to ignore "DESYNC" alerts
- Real issue (e.g., exchange API returning wrong position) buried in 100 identical alerts
- On-call team dismisses all similar alerts reflexively
- Post-incident review shows "DESYNC DETECTED every 30s" → true issue never diagnosed

**Recommendations:**
```
1. Implement alert deduplication with signature matching:
   alert_signature = f"{symbol}_{exchange_qty}_{virtual_qty}"
   if alert_signature in recent_alerts_5min:
       # Already alerted on this exact desync, don't alert again
       logger.debug(f"Suppressing duplicate alert: {alert_signature}")
   else:
       # New desync or different magnitude
       logger.warning(f"NEW ALERT: {alert_signature}")
       recent_alerts_5min[alert_signature] = time.time()

2. Track alert state machine:
   class DesyncState:
       INITIAL_ALERT = "first_detection"          # Alert fires
       REPEAT_NO_REPAIR = "unresolved_5_min"      # Alert fires (NEW issue!)
       ESCALATION = "unresolved_15_min"           # PAGE on-call
       IGNORE = "known_issue"                     # Human annotation

3. Separate alert levels by root cause:
   - Partial repair failure: WARN (recoverable, monitor)
   - Direction conflict (exchange > virtual): CRITICAL (manual action needed)
   - Untracked position: CRITICAL (audit trail broken)
   - Floating point noise (< 0.1 difference): TRACE (not a real desync)

4. Add recovery tracking:
   if previous_desync_repaired:
       log_level = DEBUG  # We recovered, don't alert
   else:
       log_level = WARNING  # Still broken, alert loudly
```

---

### Edge Case 2.2: "Expected" Desyncs During Market Close
**Scenario:** Session ends at 15:15 CT, force_flat() at 15:13.
- 15:13:00: Positions at exchange = [ES: 2L, NQ: 1L], virtual = [ES: 3L, NQ: 1L]
- Auto-flattens ES position via `force_flat()`
- Virtual ES becomes 0 but brackets still pending
- Sync check at 15:13:30: "ES DESYNC: exchange=2, virtual=0"
- System generates CRITICAL alert
- But this is EXPECTED: force_flat always causes temporary desync until bracket cleanup

**Impact:**
- False alarms during known maintenance windows
- Bot operator doesn't know which desyncs are expected vs. bugs
- Difficult to distinguish "expected pattern" from "actual issue"
- Makes audit trail noisy (hard to find real problems post-incident)

**Recommendations:**
```
1. Annotate "expected desync" windows:
   EXPECTED_DESYNC_WINDOWS = {
       "force_flat": {"duration_seconds": 30, "symbols": "all"},
       "session_end": {"duration_seconds": 60, "symbols": "ES,NQ,GC"},
       "maintenance": {"duration_seconds": 120, "symbols": "ES"},  # TopStepX 3pm window
   }

   def is_expected_desync(symbol, reason):
       window = EXPECTED_DESYNC_WINDOWS.get(reason)
       if window and symbol in window.get("symbols", ""):
           if time_since_event < window["duration_seconds"]:
               return True
       return False

   # Usage:
   if sync_result.get("discrepancies"):
       for d in sync_result["discrepancies"]:
           if not is_expected_desync(d["symbol"], "session_end"):
               logger.warning(f"UNEXPECTED desync: {d}")

2. Tag desyncs with context:
   desync_event = {
       "symbol": "ES",
       "exchange_qty": 2,
       "virtual_qty": 0,
       "reason": "force_flat",  # Known cause
       "expected": True,
       "timestamp": "2025-11-26T15:13:30Z"
   }
   # If expected=True, different alert logic applies

3. Create desync audit log separate from alerts:
   - All desyncs logged with context (expected/unexpected/severity)
   - Searchable by symbol, time, reason
   - Post-incident review: "ES had 12 desyncs, 11 expected (session_end), 1 unexpected"

4. Expose "maintenance window" to alert system:
   if in_maintenance_window("15:10-17:00"):
       alert_threshold = 10  # Allow 10 desyncs
   else:
       alert_threshold = 1   # Alert immediately
```

---

### Edge Case 2.3: Floating Point Noise Creates False Alerts
**Scenario:** Exchange position has rounding, virtual position is integer.
- Exchange: 5.000000001 contracts (floating point artifact)
- Virtual: 5 contracts (stored as int then float)
- Difference: 0.000000001 (well below epsilon)
- But logging shows: "exchange=5.0000000001 virtual=5.0 diff=0.0000000001"
- Some alert systems treat "any non-zero diff" as ALERT

**Impact:**
- Noisy alerts for non-issues
- Alert rule becomes "diff > 0" instead of "diff > epsilon"
- Hard to distinguish real desync (0.5 difference) from noise (1e-9 difference)
- Complicates audit trail (what's signal vs. noise?)

**Recommendations:**
```
1. Epsilon-aware logging:
   EPSILON = 0.001  # 1 contract granularity for logging

   if abs(diff) < EPSILON:
       logger.debug(f"Floating point noise: {abs(diff):.2e} (< {EPSILON})")
   elif abs(diff) < 0.1:
       logger.info(f"Minor desync: {diff:.2f} contracts")
   else:
       logger.warning(f"MAJOR desync: {diff:.0f} contracts")

2. Round for display:
   def format_quantity(qty, round_to=0.1):
       return round(qty / round_to) * round_to

   display_diff = format_quantity(diff)
   if display_diff == 0:
       logger.debug("Positions match (within rounding)")

3. Store raw diff in structured log, display in alerts:
   {
       "raw_diff": 0.000000001,
       "display_diff": 0.0,
       "alert_level": "TRACE"  # Not alertable
   }

4. Define alert thresholds explicitly:
   ALERT_THRESHOLDS = {
       "DEBUG": 0.001,      # Log but don't alert
       "INFO": 0.01,        # Monitor closely
       "WARNING": 0.1,      # Alert humans
       "CRITICAL": 1.0,     # Page on-call
   }
```

---

## TIER 3: Debugging & Audit Trail (Medium Risk)

### Edge Case 3.1: Lost Context During Repair ("Why Was This Zeroed?")
**Scenario:** Trade review, 3 days later: "Why was GC_1M_05 position zeroed at 14:32:15?"
- Repair logs show: "Zeroed GC_1M_05: 2 -> 0"
- But WHY? Was it:
  - Phantom position cleanup?
  - Direction conflict?
  - Partial repair leftover?
  - User forced flat?
  - Session maintenance?
- Context is missing from log entry

**Impact:**
- Can't debug past desyncs
- Can't distinguish "good repair" from "bad repair"
- No audit trail for compliance (showing how positions were managed)
- Impossible to review incident 3 days later

**Recommendations:**
```
1. Rich repair context logging:
   repair_record = {
       "timestamp": "2025-11-26T14:32:15Z",
       "strategy": "GC_1M_05",
       "symbol": "GC",
       "action": "zero_phantom_position",
       "reason_code": "excess_long_zeroed",  # Machine-parseable
       "context": {
           "exchange_qty": 2,
           "virtual_qty": 4,
           "excess": 2,
           "why": "virtual > exchange, phantom longs detected"
       },
       "before": {"position": 2, "avg_entry": 2045.50, "pnl": 150},
       "after": {"position": 0, "avg_entry": None, "pnl": 0},
       "brackets_cancelled": ["BR_GC_1M_05_001", "BR_GC_1M_05_002"],
       "fills_cleared": 3,
       "duration_ms": 145,
       "success": True
   }

   # Log as JSON for parsing:
   logger.warning(f"[REPAIR] {json.dumps(repair_record)}")

2. Create repair audit log file:
   # position_repairs_audit.jsonl (one JSON per line)
   {"timestamp": "2025-11-26T14:32:15Z", "strategy": "GC_1M_05", ...}
   {"timestamp": "2025-11-26T14:35:22Z", "strategy": "ES_1M_03", ...}

   # Can query later:
   jq 'select(.symbol == "ES" and .timestamp > "2025-11-26T14:00:00Z")' \
     position_repairs_audit.jsonl

3. Include reason code enum:
   class RepairReason(Enum):
       EXCESS_LONG_ZEROED = "phantom_long_cleanup"
       EXCESS_SHORT_ZEROED = "phantom_short_cleanup"
       DIRECTION_CONFLICT = "direction_mismatch_unrecoverable"
       UNTRACKED_POSITION = "exchange_position_missing_from_virtual"
       PARTIAL_REPAIR = "partial_repair_remainder"

4. Timestamp correlation for incident review:
   # If desync at 14:32:00, can query:
   # - Sync check results at 14:32:00
   # - Repair actions at 14:32:05
   # - Bracket cancel logs at 14:32:08
   # - Next sync check at 14:32:30
   # Full timeline reconstructable
```

---

### Edge Case 3.2: Bracket Cancellation in Repair Creates Silent Failures
**Scenario:** Repair tries to cancel bracket associated with zeroed position.
```python
# Current code (line 2206):
try:
    cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
    if cancel_result.get("cancelled"):
        result["brackets_cancelled"].extend(cancel_result["cancelled"])
except Exception as ce:
    self.logger.warning(f"[POSITION-REPAIR] Failed to cancel brackets for {sid}: {ce}")
```

Problems:
1. If `cancel_brackets_for_strategy()` logs its own warnings (e.g., "Order doesn't exist"), those logs are separate from repair context
2. Caller doesn't know if 2 of 3 brackets cancelled (partial failure)
3. Error message format varies: different exceptions have different details
4. No timestamp correlation between cancel attempt and desync

**Impact:**
- Repair thinks it succeeded ("cancelled brackets") but only 2 of 3 actually cancelled
- Orphaned bracket still exists (can cause future issues)
- Repair audit log says "success: true" but bracket still active
- Hard to trace bracket lifetime

**Recommendations:**
```
1. Return detailed bracket cancellation result:
   cancel_result = {
       "requested": ["BR_GC_1M_05_001_SL", "BR_GC_1M_05_001_TP"],
       "success": ["BR_GC_1M_05_001_SL"],
       "failed": [
           {
               "bracket_id": "BR_GC_1M_05_001_TP",
               "order_id": 999,
               "error": "Order doesn't exist (API error 5)",
               "reason": "Already filled"
           }
       ]
   }

   # Repair can then decide: partial success OK or full failure?
   if len(cancel_result["failed"]) > 0:
       repair_record["bracket_cancel_partial"] = True
       repair_record["brackets_failed_to_cancel"] = cancel_result["failed"]

2. Log each bracket cancel attempt separately:
   for order_id in bracket_ids:
       try:
           resp = self.client.order_cancel(order_id)
           logger.info(f"[BRACKET-CANCEL] {order_id} -> success")
       except Exception as e:
           logger.warning(f"[BRACKET-CANCEL] {order_id} -> failed: {e}")

3. Create bracket lifecycle log:
   bracket_events.jsonl:
   {"timestamp": "14:30:00", "bracket_id": "BR_GC_1M_05_001", "event": "registered"}
   {"timestamp": "14:32:00", "bracket_id": "BR_GC_1M_05_001", "event": "desync_repair_attempt"}
   {"timestamp": "14:32:05", "bracket_id": "BR_GC_1M_05_001_SL", "event": "cancel_success"}
   {"timestamp": "14:32:05", "bracket_id": "BR_GC_1M_05_001_TP", "event": "cancel_failed", "reason": "already_filled"}

   # Trace full lifecycle of any bracket
```

---

### Edge Case 3.3: Multiple Repair Attempts in Quick Succession Lose History
**Scenario:** Desync at 14:32:00 triggers repair attempt #1.
- Repair #1 zeros 2 strategies, partially successful
- Sync check at 14:32:30 still shows desync (remaining_to_zero > 0)
- Repair #2 triggered
- But repair logs don't show "this is attempt #2" vs. "new desync"
- If repair #3 triggered: no way to know retry count

**Impact:**
- Repair history is lost (can't see escalation pattern)
- Difficult to debug "why did we repair 5 times?" incidents
- Can't detect runaway repair loops (safety issue)
- Audit trail doesn't show retry strategy

**Recommendations:**
```
1. Implement repair attempt tracking:
   class DesyncRepairAttempt:
       desync_signature: str  # e.g., "ES_2024-11-26T14:32:00Z"
       attempt_number: int    # 1, 2, 3, ...
       previous_attempt_time: Optional[datetime]
       repairs_made: List[Dict]
       remaining_to_zero: float  # < 0.001 = success, > 0.1 = still broken
       escalation_level: str  # "auto_repair", "manual_intervention", "emergency_stop"

   # Track active desyncs:
   active_desync_repairs = {}  # {desync_signature: DesyncRepairAttempt}

   # On each repair attempt:
   desync_sig = f"{symbol}_{sync_result['exchange_qty']}_{sync_result['virtual_qty']}"
   if desync_sig in active_desync_repairs:
       attempt = active_desync_repairs[desync_sig]
       attempt.attempt_number += 1
       attempt.previous_attempt_time = datetime.now()

       if attempt.attempt_number > 5:
           logger.critical(f"[REPAIR-ESCALATION] {symbol} desync has failed 5 repair attempts!")
           # Escalate: stop auto-repair, require manual intervention

   result["attempt_number"] = attempt.attempt_number

2. Log repair attempt sequence:
   repair_attempts.jsonl:
   {"timestamp": "14:32:05", "desync_sig": "ES_2_4", "attempt": 1, "result": "partial"}
   {"timestamp": "14:32:35", "desync_sig": "ES_2_4", "attempt": 2, "result": "partial"}
   {"timestamp": "14:33:05", "desync_sig": "ES_2_4", "attempt": 3, "result": "STOP_RUNAWAY"}

3. Add retry abort condition:
   MAX_REPAIR_ATTEMPTS_PER_DESYNC = 5
   if attempt_number >= MAX_REPAIR_ATTEMPTS_PER_DESYNC:
       logger.critical(f"[REPAIR-ABORT] Desync '{desync_sig}' failed {attempt_number} repairs!")
       logger.critical(f"Stopping auto-repair to prevent loop. Manual intervention required.")
       # Stop trying, only log warnings
```

---

### Edge Case 3.4: No Visibility Into Which Strategy Caused Desync
**Scenario:** Desync detected: ES virtual=5, exchange=3.
- Which strategy(ies) have the "phantom" 2 ES contracts?
- Logs show "Zeroed ES_1M_05: 3 -> 0" but also "Zeroed ES_1M_08: 1 -> 0"
- How many long positions vs. short? How did this desync happen?
- Need post-incident analysis: "Which strategy's order fill was missed?"

**Impact:**
- Can't diagnose root cause
- Can't improve fill detection for broken strategy
- Can't distinguish "API missed the fill" from "trading logic error"
- Leads to repeated desyncs with same strategy

**Recommendations:**
```
1. Pre-repair position snapshot:
   desync_context = {
       "symbol": "ES",
       "exchange_qty": 3,
       "virtual_qty": 5,
       "excess": 2,
       "per_strategy_virtual": {
           "ES_1M_05": {"qty": 3, "side": "LONG", "avg_entry": 4500.50},
           "ES_1M_08": {"qty": 1, "side": "LONG", "avg_entry": 4499.00},
           "ES_1M_10": {"qty": 1, "side": "SHORT", "avg_entry": 4505.00},  # Opposite direction!
       },
       "strategies_with_phantom": ["ES_1M_05", "ES_1M_08"],
       "strategies_with_opposite": ["ES_1M_10"]
   }

   logger.warning(f"[DESYNC-ANALYSIS] {json.dumps(desync_context)}")

2. Post-repair comparison:
   repair_analysis = {
       "symbol": "ES",
       "zeroed_strategies": ["ES_1M_05", "ES_1M_08"],
       "strategies_not_zeroed": {
           "ES_1M_10": {"reason": "opposite_direction", "qty": 1}
       },
       "total_exposure_before": 5,
       "total_exposure_after": 1,  # ES_1M_10 still has short
       "repair_success": "partial"  # Opposite position not removed
   }

3. Attribution analysis for debugging:
   # Group by strategy for later analysis:
   per_strategy_repair_log = {
       "ES_1M_05": [
           {"timestamp": "14:32:05", "action": "zeroed", "qty": 3, "reason": "phantom"},
           {"timestamp": "14:35:22", "action": "zeroed", "qty": 2, "reason": "phantom"}
       ]
   }

   # Can then analyze: "ES_1M_05 has 3 phantom-zeros in 30 days"
   # -> likely fill detection bug

4. Create "strategy incident report":
   # Generate after repair:
   report = {
       "strategy": "ES_1M_05",
       "symbol": "ES",
       "desync_count": 3,  # This strategy was involved in 3 desyncs
       "total_exposure_lost": 6,  # Total zeroed positions
       "root_cause_hypothesis": "Fill detection missed 6 market orders",
       "recommendation": "Review fill polling logic"
   }
```

---

## TIER 3 (Continued): Debugging Recommendations

### Edge Case 3.5: Sync Check Errors Silently Swallowed
**Scenario:** API returns error, sync check aborts early.
```python
# Current code (line 2014-2017):
if not sync_result.get("synced"):
    if sync_result.get("error"):
        # API error - log but don't alarm
        pass  # Already logged by bracket_manager
```

Problems:
1. Main loop catches exception silently (line 2053-2055)
2. If exception thrown during position_search_open(): logged as warning only
3. No distinction between "API temporarily down" vs. "credentials invalid"
4. No retry logic: if API fails once, sync checks silently fail for minutes

**Impact:**
- Unknown if position sync is actually working (might be broken for 10 minutes)
- Alert system doesn't know sync checks are failing
- Could be trading with wrong position state but sync never reports it
- No visibility into API health

**Recommendations:**
```
1. Track sync check health:
   class SyncCheckHealth:
       consecutive_failures: int = 0
       last_failure_time: Optional[datetime] = None
       last_error_type: Optional[str] = None  # "api_down" vs. "auth_failed"
       health_status: str = "OK"  # "OK" / "DEGRADED" / "FAILED"

   health = SyncCheckHealth()

   def check_position_sync(self):
       try:
           # ... existing sync check ...
           health.consecutive_failures = 0
           health.health_status = "OK"
       except Exception as e:
           health.consecutive_failures += 1
           health.last_error_type = type(e).__name__

           if health.consecutive_failures >= 5:
               health.health_status = "FAILED"
               logger.critical(f"[SYNC-HEALTH] 5 consecutive sync check failures!")

           raise  # Let caller handle

2. Expose sync health to main loop:
   sync_health = bracket_manager.get_sync_health()
   if sync_health["status"] == "FAILED":
       # Safe behavior: stop trading until sync works
       logger.critical("[MAIN] Position sync is broken, stopping new orders")

3. Distinguish error types:
   try:
       pos_response = self.client.position_search_open(...)
   except (ConnectionError, TimeoutError) as e:
       logger.warning(f"[SYNC] Temporary API issue: {e}")
       # Retry logic: wait 5s, try again
   except AuthenticationError as e:
       logger.critical(f"[SYNC] Auth failed: {e}")
       # Non-retryable: alert ops
   except Exception as e:
       logger.error(f"[SYNC] Unknown error: {e}")
       # ???
```

---

### Edge Case 3.6: Repair Modifies State Without Logging Entry/Exit
**Scenario:** During repair, state is modified across multiple steps:
1. Bracket cancellation (async)
2. Virtual position reset
3. Processed fills cleared
4. Strategy entry price cleared

If an exception occurs halfway (e.g., network timeout during bracket cancel), state is partially modified. Subsequent repair attempts have wrong starting state.

**Impact:**
- State corruption during partial repair
- Can't roll back changes
- Subsequent sync checks have wrong reference state
- Repair audit trail incomplete

**Recommendations:**
```
1. Implement state snapshot + rollback:
   class RepairTransaction:
       def __init__(self, strategy_state):
           self.snapshot = {
               "position": strategy_state.tracker.positions.copy(),
               "entry_price": strategy_state.entry_price,
               "take_profit_price": strategy_state.take_profit_price,
               "stop_loss_price": strategy_state.stop_loss_price,
               "processed_fills": strategy_state.processed_fills.copy() if hasattr(...) else None
           }

       def rollback(self, strategy_state):
           strategy_state.tracker.positions = self.snapshot["position"]
           strategy_state.entry_price = self.snapshot["entry_price"]
           # ... restore all fields

   # In repair:
   txn = RepairTransaction(state)
   try:
       # Perform repairs
       cancel_brackets(...)
       state.tracker.reset()
       clear_fills(...)
       logger.info("[REPAIR] Successfully completed")
   except Exception as e:
       logger.error(f"[REPAIR] Failed midway: {e}, rolling back")
       txn.rollback(state)
       raise

2. Log state transitions:
   logger.debug(f"[REPAIR-TXN] Before: {json.dumps(snapshot)}")
   # ... perform repair steps ...
   logger.debug(f"[REPAIR-TXN] After: {json.dumps(new_state)}")
```

---

## TIER 4: Operational Intelligence (Lower Risk)

### Edge Case 4.1: No Metrics/Dashboard for Desync Frequency
**Scenario:** Desync happens 3-5 times per day.
- Is this normal? Good? Bad?
- Trending up (getting worse) or stable?
- Which symbols have most desyncs (ES vs. NQ vs. GC)?
- Are desyncs clustered at market open/close or random?

**Impact:**
- No early warning for degrading system health
- Can't correlate desyncs with market conditions
- No objective basis for "is this system ready for production?"
- Hard to prioritize fixes (which symbol to debug first?)

**Recommendations:**
```
1. Implement desync metrics:
   class DesyncMetrics:
       desync_count: Counter = Counter()      # per symbol, per hour
       repair_success_rate: float = 0.0       # % of repairs that fully resolved desync
       mean_time_to_repair: float = 0.0       # avg seconds from desync to resolved
       phantom_position_frequency: float = 0.0  # % of desyncs due to phantom positions

   # Track in database/prometheus:
   desync_count_es_1600 = 3    # 3 desyncs in 4pm hour (16:00)
   desync_count_nq_1600 = 1
   repair_success_rate = 0.85   # 85% of repairs fully resolved

2. Create daily desync report:
   ```
   Daily Desync Summary (2025-11-26)

   Symbol    Count  Avg Duration  Repair %  Root Causes
   ------    -----  -----------  ---------  -----------
   ES          5        45s         100%      4 phantom, 1 direction_conflict
   NQ          2        120s         50%      1 phantom, 1 unresolved
   GC          1        30s          100%     1 phantom

   Peak hour: 15:00-16:00 CT (3 desyncs during market close window)
   Trending: Stable (5-day avg: 4.2 desyncs)
   ```

3. Alert on anomalies:
   if desync_count_today > 2 * rolling_7day_avg:
       logger.critical("ANOMALY: Desync rate 2x higher than normal!")
```

---

### Edge Case 4.2: No Correlation Between Desync and Market Volatility/Volume
**Scenario:** Desyncs spike when trading ES at market open (high volume, fast fills).
- Virtual tracker misses fill due to latency
- But API often returns positions with delay
- Hard to tell if this is normal or a bug

**Impact:**
- Can't distinguish "high-volume-related lag" from "system bug"
- No way to say "desyncs are OK during market open but BAD at 3pm"
- Can't alert operators "watch for desyncs during open"

**Recommendations:**
```
1. Tag desyncs with market context:
   desync_context = {
       "symbol": "ES",
       "timestamp": "14:32:00Z",
       "market_info": {
           "session": "US_EQUITIES",
           "time_to_close": 90,  # minutes
           "minutes_into_session": 30,
           "implied_volatility": 0.18,
           "volume_5min": 2500,  # contracts
           "price_movement_5min": 0.15  # %
       }
   }

2. Create desync pattern report:
   Desyncs by time-of-session:
   - 00-30 min into session: 8 desyncs (market open)
   - 30-120 min: 2 desyncs
   - Last 60 min: 5 desyncs (market close)

   Pattern: Desyncs cluster at session boundaries (EXPECTED)

3. Alert on unexpected patterns:
   if desync_in_quiet_market_hour:
       logger.critical("Desync during low-activity period (unusual!)")
```

---

## Summary Table: All 18 Edge Cases

| Tier | ID | Edge Case | Severity | Root Cause | Recommendation |
|------|----|----|----------|-----------|---|
| 1 | 1.1 | Log explosion from repeat desyncs | HIGH | 50+ log lines every 30s | Desync window suppression + log level bucketing |
| 1 | 1.2 | Logger lock contention | HIGH | Simultaneous logger/print writes | Batch logging + structured JSON |
| 1 | 1.3 | Uninitialized logger crashes | MEDIUM | No logger config | Explicit initialization + dependency injection |
| 2 | 2.1 | Alert fatigue from duplicates | HIGH | Same alert fires 6x in 5min | Deduplication + alert state machine |
| 2 | 2.2 | False alerts during maintenance | HIGH | Expected desyncs look like bugs | Annotate expected windows + tag context |
| 2 | 2.3 | Floating point noise alerts | MEDIUM | 1e-9 difference triggers alert | Epsilon-aware thresholds + rounding |
| 3 | 3.1 | Lost context during repair | MEDIUM | "Why was this zeroed?" unanswerable | Rich JSON repair logs + audit trail |
| 3 | 3.2 | Silent bracket cancel failures | MEDIUM | Partial cancel not visible | Detailed cancel result + lifecycle log |
| 3 | 3.3 | Multiple repair attempts lose history | MEDIUM | No retry count tracking | Attempt tracking + escalation logic |
| 3 | 3.4 | No visibility into phantom positions | MEDIUM | "Which strategy caused desync?" unknown | Per-strategy position snapshot + attribution |
| 3 | 3.5 | Sync check errors silently swallowed | MEDIUM | API failures masked | Health status tracking + retry logic |
| 3 | 3.6 | Partial repair state corruption | LOW | Exception during repair leaves bad state | Snapshot + rollback transactions |
| 4 | 4.1 | No desync metrics/dashboard | LOW | Can't measure system health | Desync counter + daily reports |
| 4 | 4.2 | No market context in desyncs | LOW | Desync pattern unclear | Tag with volume/volatility/session time |

---

## Implementation Roadmap

### Phase 1: Immediate (Week 1)
- [ ] Add desync window suppression (Edge Case 1.1)
- [ ] Implement repair attempt tracking (Edge Case 3.3)
- [ ] Add alert deduplication (Edge Case 2.1)
- [ ] Switch to structured JSON logging for repairs (Edge Case 3.1)

### Phase 2: Short-term (Week 2-3)
- [ ] Add sync check health tracking (Edge Case 3.5)
- [ ] Create bracket lifecycle log (Edge Case 3.2)
- [ ] Implement per-strategy position snapshots (Edge Case 3.4)
- [ ] Add expected desync window annotations (Edge Case 2.2)

### Phase 3: Medium-term (Week 4-6)
- [ ] Add repair transaction rollback (Edge Case 3.6)
- [ ] Implement desync metrics/dashboard (Edge Case 4.1)
- [ ] Create daily desync report (Edge Case 4.1)
- [ ] Add market context to desyncs (Edge Case 4.2)

---

## Testing Strategy

### Unit Tests
```python
def test_desync_window_suppression():
    """First desync in 5min window: alert. Second: suppressed."""

def test_repair_attempt_tracking():
    """Track repair attempts 1, 2, 3... escalate at 5."""

def test_floating_point_epsilon():
    """0.001 difference = no alert. 0.1 difference = warning."""

def test_partial_repair_detection():
    """Repair that doesn't fully resolve sets warning flag."""
```

### Integration Tests
```python
def test_live_desync_to_audit_trail():
    """Simulate desync, repair, verify audit log has full context."""

def test_api_error_resilience():
    """API fails 3x in a row, sync check marks health=FAILED, bot stops trading."""
```

### Manual QA Checklist
- [ ] Desync logs don't fill disk (monitor log growth for 1 day)
- [ ] Alerts don't repeat (same desync shouldn't generate 5 identical alerts)
- [ ] Audit trail is queryable (can grep for specific symbol/time)
- [ ] Expected desyncs don't create false alarms (session end, maintenance)
