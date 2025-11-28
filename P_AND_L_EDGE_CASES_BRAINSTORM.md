# P&L and Attribution Edge Cases: Position Sync/Repair Logic

## Overview
When `repair_position_desync()` zeros phantom virtual positions (where virtual net > exchange qty), it creates several data integrity issues around P&L tracking and strategy attribution.

---

## 1. UNREALIZED P&L VAPORIZATION

### The Problem
When a phantom position is zeroed via `state.tracker.reset()`:
- **Unrealized gains/losses are lost forever** - no record of them
- The `calculate_pnl()` result at time of zeroing is never captured
- Attribution system has no knowledge of this loss

### Scenario: Phantom Long Position with Unrealized Gains
```
ES_1M_01:
  - Virtual position: +5 contracts @ $5850 avg
  - Current price: $5900
  - Unrealized P&L: 5 * (5900 - 5850) * 50 = $12,500 GAIN

ES_1M_02:
  - Virtual position: -5 contracts @ $5900 avg
  - Current price: $5900
  - Unrealized P&L: $0

Exchange position: 0 (net-flat)
Virtual net: 0 (they offset)

>>> repair_position_desync() detects phantom exposure
>>> Zeros ES_1M_01's position (has long excess)
>>> Lost: $12,500 unrealized gain (evaporated, never recorded)
```

### What Could Go Wrong
- **Attribution shows no trade** → strategy gets no credit/debit for the phantom
- **Equity curve gap** → account shows sudden $12.5k drop with no explanation
- **Mystery losses in reports** → reconciliation fails
- **P&L attribution incomplete** → total portfolio P&L ≠ sum of strategy P&L

### Why It Happens
`VirtualPositionTracker.force_flat()` is called with:
```python
pos.quantity = 0
pos.total_cost = 0
pos.last_update = datetime.now()
# NO RECORD OF UNREALIZED P&L AT TIME OF ZEROING
```

The `calculate_pnl()` is only called when you have the current price available, but repair logic doesn't:
1. Capture current price
2. Record the P&L being lost
3. Notify attribution system

---

## 2. ORPHANED UNREALIZED P&L IN ATTRIBUTION

### The Problem
`StrategyAttribution` only tracks **closed trades** via `record_trade()`:
- Entry price, exit price, quantity → calculates realized P&L
- Unrealized P&L is never tracked by attribution (it's in VirtualPositionTracker only)
- When repair zeros the position, attribution has nothing to record

### Scenario: Multi-Strategy Phantom Squeeze
```
Three strategies on ES:
1. ES_1M_01: +2 contracts (unrealized +$1000)
2. ES_1M_02: +2 contracts (unrealized +$2000)
3. ES_1M_03: -4 contracts (unrealized -$500)

Virtual net: 0 (neutral)
Exchange: 0 (actually flat)
But repairs detect +4 long phantom exposure

Repair chooses to zero smallest: ES_1M_01 (-2)
Lost P&L: $1000 (never in attribution.record_trade())
```

### What Could Go Wrong
- **Attribution P&L snapshot** shows ES_1M_01 has 0 closed trades and $0 total_pnl
- **Unrealized P&L is invisible** in reports
- **Month-end statements** don't match (ghosted trades)
- **Sharpe ratio/drawdown calculations** are wrong (missing volatility)
- **Strategy rank ordering** is corrupted (performance metrics incomplete)

---

## 3. PHANTOM POSITION WITH ACCUMULATED FEES

### The Problem
If a phantom position has **passed through fills** that accumulated commissions/fees:
- These fees are part of `VirtualPosition.total_cost`
- When position is zeroed, fees are lost
- Attribution never recorded them (they're in the virtual position, not trade history)

### Scenario: Phantom Position with 3 Fills
```
ES_1M_04 virtual position history:
  Fill 1: BUY 1 @ 5850 + $25 fee = cost $92.5/contract
  Fill 2: BUY 1 @ 5860 + $25 fee = cost $92.5/contract
  Fill 3: BUY 1 @ 5840 + $25 fee = cost $92.5/contract

VirtualPosition state:
  - quantity: 3
  - avg_entry_price: 5850 (weighted average)
  - total_cost: 17550 + 75 (fees) = 17625
  - Implicit fee total: $75

repair_position_desync() zeros it out.
>>> Lost fees: $75 (no attribution record, no trade closure)
>>> P&L attribution for this strategy: MISSING $75 expense
```

### What Could Go Wrong
- **Total fees tracking is incomplete** → reconciliation to broker statement fails
- **Cost basis is wrong** for future positions on same symbol
- **Strategy attribution shows wrong net P&L** (doesn't account for lost fees)
- **Broker fee reconciliation fails** → accounting issues

---

## 4. ENTRY PRICE AVERAGING CORRUPTION

### The Problem
When repair zeros a position, the entry price state is lost. If **the same strategy tries to re-enter** immediately after repair:
- Old entry price history is gone
- New entry price will be calculated from scratch
- Any averaging logic is broken

### Scenario: Repair Mid-Scale
```
ES_1M_05 scaling IN (adding to position):
  Scale 1: BUY 2 @ 5900 → avg = 5900
  Scale 2: BUY 1 @ 5895 → avg = 5898.33 (weighted)
  >>> Phantom repair zeros the position

Next bar: Signal says BUY 2 more
  New avg_entry_price = 5895 (fresh start)
  >>> Lost the scale history: No one knows we bought at 5900 first
  >>> Cost basis calculations are wrong going forward
```

### What Could Go Wrong
- **Average entry price mismatches broker's cost basis** when position is re-established
- **Subsequent profit calculations are off** (based on wrong entry point)
- **Risk management broken** (stop-loss distances calculated from wrong baseline)
- **Bracket orders invalid** (TP/SL based on phantom entry price)

---

## 5. PARTIAL REPAIR INCONSISTENCY

### The Problem
`repair_position_desync()` selects strategies "by smallest position first" to minimize impact. But:
- **Repair is arbitrary** (why zero ES_1M_01 instead of ES_1M_02?)
- **No explanation recorded** in attribution (strategy X vanished, why?)
- **Different strategies have different fairness** (why did my larger position survive?)

### Scenario: 5 Strategies, Need to Zero 3 Contracts
```
Symbol: NQ, Excess: 5 long contracts
Strategies with long positions:
  NQ_1M_01: +3 contracts (unrealized +$5000)
  NQ_1M_02: +1 contract  (unrealized +$1500)
  NQ_1M_03: +2 contracts (unrealized +$2000)

Repair sorts by quantity (smallest first):
  1. Zero NQ_1M_02 (+1) → remaining_to_zero = 4
  2. Zero NQ_1M_01 (+3) → remaining_to_zero = 1
  3. Partial zero of NQ_1M_03 (-1 from 2) → remaining_to_zero = 0

Attribution for NQ_1M_02: $1500 unrealized gain LOST
Attribution for NQ_1M_01: $5000 unrealized gain LOST
Attribution for NQ_1M_03: $1000 unrealized gain LOST (partial)
Total impact: $7500 phantom P&L vaporized
```

### What Could Go Wrong
- **Strategy backtests don't match live** (live had unexpected zeroing)
- **Fairness is opaque** → traders don't know why their strategy was chosen
- **No audit trail** of why repair happened to THIS strategy
- **Historical P&L is non-deterministic** (same trade sequence could zero different strategies)
- **Risk limits violated** → risk manager approved X strategy, not knowing it could be zeroed

---

## 6. TRADE HISTORY LOSS IN TRACKER

### The Problem
`state.tracker.reset()` clears **entire trade_history**:
```python
def reset(self):
    self.positions.clear()
    self.trade_history.clear()  # ← ALL TRADES ERASED
    self.start_time = datetime.now()
```

But trade_history is also used for:
- Idempotency checks (`_processed_order_ids`)
- Backtest/live comparison
- Debugging fills

### Scenario: Repair Clears Trade History
```
ES_1M_06 trade history before repair:
  [
    {time: 10:00, side: buy, qty: 1, price: 5850, order_id: "ord_001"},
    {time: 10:15, side: buy, qty: 1, price: 5855, order_id: "ord_002"},
    {time: 10:30, side: buy, qty: 1, price: 5860, order_id: "ord_003"},
  ]

repair_position_desync() → state.tracker.reset()
>>> trade_history cleared completely
>>> order_ids still in _processed_order_ids (idempotency still works)
>>> But if system crashes and restarts, those orders might be re-processed

>>> Manual investigation: "What trades happened to ES_1M_06?"
    Answer: Blank slate (no history), even though they happened
```

### What Could Go Wrong
- **Audit trail is lost** for regulatory/compliance reviews
- **Debugging becomes impossible** → "Why did ES_1M_06 have 3 contracts?"
- **Crash recovery is broken** → if system restarts during repair, duplicates possible
- **P&L reconciliation for past bars is impossible** → can't prove what happened before repair

---

## 7. BRACKET ORDER ORPHANS DURING REPAIR

### The Problem
`repair_position_desync()` calls `cancel_brackets_for_strategy()` but:
- Cancel might fail (API timeout, order already filled)
- Orphaned brackets remain on exchange
- Position is zeroed locally, but orders still active remotely

### Scenario: TP Order Fills After Repair
```
ES_1M_07 with bracket:
  - Entry: 1 contract @ 5900
  - TP order: SELL 1 @ 5910 (pending)
  - SL order: SELL 1 @ 5890 (pending)

Repair detected phantom, calls cancel_brackets_for_strategy()
>>> SL order cancelled
>>> TP order: **API timeout, CANCEL FAILS**
>>> Virtual position zeroed
>>> But TP order is STILL LIVE on exchange

Next bar: Price rallies to 5910
>>> TP order FILLS
>>> Exchange now shows: FLAT (filled)
>>> Virtual tracker shows: FLAT (was zeroed)
>>> BUT: No attribution record of this trade!
>>> The fill is invisible to strategy attribution
```

### What Could Go Wrong
- **Ghost trades on exchange** (position filled after repair, attributed to nobody)
- **Orphaned orders in registry** → trade reports show fills with no entry
- **P&L mystery** → account shows gain but no strategy claims it
- **Margin violations** → expected flat position, but order fills create new position
- **Next cycle's repair** sees "untracked position" on exchange

---

## 8. COMMISSION ALLOCATION WHEN ZEROING PARTIAL POSITION

### The Problem
When repair **partially zeros** a position (e.g., -1 from 3 contracts):
- `VirtualPosition.total_cost` is updated
- But **how is the fee allocation handled?**
- Are fees prorated? Ignored? Lost?

### Current Code Behavior (Lines 2155-2160)
```python
# Only fully zero if the whole position needs to go
if zero_qty >= abs(qty) - 0.001:
    # Zero out completely
    old_qty = qty
    state.tracker.reset()  # Clears total_cost including all fees
else:
    # Partial zero - NOT IMPLEMENTED?
    # Code doesn't show how to handle this case
```

### Scenario: Partial Zero with 3 Fills
```
ES_1M_08: +3 contracts
  Fill 1: +1 @ 5900, fee $25
  Fill 2: +1 @ 5905, fee $25
  Fill 3: +1 @ 5895, fee $25
  Total cost: (5900 + 5905 + 5895) + 75 = $17775

Repair needs to zero 1 contract (smallest position)
>>> Code path: doesn't handle partial zero (falls to next case)
>>> What happens to fee allocation?

If prorated by quantity:
  Fee per contract: 75 / 3 = $25 per contract
  Remove 1 contract = remove $25 fee
  New total_cost: 11800 + 50 = 11850

If prorated by price:
  Hmm, which fill to remove? (FIFO, LIFO, pro-rata?)
```

### What Could Go Wrong
- **Partial zeros aren't handled** in repair logic (may not be possible)
- **If they are implemented elsewhere**, fee allocation is ambiguous
- **Double-counting or loss of fees** in attribution
- **Cost basis becomes wrong** for remaining position

---

## 9. EQUITY CURVE DISCONTINUITY

### The Problem
When phantom positions are zeroed, the equity curve (in StrategyAttribution) has:
- **Unrealized P&L snapshot at N-1** → includes phantom gains
- **Unrealized P&L snapshot at N** → phantom is gone
- **Sudden gap** in equity curve (not from a trade, just repair)

### Scenario: Daily Equity Curve
```
Time: 10:00 AM
  Portfolio value: $100,000
  Unrealized: +$5,000 (phantoms)
  Equity curve: $105,000

(repair_position_desync() runs at 10:15)
  >>> Phantom positions zeroed

Time: 10:16 AM
  Portfolio value: $100,000
  Unrealized: $0
  Equity curve: $100,000

>>> $5,000 DROP WITH NO TRADE EXPLANATION
```

### What Could Go Wrong
- **Max drawdown calculation wrong** (includes repair gap)
- **Sharpe ratio invalid** (includes non-market volatility)
- **Equity curve visualization misleading** → looks like strategy lost money
- **Risk reporting wrong** → VaR/CVaR includes repair noise
- **Benchmarking broken** → comparison to index includes phantom gap

---

## 10. MULTI-STRATEGY ATTRIBUTION INCONSISTENCY

### The Problem
When repair zeros ES_1M_01 but keeps ES_1M_02 (both on same symbol):
- **ES_1M_01's total_pnl** is incomplete (missing unrealized from phantom)
- **ES_1M_02's total_pnl** is also incomplete (different reason: offset phantom)
- **Sum of strategy P&Ls ≠ total portfolio P&L**

### Scenario: 3-Strategy Hedge Gets Broken
```
Portfolio objective: Hedge ES with NQ
  ES_1M_01 (macro): +2 contracts, unrealized +$2000
  NQ_1M_01 (macro): -1 contract, unrealized -$1000
  Strategy_X (micro): -1 contract, unrealized +$500

Virtual net: 0 (perfectly hedged)
Exchange: 0 (perfectly hedged)

>>> PHANTOM DETECTED (somehow virtual > exchange)
>>> Repair zeros ES_1M_01
>>> Lost: $2000

Now:
  ES_1M_01: $0 P&L (missing $2000)
  NQ_1M_01: -$1000 P&L
  Strategy_X: +$500 P&L
  Total: -$500 (should be 0)

Portfolio is unhedged but reports suggest it was!
```

### What Could Go Wrong
- **Hedge effectiveness metrics wrong** → risk committee approves strategy thinking it's hedged
- **Strategy rank ordering changes** → phantom repair changes which strategy looks best
- **Capital allocation decisions wrong** → allocate more to Strategy_X based on false P&L
- **Reconciliation to broker statement fails** → broker shows aggregate P&L, but strategy attribution doesn't match

---

## 11. FEES IN ORDER REGISTRY MISMATCH

### The Problem
If `OrderRegistry` is tracking fees independently from VirtualPositionTracker:
- **OrderRegistry has a fee record** for phantom orders
- **VirtualPositionTracker fees are lost** when zeroed
- **Sum of strategy fees ≠ actual broker fees**

### Scenario: Order Registry Vs Tracker
```
OrderRegistry for ES_1M_09:
  - Order ord_001: -$30 fee (recorded)
  - Order ord_002: -$30 fee (recorded)
  - Order ord_003: -$30 fee (recorded)
  Total fees: -$90

VirtualPositionTracker for ES_1M_09:
  - total_cost includes $90 in fees

repair_position_desync() zeros tracker
>>> tracker.total_cost = 0 (fees lost)
>>> OrderRegistry still shows -$90 (fees recorded)

Fee reconciliation:
  Expected: -$90 (from OrderRegistry)
  Actual in tracker: $0
  Discrepancy: $90 (unexplained)
```

### What Could Go Wrong
- **Fee reconciliation to broker fails** by $90
- **Strategy attribution P&L wrong** (missing fee expense)
- **Audit trail broken** → "Where did the $90 go?"
- **Multi-source P&L reconciliation impossible**

---

## 12. SESSION/BRACKET CONTEXT LOSS

### The Problem
When repair zeros a position, it loses context about:
- **Which session the position was entered in** (NY? London? Asia?)
- **Current bracket TP/SL prices** (stored in `state.take_profit_price`, `state.stop_loss_price`)
- **Entry timestamp** (for P&L analysis by session)

### Current Code (Lines 2200-2202)
```python
state.entry_price = None
state.take_profit_price = None
state.stop_loss_price = None
```

### Scenario: Cross-Session Phantom
```
ES_1M_10 entered during London session:
  - Entry time: 2025-11-26 08:00:00 UTC
  - Entry price: 5900
  - Entry session: London
  - Take-profit: 5920
  - Stop-loss: 5880

Repair occurs during NY session:
  - Zeros the position
  - state.entry_price = None
  - state.entry_time is kept (but other context lost)

What was this position's context?
  >>> No answer (context data was one-time, not in trade_history)
  >>> Attribution doesn't know which session P&L came from
```

### What Could Go Wrong
- **Session-based performance metrics wrong** → "Which session was most profitable?"
- **Time-to-profit analysis impossible** → duration from entry to repair unknown
- **Cross-session hedge tracking breaks** → thought we were hedged NY+London, but repair broke it
- **Risk dashboard incomplete** → can't show "ES at risk in London session"

---

## 13. IDEMPOTENCY ISSUES WITH CLEARED FILLS

### The Problem
`repair_position_desync()` calls `_clear_processed_fills_for_strategy()` (Line 2213):
```python
cleared_count = self._clear_processed_fills_for_strategy(sid)
```

But `_processed_order_ids` is still populated. So:
- **Order ID is still marked as processed**
- **But the trade record is gone from trade_history**
- **If same order is processed again**, it returns the current position (which is now FLAT)

### Scenario: Order Re-processing Post-Repair
```
Order ord_004: BUY 1 @ 5850

First cycle:
  - execute_order("ES", 1, "buy", 5850, order_id="ord_004")
  - VirtualPosition: +1 @ 5850
  - _processed_order_ids: {"ord_004"}
  - trade_history: [{ord_004, ...}]

Repair zeros position:
  - _clear_processed_fills_for_strategy() removes trade_history
  - _processed_order_ids: still has "ord_004"  ← PROBLEM

Second cycle (crash recovery, duplicate fill):
  - execute_order("ES", 1, "buy", 5850, order_id="ord_004") [DUPLICATE]
  - Checks: "is ord_004 in _processed_order_ids?" YES
  - Returns: self.positions.get("ES") → FLAT
  - No update: Position stays flat instead of +1

>>> Double-spend check worked (prevented double-booking)
>>> But now position is wrong, and no one knows why
```

### What Could Go Wrong
- **Position recovery breaks** → crash happens during repair, recovery fails
- **Crash recovery is unreliable** → lose data on system restart
- **Phantom positions can't be recovered** → even if you reload from logs, tracker is gone

---

## 14. MULTIPLIER DEFAULTING DURING P&L LOSS

### The Problem
`VirtualPosition.calculate_pnl()` tries to load multiplier for futures:
```python
def calculate_pnl(self, current_price: float) -> float:
    if self.quantity == 0:
        return 0.0
    try:
        from custom_portfolio.data.futures_metadata import get_multiplier
        multiplier = get_multiplier(self.symbol)
        # Track if multiplier returned default value of 1.0
        if multiplier == 1.0:
            _multiplier_defaults.add(self.symbol)
    except Exception:
        multiplier = 1.0
        _multiplier_defaults.add(self.symbol)
    return self.quantity * (current_price - self.avg_entry_price) * multiplier
```

When repair zeros a position **without calling calculate_pnl()**, the multiplier is never verified.

### Scenario: Wrong Multiplier for GC
```
GC (Gold) actual multiplier: 100 oz

ES_1M_11 has phantom position:
  +1 GC @ 2500 (phantom)
  Current price: 2505
  Unrealized P&L: 1 * (2505 - 2500) * 100 = $500

repair_position_desync() zeros it WITHOUT calculating P&L
>>> Lost $500 (never recorded)
>>> _multiplier_defaults is never populated (would have been)
>>> End-of-run warning "GC had multiplier default" doesn't appear
>>> No one knows the P&L loss was sized at $500/tick

If someone manually investigates:
  "What was the P&L when zeroed?"
  Answer: Unknown (not recorded)
  Estimated: +$5 (if 1.0 multiplier used) vs actual $500
  Off by 100x!
```

### What Could Go Wrong
- **P&L magnitude loss is unknown** → repair loss could be off by 10-100x
- **Different symbols have different multipliers**, all lost with same zeroing
- **End-of-run warnings don't cover repair losses**
- **Manual reconciliation impossible** without broker statement details

---

## 15. NO COMPENSATION/FORCED TRADE RECORD

### The Problem
When repair zeros a position, it's **not recorded as a trade** in attribution:
- No `attribution.record_trade()` call
- No entry/exit prices recorded
- No forced closure documented

Contrast with normal trade closure:
```python
# Normal trade closure:
attribution.record_trade(
    strategy_id="ES_1M_12",
    entry_price=5900,
    exit_price=5905,
    quantity=2,
    timestamp=datetime.now(),
    fees=50,
)
```

### What Repair Does
```python
# repair_position_desync() does:
state.tracker.reset()  # ← Just zeros, no attribution record
state.entry_price = None
state.take_profit_price = None
state.stop_loss_price = None
# ← That's it! No record of P&L impact
```

### Scenario: Forced Closure vs Normal Trade
```
Scenario A - Normal trade (recorded):
  attribution.record_trade(ES_1M_13, entry=5900, exit=5910, qty=1, pnl=+$500)
  Reports show: +$500 gain from ES_1M_13

Scenario B - Phantom forced closure (not recorded):
  position: +1 @ 5900
  current price: 5910
  unrealized: +$500
  repair zeros it

  Reports show: ES_1M_13 has $0 trades, $0 pnl
  Truth: +$500 was lost
  Discrepancy: $500 (orphaned)
```

### What Could Go Wrong
- **No audit trail for forced closures** → can't explain why position was zeroed
- **Strategy P&L is incomplete** → missing forced closure trades
- **Reports are misleading** → show $0 P&L when reality is +$500 loss
- **Regulatory audits fail** → "Explain this $500 discrepancy" → No record exists
- **Automated reconciliation breaks** → scripts can't find matching trade

---

## SUMMARY TABLE: IMPACT BY SEVERITY

| Edge Case | Severity | Impact | Detection |
|-----------|----------|--------|-----------|
| Unrealized P&L vaporization | **CRITICAL** | Lost gains evaporate, equity curve gap | Sudden net worth drop |
| Orphaned unrealized P&L in attribution | **CRITICAL** | Attribution metrics invalid | P&L reports don't match broker |
| Phantom position with fees | **HIGH** | Fee reconciliation fails, cost basis wrong | Broker fee mismatch |
| Entry price averaging corruption | **HIGH** | Future positions have wrong cost basis | P&L wrong on re-entry |
| Partial repair inconsistency | **HIGH** | Unfair strategy selection, non-deterministic | Backtest vs live mismatch |
| Trade history loss | **HIGH** | Audit trail gone, debugging impossible | Manual investigation blocked |
| Bracket order orphans | **CRITICAL** | Ghost fills on exchange, orphaned orders | Margin violations, untracked P&L |
| Commission allocation in partial zero | **HIGH** | Fees misallocated or lost | Fee reconciliation fails |
| Equity curve discontinuity | **MEDIUM** | Risk metrics wrong (Sharpe, drawdown) | Risk reports invalid |
| Multi-strategy attribution inconsistency | **CRITICAL** | Sum of P&Ls ≠ total, hedge breaks | Reconciliation fails |
| Order registry fee mismatch | **HIGH** | Fee tracking inconsistent across systems | Audit discrepancy |
| Session/bracket context loss | **MEDIUM** | Session analysis impossible, risk incomplete | Dashboard missing data |
| Idempotency issues | **CRITICAL** | Crash recovery fails, double-spend possible | Position wrong after restart |
| Multiplier defaulting during loss | **MEDIUM** | P&L magnitude unknown (10-100x off) | Manual investigation required |
| No forced trade record | **CRITICAL** | No audit trail, regulatory compliance fail | Audit finding |

---

## RECOMMENDED MITIGATIONS

### 1. Capture Unrealized P&L Before Zeroing
```python
# Before: state.tracker.reset()
if pos and pos.quantity != 0 and current_price is not None:
    unrealized_pnl = pos.calculate_pnl(current_price)
    # Record as forced trade in attribution
    attribution.record_trade(
        strategy_id=sid,
        entry_price=pos.avg_entry_price,
        exit_price=current_price,
        quantity=pos.quantity,
        timestamp=datetime.now(),
        fees=0,  # Already in cost basis
        symbol=symbol,
    )
# After: state.tracker.reset()
```

### 2. Create Repair Event Record
```python
class RepairEvent:
    timestamp: datetime
    symbol: str
    strategy_id: str
    old_qty: float
    new_qty: float
    reason: str
    unrealized_pnl: float
    entry_price: float
    current_price: float
    brackets_cancelled: List[str]
```

### 3. Attribution for Forced Closures
Create a distinct trade type:
```python
attribution.record_trade(
    ...,
    trade_type="forced_closure",  # vs "normal"
    closure_reason="phantom_repair",
)
```

### 4. Validate Against Broker Before Zeroing
```python
# Get current market price from quote
current_price = client.get_quote(symbol)
# Calculate P&L that will be lost
pnl_loss = pos.calculate_pnl(current_price)
# Log with full context
logger.warning(f"[REPAIR] Zeroing {sid} {symbol}: "
               f"Losing ${pnl_loss:+.2f} unrealized from {pos.quantity} contracts")
```

### 5. Maintain Separate Repair Ledger
```python
class RepairLedger:
    repairs: List[RepairEvent]
    total_pnl_lost: float
    total_contracts_zeroed: int

def add_repair(event: RepairEvent):
    repairs.append(event)
    total_pnl_lost += event.unrealized_pnl
    total_contracts_zeroed += abs(int(event.old_qty))
```

### 6. Prevent Bracket Orphans
```python
# Cancel brackets BEFORE zeroing position
cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=1.0)
if not cancel_result.get("fully_cancelled"):
    logger.error(f"Failed to cancel all brackets for {sid}, skipping repair")
    return result  # Don't zero if cancellation failed
```

### 7. Handle Partial Zeros Explicitly
```python
if zero_qty < abs(qty):
    # Partial zero: allocate fees pro-rata
    fee_per_contract = state.tracker.get_position(symbol).total_cost / abs(qty)
    remaining_position_qty = abs(qty) - zero_qty
    new_total_cost = remaining_position_qty * fee_per_contract
    # Update tracker explicitly
    pos.total_cost = new_total_cost
```

### 8. End-of-Day Repair Report
```python
repair_summary = {
    "total_repairs": len(repairs_made),
    "total_pnl_lost": sum(r["pnl_loss"] for r in repairs_made),
    "symbols_affected": set(r["symbol"] for r in repairs_made),
    "strategies_affected": set(r["strategy"] for r in repairs_made),
}

logger.warning(f"[REPAIR SUMMARY] {repair_summary}")
# Email/alert risk team daily
```

---

## QUESTIONS FOR IMPLEMENTATION

1. **How is current price captured during repair?** (needed for unrealized P&L)
2. **Should forced closures be recorded as trades?** (vs separate repair ledger)
3. **Who has access to correct multipliers?** (during repair, without data source)
4. **What's the recovery procedure if repair is interrupted?** (idempotency)
5. **How do we prevent bracket orphans?** (cancel before zero, or validate after?)
6. **Should partial repairs be even attempted?** (too complex, just zero completely?)
7. **How is this coordinated with StrategyAttribution?** (real-time callback?)
8. **What alert threshold triggers repair?** (only on major desync, or always?)
9. **How often does repair actually occur in practice?** (rare, or frequent?)
10. **Should repair be logged to a separate audit table?** (for compliance review)
