# Futures Contract Rollover & Expiration - Brainstorm Reading Guide

## Overview

This brainstorm explores **contract rollover and expiration edge cases** in a multi-strategy futures trading system where virtual positions tracked locally net to a single exchange position, and repair logic zeros phantom positions when exchange shows fewer contracts.

**Generated:** November 26, 2025
**Focus:** ES (S&P 500), NQ (Nasdaq), GC (Gold) contract rollovers and expirations

---

## Document Map

### 1. **BRAINSTORM_SUMMARY.md** ← START HERE
**Length:** ~500 lines | **Read Time:** 10 minutes

**Purpose:** Executive summary and quick reference

**Contains:**
- System architecture overview
- 9 critical edge cases (one-paragraph each)
- 5 core failures
- 7 concrete scenarios with numbers
- 3 must-know questions
- 3 recommended solutions
- Risk matrix
- Implementation roadmap

**Best For:** Getting oriented, understanding scope, planning next steps

---

### 2. **ROLLOVER_AND_EXPIRATION_EDGE_CASES.md** ← MAIN DOCUMENT
**Length:** ~700 lines | **Read Time:** 30 minutes

**Purpose:** Detailed analysis of contract rollover/expiration edge cases

**Contains:**
- 13 detailed scenarios (paragraphs + analysis)
- What goes wrong and why
- Unknown behaviors with ProjectX
- Impact assessment per scenario
- Multi-strategy complications
- Calendar conflicts
- Repair logic failure modes
- Practical testing recommendations

**Best For:** Understanding technical details, comprehensive risk analysis

**Key Sections:**
1. Basic mechanics & unknowns
2. Single strategy rollover
3. Multiple strategies on same symbol
4. Stale entry price problems
5. Platform maintenance timing
6. Contract expiration handling
7. Cross-contract spreads
8. Time-based exits during rollover
9. Repair logic failures (3 scenarios)
10. Recommendations & unknowns
11. Summary table (risk by scenario)
12. Next steps

---

### 3. **ROLLOVER_SCENARIOS_DETAILED.md** ← NUMERICAL DEEP DIVE
**Length:** ~800 lines | **Read Time:** 40 minutes

**Purpose:** Concrete scenarios with actual prices and P&L calculations

**Contains:**
- 8 detailed scenarios with timeline, prices, and calculations:
  - A: Silent phantom position (P&L drift +$600 error)
  - B: Repair on stale data (incorrect position zeroed)
  - C: Multi-strategy divergence (ES/NQ/GC simultaneously)
  - D: Two-legged rollover partial fill (stranded positions)
  - E: Calendar conflict (force-flat during rollover)
  - F: P&L attribution breakdown (can't track realized vs unrealized)
  - G: Consecutive expirations (NQ→ES→GC cascade)
  - H: Backtest vs live divergence (non-repeatable results)

- Per-scenario breakdown:
  - Setup with current positions
  - Event timeline with exchange behavior
  - System state after event
  - What could go wrong
  - Quantified impacts

- 4 practical tests to run

**Best For:** Understanding concrete failure scenarios, P&L impact, testing guidance

**Key Sections:**
- Scenario A-H (each ~50-100 lines)
- "Where Repair Logic Fails" summary table
- Practical tests
- P&L calculations throughout

---

### 4. **IMPLEMENTATION_GAPS_AND_SOLUTIONS.md** ← TECHNICAL SOLUTIONS
**Length:** ~900 lines | **Read Time:** 45 minutes

**Purpose:** Identify implementation gaps and provide prioritized solutions

**Contains:**

**Part 1: Current Architecture Limitations**
- VirtualPositionTracker (symbol-only design)
- BracketOrderManager (contract-unaware repair)
- MultiStrategyExecutorEnhanced (data source ambiguity)
- TradingCalendar (no expiration dates)

**Part 2: 7 Critical Gaps (each gap detailed with current vs needed)**
1. No contract lifecycle metadata
2. VirtualPositionTracker can't track contract months
3. No rollover detection
4. No contract age awareness
5. Position sync can't distinguish contracts
6. Bracket order recreation after rollover
7. No stale entry price detection

**Part 3: 5 Recommended Solutions (prioritized)**
1. Contract month tracking in metadata (QUICK WIN)
   - Effort: Low (1-2 hrs)
   - Code examples provided
2. Extend VirtualPositionTracker (MEDIUM)
   - Effort: Medium (3-5 hrs)
   - Multiple implementation options
3. Add rollover detection (MEDIUM)
   - Effort: Medium (4-6 hrs)
   - Pseudocode with class/method structure
4. Contract expiry warnings in calendar (LOW)
   - Effort: Low (1-2 hrs)
5. Improve repair logic (HIGH)
   - Effort: High (8-12 hrs)
   - Complex refactoring

**Part 4: Testing Strategy**
- Test 1: Expiry metadata
- Test 2: Multi-contract sync
- Test 3: Rollover detection
- Test 4: Bracket survival
- Test 5: P&L accuracy

**Part 5: Rollout Plan**
- 5 phases over 5 weeks
- Tasks per phase
- Dependencies

**Part 6: Risk Mitigation**
- 5 key risks + mitigations

**Part 7: Questions to Answer**
- 4 critical unknowns with ProjectX

**Best For:** Implementation planning, code structure, test design, timeline estimation

---

## Reading Paths by Role

### For Traders / Risk Managers
1. Start: **BRAINSTORM_SUMMARY.md** (risk matrix section)
2. Then: **ROLLOVER_SCENARIOS_DETAILED.md** (concrete examples)
3. Focus: P&L impact, failure modes, what can go wrong

### For Developers / Engineers
1. Start: **ROLLOVER_AND_EXPIRATION_EDGE_CASES.md** (understand scope)
2. Then: **IMPLEMENTATION_GAPS_AND_SOLUTIONS.md** (solutions + code)
3. Reference: **ROLLOVER_SCENARIOS_DETAILED.md** (for tests)

### For System Architects
1. Start: **BRAINSTORM_SUMMARY.md** (5 core failures)
2. Then: **IMPLEMENTATION_GAPS_AND_SOLUTIONS.md** (architecture limitations)
3. Then: **ROLLOVER_AND_EXPIRATION_EDGE_CASES.md** (detailed analysis)
4. Focus: Design decisions, trade-offs, implementation options

### For QA / Testers
1. Start: **ROLLOVER_SCENARIOS_DETAILED.md** (test scenarios A-H)
2. Then: **IMPLEMENTATION_GAPS_AND_SOLUTIONS.md** (testing strategy section)
3. Reference: **ROLLOVER_AND_EXPIRATION_EDGE_CASES.md** (edge cases to test)

### For Project Managers
1. Start: **BRAINSTORM_SUMMARY.md** (overview + roadmap)
2. Then: **IMPLEMENTATION_GAPS_AND_SOLUTIONS.md** (rollout plan section)
3. Reference: **ROLLOVER_AND_EXPIRATION_EDGE_CASES.md** (for scope understanding)

---

## Key Takeaways by Document

### BRAINSTORM_SUMMARY
- **9 edge cases** to consider
- **5 core failures** of current system
- **Multiple scenarios** with quantified impact
- **3 questions** blocking implementation
- **3 solutions** with relative effort/impact

### ROLLOVER_AND_EXPIRATION_EDGE_CASES
- **13 comprehensive scenarios** exploring all angles
- **"What we don't know" section** (critical unknowns)
- **"Why this happens" explanations** (root causes)
- **Risk by scenario table** (severity + likelihood)
- **Practical next steps** (5 recommendations)

### ROLLOVER_SCENARIOS_DETAILED
- **Concrete P&L calculations** showing impact
- **Scenario A:** $600 P&L error from stale entry price
- **Scenario B:** Phantom position repair cascades
- **Scenario C:** Three expirations in one week
- **Scenario D-H:** Other failure modes with numbers

### IMPLEMENTATION_GAPS_AND_SOLUTIONS
- **Architecture limitations** of each component
- **Gap 1-7** with current state vs. needed state
- **Solution 1-5** with effort/impact estimates
- **Testing strategy** (5 test cases)
- **5-week rollout plan** with phases
- **Risk mitigation** for each major risk

---

## Critical Insights

### 1. **System is Contract-Blind**
- Tracks by symbol only ("ES") not contract month ("ESZ25")
- Can't represent spread positions (long Z25, short H26)
- Rollover events are invisible

### 2. **Repair Logic Dangerous Without Context**
- Zeros "phantom" positions based on stale data
- Can't distinguish which contract to zero
- Acts immediately, irreversibly

### 3. **Entry Prices Become Stale**
- After rollover, entry price is from old contract
- All P&L calculations wrong
- Can't separate realized (old contract) from unrealized (new)

### 4. **Three Unknowns Block Implementation**
- Does ProjectX auto-roll? (Unknown)
- How does data source handle rollover? (Unknown)
- Can we detect rollover? (No mechanism currently)

### 5. **Cascading Failures Possible**
- Multiple expirations (NQ, ES, GC) in same week
- Each triggers repair logic
- Audit trail becomes impossible to trace
- Can't verify repairs were correct

---

## Quick Reference: Scenarios Summary

| ID | Scenario | Root Cause | Impact |
|----|----------|-----------|--------|
| A | Stale entry price | No contract tracking | P&L wrong by $250-600 |
| B | Repair on stale data | Race condition | Position incorrectly zeroed |
| C | Multi-strategy divergence | No coordination | Positions on wrong contracts |
| D | Partial fill on rollover | Execution dependency | Stranded positions |
| E | Calendar conflict | No contract lifecycle | Position stuck at close |
| F | P&L attribution broken | No per-contract tracking | Can't attribute P&L |
| G | Consecutive expirations | Cascading repairs | Audit trail unclear |
| H | Backtest/live divergence | Rollover not simulated | Non-repeatable results |

---

## Questions to Resolve First

**Before implementing solutions, clarify:**

1. **ProjectX auto-rollover behavior**
   - Does it auto-roll positions? When? To which contract?
   - Can we detect it programmatically?

2. **Data source behavior**
   - When we request "ES" data, which contract?
   - Does it auto-switch at rollover?
   - Can we specify contract explicitly?

3. **Position reporting**
   - Does ProjectX show positions per-contract or aggregated?
   - Can we query historical positions after expiry?

4. **Order routing**
   - When we submit order for "ES", which contract?
   - What happens if we submit for expired contract?

---

## Recommended Next Actions

### Immediate (This Week)
1. [ ] Review all 4 documents
2. [ ] Clarify 4 unknowns with ProjectX/TopStepX team
3. [ ] Schedule design review with stakeholders

### Short Term (Next 2 Weeks)
1. [ ] Prioritize solutions based on risk/impact
2. [ ] Start with Solution 1 (metadata) as quick win
3. [ ] Design Solution 2-3 (tracking + detection)

### Implementation (Weeks 3-7)
1. [ ] Execute 5-week rollout plan
2. [ ] Build solutions incrementally
3. [ ] Heavy testing near expirations
4. [ ] Monitor in paper trading first

---

## File Sizes & Estimated Read Time

| Document | Size | Read Time | Audience |
|----------|------|-----------|----------|
| BRAINSTORM_SUMMARY | 12 KB | 10 min | Everyone |
| ROLLOVER_AND_EXPIRATION_EDGE_CASES | 18 KB | 30 min | Risk analysts, architects |
| ROLLOVER_SCENARIOS_DETAILED | 19 KB | 40 min | Developers, QA |
| IMPLEMENTATION_GAPS_AND_SOLUTIONS | 27 KB | 45 min | Developers, architects |
| **TOTAL** | **76 KB** | **~2 hours** | Comprehensive review |

---

## Document Interdependencies

```
BRAINSTORM_SUMMARY
    ├─ Mentions all 9 scenarios
    ├─ Links to detailed analysis
    └─ Points to solutions

ROLLOVER_AND_EXPIRATION_EDGE_CASES (Main document)
    ├─ Deep dive on 13 scenarios
    ├─ Explains failures
    ├─ Lists unknowns
    └─ Recommends next steps

ROLLOVER_SCENARIOS_DETAILED
    ├─ Numerical examples of scenarios
    ├─ P&L calculations
    ├─ Test recommendations
    └─ Cascade analysis

IMPLEMENTATION_GAPS_AND_SOLUTIONS
    ├─ Addresses gaps mentioned above
    ├─ Provides code examples
    ├─ Testing strategy
    ├─ Rollout plan
    └─ Risk mitigation
```

---

## How to Use This Brainstorm

### For Planning
1. Read BRAINSTORM_SUMMARY (10 min)
2. Review risk matrix and roadmap
3. Identify quick wins (metadata solution)
4. Estimate effort for each phase

### For Implementation
1. Review IMPLEMENTATION_GAPS_AND_SOLUTIONS
2. Study code examples
3. Use testing strategy as checklist
4. Reference scenarios during development

### For Validation
1. Use ROLLOVER_SCENARIOS_DETAILED for test cases
2. Compare with ROLLOVER_AND_EXPIRATION_EDGE_CASES
3. Verify all risks mitigated
4. Check off rollout plan tasks

### For Handoff
1. Provide BRAINSTORM_SUMMARY to new team members
2. Deep dive with ROLLOVER_AND_EXPIRATION_EDGE_CASES
3. Use IMPLEMENTATION_GAPS_AND_SOLUTIONS as development guide
4. Track progress against rollout plan

---

**Total Pages:** ~2,400 lines of analysis + code
**Total Scenarios:** 21 (13 detailed + 8 numerical examples)
**Total Solutions:** 5 (with code, tests, timeline)
**Unknowns Identified:** 12+ (blocking implementation)
**Risks Catalogued:** 8 (with mitigations)

---

**Status:** Brainstorm Complete - Ready for Review and Implementation Planning
