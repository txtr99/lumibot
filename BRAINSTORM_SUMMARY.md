# Futures Rollover & Expiration Edge Cases - Brainstorm Summary

**Focus:** Contract Rollover and Expiration in Multi-Strategy Futures Trading System

---

## What Was Analyzed

### System Architecture
- **VirtualPositionTracker:** Tracks positions independently of broker, assumes market orders fill immediately
- **BracketOrderManager:** Manages OCO-like bracket orders, performs position sync/repair checks
- **MultiStrategyExecutorEnhanced:** Runs 30+ strategies, shares data infrastructure, tracks per-strategy P&L
- **TradingCalendar:** Enforces session restrictions and platform maintenance windows
- **ProjectX Broker:** TopStepX trading platform with unreliable position API (hence virtual tracking)

### Key Constraint
- **Symbol-only tracking:** VirtualPositionTracker uses `symbol="ES"` only, no contract month (Z25, H26, etc.)
- **Repair logic:** Zeros phantom positions when exchange < virtual, assuming market orders fill immediately

---

## 9 Critical Edge Cases Identified

### 1. **Silent Phantom Position After Auto-Rollover**
- Exchange auto-rolls position from Z25 → H26
- Virtual tracker doesn't detect this (sees symbol "ES" as atomic)
- Entry price becomes stale and all P&L calculations wrong
- Sync check doesn't catch it (quantity matches, but on wrong contract)

### 2. **Repair Logic Zeroes Phantom on Stale Exchange Data**
- Exchange feed drops connection briefly
- Broker assumes position was liquidated as safety measure
- Sync check detects "phantom" (actually still exists)
- Repair logic zeros the virtual position irreversibly
- Feed reconnects, now we have opposite mismatch

### 3. **Multi-Strategy Rollover Divergence**
- Strategy 1 (ES) rolls automatically with exchange
- Strategy 2 (NQ) expired days ago but virtual tracker didn't notice
- Virtual net shows "ES: -1 NET" but actually holds ESZ25 and ESH26 simultaneously
- They're not fungible! Can't net them

### 4. **Two-Legged Rollover with Partial Fill**
- Strategy tries to roll: SELL 1 ESZ25, then BUY 1 ESH26
- Close order fills only 0.6 contracts
- Open order fills 1.0 contract (reversal)
- Virtual net shows 0.4 LONG but split across Z25 and H26
- Can't correctly track or close this position

### 5. **Platform Maintenance During Rollover Window**
- ESZ25 rollover window: 14:30-15:15 CT
- Platform maintenance starts: 15:10 CT
- Force-flat deadline: 15:50 CT
- Order to close ESZ25 gets rejected (contract halted during rollover)
- Retry order routed to H26, but we're flat on Z25
- At 15:50, platform closes position we couldn't manage

### 6. **Contract Expiration While Position Held**
- ESZ25 expires Dec 28, position still open
- Exchange auto-closes at settlement price
- Virtual tracker doesn't know settlement happened
- Sync check detects "phantom"
- Repair zeros the position (correct action, but silent)
- No one captures the settlement P&L or reports to strategy

### 7. **Stale Entry Price After Rollover**
- Enter at 5900 on ESZ25
- Rollover: Z25 settles @ 5910, H26 opens @ 5905
- Virtual entry price: still 5900
- P&L calculation: (current - 5900) includes rollover slippage mix
- Can't separate realized (Z25) from unrealized (H26) P&L
- Attribution reports become meaningless

### 8. **Consecutive Expirations (ES, then NQ, then GC)**
- NQ expires Dec 19 (before ES/GC)
- NQ position auto-closed, but virtual tracker doesn't know
- Repair zeros the phantom NQ
- Days later ES expires
- And GC expires
- Three separate repair events, three different root causes
- Audit trail becomes impossible to trace

### 9. **Repair Logic Can't Distinguish Contracts**
- Exchange has: Z25 +1, H26 -1, net 0
- Virtual: ES 0 (correctly)
- But virtual doesn't know the internals
- If we need to close Z25 (expiring tomorrow), system can't identify it
- Repair logic only sees symbol, not contract month
- Can't selectively close one contract while keeping another

---

## 5 Core Failures

### Failure 1: No Contract Month Tracking
```
Current: positions["ES"] = VirtualPosition(qty=+1, entry=5900)
Problem: Can't represent +1 ESZ25 and -1 ESH26 simultaneously
Impact:  Spread positions collapse, rollover invisible
```

### Failure 2: No Rollover Detection
```
Current: Sync checks only verify quantity matches
Problem: Doesn't know position physically moved to different contract
Impact:  Entry prices become stale, P&L calculations wrong
```

### Failure 3: No Contract Lifecycle Awareness
```
Current: TradingCalendar enforces trading hours, not expiration dates
Problem: System doesn't know which contracts are "too old" to trade
Impact:  Can get stuck with positions on expired contracts
```

### Failure 4: Repair on Wrong Data
```
Current: check_position_sync() compares totals per symbol
Problem: Can't distinguish which contract the exchange position is on
Impact:  Repair might zero wrong contract or wrong strategy
```

### Failure 5: P&L Attribution Breaks
```
Current: Entry/exit prices tied to positions, not contracts
Problem: Position spans contracts, entry price meaningless
Impact:  Can't attribute P&L to strategies correctly
```

---

## 7 Concrete Scenarios with Numbers

### Scenario A: Silent P&L Drift
```
Entry: LONG 1 ES @ 5900 (Dec 15)
Rollover: Z25→H26 on Dec 22 @ 5905/5910
Exit: 5920

Calc P&L: (5920 - 5900) * 50 = $1000
Actual P&L:
  - Z25 portion: (5910 - 5900) * 50 = $500
  - H26 portion: (5920 - 5905) * 50 = $750
  - Total: $1250
Error: $250 discrepancy
```

### Scenario B: Dual Contract Spread
```
Virtual:  ES = +1 NET
Physical: +2 ESZ25, -1 ESH26

Exchange Query: "ES" = +1
Sync: ✓ Appears synced

Reality: Z25 and H26 aren't fungible
         When Z25 expires, system loses +2 without knowing it
         H26 position survives but is now wrong
```

### Scenario C: Three Expirations in One Week
```
Dec 19: NQ expires → repair zeros phantom +1
Dec 22: ES rolling → entry price goes stale
Dec 23: GC rolling → entry price goes stale
Result: Multiple repair events, P&L tracking broken
```

### Scenario D: Cascade Mismatch
```
T1: Virtual +1, Exchange 0 (stale data) → Repair zeros it
T2: Feed reconnects, Exchange shows +1, Virtual 0
T3: Now we can't repair in opposite direction
    "Untracked position" warning
    Manual intervention required
```

### Scenario E: Bracket Orphan
```
Entry: LONG 1 ESZ25 @ 5900
Brackets: TP 5910, SL 5890 (on Z25)
Rollover: Z25 halted, H26 opens
Bracket Manager: Can't find brackets (on expired Z25)
Result: Position on H26 without any exit rules
```

### Scenario F: Platform Force-Flat
```
Position: LONG 1 ES
Calendar: force_flat at 15:50 CT
Exchange: Rolls at 14:30-15:15
Close order: Rejected during rollover
Result: Position liquidated at force_flat time at market price
        Strategy unaware it happened
        No way to know if close attempted or failed
```

### Scenario G: Backtest/Live Mismatch
```
Backtest: Enter ESZ25 @ 5900, exit @ 5910 = $500
Live: Enter ESZ25 @ 5900
      Rollover (rollover slippage: ~5 points)
      Exit ESH26 @ 5915
      Net still $500 but path included rollover event

Backtest: Non-repeatable (no rollover in test)
Live: Rollover happened, path different
```

---

## 3 Must-Know Questions

### 1. ProjectX Auto-Rollover
- **Does it auto-roll positions?** (Unknown)
- **Can we detect it?** (No current mechanism)
- **What contract do they roll to?** (Unknown)
- **Does it appear as same symbol or different?** (Unknown)

### 2. Data Source Routing
- **When we request "ES" data, which contract?** (Unknown)
- **Does it auto-switch at rollover?** (Unknown)
- **Can we specify contract month explicitly?** (Probably not in current API)
- **What if we ask for expired contract?** (Unknown)

### 3. Exchange Position Reporting
- **Does ProjectX show positions per contract or aggregated?** (Unknown)
- **Can we query historical positions after expiry?** (Unknown)
- **Can we see settlement prices?** (Unknown)
- **Are rolled positions atomic or visible as two separate positions?** (Unknown)

---

## 3 Recommended Solutions

### Solution 1: Contract Month Metadata (LOW EFFORT)
```python
# Add to futures_metadata.py
def get_contract_expiry_date(symbol, contract_month):
def is_contract_expired(symbol, contract_month):
def days_until_expiry(symbol, contract_month):

# Enables: Know when contracts are expiring, avoid trading them
```

### Solution 2: Multi-Contract Tracking (MEDIUM EFFORT)
```python
# Extend VirtualPositionTracker
positions[("ES", "Z25")] = VirtualPosition(...)
positions[("ES", "H26")] = VirtualPosition(...)

# Enables: Distinguish contracts, detect rollovers
```

### Solution 3: Rollover Detection & Recovery (HIGH EFFORT)
```python
# Add to BracketOrderManager
def detect_rollover_event(symbol) → RolloverEvent
def _process_rollover(event):
  - Update tracker with new contract
  - Recreate brackets on new contract
  - Capture settlement prices

# Enables: Automatic recovery from rollovers, P&L preservation
```

---

## Risk Matrix

| Risk | Likelihood | Severity | Current Mitigation | Recommended |
|------|-----------|----------|-------------------|------------|
| Silent stale entry price | HIGH | HIGH | None | Contract tracking |
| Phantom position repair | HIGH | CRITICAL | None | Rollover detection |
| Brackets orphaned | MEDIUM | HIGH | None | Auto-recreate on rollover |
| Force-flat during rollover | MEDIUM | HIGH | None | Earlier stop_new_orders |
| Contract age undetected | MEDIUM | MEDIUM | None | Expiry metadata |
| P&L attribution broken | HIGH | MEDIUM | None | Per-contract P&L tracking |
| Cascade repairs | MEDIUM | HIGH | None | Detect rollover skip repair |
| Repair zeros wrong contract | MEDIUM | CRITICAL | None | Contract-aware repair |
| Backtest/live divergence | HIGH | MEDIUM | None | Simulate rollovers |

---

## Implementation Roadmap

### Week 1: Foundation
- [ ] Add contract expiry functions to metadata
- [ ] Test contract age detection

### Week 2: Tracking
- [ ] Extend VirtualPositionTracker with contract months
- [ ] Parallel `contract_positions` dict
- [ ] Test multi-contract aggregation

### Week 3: Detection
- [ ] Implement rollover detector
- [ ] Add rollover event handler
- [ ] Recreate brackets on rollover

### Week 4: Repair Safety
- [ ] Make sync per-contract aware
- [ ] Update repair logic
- [ ] Add stale entry price warnings

### Week 5: Calendar Integration
- [ ] Add expiry checks to TradingCalendar
- [ ] Restrict near-expiry trading
- [ ] Integration tests

---

## Key Learnings

1. **Futures trading requires contract lifecycle awareness** - current symbol-only approach insufficient
2. **Rollover is silent but critical** - must detect and handle explicitly
3. **Repair logic is dangerous without contract context** - can zero wrong positions
4. **P&L attribution impossible across contracts** - need per-contract tracking
5. **Platform constraints intersect with rollover windows** - force-flat timing vs rollover timing is hard problem
6. **Testing must simulate rollovers** - backtest results won't match live otherwise
7. **Bracket orders need contract awareness** - may be orphaned or on wrong contract after rollover

---

## Files Generated

1. **ROLLOVER_AND_EXPIRATION_EDGE_CASES.md** (Main document)
   - 13 detailed scenarios
   - Unknown/ambiguities documented
   - Risk/mitigation tables

2. **ROLLOVER_SCENARIOS_DETAILED.md** (Numerical examples)
   - 8 concrete scenarios with prices
   - P&L calculations shown
   - Cascade failures illustrated

3. **IMPLEMENTATION_GAPS_AND_SOLUTIONS.md** (Solutions)
   - 7 critical gaps detailed
   - 5 prioritized solutions with code
   - Testing strategy
   - Rollout plan with timeline

4. **BRAINSTORM_SUMMARY.md** (This file)
   - Executive summary
   - Quick reference
   - Risk matrix
   - Implementation roadmap

---

## Next Steps

1. **Clarify unknowns** with ProjectX/TopStepX team
2. **Review edge cases** with trading team
3. **Prioritize solutions** based on risk/impact
4. **Start with Foundation** (metadata) as quick win
5. **Build incrementally** (tracking → detection → recovery)
6. **Test heavily** with near-expiry contracts
7. **Monitor in paper trading** before live deployment

---

**Generated:** 2025-11-26
**Focus:** Contract Rollover & Expiration Edge Cases
**Status:** Brainstorm Complete - Ready for Implementation Planning
