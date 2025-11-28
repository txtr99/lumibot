# Futures Rollover & Expiration: Detailed Scenarios with Numbers

## Scenario A: Silent Phantom Position After Auto-Rollover

### Setup
```
Contract: ES (E-mini S&P 500)
Expiry Dates:
  - ESZ25 (Dec 2025): expires Dec 28, 2025
  - ESH26 (Mar 2026): begins trading Dec 20, 2025

System Clock: Dec 22, 2025, 13:00 CT

Active Positions:
  ├─ ES_1M_01: LONG 1 contract @ 5900.50 (entered Dec 19)
  └─ ES_1M_02: SHORT 2 contracts @ 5902.00 (entered Dec 20)

Virtual Position (from tracker):
  symbol: "ES"
  quantity: -1  (1 long - 2 short = -1 short net)
  avg_entry_price: 5901.25

Exchange Position (before rollover):
  ESZ25: -1 short @ some avg price

Market Conditions:
  ESZ25 bid/ask: 5910 / 5911  (illiquid, near expiry)
  ESH26 bid/ask: 5905 / 5906  (active contract)
  Spread: ~5 points (5910 - 5905) due to contango
```

### Event: Exchange Auto-Rollover (Dec 22, 14:00 CT)
```
Exchange Decision: "ESZ25 has < 6 days to expiry, volume drying up,
                    force roll all positions to ESH26"

Exchange Action:
  1. Close any open ESZ25 positions
  2. Immediately open matching position on ESH26
  3. Settlement price for Z25: 5910.25
  4. New entry on H26: 5905.00
```

### After Rollover - System State
```
Virtual Tracker (unchanged):
  symbol: "ES"
  quantity: -1
  avg_entry_price: 5901.25  ← STALE! Mix of old Z25 and new H26
  last_update: Dec 22, 14:30

Exchange Positions:
  ESZ25: CLOSED (all)
  ESH26: -1 short (rolled over)

Price Action:
  ESH26 mid: 5905.50

P&L Calculation:
  Virtual: (5905.50 - 5901.25) * -1 * 50 = -$212.50
           (Loss increased because entry price is stale)

Actual P&L:
  - Z25 portion: (5910.25 - ~5902) * -1 * 50 = +$412.50 (REALIZED on rollover)
  - H26 portion: (5905.50 - 5905.00) * -1 * 50 = -$25.00 (UNREALIZED)
  - Total: +$387.50 (but we calculated -$212.50!)

P&L Tracking: ✗ BROKEN by ~$600
```

### Sync Check (routine, every 5 min)
```
Exchange Query: "What's the net position on ES?"
  Returns: ESH26 position -1

Virtual: ES = -1
Exchange: ES = -1 (now on H26)

Result: ✓ SYNCED - no discrepancy detected
         But entry price is now WRONG for all future P&L calculations
```

### Compound Problem: What if Strategy Closes Position?
```
Time: Dec 22, 15:00 CT

Strategy ES_1M_01 receives close signal:
  - Current data shows ESH26 @ 5906.00
  - Strategy: "Close my position!"
  - Sends: BUY 1 @ market (closing short)
  - Executes at: 5906.25

Virtual Tracker Update:
  Closing qty: 1, side: BUY
  New position: ES = 0
  P&L Realized: (5906.25 - 5901.25) * -1 * 50 = -$250
                (calculated from tracker's stale entry price)

Actual P&L:
  - From Z25 close on rollover: +$412.50
  - From H26 close at 5906.25: (5905.00 - 5906.25) * -1 * 50 = +$62.50
  - Total: +$475.00

Reported P&L: -$250 ✗ WRONG by $725!
```

### Why This Happens
- VirtualPositionTracker has no concept of contract months
- It sees symbol "ES" as atomic
- When exchange auto-rolls, tracker doesn't know and keeps stale entry price
- All subsequent P&L calculations are wrong

---

## Scenario B: Repair Logic Incorrectly Zeros Position During Rollover Race

### Setup
```
Time: Dec 22, 13:45 CT (active trading)

Positions:
  ES_1M_01: LONG 2 ESZ25 @ 5900.00
  ES_1M_02: SHORT 1 ESZ25 @ 5903.00
  Net Virtual: +1 short ESZ25? NO WAIT...

  Actually:
  +2 LONG - 1 SHORT = +1 LONG

Virtual Tracker:
  "ES" = +1 @ 5901.00 (weighted avg)

Exchange:
  ESZ25 position: +1 LONG

Sync: ✓ MATCH
```

### Event: Broker System Issues (Cascading)
```
T0 (13:46): Order system glitch
   Broker's data feed drops connection briefly
   Exchange still has positions but updates stall

T1 (13:47): Automatic Remediation
   Broker: "Position feed stale > 30 seconds, force check"
   Broker Query Result: Can't connect to live feed
   Broker: "Assume position was liquidated as safety measure"
   Exchange Actual State: Position still exists (+1 ESZ25 LONG)
   Broker Reported: "Position = 0" (false)

T2 (13:48): Sync Check Runs
   Virtual: "ES" = +1 LONG
   Exchange: "ES" = 0 (due to stale feed + safety assumption)

   Discrepancy: Virtual +1 > Exchange 0 → PHANTOM POSITION

T3 (13:49): Repair Logic
   Repair: "We need to reduce LONG exposure by 1"
   Find strategies with LONG positions: ES_1M_01 has +2
   Action: Zero out ES_1M_01's position completely

   Virtual Result: ES_1M_01.tracker.reset() → now 0

T4 (13:50): Feed Reconnects
   Exchange: "Positions are back online"
   Actual Exchange: +1 ESZ25 LONG still there
   Virtual: 0 (we incorrectly zeroed it)

   New Sync: MISMATCH in opposite direction now!
            Exchange +1, Virtual 0

T5 (13:51): Another Repair Cycle?
   Repair: "Exchange has phantom +1, but we have no virtual positions"
   Result: "Untracked position on exchange - manual intervention required"
```

### Why This Was Dangerous
1. **Repair acted on stale data** - broker's feed problem, not real exchange state
2. **Repair was irreversible** - zeroed positions without notification
3. **Race condition** - real exchange position existed but system thought it was gone
4. **No contract month awareness** - couldn't tell if the +1 on exchange was Z25, H26, or both

---

## Scenario C: Multi-Strategy Rollover Divergence

### Setup
```
Instruments:
  ES (primary contract): ESZ25 (expiring Dec 28)
  NQ (primary contract): NQZ25 (expiring Dec 19 - ALREADY EXPIRED!)
  GC (primary contract): GCZ25 (expiring Dec 27)

Strategies:
  ES_1M_01: LONG 1 ES   @ 5900 [entered Dec 15]
  ES_1M_02: SHORT 2 ES  @ 5905 [entered Dec 18]
  NQ_1M_01: LONG 1 NQ   @ 19200 [entered Dec 12 - RISKY!]
  GC_1M_01: SHORT 1 GC  @ 2650 [entered Dec 20]

Current Time: Dec 22, 2025

Virtual Positions:
  "ES": -1 (2 - 1 = 1 SHORT, wait... 1 LONG - 2 SHORT = -1 SHORT)
  "NQ": +1
  "GC": -1

Exchange Positions (before rollover):
  ESZ25: -1 SHORT
  NQZ25: +1 LONG  [PROBLEM: Z25 already expired Dec 19!]
  GCZ25: -1 SHORT
```

### Issue 1: NQ Already Expired
```
Dec 19, 2025 (when NQZ25 expired):
  Exchange: Auto-closed NQZ25 position @ settlement = 19210
  Virtual: NQ_1M_01 still thinks it's holding +1 @ 19200

Expected Exchange Behavior:
  Position auto-liquidated
  Settlement price: 19210
  P&L realized: (19210 - 19200) * 1 * 100 = +$1,000

But if no order was sent...
  No explicit close event triggered
  Virtual tracker doesn't know position was closed

Dec 22 Sync:
  Virtual: "NQ" = +1
  Exchange: "NQ" = 0 (closed days ago)
  Discrepancy: PHANTOM +1 NQ

Repair Logic:
  "NQ has +1 phantom position, zero it out"
  Action: NQ_1M_01.tracker.reset()

Result: ✓ Virtual matches exchange
         But no one realized the settlement P&L
         Or if they did, it's now lost in repair
```

### Issue 2: ES Starts Rolling (Dec 22, 14:00)
```
Exchange: Auto-rolls ESZ25 → ESH26
  ESZ25: -1 SHORT → closed @ 5910
  ESH26: -1 SHORT → opened @ 5905

Virtual (unchanged yet): "ES" = -1 STILL

During rollover window (few minutes):
  Sync Check A (13:55): ESZ25 -1, Exchange -1 ✓
  Sync Check B (14:05): ESH26 -1, Exchange -1 ✓ (but now on different contract)

Entry Price Problem:
  Original entry: 5905 (on ESZ25)
  Now position on: ESH26 @ 5905
  Entry price tracker: STALE
```

### Issue 3: GC Also Rolling (Dec 22, 14:15)
```
Similar to ES, but offset timing

Compound State (Dec 22, 14:30):
  Virtual:
    "ES": -1 @ 5901.25 [stale, was Z25, now H26]
    "NQ": 0 [just repaired from phantom +1]
    "GC": -1 @ 2650 [stale, was Z25, now Z26 or H26?]

  Exchange:
    ESH26: -1 SHORT
    NQH26: 0
    GCZ25 or GCH26: -1 SHORT

  Sync: CONFUSING
    - NQ is "correct" but wrongly repaired
    - ES/GC might match quantity but are on wrong contracts
    - Entry prices are meaningless across the rollover
```

### Issue 4: Replay During Backtest
```
If we backtest with same positions:

  Backtest engine loads: ESZ25 data
  Strategy simulates: Dec 12-22 trades on Z25
  Then data ends or rolls over

  If backtest auto-fills data gap with ESH26:
    Backtest shows: Position maintained thru rollover at specific price
    Live system shows: P&L jump, entry price anomaly, repair events

  Backtest results: Non-repeatable
```

---

## Scenario D: Two-Legged Rollover with Partial Fill

### Setup
```
Strategy: "ES Momentum"
Config:
  symbol: "ES"
  contracts: 1
  max_bars: 50

Current State (Dec 21, 15:50 CT):
  Position: LONG 1 ESZ25 @ 5900
  bars_in_trade: 48
  Entry time: Dec 19, 08:00

Market Forecast:
  ESZ25 will expire Dec 28 (7 days away)
  Need to roll to ESH26 within 48 hours for best liquidity

Strategy Logic (simplified):
  if bars_in_trade >= 50: close_position()
  # Will trigger Dec 22 ~8:00 AM
```

### Timeline
```
Dec 22, 07:55 CT
  Pre-Market
  bars_in_trade: 49
  ESZ25 bid/ask: 5910 / 5911
  ESH26 bid/ask: 5904 / 5905

  # Market opens in 5 minutes

Dec 22, 08:00 CT
  bars_in_trade: 50 → TRIGGER TIME EXIT

  Execution Flow:
    1. Create close order: SELL 1 ES
       (Doesn't specify contract, just "ES")

    2. Order routing (UNKNOWN behavior):
       Option A: Routes to "most liquid" → ESH26
       Option B: Routes to "original symbol" → ESZ25
       Option C: Error - ambiguous contract in rollover window

  Let's assume Option B: Routes to ESZ25

Dec 22, 08:02 CT
  Close order fills: SELL 1 ESZ25 @ 5911
  Virtual: ES = 0 (flat, closed at 5911)
  Exchange: ES = 0 (ESZ25 closed)

  P&L Realized: (5911 - 5900) * 1 * 50 = $550

  Seems fine! But then...

Dec 22, 08:05 CT
  Exchange announces: "ESZ25 in final hour, no more new orders accepted"

Dec 22, 08:07 CT
  Exchange initiates auto-rollover:
    "All remaining ESZ25 positions moved to ESH26"
    But we already closed!

Dec 22, 08:10 CT
  Our close order still pending (never confirmed)?
  Status: ??? (filled on Z25? filled on H26? rejected?)

If Order Wasn't Actually Filled:
  Virtual: 0 (our tracker thinks it's closed)
  Exchange: LONG 1 ESH26 (auto-rolled)

  Sync: MISMATCH
  Virtual: 0
  Exchange: +1

  Repair: Can't repair! Exchange > Virtual
          "Untracked position, manual intervention"
```

### More Complex: Partial Fill
```
Dec 22, 08:02 CT
  Market Order: SELL 1 ES (intended for ESZ25)
  Execution: Only 0.6 contracts fill @ 5911
  Virtual: ES = 0.4 LONG (partial close)
  Exchange: ESZ25 still has 0.4 remaining

Dec 22, 08:05 CT
  Auto-rollover begins
  Exchange: 0.4 ESZ25 → rolls to → 0.4 ESH26

Dec 22, 08:10 CT
  Virtual: ES = 0.4 @ 5900 (original entry price)
  Exchange: 0.4 ESH26
  Sync: ✓ MATCH (but on different contract!)

  Days later when we close:
    Virtual P&L: (5920 - 5900) * 0.4 * 50 = $400
    Actual: Only part of this is unrealized, rest was realized at rollover
```

---

## Scenario E: Calendar Conflict During Rollover

### Setup
```
System: TopStepX paper trading
Trading Hours: 9:30 CT to 16:00 CT weekdays

Position:
  ES_1M_01: LONG 1 ESZ25 @ 5900 (entered Dec 19)

Constraints:
  TradingCalendar.stop_new_orders: 15:30 CT (30 min before close)
  TradingCalendar.force_flat: 15:50 CT (MUST be flat)

Problem: ESZ25 rollover window = 14:30-15:15 CT on Dec 22
```

### Timeline
```
Dec 22, 14:25 CT
  Position: LONG 1 ESZ25 @ 5900
  Calendar: "Can still enter orders"

Dec 22, 14:35 CT
  Exchange initiates rollover
  ESZ25 "halt trading for rollover"
  Our order to close ESZ25 arrives → REJECTED (halted)

  Virtual: ES = 1 LONG (still)
  Exchange: ESZ25 halted (in process of rolling to H26)

Dec 22, 14:45 CT
  Rollover complete
  Exchange: ESH26 now has the +1 LONG position

  Our system: Still trying to close ESZ25? Or switched to H26?

Dec 22, 15:30 CT
  TradingCalendar: stop_new_orders
  "You can't enter new positions"
  (But we already have one, can we close it?)

  Virtual: ES = 1 LONG
  Calendar: "Still 20 minutes to force_flat"

  Can we close ESH26 now?
  Or is our close order stuck on ESZ25 (halted)?

Dec 22, 15:40 CT
  Close Order Still Pending
  (Estimated: stuck on ESZ25 which is no longer trading)

  Retry: Send new close order to ESH26?
  System: "Do I already have a close order? Don't duplicate"

Dec 22, 15:50 CT
  TradingCalendar: force_flat DEADLINE
  "You MUST be flat NOW"

  Virtual: ES = 1 LONG
  Exchange: ES = 1 LONG (on H26)

  Action: Immediate liquidation of all positions
          Execute at market (whatever price)

  Result: ✓ Flat, but:
          - Lost the ability to choose exit timing
          - Got worse slippage due to forced exit
          - Order was still pending from 14:45
          - Never knew what happened to it
```

### Calendar Decision Problem
```
At 15:30, when stop_new_orders hits:
  Our code checks: virtual_qty != 0? Yes (1 LONG)
  Decision: "I have a position, calendar prevents new entries,
            but I can still manage my position"

But if the position is "stuck" due to contract halt:
  We think we're managing it
  We're actually unable to

Missing context: "The symbol you're trying to close
                  is in a contract rollover window"
```

---

## Scenario F: P&L Attribution Breakdown

### Setup
```
Strategy: "Multi-Contract Spread"
Position A: LONG 2 ESZ25 @ 5900 [entered Dec 12]
Position B: SHORT 1 ESH26 @ 5910 [entered Dec 20]
Net: LONG 1 contract (spread position)

Virtual Tracker:
  "ES" = +1 @ 5903 (weighted average)

Tracking What We Lost:
  - Contract month for each position
  - Entry time per contract
  - Intent (is this a spread or a mistake?)
```

### Reality Check (Dec 22, 14:30, after rollover)
```
ESZ25 → ESH26 auto-rollover

Physical State:
  Position A closes on Z25 @ 5910, profit = (5910-5900)*2*50 = $1000
  Position B was already on H26, continues at 5910

But Virtual Tracker:
  Doesn't distinguish Position A vs B
  Doesn't know about the rollover
  Doesn't associate the $1000 realized profit with anything

Strategy Attribution:
  "ES" realized: ???
  "ES" unrealized: ???

  Hard to trace which trade made/lost how much
```

### Further Complication: Bracket Orders
```
Position B was entered with brackets:
  Entry: SHORT 1 ESH26 @ 5910
  TP: 5900 (10 points profit)
  SL: 5920 (10 points loss)

During rollover:
  Z25 closes (profit from Position A)
  H26 continues with brackets

If bracket hits between rollover:
  Bracket order might be:
    - On Z25 (expired, invalid)
    - On H26 (rolled, but original order was on Z25?)
    - Orphaned and never re-created

Bracket Manager Must:
  1. Detect that ESZ25 position is gone
  2. Recognize that equivalent H26 position exists
  3. Re-issue brackets on H26 with new contract ID
  4. But must know the original TP/SL levels!

  Currently: Can't do this without tracking contract months
```

---

## Scenario G: Consecutive Expirations (ES then NQ then GC)

### Setup
```
Three instruments, three different expiration dates:

NQ expires: Dec 19, 2025 (THIS IS TODAY'S DATE)
ES expires: Dec 28, 2025 (9 days away)
GC expires: Dec 27, 2025 (8 days away)

Current Time: Dec 22, 2025 (post-NQ expiry, ES/GC still active)

Positions at start of Dec 22:
  NQ_1M_01: LONG 1 @ 19200 [entered Dec 12, before expiry]
  ES_1M_01: LONG 2 @ 5900 [entered Dec 15]
  GC_1M_01: SHORT 3 @ 2645 [entered Dec 18]

Exchange (after NQ auto-closed, ES/GC rolling):
  NQH26 or NQM26: LONG 1 (if auto-rolled) OR nothing (if auto-closed)
  ESH26: LONG 2 (rolled from Z25)
  GCZ25 or GCH26: SHORT 3 (rolling soon)
```

### Daily Sync Pattern
```
Dec 20 (NQ EXPIRY DAY):
  Morning: NQZ25 LONG 1 @ 19200
  Afternoon: NQZ25 expires, auto-closes @ 19210 (unknown to us)
  System: Still thinks NQ = +1
  Sync: Detects phantom, repairs (zeros out NQ position)
  Result: ✓ Correct action, but position was auto-liquidated
          No one tracked the settlement price or P&L

Dec 22 (ES/GC ROLLING):
  14:00 - ES rollover starts
  14:30 - GC rollover starts (simultaneously!)

  Sync might see:
    Virtual: ES +2, GC -3
    Exchange: ESH26 +2, GCH26 -3 (or still Z25?)

  If exchange data lags:
    Sync: ES ?, GC ? (can't determine)
    Repair: Can't act
    Result: Manual verification needed

Dec 23-26:
  ES @ H26, GC @ H26, NQ @ H26
  Normal trading

Dec 27 (GC EXPIRY):
  Repeat of Dec 20 scenario

Dec 28 (ES EXPIRY):
  Repeat of Dec 20 scenario
```

### Systemic Risk: Repair Cascades
```
If repair logic runs 3 times in 9 days:
  - Different strategies might be affected differently
  - Some might have phantom positions repaired correctly
  - Some might be incorrectly zeroed due to exchange lag
  - Some might miss settlement prices

Audit trail gets messy:
  - Many repair events with different root causes
  - Hard to distinguish "good" repairs from "bad" ones
  - No automatic way to verify repairs were correct
```

---

## Scenario H: Backtest vs. Live Divergence

### Backtest Setup
```
Strategy: ES_1M_01 "Simple Moving Average Crossover"
Backtest Period: Dec 1 - Dec 31, 2025
Data Source: ESZ25 contracts (what was available at backtest time)

Results:
  Entry: Dec 19 @ 5900
  Exit: Dec 22 @ 5910
  Gross P&L: $500 (1 * (5910-5900) * 50)

Reality Check: ✓ This works if entire trade is on Z25 contract
```

### Live Trading (Same Period)
```
Order Submitted: Dec 19 @ 5900 (intended for ESZ25)
  Fills immediately: LONG 1 ES @ 5900

Dec 22 @ 14:00-14:30: Auto-rollover ESZ25 → ESH26
  Position physically rolls
  Virtual tracker doesn't notice
  Entry price becomes stale

Order Submitted: Dec 22 @ 5910 (to close)
  But which contract? Z25 or H26?
  System doesn't know
  If routed wrong: might not close or close at bad price

Results (if close successful):
  Gross P&L: Still $500 (if filled at same prices)
  But: Entry/exit timestamps are tied to H26, not Z25
       P&L might be attributed to wrong instrument in reports
```

### Why They Diverge
```
Backtest:
  - Entire trade on ESZ25 with ESZ25 data
  - Clean entry/exit

Live:
  - Entered on ESZ25 @ 5900
  - Rolled to ESH26 @ ~5905 by exchange
  - Exited on ESH26 @ 5910
  - Path included rollover event (slippage/timing/contract change)

Edge Case: What if close failed?
  Backtest: Position closed at 5910
  Live: Position never closed, still holding at rollover price
  Results: DRAMATICALLY different
```

---

## Summary: Where Repair Logic Fails

| Scenario | What Goes Wrong | Why | Impact |
|----------|-----------------|-----|--------|
| A | Stale entry price | No contract tracking | All P&L calculations wrong |
| B | Repair on stale data | Race condition | Positions incorrectly zeroed |
| C | Multi-contract divergence | No coordination | Positions on wrong contracts |
| D | Partial fill during rollover | No rollover awareness | Stranded positions |
| E | Calendar conflicts | No contract lifecycle | Position stuck at close time |
| F | P&L attribution broken | No per-contract tracking | Unknown who made money |
| G | Consecutive expirations | Cascading repairs | Audit trail unclear |
| H | Backtest/live mismatch | Contract changes ignored | Overfitted strategies |

---

## Practical Tests to Run

### Test 1: Detect NQ Contract Age
```bash
# Check if we can identify NQZ25 is expired
python -c "
from custom_portfolio.data.futures_metadata import is_contract_expired
print(is_contract_expired('NQ', 'Z25'))  # Should be True on Dec 22
print(is_contract_expired('NQ', 'H26'))  # Should be False
"
# Expected: Need to implement this
```

### Test 2: Simulate Rollover
```python
# Send order for ES, check which contract it routes to
order = Order(..., symbol="ES")
broker.submit_order(order)
# Check order.contract_id - is it Z25 or H26?
# On Dec 22, should be H26
```

### Test 3: Repair During Rollover
```python
# Simulate exchange having position on H26 while
# virtual tracker thinks it's on Z25
# Run repair, check what gets zeroed
```

### Test 4: P&L Attribution
```python
# Track P&L for position that spans rollover
# Compare with position that doesn't
# Are they calculated the same way?
```

---
