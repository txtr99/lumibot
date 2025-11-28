# Futures Contract Rollover & Expiration Edge Cases

**Brainstorm for Position Sync/Repair Logic in Multi-Strategy System**

Context: Virtual positions tracked per-strategy net to a single exchange position. Repair logic zeros phantom positions when exchange shows fewer contracts than local virtual net.

---

## 1. BASIC ROLLOVER MECHANICS & UNKNOWNS

### What We Know
- **GLOBEX_TO_SYMBOL** mapping (e.g., `EP` → `ES`) is **STATIC** - never changes
- **Expiry codes are DYNAMIC** - only the month/year part changes (e.g., `ESZ25` → `ESH26`)
- **Contract symbols in metadata** (e.g., `"ES"`) represent the **instrument class**, not the expiry
- **ProjectX contract IDs** use format: `CON.F.US.<GLOBEX>.<EXPIRY>` (e.g., `CON.F.US.EP.Z25`)

### What We DON'T Know
- **How does data fetching work during rollover?**
  - Do we automatically fetch from the new contract when the old one expires?
  - Who handles the symbol transition (ES → ESH26)?
  - What happens if we request historical data that spans the rollover?

- **What is the ProjectX API's behavior?**
  - Does it automatically map `ES` → current active contract?
  - Or do we need to specify contract month explicitly?
  - What happens to positions in the old contract when it expires?

- **How does VirtualPositionTracker.execute_order() handle symbol changes?**
  - It only sees the symbol (e.g., `"ES"`), not the contract expiry
  - If we send orders for ESZ25 then ESH26, does it treat them as same position?
  - What if exchange auto-closes ESZ25 but our virtual tracker still holds it?

---

## 2. SCENARIO: SINGLE STRATEGY, SINGLE SYMBOL, ROLLOVER

### Setup
- **Strategy:** `ES_1M_01` trading ES (S&P 500 e-mini)
- **Position:** Long 1 contract at 5900.50 (ESZ25 - December 2025)
- **Current Time:** Dec 19, 2025 (10 days before expiry on Dec 28)
- **Rollover Window:** Dec 26-30 (typical CME rollover period)

### Timeline & Edge Cases

#### **T0: Dec 19 - Normal Position**
```
Virtual:  ES = +1 @ 5900.50
Exchange: ES = +1
Sync:     ✓ Match
```
- ✓ No issues yet

#### **T1: Dec 22 - Exchange Initiates Auto-Rollover**
```
Virtual:  ES = +1 @ 5900.50  [still thinks it's ESZ25]
Exchange: Closed ESZ25 position, opened new ESH26 position
          (or: position auto-moved to ESH26 contract)
Sync:     ⚠️ **MISMATCH!**
          - Virtual: +1 ES
          - Exchange: +0 ESZ25, +1 ESH26 (or position automatically on H26)
```

**What Could Go Wrong:**
1. **Symbol Ambiguity:** If both `ESZ25` and `ESH26` exist simultaneously, position sync check only looks at symbol `"ES"` - does it see the total? Or split?
2. **Contract ID Mismatch:** Our `tracker` uses `symbol="ES"`, but ProjectX reports contracts as separate line items with different contract IDs
3. **Price Drift:** ESZ25 and ESH26 have different prices (ESZ25 is expiring, ESH26 is prime contract). Entry price becomes stale.

#### **T2: Dec 26 - Manual Rollover (If Allowed)**
```
Strategy triggers: "Close ESZ25, open ESH26"
  1. Submit SELL 1 ESZ25 at 5912.25 → +$11.75/contract = +$587.50 gross
  2. Submit BUY 1 ESH26 at 5908.50 → new entry at 5908.50

Virtual Tracker:
  Step 1: ES = 0 (flat from closing Z25)
  Step 2: ES = +1 @ 5908.50 (now tracking H26)

Exchange:
  Step 1: ESZ25 closed ✓
  Step 2: ESH26 opened ✓
```

**What Could Go Wrong:**
1. **Slippage Between Legs:** If ESZ25 close fills at 5912.25 but ESH26 buy doesn't fill (exchange issue), we're flat on Z25 but don't have H26 position
   - Virtual shows: ES = +1 @ 5908.50
   - Exchange shows: ES = 0 (closed Z25, no H26 yet)
   - **Repair Logic Triggers:** excess = +1, zeros out the virtual position
   - **Result:** We lose the intended rollover position!

2. **Partial Fill on Rollover:**
   - Close order: SELL 1 ESZ25 → fills 0.8 contracts
   - Open order: BUY 1 ESH26 → fills fully at 1.0 contracts
   - Virtual: ES = +0.2 (net long from reversal)
   - Exchange: Shows 0.8 short, 1.0 long? Or already netted?

3. **Two-Leg Execution Order Dependency:**
   - What if close fills but we can't enter because market closes?
   - What if we enter but close fails due to liquidity?
   - Our virtual tracker is ahead of the exchange state

---

## 3. SCENARIO: MULTIPLE STRATEGIES ON SAME SYMBOL DURING ROLLOVER

### Setup
- **Strategy 1:** `ES_1M_01` - Long 2 contracts at 5900.50
- **Strategy 2:** `ES_1M_02` - Short 1 contract at 5905.00
- **Net Exchange Position:** Long 1 contract = 2 - 1 = +1
- **Rollover Event:** ESZ25 → ESH26

### Virtual Positions (Before Rollover)
```
Tracker.positions["ES"] = +1  [aggregation of all strategies]
  - ES_1M_01: +2 @ 5900.50
  - ES_1M_02: -1 @ 5905.00
  - Net: +1
```

### Exchange Auto-Rollover Scenario
```
T0: Both strategies in position
  Virtual: +1 ES (total)
  Exchange: +1 ES (total)

T1: Exchange closes ESZ25, opens ESH26
  Virtual: +1 ES (tracker doesn't know rollover happened)
  Exchange: +1 ES (now on ESH26)

T2: Strategy 1 sends "close 2" order (thinking Z25)
  - Sends SELL 2 ESH26
  - Virtual: ES = -1 @ some_price (reversal)
  - Exchange: ES = -1 (closed the 2, now short 1)

T3: Sync Check
  Virtual: -1 ES
  Exchange: -1 ES
  ✓ Match (but semantically wrong - we closed the wrong position)
```

**What Could Go Wrong:**
1. **Asymmetric Rollover:** If Strategy 1 rolls but Strategy 2 doesn't (different allowed_sessions or risk limits):
   - ES_1M_01: ES = +2 @ 5908.50 (ESH26)
   - ES_1M_02: ES = -1 @ 5905.00 (still thinks ESZ25)
   - Virtual net: +1
   - Exchange: +1 ESH26, -1 ESZ25 (two contracts!)
   - **Repair fails** because we're split across contracts

2. **Phantom Cross-Contract Position:**
   - One strategy in ESZ25, another in ESH26, virtual tracker thinks they're the same
   - They're not fungible! Can't net them.
   - Repair logic has no way to know this

---

## 4. SCENARIO: STALE ENTRY PRICE AFTER ROLLOVER

### Setup
- **Strategy:** `ES_1M_01` with entry at 5900.50 (ESZ25)
- **P&L Calculation:** `(current_price - entry_price) * qty * multiplier`
- **Rollover occurs:** Exchange auto-moves to ESH26 at 5905.00

### The Problem
```
VirtualPosition:
  symbol: "ES"
  quantity: +1
  avg_entry_price: 5900.50  ← Stale! This is Z25 entry
  last_update: Dec 22, 2025

Current price (ESH26): 5908.50

Calculated P&L: (5908.50 - 5900.50) * 1 * 50 = +$400

Actual P&L breakdown:
  - Z25 portion: (5905.00 - 5900.50) * 1 * 50 = +$225 (REALIZED on rollover)
  - H26 portion: (5908.50 - 5905.00) * 1 * 50 = +$175 (UNREALIZED on new contract)
  - Total: +$400 ✓ Matches

But if strategy closes ESH26 at 5910:
  - Calculated: (5910 - 5900.50) * 1 * 50 = +$475
  - Should be: +$225 (Z25 realization) + (5910 - 5905) * 50 = +$475 ✓

Actually it works out! But only because we closed the entire position.
```

**Risk:** If strategies partially exit and re-enter the contract, the cross-contract P&L attribution becomes impossible to track correctly.

---

## 5. SCENARIO: PLATFORM MAINTENANCE / FORCE-FLAT DURING ROLLOVER

### Setup
- **Time:** Dec 27, 2025, 14:50 CT (TopStep maintenance window starts at 15:10)
- **Position:** ES_1M_01 long 1 ESZ25
- **Force-Flat Countdown:** 20 minutes

### Timeline
```
T0: 14:50 - Position open in ESZ25
  Virtual: ES = +1 @ 5900.50
  Exchange: ES = +1 (ESZ25)
  TradingCalendar: stop_new_orders in 20 min, force_flat in 30 min

T1: 15:00 - Order routing issue (common during rollover)
  Strategy tries to close: SELL 1 ES
  Request goes to: ESH26 (new contract)
  But position still on: ESZ25 (old contract)
  Exchange: Request rejected or routed to wrong contract

T2: 15:05 - Retry with explicit contract ID?
  No way to specify in current architecture - we only have symbol "ES"

T3: 15:10 - Platform maintenance starts
  Exchange auto-closes all positions (both Z25 and H26?)

T4: Force-Flat Sync Check
  Virtual: ES = +1 @ 5900.50
  Exchange: ES = 0 (auto-closed by platform)

  Repair Logic:
    excess = 1 - 0 = 1 (too long)
    Strategy ES_1M_01 has +1
    Zero it out

  Result: ✓ Virtual synced, but strategy doesn't know why
          its position was liquidated (platform maintenance)
```

**What Could Go Wrong:**
1. **Cross-Contract Confusion:** If Z25 is expired but system still tries to close it, order gets rejected
2. **Maintenance Window Timing:** Force-flat might happen BETWEEN close and new entry orders during rollover
3. **Contract Age Checks:** Some brokers reject orders for contracts past certain date. Does our system know ESZ25 is "too old" to accept new orders?

---

## 6. SCENARIO: STRATEGIES ON DIFFERENT CONTRACT MONTHS

### Setup (Deliberately Different)
```
Strategy      Symbol  Intended Contract  Why?
ES_1M_01      ES      ESZ25              Prefer Dec contract
ES_1M_02      ES      ESH26              Prefer next contract early
NQ_1M_01      NQ      NQZ25              Calendar spread strategy
```

### Ambiguity
```
VirtualPositionTracker can't distinguish:
  - positions["ES"] = {+1: Z25, +1: H26} = +2 total
  - But we don't track WHICH contract each is in

When sync checks exchange:
  - Are we looking at Z25 only?
  - H26 only?
  - Both aggregated?

If exchange has: Z25 +1, H26 +1, total +2
But we ask: "ES" = +2 globally
  ✓ Match (but we don't know which is which)
```

**Critical Issue:** Repair logic only works per-symbol, not per-contract. If we need to close one contract month but keep another:
```
Exchange: Z25 +1, H26 +1
Virtual:  ES +2
Repair:   "Z25 is expiring, need to close it"
Repair Logic: "zero out 1 ES"
Problem: We zeroed H26 instead of Z25!
```

---

## 7. SCENARIO: CONTRACT EXPIRATION WHILE POSITION HELD

### Setup
```
Date: Dec 27, 2025 (ESZ25 expires Dec 28)
Position: ES_1M_01 long 1 ESZ25 @ 5900.50
Entry Time: Dec 19 (8 days old)
No Close Signal Generated
```

### Exchange Behavior (ProjectX/TopStep)
```
Dec 28, 08:00 CT - Contract Expires
  Option A: Exchange auto-closes all ESZ25 positions at settlement price
  Option B: Exchange moves positions to next contract automatically
  Option C: Exchange cancels/rejects all orders for ESZ25

Our System: Unknown which happens!
```

### What We Detect
```
T0: Pre-expiry
  Virtual: ES = +1 @ 5900.50
  Exchange: ES = +1 (ESZ25)
  Sync: ✓

T1: Post-expiry (after settlement)
  Virtual: ES = +1 @ 5900.50  [doesn't know contract expired]
  Exchange: ?? (depends on option A/B/C above)

  If Option A (auto-closed):
    Virtual: +1
    Exchange: 0
    Repair: Zeroes out the phantom position
    Result: ✓ Correct, but silent (strategy unaware)

  If Option B (auto-rolled):
    Virtual: +1 @ 5900.50 [still has stale Z25 entry price]
    Exchange: +1 @ settlement price (now H26)
    Sync: ✓ Match by quantity
    Problem: Entry price mismatch is undetected

  If Option C (rejected):
    Virtual: +1 @ 5900.50
    Exchange: 0 (no orders accepted after expiry)
    Repair: Zeroes out phantom
    Result: Position liquidated by market, not by strategy signal
```

**Critical Unknown:** How does ProjectX actually handle ESZ25 positions after Dec 28? Does it:
- Auto-close at settlement? → We need to detect this as an unexpected flat
- Auto-roll to H26? → We need to know the new contract ID and adjust tracking
- Reject new orders? → We need to know when a contract is "too old" to trade

---

## 8. SCENARIO: SPLIT-STRIKE BUTTERFLY ACROSS EXPIRATIONS

### (Theoretical but possible with spread orders)
```
Strategy: Create synthetic position spanning multiple contracts
  Long 2 ESH26
  Short 1 ESZ25
  Long 1 ESM26

VirtualTracker.positions["ES"] = +1 @ weighted_avg_price

Exchange: Has 3 different contract months on books
          Can't close without being explicit about which contract

When Repair Runs:
  - Can only see aggregate: ES = +1
  - Can't know about the spread structure
  - If one contract month gap-closes or liquidity dries up,
    repair logic might zero out the wrong leg
```

---

## 9. SCENARIO: TIME-BASED EXIT DURING ROLLOVER

### Setup
```
Strategy: ES_1M_01
  params: max_bars_in_trade = 50 bars
  entry_time: Dec 19, 08:00 CT (ESZ25)
  bars_in_trade: 49 (as of Dec 21, 16:00 CT)

Rollover: Dec 22 (ESZ25 auto-closed, ESH26 auto-opened by exchange)
```

### Execution
```
Dec 22, 08:00 CT - Iteration 51
  on_trading_iteration():
    - Fetch data for ES (which contract? Z25 or H26?)
    - Data returned: ESH26 bars (new contract)
    - bars_in_trade += 1 → 50
    - Check: 50 >= 50? YES → trigger time exit
    - Send SELL 1 ES

Exchange:
  - Closes ESH26 (the new contract) instead of Z25
  - Our strategy thinks it exited, but:
    - It's not exiting the original Z25 position
    - It's exiting the auto-rolled position
    - The original Z25 entry time is now irrelevant
```

**Problem:** `entry_time` is based on calendar time, not bars. If contract changes, bar count continues across the rollover, but entry time semantically should reset (new contract = new entry).

---

## 10. REPAIR LOGIC FAILURE SCENARIOS

### 10a: Cannot Distinguish Contract Months
```
Scenario: Exchange has Z25 +1, H26 -1, net 0
          Virtual: ES 0 (correctly) but WRONG internals

When we need to manually close Z25 (expiring):
  - Repair logic: no discrepancy detected, does nothing
  - Z25 still on books at expiry
  - Force-flat triggers and closes it at settlement price
  - H26 position survives
  - Virtual: now wrong (missing the Z25 close P&L)
```

### 10b: Partial Fill on Rollover Creates Perpetual Mismatch
```
Scenario: Strategy tries to roll from Z25 to H26
          Close Z25: SELL 1 → fills 0.7 contracts
          Open H26:  BUY 1 → fills 1.0 contracts

Virtual: ES = 0.3 (net long from reversal)
Exchange: ES = 0.3 (same)
Sync: ✓ Match

But: We're SUPPOSED to have 1 contract on H26
     We only have 0.3
     The other 0.7 wasn't bought!
     This mismatch can't be auto-detected by our sync logic
```

### 10c: Multiple Strategies Create Unrecoverable State
```
Scenario: During rollover from Z25 to H26:

  Strategy 1: Sends close order for Z25 (gets filled on Z25)
  Strategy 2: Data fetcher gets H26 data
  Strategy 2: Thinks it's holding H26, sends another close

  Exchange: Z25 is closed (by strat 1)
            H26 is closed (by strat 2)
  Virtual:  Strat1 shows 0, Strat2 shows 0
  Exchange: 0
  ✓ Sync matches but:
    - No strategy was holding H26
    - Strat2 closed an empty position (or the H26 it shouldn't have)
    - P&L is now completely disconnected from strategy entry/exit points
```

---

## 11. RECOMMENDATIONS & UNKNOWNS TO RESOLVE

### Critical Questions to Answer First
1. **How does ProjectX API behave at contract expiry?**
   - Auto-close? Auto-roll? Reject new orders?
   - Does `position_search_open` return both old and new contracts simultaneously?

2. **Does the data source automatically map `ES` → current contract?**
   - Or do we need to explicitly fetch ESZ25, ESH26, etc.?
   - What happens in the gap between expiry and rollover?

3. **Can we detect contract age/expiration in our system?**
   - Is there metadata about which contract month is "active"?
   - Can we calculate when to expect auto-rollover?

4. **What's the actual behavior of `force_flat()` on expired contracts?**
   - Does calling `force_flat("ES")` work if Z25 is expired?
   - Does it flatten both Z25 and H26 simultaneously?

5. **How precise do we need position tracking to be?**
   - Accept that cross-contract positions can't be perfectly tracked?
   - Implement per-contract-month tracking (massive refactor)?
   - Add a layer that detects rollover and resets entry points?

### Possible Implementation Mitigations
1. **Contract Age Detection**
   - Fetch contract metadata before trading
   - Skip/close positions in contracts within 2 weeks of expiry
   - Implement `is_contract_expired(symbol, contract_id)` check

2. **Explicit Rollover Handling**
   - Add strategy config: `allowed_contract_months` (e.g., ["Z", "H"] for next 2 quarters)
   - Detect rollover events and reset `entry_time` / `entry_price`
   - Track which contract month each virtual position is on

3. **Per-Contract Tracking**
   - Extend VirtualPositionTracker to track `(symbol, contract_month)` tuples
   - Update sync logic to reconcile multiple contracts per symbol
   - Major refactor, high risk

4. **Disable Near-Expiry Trading**
   - Add calendar rule: "no new ES trades after Dec 10" (if expiry is Dec 28)
   - Automatically close positions 3-5 days before expiry
   - Simple, conservative, reduces edge cases

5. **Improve Sync/Repair Observability**
   - Log EVERY sync check with full detail:
     - Contract IDs involved
     - Whether rollover detected
     - Which strategies matched which contracts
   - Make repair decisions explicit and reversible

---

## 12. SUMMARY TABLE: Risk by Scenario

| Scenario | Likelihood | Severity | Root Cause | Mitigation |
|----------|-----------|----------|-----------|-----------|
| Auto-rollover breaks entry price | HIGH | MEDIUM | No contract month tracking | Detect rollover, reset entry |
| Two-legged rollover partial fill | MEDIUM | HIGH | Execution dependency | Validate rollover completion |
| Platform maintenance during rollover | MEDIUM | HIGH | Timing conflict | Stop new orders earlier |
| Force-flat closes wrong contract | MEDIUM | MEDIUM | Contract ambiguity | Explicit contract IDs or disable near-expiry |
| Repair zeros wrong strategy position | HIGH | CRITICAL | No contract month in virtual tracker | Extend tracker to track contracts |
| Phantom cross-contract spread | LOW | CRITICAL | Intentional multi-contract positions | Document limitations |
| Entry time invalid after rollover | HIGH | MEDIUM | Entry time not reset | Detect rollover, reset timing |
| Expired contract still in tracker | MEDIUM | HIGH | No auto-detection | Check contract age, auto-close |

---

## 13. NEXT STEPS

1. **Define:** What does ProjectX actually do at expiry? (TEST with near-expiry contract)
2. **Design:** Rollover detection logic (how to know when it happens?)
3. **Implement:** Contract month tracking in metadata or virtual tracker
4. **Document:** Limitations of current system (spread positions, multi-month scenarios)
5. **Test:** Simulate rollover scenarios with bracket order manager
6. **Monitor:** Log all sync/repair operations for post-mortem analysis

---
