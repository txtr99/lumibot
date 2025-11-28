# Position Sync/Repair Edge Cases: Fractional & Partial Quantity Scenarios

## Overview
This document brainstorms failure modes in a multi-strategy futures trading system where:
- Virtual positions are tracked **locally per-strategy**
- Exchange reports a **single aggregated position** per symbol
- Repair logic zeros **phantom virtual positions** when `exchange_qty < virtual_net_qty`
- **FOCUS**: Fractional quantities, rounding errors, floating-point precision issues

---

## System Architecture (Context)

### Current Thresholds
- **POSITION_EPSILON = 1e-9** (line 17, virtual_position_tracker.py)
- **Mismatch tolerance = 0.001** (line 1117, portfolio_manager.py)
- **Repair reduction threshold = 0.001** (line 2112, bracket_order_manager.py)

### Aggregation Logic
```python
# From portfolio_manager.py (line 1089)
result["exchange_positions"][symbol] = result["exchange_positions"].get(symbol, 0.0) + qty

# From portfolio_manager.py (lines 1098-1102)
for strategy_state in self.executor.strategies:
    symbol = strategy_state.symbol
    pos = strategy_state.tracker.get_position(symbol)
    qty = pos.quantity if pos else 0.0
    result["virtual_positions"][symbol] = result["virtual_positions"].get(symbol, 0.0) + qty
```

### Repair Logic (simplified)
```python
# From bracket_order_manager.py (lines 2110-2125)
excess = virtual_qty - exchange_qty
if abs(excess) < 0.001:  # Tolerance check
    continue  # Skip repair
remaining_to_zero = abs(excess)
for sid, state, qty in strategies_with_pos:
    zero_qty = min(abs(qty), remaining_to_zero)
    remaining_to_zero -= zero_qty
    if zero_qty >= abs(qty) - 0.001:
        state.tracker.reset()  # Zero out completely
```

---

## Edge Case Categories

### 1. ROUNDING ERROR ACCUMULATION

#### Scenario 1.1: Micro-contracts Accumulating Fractions
**Setup:**
- 30 strategies trading MES (0.5x ES multiplier)
- Each strategy reports virtual qty as float: 1.0, 1.0, 1.0, ...
- Exchange API sums to: 30.0
- But internal float representations have tiny variance

**Problem:**
```
Strategy virtual positions (float):
  ES_1M_01: 1.0000000001 (due to avg price calculation)
  ES_1M_02: 0.9999999999
  ES_1M_03: 1.0000000000
  ...
  ES_1M_30: 1.0000000001

Virtual net: 30.000000030 (30 * 1.000000001 approximately)
Exchange: 30.0 (exact, from broker)

Delta: 0.000000030 (well below 0.001 tolerance)
BUT if we accumulate across 100 iterations...
```

**What goes wrong:**
- Tolerances are too coarse for micro-contracts traded at scale
- Over 100 iterations, tiny errors compound: `30 * 1.000000001^100 ≈ 30.000003`
- Eventually exceeds 0.001 tolerance and triggers false positive repair

#### Scenario 1.2: Division-by-Zero Rounding in Average Price
**Setup:**
- Strategy opens position: buy 1 MES at $5000
- Virtual tracker: `avg_entry_price = total_cost / qty` (line 148, virtual_position_tracker.py)
- Each subsequent trade adds cost via `pos.total_cost += new_cost`

**Problem:**
```python
# First trade
pos.total_cost = 1 * 5000 = 5000.0
pos.avg_entry_price = 5000.0 / 1 = 5000.0

# Second trade (add 1 more contract at 5001.50)
pos.total_cost = 5000.0 + 5001.50 = 10001.50
pos.avg_entry_price = 10001.50 / 2 = 5000.75

# After partial close (reduce by 1.5 contracts)
# Line 158: pos.total_cost = abs(new_qty * pos.avg_entry_price)
new_qty = 0.5
pos.total_cost = 0.5 * 5000.75 = 2500.375
# BUT original total_cost was 10001.50
# Rounding error: 10001.50 - 2500.375 = 7501.125 (incorrect cost basis)
```

**What goes wrong:**
- Floating-point division introduces rounding errors
- Recalculated `total_cost` from `avg_entry_price` is NOT the same as actual sum
- If position is later zeroed and recreated, new average price will be wrong
- P&L calculations will be off

#### Scenario 1.3: Aggregation Order-Dependency
**Setup:**
- 10 strategies, each with floating-point quantities
- Aggregation order: Strategy A + B + C + ... (line 1102, portfolio_manager.py uses list iteration)

**Problem:**
```python
result = 0.0
for qty in [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]:
    result += qty

# Python: 0.1 is not exactly representable in binary floating-point
# result ≈ 0.9999999999999999 (due to IEEE 754 precision)
# NOT 1.0!

# Exchange might report exactly 1.0 (rounded by broker)
# Delta: 0.0000000000000001 (well below tolerance)
```

**What goes wrong:**
- Commutative addition is NOT associative in floating-point
- Sum depends on order of operations
- Exchange may use different rounding strategy
- Over many symbols/strategies, errors accumulate unevenly

---

### 2. FRACTIONAL FILLS & PARTIAL CLOSES

#### Scenario 2.1: Partial Fill with Fractional Remainder
**Setup:**
- Strategy places order to buy 2.5 contracts (allowed in some futures markets)
- Exchange fills 2.0 contracts, 0.5 remains open order
- System assumes market orders fill immediately (line 97, multi_strategy_executor_enhanced.py)

**Problem:**
```python
# Virtual tracker executes_order(qty=2.5, side='buy')
# Assumes fill for full 2.5, updates virtual position: qty=2.5

# But exchange only filled 2.0
# Virtual: 2.5
# Exchange: 2.0
# Delta: 0.5 ❌ EXCEEDS 0.001 TOLERANCE

# On next reconciliation:
# Repair logic tries to zero 0.5 contracts
# But which strategy gets zeroed? (line 2187, bracket_order_manager.py)
# If only 1 strategy has position, it gets zeroed entirely
```

**What goes wrong:**
- Assumes all market orders fill completely and immediately
- Doesn't handle partial fills or rejected orders
- Repair logic is too aggressive (zeroes entire position, not partial)
- Could lose 2.0 contracts of real position trying to fix 0.5 phantom

#### Scenario 2.2: Fractional Share Division in Multi-Leg Repair
**Setup:**
- Symbol ES has 5 phantom contracts
- 3 strategies holding longs: S1 (2.0 qty), S2 (1.5 qty), S3 (2.0 qty)
- Need to zero 5.0 total

**Problem:**
```python
# Repair logic (line 2182, bracket_order_manager.py):
strategies_with_pos.sort(key=lambda x: abs(x[2]))
# Sorted: [(S2, 1.5), (S1, 2.0), (S3, 2.0)]

remaining_to_zero = 5.0

# First iteration:
zero_qty = min(1.5, 5.0) = 1.5
remaining_to_zero = 3.5  ✓

# Second iteration:
zero_qty = min(2.0, 3.5) = 2.0
remaining_to_zero = 1.5  ✓

# Third iteration:
zero_qty = min(2.0, 1.5) = 1.5
# BUT: Line 2196 checks: zero_qty >= abs(qty) - 0.001
# 1.5 >= 2.0 - 0.001 = 1.999? FALSE!
# So it doesn't zero S3 completely, partial zero leaves S3 with 0.5 qty
remaining_to_zero = 0.0  ✓

# BUT: How does state.tracker.reset() work with partial amounts?
# reset() zeros EVERYTHING for that strategy (line 2199)
# S3 was completely zeroed even though we only wanted 1.5 ❌
```

**What goes wrong:**
- Repair logic calls `state.tracker.reset()` unconditionally
- Doesn't support partial repairs
- Rounding check (line 2196) is binary: either zero all or zero nothing
- Can wipe out valid position trying to fix fractional error

#### Scenario 2.3: Micro-Contract Trades Below Exchange Precision
**Setup:**
- MGC (micro gold, 0.01 oz = 1/10 of GC)
- Strategy attempts: buy 0.1 MGC (really 0.01 oz of gold)
- Exchange minimum trade: 0.1 MGC (but internally tracks as 10^-8)

**Problem:**
```python
# Virtual order:
execute_order(symbol='MGC', quantity=0.1, side='buy', price=2000.5)
# Virtual position: qty = 0.1
# total_cost = 0.1 * 2000.5 = 200.05
# avg_entry_price = 200.05 / 0.1 = 2000.5

# Exchange fill confirmation:
# Actually filled: 0.10000000001 (due to precision in API response)
# Exchange sees: 0.10000000001 MGC
# Virtual sees: 0.1 MGC
# Delta: 0.00000000001 ❌ Below epsilon tolerance

# But across 1000 positions, this becomes:
# 1000 * 0.00000000001 = 0.00001 (NOW exceeds tolerance)
```

**What goes wrong:**
- POSITION_EPSILON (1e-9) is too small for large-scale trading
- Exchange API precision varies by contract/broker
- Accumulation of sub-epsilon errors becomes supra-epsilon over time

---

### 3. EXCHANGE API INCONSISTENCIES

#### Scenario 3.1: Exchange Rounds, Virtual Doesn't
**Setup:**
- Multiple strategies send staggered orders over 30 seconds
- Each fills at slightly different prices (market movement)
- Exchange aggregates positions using internal rounding (e.g., banker's rounding)
- Virtual tracker sums raw floats

**Problem:**
```python
# Virtual (raw sum):
qty1 = 1.0000000001 ES @ 4500.00
qty2 = 1.0000000001 ES @ 4500.01
qty3 = 0.9999999998 ES @ 4499.99
virtual_net = 3.0000000000 (30 decimal places!)

# Exchange (rounded representation):
qty1 = 1.0 ES
qty2 = 1.0 ES
qty3 = 1.0 ES
exchange_sum = 3.0 (clean)

# BUT: Exchange displays 3.0, API returns 3.0
# Virtual thinks it's 3.0000000000
# This is actually OK (below tolerance)

# HOWEVER: What if exchange uses TRUNCATION instead of ROUNDING?
exchange_sum = 2.999999999 (truncated)
delta = 3.0000000000 - 2.999999999 = 0.0000000001 ✓ OK

# But if exchange uses BANKER'S ROUNDING:
exchange_sum = 3.0 (round to nearest even)
virtual_net = 3.0000000001
delta = 0.0000000001 ✓ OK
```

**What goes wrong:**
- Exchange rounding strategy is not documented
- Different exchanges may use different rounding
- System has no way to know what precision level exchange actually uses
- Mismatch tolerance (0.001) is arbitrary and may not reflect actual precision

#### Scenario 3.2: Exchange Stale Position Cache
**Setup:**
- 15:08 CT: Strategy enters 2.0 ES long
- 15:09 CT: Reconciliation happens, exchange API hit
- Exchange position cache returns 1.5 ES (stale, from previous iteration)
- 15:10 CT: Actual position confirmed as 2.0 ES

**Problem:**
```python
# Iteration 1 (15:09):
virtual_net = 2.0
exchange_cached = 1.5
delta = 0.5 ❌ EXCEEDS TOLERANCE
repair triggered: zero out 0.5 phantom

# Virtual tracker zeroed:
virtual_net now = 1.5 (matches exchange cache!)

# Iteration 2 (15:10):
virtual_net = 1.5 (after zeroing)
exchange_fresh = 2.0 (real position confirmed)
delta = -0.5 ❌ EXCEEDS TOLERANCE (other direction)

# Now we have "untracked position" problem
# Exchange shows 2.0 but virtual shows 1.5
# Can't auto-repair because direction conflict (line 2148)
```

**What goes wrong:**
- Assumes exchange API is real-time (it's not always)
- Repair happens immediately based on potentially stale data
- Creates position delta in opposite direction
- System blocks repairs when direction is wrong (safety feature but creates deadlock)

#### Scenario 3.3: Exchange Batches Fills with Rounding
**Setup:**
- System places 30 orders at once (rate limiter: 2 sec/order = 60 sec total)
- Exchange fills orders in batches and rounds at batch boundary

**Problem:**
```python
# Orders 1-15 placed and filled in batch 1:
# Virtual net: 1.0 * 15 = 15.0
# Exchange reports after batch 1: 15.0

# Orders 16-30 placed and filled in batch 2:
# Virtual net: 1.0 * 15 = 15.0
# Exchange reports after batch 2: 15.0

# BUT: Exchange has rounding on batch boundary
# Batch 1 result: 14.9999999999 (before rounding)
# Batch 2 result: 14.9999999999 (before rounding)
# Exchange rounds each batch to: 15.0

# API call gets: batch1_rounded + batch2_rounded = 30.0
# Virtual thinks: 15.0 + 15.0 = 30.0
# BUT actual unrounded: 14.9999999999 + 14.9999999999 = 29.9999999998

# If exchange API returns unrounded for audit trail:
# You get: 29.9999999998
# Virtual: 30.0
# Delta: 0.0000000002 ✓ OK (but fragile)
```

**What goes wrong:**
- Exchange may apply rounding at different points in pipeline
- API may return different values depending on endpoint (realtime vs audit)
- System has no visibility into where rounding happens

---

### 4. MICRO-CONTRACT SCALE ISSUES

#### Scenario 4.1: MES Scale Explosion with 50 Strategies
**Setup:**
- 50 strategies each trading 5 MES contracts (50 * 5 = 250 MES total)
- MES notional: $250 * 50 * 0.5 = $6,250,000
- Each 0.01-point move = $125 on each contract

**Problem:**
```python
# Each strategy's precision needs:
# - Price precision: $0.25/tick (MES)
# - Qty precision: 1.0 (can't trade 0.5 MES easily)
# - BUT: Avg price calculation divides by qty

# Strategy 1:
# Buy 5 MES @ 5000.00 = 1,250,000 (notional)
# total_cost = 5 * 5000.00 = 25,000.0
# avg_price = 25,000.0 / 5 = 5000.00 ✓

# After selling 2.5 MES (partial close):
# Line 158: total_cost = 2.5 * 5000.00 = 12,500.0
# avg_price = 12,500.0 / 2.5 = 5000.00 ✓

# BUT: In 32-bit float, 25,000.0 might be:
# 25000.0 = 0x460C4000 (exact in IEEE 754)
# After division: 25000.0 / 5 = 5000.0 (exact)
# In 64-bit float (Python uses), no problem

# HOWEVER: If intermediate calculations use 32-bit:
# 25000.0000 -> 25000.0001 (rounding up)
# avg_price = 25000.0001 / 5 = 5000.00002
# Lost precision!
```

**What goes wrong:**
- Large notional amounts amplify rounding errors
- Intermediate float precision loss compounds
- P&L calculations depend on precise average prices
- System has no validation that avg_price is actually achievable

#### Scenario 4.2: Micro vs Full Contract Mixing
**Setup:**
- Strategy trades both ES (full) and MES (micro, 0.5 multiplier)
- Notional: 1 ES = 2 MES
- Virtual tracker aggregates by qty, not by notional

**Problem:**
```python
# Virtual positions:
ES: 10.0 contracts (notional: $500,000)
MES: 10.0 contracts (notional: $125,000)
Total virtual qty: 20.0
Total notional: $625,000

# Exchange positions (if it combines them):
# Option 1: Exchange treats them separately
# ES: 10.0, MES: 10.0 (sum = 20.0 qty)

# Option 2: Exchange normalizes to ES equivalent
# ES equivalent: 10.0 + (10.0 / 2) = 15.0 ES-equivalent
# If aggregation uses ES-equivalent: 15.0
# Virtual uses raw qty: 20.0
# Delta: 5.0 ❌ EXCEEDS TOLERANCE!

# Repair logic sees:
# excess = 20.0 - 15.0 = 5.0 phantom contracts
# Tries to zero 5.0 contracts' worth
# But which symbol? ES or MES?
# Line 2104-2105: scans by symbol, processes ES_phantom then MES_phantom
# Might zero both partially in wrong ratio
```

**What goes wrong:**
- Virtual tracker uses qty, exchange may use notional or ES-equivalent
- No standardization of "position units"
- Repair logic assumes all positions in same symbol can be zeroed equally
- Doesn't account for micro/full contract multipliers

#### Scenario 4.3: Contract Multiplier Mismatch in Repair
**Setup:**
- Symbol: GC (gold future, multiplier = 100)
- Virtual position: 2.0 GC
- Exchange shows: 2.0 GC
- But P&L from virtual tracker uses wrong multiplier

**Problem:**
```python
# Virtual position:
pos.quantity = 2.0 GC
pos.avg_entry_price = 2000.00

# P&L calculation (line 74, virtual_position_tracker.py):
def calculate_pnl(self, current_price):
    multiplier = get_multiplier(self.symbol)  # = 100 for GC
    return self.quantity * (current_price - self.avg_entry_price) * multiplier

# At $2010.00:
pnl = 2.0 * (2010.00 - 2000.00) * 100 = 2.0 * 10 * 100 = $2000 ✓

# BUT: If get_multiplier() fails (line 71-73):
multiplier = 1.0  # ❌ DEFAULT!
pnl = 2.0 * 10 * 1.0 = $20 (100x too low!)

# System continues with wrong P&L
# Repair logic might zero position based on:
# "We're underwater because P&L is too low"
# BUT we're actually up $2000, not $20
```

**What goes wrong:**
- Multiplier lookup can silently fail
- System continues with wrong values
- P&L used for repair decision is incorrect
- Repair might happen when not needed

---

### 5. REPAIR LOGIC BOUNDARY CONDITIONS

#### Scenario 5.1: Exact Boundary at Tolerance
**Setup:**
- Exchange reports: 10.0
- Virtual sums to: 10.0005 (just at or above 0.001 tolerance)

**Problem:**
```python
delta = 10.0005 - 10.0 = 0.0005
if abs(delta) > 0.001:  # Line 1117, portfolio_manager.py
    # This evaluates to: 0.0005 > 0.001? FALSE
    # NO mismatch detected!

# But on next iteration:
# Due to floating point arithmetic:
delta = 10.000500001 - 10.0 = 0.000500001
# Still FALSE (0.000500001 > 0.001? NO)

# Then:
delta = 10.00050001 - 10.0 = 0.00050001
# Still FALSE

# Then due to accumulation:
delta = 10.0005 - 10.0 = 0.0005
# Still FALSE, but what if order of operations changes?
delta = (10.0 + 0.00025 + 0.00025) - 10.0 = 0.0005
# vs
delta = (10.00025 + 0.00025) - 10.0 = 0.0005
# Same, but might differ due to intermediate rounding

# Then at some point:
delta = 10.001000001 - 10.0 = 0.001000001
# NOW TRUE (0.001000001 > 0.001)
# REPAIR TRIGGERED!
```

**What goes wrong:**
- Tolerance boundary (0.001) is too sharp
- Floating-point accumulation can cross threshold unexpectedly
- No hysteresis: once you cross, you can easily reverse and cross back
- Potential for oscillation: trigger repair → overshoot → trigger opposite direction

#### Scenario 5.2: Remainder After Fractional Zeroing
**Setup:**
- Need to zero 5.5 contracts
- Have 4 strategies with positions: 2.0, 2.0, 2.0, 1.0

**Problem:**
```python
# From repair logic (line 2186-2238):
remaining_to_zero = 5.5
strategies = [(S1, 1.0), (S2, 2.0), (S3, 2.0), (S4, 2.0)]  # sorted by qty

# Iteration 1: S1
zero_qty = min(1.0, 5.5) = 1.0
remaining_to_zero = 4.5
S1 zeroed? 1.0 >= 1.0 - 0.001 = 0.999? YES → S1.reset()

# Iteration 2: S2
zero_qty = min(2.0, 4.5) = 2.0
remaining_to_zero = 2.5
S2 zeroed? 2.0 >= 2.0 - 0.001 = 1.999? YES → S2.reset()

# Iteration 3: S3
zero_qty = min(2.0, 2.5) = 2.0
remaining_to_zero = 0.5
S3 zeroed? 2.0 >= 2.0 - 0.001 = 1.999? YES → S3.reset()

# Iteration 4: S4
zero_qty = min(2.0, 0.5) = 0.5
remaining_to_zero = 0.0 (goal achieved!)
S4 zeroed? 0.5 >= 2.0 - 0.001 = 1.999? NO
# S4 NOT zeroed (correct!)

# BUT: remaining_to_zero = 0.0, so we exit cleanly
# Line 2234-2248: Check if remaining_to_zero > 0.001
# 0.0 > 0.001? NO
# No warning issued even though we're good

# SCENARIO B: What if orders don't fill cleanly?
remaining_to_zero = 5.5
strategies = [(S1, 2.3), (S2, 2.2), (S3, 1.1)]  # 5.6 total

# Iteration 1: S1
zero_qty = min(2.3, 5.5) = 2.3
remaining_to_zero = 3.2
S1.reset()

# Iteration 2: S2
zero_qty = min(2.2, 3.2) = 2.2
remaining_to_zero = 1.0
S2.reset()

# Iteration 3: S3
zero_qty = min(1.1, 1.0) = 1.0
remaining_to_zero = 0.0
S3 zeroed? 1.0 >= 1.1 - 0.001 = 1.099? NO
# S3.reset() NOT called
# But S3 still has 0.1 phantom!

# Line 2234: remaining_to_zero > 0.001? 0.0 > 0.001? NO
# No warning about S3's 0.1 phantom
```

**What goes wrong:**
- Tolerance check (0.001) is absolute but should be relative
- Can leave behind sub-tolerance remainder
- Doesn't account for position that only partially filled
- S3 still has 0.1 contract phantom

#### Scenario 5.3: What if Tolerance Exactly Equals Epsilon?
**Setup:**
- POSITION_EPSILON = 1e-9
- Mismatch tolerance = 0.001 (1e-3)
- Repair threshold = 0.001 (1e-3)

**Problem:**
```python
# Position after multiple rounds of adding/reducing:
virtual_qty = 1.0000000001  # Just above epsilon
exchange_qty = 1.0

delta = 0.0000000001  # 1e-10
if abs(delta) > 0.001:  # 1e-10 > 1e-3? NO
    # Not treated as mismatch ✓

# But what if:
virtual_qty = 0.0000000005  # Between epsilon and tolerance
exchange_qty = 0.0

if abs(virtual_qty) > 0:  # Tracker says it has position
    pos = get_position()  # Returns VirtualPosition with qty=0.0000000005
    if pos and abs(pos.quantity) > 0.001:  # Line 2137
        # 0.0000000005 > 0.001? NO
        # This position is IGNORED in repair
        # But tracker still has it!
        # Next cycle, might accumulate to 0.001

# What about POSITION_EPSILON check?
# Line 147-153: when position closes:
if abs(new_qty) < POSITION_EPSILON:
    pos.quantity = 0
    # 0.0000000001 < 1e-9? NO (1e-10 > 1e-9? NO)
    # Position NOT zeroed!
    # Stays at 0.0000000001
```

**What goes wrong:**
- POSITION_EPSILON (1e-9) is too strict
- Mismatch tolerance (1e-3) is too loose
- Gap between them (1e-9 to 1e-3) allows "zombie" positions
- Positions < 1e-3 but >= 1e-9 can live forever
- Accumulate to exceed tolerance threshold

---

### 6. PARTIAL FILL RACE CONDITIONS

#### Scenario 6.1: Reconciliation During Order Execution
**Setup:**
- 15:00:00.000: Executor places 30 orders (takes ~60 seconds due to rate limit)
- 15:00:30.000: Reconciliation check runs (mid-execution)
- Orders 1-15 have filled, orders 16-30 not filled yet

**Problem:**
```python
# At 15:00:30.000:
virtual_net = 15.0 (filled) + 15.0 (not yet filled) = 30.0
exchange = 15.0 (only filled orders)

delta = 30.0 - 15.0 = 15.0 ❌ EXCEEDS TOLERANCE!

# Reconciliation thinks: "We have 15 phantom!"
# Repair triggered: zero 15 contracts from virtual

# At 15:01:00.000: Orders 16-30 finally execute
# Executor tries to update virtual position for orders 16-30
# But virtual already zeroed those!
# Double-counting or loss of position
```

**What goes wrong:**
- Reconciliation assumes no orders in-flight
- System doesn't track "pending" orders separately
- Repair assumes all virtual positions are on exchange

#### Scenario 6.2: Order Rejected After Partial Fill
**Setup:**
- Strategy places limit order for 1.5 contracts
- Exchange fills 1.0, rejects 0.5 (price moved away)
- Virtual tracker assumes full fill (line 97, multi_strategy_executor_enhanced.py)

**Problem:**
```python
# Virtual:
execute_order(qty=1.5, price=5000.00, order_id="abc123")
# Assumes fill for full 1.5
virtual_qty = 1.5

# Exchange (actual):
# Fill: 1.0 @ 5000.00
# Reject: 0.5 (price no longer available)

# Next reconciliation:
exchange_qty = 1.0
virtual_qty = 1.5
delta = 0.5

# Repair logic:
# Tries to zero 0.5 phantom
# But if strategy only has 1.5 contracts (exactly matching delta):
# Line 2187-2231: Zeros entire position!
# Now virtual = 0, but exchange = 1.0 still holds the fill

# Next order:
# Strategy thinks it's flat, sends NEW buy order for 2.0 contracts
# Exchange now has: 1.0 (old fill) + 2.0 (new fill) = 3.0
# Virtual: 2.0 (only sees new fill)
# Delta: 1.0 phantom (from old fill)
```

**What goes wrong:**
- Doesn't distinguish between rejected and filled portions
- Repair is too aggressive when position size matches delta
- Creates divergence instead of convergence

---

### 7. AGGREGATE ERRORS ACROSS MANY STRATEGIES

#### Scenario 7.1: 50 Strategies, Each 0.002 Off
**Setup:**
- 50 strategies, each trading 1.0 contract of ES
- Each has floating point error of +0.002

**Problem:**
```python
# Virtual sum:
virtual_total = sum([1.002 for _ in range(50)])
# Due to floating point:
# ≈ 50.1 (NOT 50.0)

# Exchange: 50.0 (exact)

delta = 50.1 - 50.0 = 0.1 ❌ EXCEEDS TOLERANCE!

# Even though each error is tiny (0.002),
# 50 * 0.002 = 0.1 (exceeds tolerance!)

# Repair triggered on systematic error
# Might zero positions that are actually correct

# Worse: If errors are in opposite directions:
# Strategy 1: +0.001 error
# Strategy 2: -0.001 error
# Strategy 3: +0.001 error
# ...
# Some cancel, some don't
# Unpredictable aggregate delta
```

**What goes wrong:**
- Tolerance (0.001) doesn't scale with number of strategies
- Should be: tolerance = 0.001 * sqrt(num_strategies) or similar
- Currently treats 50 strategies same as 1 strategy

#### Scenario 7.2: Time-Based Drift Across Session
**Setup:**
- Trading session: 8:00 AM to 3:15 PM (8 hours = 480 minutes)
- Reconciliation every minute (480 checks)
- Each check has 0.0000001% floating point error

**Problem:**
```python
# Error per cycle: 0.000001% = 1e-8
# After 480 cycles:
# cumulative = 480 * 1e-8 = 4.8e-6 (still tiny)

# But what if error is multiplicative or exponential?
# (due to how floating point rounding works)
# cumulative ≈ 1e-6 after 10 cycles
# cumulative ≈ 1e-4 after 100 cycles
# cumulative ≈ 1e-2 after 480 cycles (0.01!)

# If we have 50 strategies:
# 50 * 0.01 = 0.5 ❌ EXCEEDS TOLERANCE!

# Repair triggered due to session-long drift
```

**What goes wrong:**
- Doesn't account for systematic drift over time
- Errors compound in non-linear ways
- Tolerance should decay or refresh per session

---

## Critical Issues & Recommendations

### Issue 1: Tolerance is Arbitrary
**Problem:** 0.001 tolerance is:
- Too tight for 1000+ micro-contract scales
- Too loose for detecting real sync errors

**Recommendation:**
```python
# Use adaptive tolerance:
def compute_tolerance(virtual_qty, exchange_qty, num_strategies):
    base_tolerance = 0.001
    scale_factor = abs(virtual_qty + exchange_qty) / 2.0
    count_factor = sqrt(num_strategies)
    return base_tolerance * max(scale_factor, 1.0) * count_factor
```

### Issue 2: Repair is All-or-Nothing
**Problem:** `state.tracker.reset()` zeros entire position, not partial

**Recommendation:**
```python
# Support partial position reduction:
def reduce_position(tracker, symbol, target_qty):
    """Reduce position to target_qty instead of complete reset."""
    pos = tracker.get_position(symbol)
    if pos:
        current = pos.quantity
        reduction = current - target_qty
        # Execute sell order for reduction amount
        # Don't just reset tracker
```

### Issue 3: No Float Precision Validation
**Problem:** System doesn't validate that calculations are precise

**Recommendation:**
```python
# Add precision guards:
def safe_average_price(total_cost, quantity):
    """Calculate with precision validation."""
    if quantity < POSITION_EPSILON:
        return 0.0
    avg = total_cost / quantity
    # Verify: round-trip check
    recalc_cost = avg * quantity
    error = abs(recalc_cost - total_cost)
    if error > quantity * 0.01:  # >1% error
        log_warning(f"Precision loss: {error}/{quantity}")
    return avg
```

### Issue 4: No Partial Fill Tracking
**Problem:** Assumes all market orders fill completely

**Recommendation:**
```python
# Track order status before updating position:
def execute_order_safe(order_id, symbol, qty, side):
    """Only update virtual position after fill confirmation."""
    status = check_order_status(order_id)
    if status == "filled":
        filled_qty = get_filled_qty(order_id)  # Get actual fill
        execute_order(symbol, filled_qty, side)  # Update with real qty
    elif status == "partial":
        filled_qty = get_filled_qty(order_id)
        execute_order(symbol, filled_qty, side)
        # Log pending: qty - filled_qty
    else:
        # Don't update virtual position yet
        pass
```

### Issue 5: Repair Lacks Idempotency
**Problem:** Multiple repair calls compound errors

**Recommendation:**
```python
# Add repair history tracking:
class RepairLog:
    def __init__(self):
        self.history = {}  # strategy_id -> list of repairs
        self.cooldown = {}  # strategy_id -> last_repair_time

    def can_repair_again(self, strategy_id, min_interval_seconds=300):
        """Prevent repeated repairs within interval."""
        last = self.cooldown.get(strategy_id)
        if last and (now - last).total_seconds() < min_interval_seconds:
            return False
        return True
```

---

## Test Cases to Add

1. **Fractional Accumulation**: Sum 0.1 * 1000 strategies, verify rounding
2. **Partial Fill**: Place limit order, fill 0.6/1.0, verify repair doesn't zero wrong amount
3. **Micro Contract Scale**: 100 MES positions, 0.0001-level fractional movements
4. **Direction Conflict**: Create virtual longs while exchange short, verify error handling
5. **Tolerance Boundary**: Create delta = 0.0009999, verify it doesn't trigger repair
6. **Epsilon Boundary**: Create position = 1e-8, verify it's handled correctly
7. **Repair Remainder**: 5.5 to zero across 3 positions, verify partial doesn't get missed
8. **Multiplier Failure**: get_multiplier() fails for GC, verify P&L doesn't blow up
9. **Stale Exchange**: Exchange returns old position, verify doesn't create opposite delta
10. **Batch Rounding**: Exchange batches fills differently, verify aggregation is precise

---

## Summary Table

| Edge Case | Severity | Current Handling | Risk Level |
|-----------|----------|------------------|-----------|
| 1.1 Rounding accumulation (30 strats) | Medium | Tolerance check may miss | High |
| 1.2 Division rounding in avg price | High | No recalculation check | High |
| 1.3 Aggregation order-dependency | Medium | Floating point arithmetic | Medium |
| 2.1 Partial fill (2.0/2.5) | Critical | Assumes full fill, repairs aggressively | Critical |
| 2.2 Fractional multi-leg repair | High | Uses qty-based split, may leave remainder | High |
| 2.3 Sub-epsilon accumulation | Medium | No per-symbol precision tracking | Medium |
| 3.1 Exchange rounding inconsistency | Medium | No visibility into exchange rounding | Medium |
| 3.2 Stale position cache | Critical | Repair triggered immediately | Critical |
| 3.3 Batch rounding | Medium | Tolerance may not capture | Medium |
| 4.1 MES scale (50 strats * 5) | High | Precision loss at 32-bit intermediate | High |
| 4.2 Micro vs full mixing | Critical | No notional normalization | Critical |
| 4.3 Multiplier failure | Critical | Silent default to 1.0 | Critical |
| 5.1 Tolerance boundary oscillation | Medium | No hysteresis | Medium |
| 5.2 Remainder after fractional zeroing | Medium | Sub-tolerance phantom remains | Medium |
| 5.3 Epsilon vs tolerance gap | Medium | Zombie positions 1e-9 to 1e-3 | Medium |
| 6.1 Reconciliation during execution | Critical | No in-flight order tracking | Critical |
| 6.2 Partial reject repair loop | Critical | Repair aggravates divergence | Critical |
| 7.1 Aggregate errors (50 * 0.002) | High | Tolerance doesn't scale with count | High |
| 7.2 Session-long drift | Medium | No time-based tolerance decay | Medium |

