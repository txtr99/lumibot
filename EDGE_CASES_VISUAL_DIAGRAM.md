# Position Sync/Repair Edge Cases - Visual Diagrams

Quick visual reference for understanding edge case relationships and system flow.

---

## System Architecture (Current State)

```
┌─────────────────────────────────────────────────────────────────┐
│                      RUN_PORTFOLIO.PY (Main Loop)              │
│                                                                  │
│  Every iteration:                                               │
│  - Execute strategy signals                                    │
│  - Place/cancel orders                                         │
│  - Track virtual positions per strategy                        │
│                                                                  │
│  Every 15 iterations (~30 seconds):                            │
│  - Poll bracket order status                                  │
│  - Clean up orphaned orders                                   │
│  - CHECK_POSITION_SYNC() ← ⚠️ EDGE CASES HERE                 │
│  - REPAIR_POSITION_DESYNC() ← ⚠️ AND HERE                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│              BRACKET_ORDER_MANAGER.PY                           │
│                                                                  │
│  check_position_sync():                                         │
│    1. Fetch exchange positions (API call)                      │
│    2. Calculate virtual positions (from strategy states)        │
│    3. Compare and find discrepancies                           │
│    4. Log results                                              │
│       └─ Problem: 30-50 lines per desync × 6 desyncs/30s       │
│       └─ Problem: No deduplication → alert spam                │
│       └─ Problem: No context → "why?" unanswerable             │
│                                                                  │
│  repair_position_desync():                                     │
│    1. Identify phantom positions (virtual > exchange)          │
│    2. Find strategies with those positions                     │
│    3. Zero out the positions                                   │
│    4. Cancel associated brackets                               │
│    5. Clear processed fills for re-sync                        │
│       └─ Problem: Partial repairs not tracked                  │
│       └─ Problem: Bracket cancel failures silent               │
│       └─ Problem: No retry count → runaway loop                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Edge Case Distribution by Subsystem

```
┌──────────────────────────────────────────────────────────────┐
│ TIER 1: DISK & PERFORMANCE (3 cases)                         │
├──────────────────────────────────────────────────────────────┤
│  1.1: Log Explosion        ← Logging Subsystem               │
│  1.2: Logger Contention    ← Logger + Main Loop              │
│  1.3: Uninitialized Logger ← Logger Config                   │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ TIER 2: ALERT FATIGUE (3 cases)                              │
├──────────────────────────────────────────────────────────────┤
│  2.1: Duplicate Alerts     ← Alert Subsystem (missing!)      │
│  2.2: False Positives      ← Alert Logic (missing context)   │
│  2.3: Floating Point Noise ← Alert Thresholds (wrong)        │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ TIER 3: DEBUGGING & AUDIT (6 cases)                          │
├──────────────────────────────────────────────────────────────┤
│  3.1: Lost Context         ← Logging (unstructured)          │
│  3.2: Silent Failures      ← Bracket Cancel (no result detail)│
│  3.3: Retry Tracking       ← Repair Loop (no attempt count)  │
│  3.4: Strategy Visibility  ← Position Snapshot (missing)     │
│  3.5: Sync Health          ← API Error Handling              │
│  3.6: State Corruption     ← Repair Transaction (no rollback)│
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ TIER 4: OPERATIONAL INTEL (2 cases)                          │
├──────────────────────────────────────────────────────────────┤
│  4.1: No Metrics           ← Metrics Collection              │
│  4.2: Market Context       ← Event Tagging                   │
└──────────────────────────────────────────────────────────────┘
```

---

## Cascading Failure Flow: Election Day Scenario

```
14:30:00 - High volatility, rapid ES trades
           Virtual: +3, Exchange: +1 (API lag?)

           ↓

Sync Check #1
"ES DESYNC: virtual=3, exchange=1"

           ├─ Current: ALERT → Operator sees "POSITION DESYNC"
           │
           └─ Problem 2.1: Same signature fires again at 14:30:30
              Problem 2.1: And again at 14:31:00
              Problem 2.1: And again at 14:31:30
              (Total: 4 identical alerts in 60 seconds)

           ↓

Repair #1 Triggered
Virtual: 3 → 0 (zeroed)
Exchange still 1 (why?)
Logs: "Zeroed 1 strategy, remaining_to_zero=1"

           ├─ Problem 3.1: Why was there excess? API miss? Duplicate order?
           │  No context logged
           │
           └─ Problem 3.3: This is "attempt 1" but no tracking
              Will retry at 14:31:30

           ↓

14:31:30 - Same desync still visible
Sync Check #2
"ES DESYNC: virtual=0, exchange=1"

           ├─ Problem 2.1: ALERT fires again (5th alert)
           │
           └─ Problem 3.3: "Attempt 2" but system doesn't know
              If pattern continues → 5 more attempts
              No escalation (should escalate at attempt 5)

           ↓

Repair #2 Triggered
But virtual is already 0, exchange is 1
Can't repair (nothing to zero)
Logs: "DIRECTION CONFLICT" warning
Requires manual intervention (?)

           ↓

By 14:35:00
- 8 identical "ES DESYNC" alerts in 5 minutes
- Operator: "Clearly noisy, I'll ignore"
- Alert system disabled for DESYNC alerts

           ↓

14:45:00 - REAL BUG
API timeout for 30 seconds (legitimate issue)
Sync check fails silently (no health tracking)

           ├─ Problem 3.5: Sync health not exposed
           │  Bot doesn't know positions are unverified
           │
           └─ Problem 2.1: No alert (alerts disabled from earlier!)
              Operator has no visibility

           ↓

14:45-16:00
Trading continues with:
- Unverified position state
- Unknown if API is working
- No alerts (disabled)
- Log file not readable (50+ lines per sync check)

Result: Unmonitored trading for 75 minutes with broken positions!
```

---

## Fix Layering: Defense in Depth

```
Layer 1: Prevention (Trading Logic)
──────────────────────────────────
Goal: Don't let desync happen
Effort: High (months of work)
Current Status: Not implemented

┌─────────────────────────────────────┐
│ Better fill detection              │
│ Better API error handling          │
│ Better order reconciliation        │
│                                     │
│ Still won't be perfect             │
└─────────────────────────────────────┘

                    ↓

Layer 2: Suppression (Logging/Alerting)
────────────────────────────────────────
Goal: Suppress noise, prevent alert fatigue
Effort: Low (days of work)
Current Status: MISSING ENTIRELY
Priority: TIER 1 - CRITICAL PATH

┌─────────────────────────────────────┐
│ 1.1: Desync window suppression     │
│ 1.2: Batch logging                 │
│ 2.1: Alert deduplication           │
│ 2.2: Expected window tagging        │
│ 2.3: Epsilon thresholds             │
│                                     │
│ Result: Signal-to-noise ↑↑↑         │
└─────────────────────────────────────┘

                    ↓

Layer 3: Repair (Auto-Fix)
──────────────────────────
Goal: Fix 90%+ of desyncs automatically
Effort: Medium (weeks of work)
Current Status: Partially implemented

┌─────────────────────────────────────┐
│ 3.1: JSON context logging           │
│ 3.3: Repair attempt tracking        │
│ 3.4: Per-strategy snapshots         │
│ 3.2: Bracket cancel details         │
│                                     │
│ Result: 95% repair success rate     │
└─────────────────────────────────────┘

                    ↓

Layer 4: Detection (Fail-Safe)
───────────────────────────────
Goal: Stop trading if repairs fail
Effort: Low-Medium (days-weeks)
Current Status: Partially implemented

┌─────────────────────────────────────┐
│ 3.5: Sync health tracking           │
│ 3.6: State transaction rollback     │
│                                     │
│ Result: Can't trade broken state    │
└─────────────────────────────────────┘

                    ↓

Layer 5: Visibility (Post-Incident)
───────────────────────────────────
Goal: Answer "why?" in 10 minutes
Effort: Low (days of work)
Current Status: MISSING

┌─────────────────────────────────────┐
│ 3.1: JSON audit logs                │
│ 4.1: Desync metrics                 │
│ 4.2: Market context tagging         │
│                                     │
│ Result: Can diagnose in minutes     │
└─────────────────────────────────────┘
```

---

## Implementation Sequence

```
WEEK 1 (CRITICAL PATH - Must do)
─────────────────────────────────

Day 1-2: Logging Foundation
  1.1 Add desync window suppression
  1.3 Initialize logger properly
  → Result: Logs don't fill disk overnight

Day 3: Alert Deduplication
  2.1 Add signature matching + dedup dict
  → Result: Same desync doesn't alert 6x

Day 4-5: Context Logging
  3.1 Switch to JSON repair logs
  3.3 Track repair attempt_number
  → Result: Can answer "why was this zeroed?"

  Output: Main loop now has visibility
          Operators stop ignoring alerts
          Disk stops filling

═══════════════════════════════════════════════════════════════

WEEK 2-3 (IMPORTANT - Do next)
──────────────────────────────

  3.5 Sync check health tracking
  3.4 Per-strategy position snapshots
  2.2 Expected desync window annotations
  3.2 Bracket cancel result details

  Output: Distinguishes expected vs. real desyncs
          Better post-incident debugging
          Can detect repair runaway loops

═══════════════════════════════════════════════════════════════

WEEK 4-6 (NICE TO HAVE - Later)
─────────────────────────────────

  3.6 Transaction rollback
  4.1 Desync metrics + dashboard
  4.2 Market context tagging

  Output: Operational intelligence
          Early warning for degradation
          Beautiful dashboards
```

---

## Current vs. Fixed: Side-by-Side Comparison

```
SCENARIO: Desync at market open (high volatility, rapid fills)

CURRENT SYSTEM:
────────────────
14:30:00  Sync detects ES: virtual=7, exchange=5
          Logs (20 lines): "ES DESYNC DETECTED" warnings
          Alert: "POSITION DESYNC" (generic)
          Repair starts

14:30:10  Bracket cancel fails (partial success)
          Logs: "Failed to cancel bracket XYZ" (silent about partial)
          Repair thinks: "success"

14:30:30  Sync check #2 (still desync?)
          Same signature again
          Alert #2 fires: "POSITION DESYNC" (identical to alert #1)
          Operator: "Muted this, not important"

14:31:00  Sync check #3
          Alert #3 fires
          (...)

14:31:30  Sync check #4
          Alert #4 fires
          (...)

By 14:35:00:
- 4 identical alerts
- Operator disables DESYNC alerts
- Logs are 100+ lines long
- Post-mortem: Can't find root cause (no context)

═══════════════════════════════════════════════════════════════

FIXED SYSTEM:
──────────────
14:30:00  Sync detects ES: virtual=7, exchange=5
          Logs (JSON): {"symbol": "ES", "exchange": 5, "virtual": 7,
                        "reason": "phantom_long", "excess": 2}
          Alert: "POSITION DESYNC: phantom long, auto-repair initiated"
          Repair starts
          Tracking: desync_signature="ES_5_7" attempt=1

14:30:10  Bracket cancel returns:
          {success: [id1, id2], failed: [{id3: "already_filled"}]}
          Repair logs: "2 brackets cancelled, 1 already filled"
          Tracking: brackets_cancelled_partial=true

14:30:30  Sync check #2 (still desync?)
          Same signature "ES_5_7" seen recently
          Alert suppression: "Already alerted 5s ago, suppressed"
          Logs (1 line): "[DESYNC-REPEAT] ES phantom, attempt 2"
          No alert fires (deduped)

14:31:00  Sync check #3
          Still "ES_5_7"
          Escalation check: "Attempt 3 of 3 max, next=critical"
          Logs: "Desync persisting 60s, escalating to manual review"
          Alert: "CRITICAL: ES phantom desync failed 3 repair attempts"

By 14:35:00:
- 1 initial alert (actionable)
- 1 escalation alert (after 3 retries)
- 5 log lines total (highly condensed)
- Post-mortem: Full context from JSON logs

Result: Operator noticed immediately, escalated after reasonable retries
        Root cause clear from audit logs
```

---

## Alert State Machine

```
┌────────────────────────────────────────────────────────────┐
│                   Initial Detection                         │
│                   (desync_signature="ES_5_7")              │
│                                                             │
│              ✓ IS_NEW_SIGNATURE()                           │
└────────────────────┬────────────────────────────────────────┘
                     ↓
          ┌──────────────────────┐
          │  SEND ALERT         │
          │  State: INITIAL      │
          │  Time: 14:30:00      │
          │  Attempt: 1          │
          └──────────────────────┘
                     ↓
        ┌────────────────────────────┐
        │  Record signature in       │
        │  recent_alerts_5min        │
        │  Suppress further alerts   │
        │  for 5 minutes             │
        └────────────────────────────┘
                     ↓

    ┌──────────────────────────────────┐
    │  Sync Check Finds Same Desync   │
    │  14:30:30 (30 seconds later)    │
    │                                  │
    │  Is in recent_alerts_5min?       │
    │  ✓ YES                           │
    │  → SUPPRESS (no alert)           │
    │  → Log: "Repeat #2"              │
    └──────────────────────────────────┘
                     ↓

    ┌──────────────────────────────────┐
    │  Sync Check at 14:31:00          │
    │  Still same desync               │
    │  (60 seconds total)              │
    │                                  │
    │  Is attempt_count >= 3?          │
    │  ✓ YES (attempt 3 of 3)          │
    │  → Remove from recent_alerts     │
    │  → ESCALATE to CRITICAL          │
    │  → Alert: "PERSISTENT DESYNC"    │
    │  → State: ESCALATION             │
    └──────────────────────────────────┘
                     ↓

    ┌──────────────────────────────────┐
    │  Human Reviews Alert             │
    │  14:31:05                        │
    │                                  │
    │  Options:                        │
    │  a) Repair succeeded → RESOLVED  │
    │  b) Repair partial → MANUAL      │
    │  c) API issue → MAINTENANCE      │
    │  d) Mark as expected → IGNORE    │
    └──────────────────────────────────┘
```

---

## Testing Flow

```
TEST SCENARIO A: Rapid Repeat Desyncs
──────────────────────────────────────

Setup:
  Mock sync_result with ES: virtual=7, exchange=5
  Call check_position_sync() 10 times with 2-sec interval

Verify:
  ✓ First call: logs 5 lines (full detail)
  ✓ Calls 2-10: log 1 line each (suppressed)
  ✓ Alerts: 1 alert (first), 0 alerts (calls 2-10)
  ✓ Logs total: 14 lines (not 50+)

═══════════════════════════════════════════════════════════════

TEST SCENARIO B: Expected vs. Unexpected Desync
───────────────────────────────────────────────

Setup:
  15:12:00 - Call force_flat()
  15:12:30 - Sync detects virtual=0, exchange=2

Verify:
  ✓ Tagged: "expected: true, reason: session_end"
  ✓ Alert: NOT fired (expected)
  ✓ Logs: "EXPECTED DESYNC during session_end"
  ✓ Next: 15:45 (unrelated API timeout)
  ✓ Alert: FIRED (unexpected, health degraded)

═══════════════════════════════════════════════════════════════

TEST SCENARIO C: Partial Bracket Cancel
─────────────────────────────────────────

Setup:
  Repair tries to cancel 2 brackets
  SL cancellation succeeds
  TP cancellation fails (already filled)

Verify:
  ✓ Cancel result: {success: [sl_id], failed: [{tp_id: "filled"}]}
  ✓ Repair logs: "1 bracket cancelled, 1 already filled"
  ✓ Audit: "brackets_cancelled_partial: true"
  ✓ Next sync: Knows TP bracket still exists
```

---

## Summary: Before & After

```
BEFORE FIXES:
─────────────
Disk usage:       Unbounded (can fill overnight)
Alert quality:    30% actionable (70% noise)
Alert frequency:  10+ per day (same desync)
MTTR:            60+ minutes (no context)
Repair rate:      Unknown (no tracking)
Audit trail:      Impossible (unstructured)

User experience:  "System works but I don't understand why"
Ops experience:   "I ignore these alerts, they're always wrong"
Post-incident:    "We'll never figure out root cause"

═══════════════════════════════════════════════════════════════

AFTER FIXES:
────────────
Disk usage:       < 100MB/day (predictable)
Alert quality:    90% actionable (10% noise)
Alert frequency:  1-2 per real issue
MTTR:            < 10 minutes (full context)
Repair rate:      95% success in 2 attempts
Audit trail:      Full reconstruction possible

User experience:  "System works and I understand why"
Ops experience:   "Alerts mean something, I take them seriously"
Post-incident:    "We can diagnose in 10 minutes from JSON logs"
```
