# Edge Cases: Position Sync/Repair Logic
## Multi-Account & Multi-Instance Futures Trading System

This document outlines critical edge cases and failure scenarios when running virtual position tracking across multiple accounts and bot instances. The repair logic **zeros phantom virtual positions when exchange shows fewer contracts than virtual net**, but this assumption breaks down in complex deployment scenarios.

---

## Context

**Current Architecture:**
- Virtual positions tracked locally per-strategy
- Per-strategy trackers net to single exchange position (e.g., ES net across 10 strategies)
- Repair logic: if `exchange_qty < virtual_qty`, zero out virtual positions
- Reconciliation tolerance: 0.001 contracts (floating point epsilon)

**Current Scope Assumption:**
- Single account
- Single bot instance
- No external trading interference

---

## Multi-Account Scenarios

### 1. **Same Strategy Across Multiple Accounts (Independent Instances)**

**Scenario:**
- TopStepX Account A: ES_1M_01 running (10 contracts net)
- TopStepX Account B: ES_1M_01 running (10 contracts net)
- Each instance has independent virtual trackers and independent exchange accounts

**What Works:**
- Each instance has isolated broker connection to its account
- Virtual positions tracked independently per bot instance
- No sync/repair interaction between accounts

**What Can Go Wrong:**
- **Credential scope creep**: If both bots use same API key (bad practice), they can see both account positions
  - Bot A reconciles against Account A positions, sees Account B's ES position (10 contracts)
  - Bot A believes 20 contracts are on exchange (10 A + 10 B) vs its 10 virtual
  - Repair logic tries to ZERO OUT virtual positions in Bot A, causing it to exit profitable trades
  - Both accounts now unhedged and exposed

**Severity:** CRITICAL

**Fix Detection:**
- Log exchange positions by account ID before reconciliation
- Verify API credentials are account-scoped and only return that account's positions

---

### 2. **Same Strategy Across Multiple Accounts (Single Master Instance)**

**Scenario:**
- Single bot instance runs ES_1M_01 against Account A AND Account B simultaneously
- Instance has 2 strategies: `ES_1M_01_A` (Account A) and `ES_1M_01_B` (Account B)
- Both use same symbol ES, but different broker clients

**What Works:**
- Strategies are isolated (different strategy_ids, different trackers)
- Each strategy has separate broker connection

**What Can Go Wrong:**
- **Aggregation without account tagging**:
  ```python
  # In portfolio_manager.py reconcile_positions():
  # Sums ALL virtual positions for symbol regardless of account
  virtual_qty = sum(state.tracker.get_position(symbol) for all strategies)

  # Account A bot: +10 long
  # Account B bot: +5 long
  # Aggregated virtual: 15 long

  # exchange_positions() call might be ambiguous:
  # Does it query Account A, Account B, or both?
  ```

- **Ambiguous repair target**: If repair logic detects excess, which account gets zeroed?
  - Assuming it iterates through `strategy_states` dict without account awareness
  - May zero positions in wrong account or corrupt both

- **Position leakage in repair**: Current code zeroes "strategies_with_pos" until excess cleared
  - No account isolation check, may destroy Account A positions to fix Account B mismatch

**Severity:** CRITICAL

**Example Failure:**
1. Account A: ES +10 virtual, +8 exchange (repair detects 2-contract phantom)
2. Account B: ES +5 virtual, +5 exchange (correctly synced)
3. Aggregated virtual: 15, aggregated exchange: 13 (delta=2)
4. Repair loops through strategies, zeros Account B's +5 position first
5. Account B now FLAT virtually but LONG 5 on exchange
6. Account B's next order fails: expected 0, found 5 on exchange

**Fix Detection:**
- Tag each strategy_state with account_id
- Reconcile PER ACCOUNT before aggregating
- Repair only strategies belonging to affected account

---

### 3. **Different Strategies Same Account (Works Today)**

**Scenario:**
- Single account, multiple strategies on same symbol
- ES_1M_01 (10 long), ES_1M_02 (5 short) → net 5 long on exchange

**Current Behavior:**
- Reconciliation aggregates: virtual_qty = 10 + (-5) = 5 long ✓
- Exchange returns: 5 long ✓
- Synced = true ✓

**Edge Case - Partial Fill Race:**
1. ES_1M_01 places BUY 10, recorded as filled in virtual
2. ES_1M_02 places SELL 15 immediately after
3. Exchange receives SELL 15 before BUY 10 fills
4. Exchange state: SELL 15 executing
5. Virtual state: +10 + (-15) = -5 net
6. Reconcile: exchange -5 vs virtual -5 → synced ✓ (but by accident)
7. BUY 10 fills 100ms later
8. Actual exchange: BUY 10 - SELL 15 = -5 (net short)
9. Virtual still: -5
10. Next reconcile: exchange -5 vs virtual -5 → PHANTOM POSITION DETECTED
    - Excess = virtual (-5) - exchange (-5) = 0 ✓ no repair
    - BUT if accounting is off by 1 contract due to partial fill, misdetection triggers

**Severity:** MEDIUM (timing-dependent)

**Root Cause:**
- Virtual tracker assumes immediate fill; exchange may have partial fills
- Race between fill notification and reconciliation

**Fix Detection:**
- Fetch unfilled order status before reconciliation
- Account for expected pending fills in virtual position calculation

---

## Multi-Instance (Same Account) Scenarios

### 4. **Two Bot Instances, Same Account, Same Strategies**

**Scenario:**
- Instance 1 (main bot): ES_1M_01, ES_1M_02, ES_1M_03 running
- Instance 2 (backup bot): Same strategies running
- Both connected to same account (disaster recovery scenario)

**What Happens:**
1. Instance 1 places BUY 10 ES at 5000
2. Instance 2 (doesn't know about fill) places BUY 10 ES at 5001
3. Both fills happen: exchange now has 20 ES long
4. Instance 1 virtual: +10 for ES_1M_01
5. Instance 2 virtual: +10 for ES_1M_01 (different process, separate tracker)
6. Instance 1 reconcile: virtual 10 vs exchange 20 → delta=+10 (phantom)
   - Repair zeros Instance 1's +10 position
   - But Instance 2 still has +10 virtual!
7. Instance 1 thinks position is flat, Instance 2 thinks it's long 10
8. Instance 1 places new BUY 5 (thinks flat, safe entry)
9. Exchange now has 25 long, Instance 1 virtual has 5, Instance 2 virtual has 10
10. **Collision detected at Instance 1's next reconcile: exchange 25 vs (5 + 10) = 15**
    - Excess = 15 - 25 = -10 (too short?)
    - Actually we're too LONG but calculation is inverted
    - **CRASHES or enters emergency liquidation mode**

**Severity:** CRITICAL

**Variant: Staggered Detection**
- Instance 1 detects mismatch at 09:30:00
- Instance 2 detects mismatch at 09:30:05
- Instance 1 repairs by zeroing position
- Instance 2's repair uses stale sync_result, zeroes same position twice
- **Double-flat** = now short by accident

**Why This Happens:**
- No distributed lock or shared state
- Each instance has independent virtual trackers
- No cross-instance position aggregation
- Repair logic assumes "this bot owns all virtual positions for symbol"

**Fix Detection:**
- Check for other instances via shared DB/cache
- Implement distributed lock before repair
- Verify repair targets haven't been touched by other instance in last 5 seconds

---

### 5. **Two Instances, Different Symbols (Safe)**

**Scenario:**
- Instance 1: ES strategies only
- Instance 2: NQ strategies only
- Same account, different symbols

**Current Behavior:**
- Instance 1 reconciles ES: virtual ES vs exchange ES → isolated ✓
- Instance 2 reconciles NQ: virtual NQ vs exchange NQ → isolated ✓
- No cross-talk because different symbols

**Edge Case - Shared Net Margin:**
- Instance 1 ES: +10 contracts (+$50k exposure)
- Instance 2 NQ: +10 contracts (+$80k exposure)
- Account margin available: $100k
- Both positions individually valid but collectively over-leveraged
- Neither instance knows about the other's exposure
- **Margin call hits, exchange force-flattens BOTH**
- Instance 1 sees ES flat on exchange but still has +10 virtual
- Instance 2 sees NQ flat on exchange but still has +10 virtual
- Both trigger repairs (unnecessary zero-outs)
- **Entire portfolio liquidated when margin-aware trading would have succeeded**

**Severity:** HIGH

**Why Repair Fails:**
- Repair assumes exchange flat = system flat
- Actually: exchange flat because FORCED by margin call, not by system choice
- Zeroing virtual positions hides the real problem (over-leverage)

**Fix Detection:**
- Check account margin and broker emergency halt status before repair
- Repair should fail gracefully if margin event detected, trigger manual review

---

### 6. **Two Instances, Manual WebUI Trading Concurrently**

**Scenario:**
- Instance 1 (bot): BUY 10 ES via API
- Trader: BUY 5 ES via TopStepX WebUI simultaneously
- Same account

**Sequence:**
1. 09:30:00.000: Instance 1 places BUY 10 via API → order queued
2. 09:30:00.050: Trader clicks BUY 5 in WebUI → order queued
3. 09:30:00.100: Both orders hit exchange
4. Instance 1 virtual tracker: BUY 10 recorded (idempotency: order_id XYZ)
5. WebUI order: NO order_id in Instance 1's tracker
6. 09:30:05.000: Instance 1 reconcile: virtual 10 vs exchange 15
   - Excess = 10 - 15 = -5 (too short?)
   - **Direction logic inverts**: "Need to reduce SHORT but only have LONG"
   - Confusion in repair logic (line 2152-2154 handles this with error)

**Severity:** MEDIUM (detected by code, but requires manual fix)

**Worse Variant - WebUI Order Fills First:**
1. 09:30:00.000: Trader clicks BUY 5 on WebUI
2. 09:30:00.050: Instance 1 places BUY 10 via API
3. WebUI order fills at 09:30:00.100
4. Instance 1 order fills at 09:30:01.000
5. Instance 1 polls fill at 09:30:01.100 (after BUY 10 callback)
6. Trader's WebUI order is UNKNOWN to Instance 1
7. Instance 1 thinks: virtual 10, actual 15 → phantom 5 exists
8. **Repair zeros the BUY 10 position (which includes trader's BUY 5)**
9. Instance 1 now FLAT, exchange still LONG 15
10. Instance 1 thinks account is empty, places new BUY 10
11. Exchange now LONG 25 (original 5 + original 10 + new 10)
12. **Next reconcile: virtual 10 vs exchange 25 → LIQUIDATION CASCADE**

**Severity:** CRITICAL

**Why It Fails:**
- No knowledge of orders placed outside API
- Repair assumes: all exchange position = all virtual positions
- Actually: exchange position = virtual positions + manual trades

**Fix Detection:**
- Query UNFILLED order list (from API and from exchange)
- Subtract unfilled from exchange position before reconciliation
- Only reconcile against EXECUTED positions

---

### 7. **Mobile App Order Placed, Bot Order Placed (Race)**

**Scenario:**
- Bot Instance + TopStepX Mobile App both connected to same account
- Bot: "Exit if RSI > 80" → places SELL 5 ES
- Trader: Taps "close position" on mobile simultaneously
- Both orders reach exchange within 100ms

**Failure Modes:**

**A) Both Orders Fill (Net Effect Correct, But Tracking Breaks)**
- Bot SELL 5: fills at 5001.50
- Mobile SELL 5: fills at 5001.55
- Exchange: position reduced by 10 total ✓
- Bot virtual: SELL 5 recorded (order_id ABC123)
- Mobile: SELL 5 NOT recorded in virtual tracker
- Bot reconcile: virtual qty = position - 5, exchange qty = position - 10
  - Excess = virtual - exchange = -5
  - Bot thinks TOO SHORT, but actually that's the mobile order
  - Repair tries to "reduce short exposure" by buying back 5
  - **Bot now reverses trader's manual order, opening position again**

**B) Mobile Fills Twice Due to App Bug**
- Mobile SELL 5 button pressed twice rapidly
- First SELL 5 fills at 5001.50
- Second SELL 5 fills at 5001.45
- Exchange: net position down 10 (corrected by risk officer later when noticed)
- Bot virtual: SELL 5 recorded
- Bot reconcile: virtual 5, exchange 10 → "too short"
  - Repair logic: buy back to neutral
  - But position was already duplicated by app bug, now even more confused

**Severity:** HIGH

**Root Cause:**
- Virtual tracker only knows about orders it placed
- Manual trades create "blind spots" in reconciliation
- Repair logic has no way to distinguish "phantom" from "manual trade"

**Fix Detection:**
- Query last 100 fills via API (trade_search)
- Compare fills against recorded order_ids
- Identify unrecorded fills and mark them as manual
- Repair should skip symbols with recent manual fills

---

## Environmental & Permission Scenarios

### 8. **API Key With Partial Permissions**

**Scenario:**
- Instance 1 API key: read-only positions, write orders
- Instance 2 API key: read positions, write orders, cancel orders
- Same account, different permission levels

**Failure Mode:**
1. Instance 1 places order, fills, records in virtual
2. Instance 2 detects mismatch during repair
3. Instance 2 calls `cancel_brackets_for_strategy()` (line 2206)
4. Instance 1 key doesn't have cancel permission → cascading failures
5. But Instance 1 thinks brackets are cancelled (Instance 2 told it to assume so)
6. Instance 1 places new bracket orders thinking old ones are dead
7. **Old and new brackets both active on exchange, double-exposure**

**Severity:** MEDIUM (caught eventually, but brief window)

**Fix Detection:**
- Test API key permissions on startup
- Log permission scope for each instance
- Fail repair if permission mismatch detected

---

### 9. **Rate Limiting / IP Whitelist Conflicts**

**Scenario:**
- Instance 1: VPN at 203.0.113.1
- Instance 2: Local network at 198.51.100.1
- Exchange IP whitelist allows 203.0.113.1 only

**Failure Mode:**
1. Instance 2 tries to place order → rejected (IP not whitelisted)
2. Instance 2 thinks order failed, doesn't record in virtual
3. But order actually placed by Instance 1 via relay/gateway and filled
4. Instance 2 reconcile: exchange has order, virtual doesn't
5. Instance 2 repair: tries to zero out, but wrong account/permissions
6. **Cascading auth failures, emergency shutdown triggered**

**Severity:** MEDIUM (infrastructure issue)

**Fix Detection:**
- Log IP at startup
- Fail if IP changes unexpectedly (indicates instance migration)
- Warn if IP not in whitelist during order execution

---

## Distributed & Network Scenarios

### 10. **Network Partition During Repair**

**Scenario:**
- Instance 1 detects mismatch: virtual 10 vs exchange 5
- Starts repair: "Zero out 5 contracts to match exchange"
- Instance 1 calls `state.tracker.reset()` (line 2199)
- **Network partition occurs** (broker connection drops)
- Instance 1 thinks repair succeeded, resets virtual tracker
- Instance 2 (if running): still has 10 in virtual
- Network heals 30 seconds later
- Instance 1 reconnects, doesn't know position state changed during outage

**Failure Mode:**
1. Instance 1 resets virtual: ES_1M_01 = 0
2. Network down 30 seconds
3. Instance 1 reconnects, reconcile again
4. Exchange: 5 contracts (from before partition)
5. Virtual: 0 (just reset)
6. Delta = -5 (thinks it's SHORT when actually LONG)
7. **Initiates emergency buy to cover imaginary short**
8. Exchange now LONG 10 (original 5 + emergency buy 5)

**Severity:** HIGH

**Why It Fails:**
- Virtual reset not idempotent if network partitions mid-repair
- No WAL (write-ahead log) for position state changes
- Repair commits to virtual before confirming with exchange

**Fix Detection:**
- Implement position change journaling
- Repair commits = write to journal first, then execute
- On reconnect, verify journal against exchange before proceeding

---

### 11. **Clock Skew Between Instances**

**Scenario:**
- Instance 1 clock: 09:30:15 UTC
- Instance 2 clock: 09:30:25 UTC (10 second lag)
- Bot-specific repair logic has time-sensitive guards

**Failure Mode:**
1. Instance 1 places order at 09:30:15
2. Instance 1 reconcile at 09:30:15.500
3. Exchange shows order filled, Instance 1 records in virtual
4. Instance 2 (at 09:30:25) reconcile
5. Instance 2 sees exchange position, virtual position
6. Repair logic checks: "Was repair done recently?" (last 5 seconds?)
7. Instance 2 clock says 09:30:25, check sees repair at 09:30:15
8. 10 seconds ago! Beyond the 5-second window!
9. **Instance 2 repeats repair, zeros position again**
10. Instance 1 thinks position is still valid, places new order
11. **Collision detected too late**

**Severity:** MEDIUM (rare, but timing-dependent)

**Fix Detection:**
- Use exchange server time, not instance time
- Sync clocks via NTP with tolerance checks
- Fail repair if clock skew detected

---

## Aggregate Failures

### 12. **Cascade: Multi-Instance + Manual Trade + Network Partition**

**The Perfect Storm Scenario:**

1. **Setup:**
   - Instance 1 & Instance 2 running on same account
   - Trader has WebUI open
   - Network quality: poor (packet loss, jitter)

2. **Timeline:**
   - 09:30:00: Trader places WebUI SELL 10 ES
   - 09:30:00.050: Instance 1 places API BUY 10 ES (signal fired)
   - 09:30:00.100: Instance 2 places API SELL 5 ES (different strategy)
   - 09:30:00.200: Network lag spike (5 second latency)
   - 09:30:00.500: Trader's WebUI order cancels (timeout)
   - 09:30:02: Instance 1 sees only its BUY 10 filled
   - 09:30:02: Instance 2 sees BUY 10 + SELL 5 on exchange (net 5 long)
   - 09:30:02: Instance 2 reconciles

3. **Instance 2 Reconciliation:**
   ```
   Virtual ES total: BUY 10 (I1) + SELL 5 (I2) = +5
   Exchange ES: +5 (from I1 order which filled)
   Delta: 0 → Synced! ✓
   ```

4. **Network recovers, Instance 1 does reconciliation:**
   ```
   Virtual ES: +10 (only knows about its own BUY 10)
   Exchange ES: +5 (BUY 10 - SELL 5 from Instance 2)
   Delta: +5 phantom → REPAIR TRIGGERED
   Repair: Zero out Instance 1's +10 to match exchange +5
   Instance 1 virtual: 0
   ```

5. **But Instance 2 still has SELL 5 in virtual!**
   ```
   Instance 1: 0
   Instance 2: +5 (BUY 10 + SELL 5 = +5)
   Aggregated virtual: +5
   Exchange: +5
   Synced? ✓
   ```

6. **Instance 1's next signal fires: BUY 5**
   ```
   Instance 1 places BUY 5 (thinks position is flat)
   Exchange now has: +5 + +5 = +10
   Instance 1 virtual: +5
   Instance 2 virtual: +5
   Aggregated virtual: +10
   Exchange: +10
   Synced? ✓
   ```

7. **For 30 seconds everything is "synced" but Instance 1 doesn't know about Instance 2's SELL 5**
   ```
   If Instance 2 exits (SELL 5 to close), Instance 1 doesn't know
   Instance 1 thinks: my +5 should become flat
   Actually: +5 from Instance 1 stays, Instance 2 exits
   Result: Instance 1 is long 5, Instance 2 is flat
   Instance 1 virtual: +5, Instance 2 virtual: 0
   Exchange: +5
   Next reconcile: virtual +5 vs exchange +5 → Synced!
   But Instance 1 doesn't know Instance 2 exited
   If Instance 1's stop loss triggers: SELL 5
   Instance 1 virtual: 0
   Exchange: 0
   But Instance 1 thinks Instance 2 is still in +5 trade!
   ```

**Severity:** CRITICAL

**Why Repair Fails:**
- No inter-instance communication
- Repair operates on aggregated state, but virtual state is distributed
- When Instance 1 zeros position, Instance 2's position becomes "orphaned"
- Subsequent trades compound the error

---

## Summary Table

| Scenario | Severity | Detection | Root Cause |
|----------|----------|-----------|-----------|
| Cred scope creep (multi-acct) | CRITICAL | Log exchange account ID | Missing account-level isolation |
| Master instance multi-acct | CRITICAL | Tag strategies with account_id | No account awareness in repair |
| Two instances same symbol | CRITICAL | Distributed lock | No instance coordination |
| Manual WebUI orders | CRITICAL | Query unrecorded fills | Blind spot for external trades |
| Mobile app double-click | HIGH | Dedup fill history | No idempotency for fills |
| Margin call cascade | HIGH | Check margin before repair | Repair assumes system caused flat |
| Network partition mid-repair | HIGH | WAL for repairs | Repair not idempotent |
| Clock skew | MEDIUM | Sync via server time | Time-based guards fail |
| Partial permissions | MEDIUM | Test perms on startup | Silent auth failures |
| IP whitelist conflict | MEDIUM | Log IP at startup | Infrastructure mismatch |
| Perfect storm cascade | CRITICAL | Mutual exclusion + state sharing | All failures combined |

---

## Recommendations

### Immediate (Design Changes)

1. **Add account_id tagging to strategies**
   - Reconcile per-account before aggregating
   - Repair only targets affected account

2. **Query unrecorded fills before repair**
   - Call `trade_search` for last N fills
   - Identify fills not in virtual tracker
   - Mark symbol as "unsafe for repair" if recent unrecorded fills

3. **Implement idempotent repairs**
   - Write-ahead log of repair operations
   - Check log before repeating repair
   - Prevent double-zeroing

4. **Distributed instance detection**
   - Store repair timestamp in shared Redis/DB
   - Lock symbol during repair
   - Other instances skip repair if lock active

5. **Manual trade detection**
   - Query live orders + recent fills
   - Compare against API-placed orders
   - Estimate manual trade quantity
   - Fail repair gracefully if ambiguous

### Medium-term (Operational)

1. **Multi-instance deployment rules:**
   - Only run one instance per account
   - OR: Separate by symbol (Instance 1=ES, Instance 2=NQ)
   - OR: Implement shared position state database

2. **Monitoring & alerts:**
   - Alert on any repair operation (manual review)
   - Alert on unrecorded fills
   - Alert on repair frequency (>2x per hour = problem)

3. **Testing scenarios:**
   - Simulate multi-instance races
   - Simulate manual trades during reconciliation
   - Simulate network partitions during repair
   - Simulate clock skew

### Long-term (Architecture)

1. **Single source of truth:**
   - Move virtual position state to shared database (Redis/PostgreSQL)
   - Instances read/write to shared state, not local state
   - Eliminates distributed state problem entirely

2. **Event sourcing:**
   - Log all order/fill events to immutable ledger
   - Replay ledger to compute current position
   - Repairs are new events in ledger, auditable

3. **Consensus-based repair:**
   - Multiple instances vote on repair decision
   - Majority rule prevents single-instance error
   - Requires quorum to proceed

---

## Testing Checklist

- [ ] Multi-account scenario with API key scope validation
- [ ] Two instances on same symbol with concurrent orders
- [ ] Manual WebUI fills during reconciliation window
- [ ] Network partition during `state.tracker.reset()`
- [ ] Clock skew between instances (10+ second gap)
- [ ] Margin call during repair execution
- [ ] Partial permissions (read-only vs write)
- [ ] Cascade failure: multi-instance + manual + network + margin
- [ ] Idempotency: same repair run twice yields same result
- [ ] Atomicity: repair fails halfway, recovery valid

