# Position Sync/Repair Edge Cases - Complete Index

This is the master index for all edge case documentation created during the position sync/repair reliability initiative.

---

## Documents Overview

### 1. **EDGE_CASES_QUICK_REFERENCE.md** ⭐ START HERE
**Format:** 1-page cheat sheet
**Length:** 233 lines
**Purpose:** Quick scan of all 18 edge cases with priority tiers and code snippets

**Key Sections:**
- Fire Risk (TIER 1): Disk fills, logger contention, uninitialized logger
- Alert Fatigue (TIER 2): Duplicate alerts, false positives, noise
- Missing Context (TIER 3): Lost context, silent failures, retry tracking
- Scenarios: Election Day, Session End, 3-Day Debug
- Implementation Priority: Week 1, Week 2, Week 4+

**When to use:** Morning standup, quick decision-making, "remind me which edge case this is"

---

### 2. **EDGE_CASES_POSITION_SYNC_LOGGING.md** ⭐ DETAILED SPEC
**Format:** Full specification with code examples
**Length:** 851 lines
**Purpose:** Complete reference for edge cases, with detailed scenarios and recommendations

**Key Sections:**
- TIER 1: Disk & Performance Impact (High Risk)
  - 1.1: Log explosion from repeat desyncs
  - 1.2: Logger lock contention
  - 1.3: Uninitialized logger crashes
- TIER 2: Alert Fatigue & False Positives (Medium Risk)
  - 2.1: Duplicate alerts
  - 2.2: Expected desyncs during maintenance
  - 2.3: Floating point noise
- TIER 3: Debugging & Audit Trail (Medium Risk)
  - 3.1-3.6: Context preservation, bracket cancellation, retry tracking, etc.
- TIER 4: Operational Intelligence (Lower Risk)
  - 4.1-4.2: Metrics, dashboards, market context
- Implementation roadmap: 3-phase plan (Week 1 → Month 2)
- Testing strategy: Unit, integration, manual QA

**When to use:** Detailed implementation, design review, reference during coding

---

### 3. **EDGE_CASES_ROOT_CAUSE_MAP.md** ⭐ DIAGNOSIS & DEBUGGING
**Format:** Symptom → Root Cause → Edge Case mapping
**Length:** 420 lines
**Purpose:** Trace real-world symptoms back to edge cases, understand cascading failures

**Key Sections:**
- Symptom → Root Cause matrix (5 major symptoms)
  - "Disk filled overnight"
  - "Operators ignore all DESYNC alerts"
  - "Why was position zeroed?"
  - "Bot suddenly stopped trading"
  - "Repair loop never ended"
- Failure cascade examples: A, B, C with detailed walkthroughs
- Edge case interactions: Multiplicative effects
- Defensive layers: 5-layer approach (prevent → suppress → repair → detect → debug)
- Testing scenarios: A, B, C with specific test steps
- Prevention vs. Handling: Cost/benefit analysis

**When to use:** Post-incident debugging, understanding why a fix matters, designing test cases

---

### 4. **EDGE_CASES_VISUAL_SCENARIOS.md**
**Format:** Real-world scenarios with visual timelines
**Length:** 503 lines
**Purpose:** Paint a picture of how edge cases manifest in real trading

**Key Sections:**
- Scenario breakdowns with timestamps
- Visual timelines showing alert/repair sequence
- Before/after comparisons (current vs. fixed system)
- Real examples (election day volatility, session end, etc.)

**When to use:** Convincing stakeholders, understanding user impact, design decisions

---

### 5. **EDGE_CASES_SUMMARY.md**
**Format:** 1-page executive summary
**Length:** 343 lines
**Purpose:** High-level overview for decision makers

**Key Sections:**
- Problem statement
- 18 edge cases grouped by impact
- Business impact of each tier
- ROI of fixes
- Implementation timeline

**When to use:** Leadership briefing, project planning, roadmap decisions

---

### 6. **EDGE_CASES_POSITION_SYNC.md**
**Format:** Older detailed spec (from previous session)
**Length:** 505 lines
**Purpose:** Archived documentation on position sync logic

**Note:** Overlaps with EDGE_CASES_POSITION_SYNC_LOGGING.md. Use the newer document.

---

### 7. **EDGE_CASES_MULTI_ACCOUNT_MULTI_INSTANCE.md**
**Format:** Older spec on multi-account scenarios
**Length:** 616 lines
**Purpose:** Archived documentation on account-level issues

**Note:** From previous session. May be relevant if expanding to multiple accounts.

---

### 8. **EDGE_CASES_RECOVERY_RESTART.md**
**Format:** Older spec on recovery
**Length:** 494 lines
**Purpose:** Archived documentation on bot restart scenarios

**Note:** From previous session. Relevant for crash recovery + restart logic.

---

## Reading Paths by Role

### For Product Manager / Tech Lead
1. Start: QUICK_REFERENCE.md (understand what edge cases exist)
2. Context: EDGE_CASES_SUMMARY.md (business impact)
3. Deep dive: ROOT_CAUSE_MAP.md (understand cascading failures)
4. Decision: POSITION_SYNC_LOGGING.md (Phases 1-2 priorities)

### For Implementing Engineer
1. Start: POSITION_SYNC_LOGGING.md (full spec)
2. Reference: QUICK_REFERENCE.md (code snippets, priorities)
3. Testing: ROOT_CAUSE_MAP.md (test scenarios)
4. Debugging: ROOT_CAUSE_MAP.md (symptom → fix mapping)

### For QA / Testing
1. Start: QUICK_REFERENCE.md (understand 18 edge cases)
2. Test Plan: ROOT_CAUSE_MAP.md (Testing Scenarios A, B, C)
3. Reference: POSITION_SYNC_LOGGING.md (Unit test recommendations)

### For On-Call / Operations
1. Quick ref: QUICK_REFERENCE.md (remember which edge case is which)
2. Diagnosis: ROOT_CAUSE_MAP.md (symptom → root cause)
3. Response: ROOT_CAUSE_MAP.md (cascading failure understanding)

---

## Implementation Checklist

### Phase 1: Week 1 (Critical Path) - Log Explosion + Alert Fatigue
- [ ] Read: QUICK_REFERENCE.md + POSITION_SYNC_LOGGING.md (Edge Cases 1.1-2.1)
- [ ] Design: Desync suppression window (5-minute window, signature matching)
- [ ] Code: Add DESYNC_SUPPRESSION_WINDOW dict + should_alert_on_desync()
- [ ] Code: Add alert deduplication with recent_alerts_5min tracking
- [ ] Code: Switch repair logging to JSON format
- [ ] Code: Add repair_attempt_count tracking
- [ ] Test: Run Test Scenario A from ROOT_CAUSE_MAP.md
- [ ] Verify: Log size stays < 50MB/day

### Phase 2: Week 2-3 (Important) - Missing Context
- [ ] Read: POSITION_SYNC_LOGGING.md (Edge Cases 3.1-3.5)
- [ ] Code: Implement position_repairs_audit.jsonl logging
- [ ] Code: Add per-strategy position snapshots pre-repair
- [ ] Code: Implement bracket cancel result details
- [ ] Code: Add sync check health tracking
- [ ] Code: Annotate expected desyncs (session end, maintenance)
- [ ] Test: Run Test Scenario B (Expected vs. Real desyncs)
- [ ] Test: Run Test Scenario C (Partial repair detection)

### Phase 3: Week 4+ (Nice to Have) - Observability
- [ ] Code: Implement repair transaction rollback
- [ ] Code: Create desync metrics (counters, gauges)
- [ ] Code: Generate daily desync report
- [ ] Code: Add market context to desyncs (volume, IV, session time)
- [ ] Dashboard: Visualize desync trends
- [ ] Alert: Create anomaly detection rules

---

## Key Files to Modify

### brackets_order_manager.py
- `check_position_sync()` - Lines 1953-2070
  - Add: Desync window suppression
  - Add: Health status tracking
  - Add: Expected desync annotations

- `repair_position_desync()` - Lines 2072-2256
  - Add: Repair attempt tracking
  - Add: Per-strategy position snapshots
  - Add: Bracket cancel result details
  - Add: Rollback transaction
  - Change: Logging to JSON format

### run_portfolio.py
- Sync check loop - Lines 2008-2055
  - Add: Alert deduplication
  - Add: Escalation logic
  - Change: Alert only on new desyncs

### New Files to Create
- `tools/desync_audit_logger.py` - Structured JSON logging for repairs
- `tools/desync_metrics.py` - Counters + reporters for desync trends
- `tools/alert_deduplicator.py` - Alert signature matching + suppression
- `tests/test_edge_cases_*.py` - Test scenarios A, B, C

---

## Core Concepts

### Desync Signature
Unique identifier for a specific desync event:
```python
signature = f"{symbol}_{exchange_qty}_{virtual_qty}"
# Example: "ES_5_7" = ES desync where exchange has 5, virtual has 7
```

### Alert Deduplication
Only alert if this signature hasn't been seen in last 5 minutes:
```python
if signature in recent_alerts_5min and time.time() - recent_alerts_5min[signature] < 300:
    return False  # Suppress duplicate
return True  # New alert
```

### Repair Attempt Tracking
Track how many times we've tried to fix the same desync:
```python
if attempt_number >= 5:
    escalate_to_manual_review(desync_sig)
```

### Floating Point Epsilon
Only consider difference meaningful if > 0.001 contracts:
```python
ALERT_THRESHOLD = 0.001
if abs(diff) < ALERT_THRESHOLD:
    return "noise"  # No alert
```

### Expected Desync Windows
Desyncs that are normal during specific times/operations:
```python
EXPECTED_DESYNC_WINDOWS = {
    "force_flat": {"duration_seconds": 30},
    "session_end": {"duration_seconds": 60},
    "maintenance": {"duration_seconds": 120}  # TopStepX 3pm
}
```

---

## Questions to Answer Before Starting Implementation

1. **Current Disk Usage:** How much space does bot.log consume per day? (baseline)
2. **Alert Infrastructure:** Email? Slack? PagerDuty? (determines alert dedup target)
3. **Audit Requirements:** SOC2/compliance? How long to retain logs? (determines retention)
4. **Session Schedule:** Always 15:10-17:00 CT maintenance? (determines expected windows)
5. **Acceptable Desync Rate:** What's "normal" per day? (determines metrics thresholds)
6. **Team Capacity:** 1 person (6 weeks)? 2 people (3 weeks)? (determines Phase 2-3 timing)

---

## Success Metrics

After implementing all phases:

1. **No Disk Fills:** Log size stays < 100MB/day (currently unbounded)
2. **Alert Quality:** 90% of alerts are actionable (currently 30%)
3. **MTTR:** Mean time to diagnose desync < 10min (currently 60min+)
4. **Repair Rate:** 95% of desyncs auto-repair within 2 attempts (currently unknown)
5. **False Positive Rate:** < 1 false alert per day (currently 10+/day)
6. **Audit Trail:** Can reconstruct any desync event from logs (currently impossible)

---

## Related Documentation

### Core System
- `lumibot/tools/virtual_position_tracker.py` - Virtual position tracking
- `tools/bracket_order_manager.py` - Bracket + sync/repair logic
- `custom_portfolio/strategies/run_portfolio.py` - Main loop

### User Documentation
- `CLAUDE.md` - Architecture overview (section: Bracket Order Management)
- `pnl_validation_workflow_notes.md` - P&L validation + bracket orders
- `custom_portfolio/README.md` - Portfolio system overview

---

## Document Maintenance

**Last Updated:** 2025-11-26
**Created By:** Claude Code analysis
**Status:** Ready for implementation Phase 1

**To Update:** When new edge cases discovered or implementation phases completed, update this index and relevant linked documents.

---

## Quick Reference: 18 Edge Cases at a Glance

| Tier | Count | Category | Risk | Examples |
|------|-------|----------|------|----------|
| 1 | 3 | Disk & Performance | HIGH | Log explosion, logger contention, crash |
| 2 | 3 | Alert Fatigue | HIGH | Duplicates, false positives, noise |
| 3 | 6 | Debugging & Audit | MEDIUM | Lost context, silent failures, retry tracking |
| 4 | 2 | Observability | LOW | Metrics, market context |

**Implementation Time Estimate:**
- Phase 1 (TIER 1+2): 40-50 hours (1.5 weeks, 1 person)
- Phase 2 (TIER 3): 30-40 hours (1 week, 1 person)
- Phase 3 (TIER 4): 20-30 hours (optional, lower priority)

**Total:** 90-120 hours (4-6 weeks, 1 person) or (2-3 weeks, 2 people)
