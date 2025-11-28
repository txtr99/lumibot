# Implementation Gaps & Solutions for Rollover/Expiration Safety

## Executive Summary

The current multi-strategy position sync/repair system has **critical gaps** when handling futures contract rollovers and expirations:

1. **No contract month tracking** - positions are tracked by symbol only (e.g., "ES") not contract (e.g., "ESZ25")
2. **No rollover detection** - system doesn't know when exchange auto-rolls positions between contracts
3. **Stale entry prices** - after rollover, virtual positions have entry prices from old contract
4. **P&L attribution breaks** - positions spanning contracts have incorrect entry/exit P&L
5. **Repair on wrong data** - sync checks can't distinguish between contract months
6. **No calendar awareness** - system doesn't know which contracts are "about to expire" or "already expired"

---

## Current Architecture Limitations

### VirtualPositionTracker: Symbol-Only Design
```python
class VirtualPositionTracker:
    positions: Dict[str, VirtualPosition] = {}  # Key is symbol like "ES"

    # Problem: Can't represent:
    # - +1 ESZ25 and -1 ESH26 simultaneously (both show as "ES")
    # - Which contract each position is physically on
    # - When rollover happens (tracker sees no change)
```

**Impact:**
- Spread positions (long Z25, short H26) collapse to single net position
- Rollover events invisible to tracker
- Entry prices valid only if entire trade stays on one contract

### BracketOrderManager: Contract-Unaware Repair
```python
def repair_position_desync(self):
    # Compares:
    #   Virtual: symbol="ES", qty=+1, side="long"
    #   Exchange: ??? (could be ESZ25, ESH26, or both)

    # Can't know:
    # - Which contract to close?
    # - Is exchange showing multiple contracts or single aggregate?
    # - Should we zero Z25 or H26?
```

**Impact:**
- Repair might zero wrong contract
- Zeros might spread across multiple contracts unexpectedly
- Exchange might have position on expired contract that repair can't close

### MultiStrategyExecutorEnhanced: Data Source Ambiguity
```python
def on_trading_iteration(self):
    market_data = self.shared_data.fetch_for_all_strategies(
        symbols=["ES", "NQ", "GC"],  # Just symbols!
        # Missing: contract_months=["Z25", "H26", "Z25"]
    )

    # What contract does "ES" resolve to?
    # - Most active? (H26)
    # - Original trade contract? (Z25)
    # - Broker default? (Unknown)
```

**Impact:**
- Data fetches might grab wrong contract
- Strategies on old contract get new contract's prices
- Decisions based on wrong data

### TradingCalendar: No Expiration Dates
```python
class TradingCalendar:
    # Checks trading hours, session restrictions, maintenance windows
    # Missing: Contract expiration awareness

    # Can't answer:
    # - When is ES expiring?
    # - Should I avoid trading because Z25 expires tomorrow?
    # - Is this position on an expired contract?
```

**Impact:**
- No automatic pre-expiry shutdown
- Can't detect position on expired contract
- Calendar rules same for new vs. old contract months

---

## Critical Gaps Analysis

### Gap 1: No Contract Lifecycle Metadata

**Current State:**
```python
FUTURES_METADATA = {
    "ES": {
        "name": "E-mini S&P 500",
        "tick_size": 0.25,
        # Missing:
        # "expiration_dates": [monthly list],
        # "rollover_window": "2 weeks before expiry",
        # "contract_months": ["H", "M", "U", "Z"],  # quarterly
    }
}
```

**Needed:**
```python
FUTURES_METADATA = {
    "ES": {
        # Existing...

        # New fields:
        "expiration_schedule": {
            "frequency": "monthly",  # or "quarterly", "weekly"
            "day_of_month": 28,      # CME standard
            "contract_months": ["H", "M", "U", "Z"],  # Mar, Jun, Sep, Dec
        },
        "rollover_settings": {
            "rollover_window_days": 14,  # Start rolling 14 days before expiry
            "halt_new_orders_days": 2,   # Stop new orders 2 days before
            "close_all_days": 1,         # Auto-close 1 day before expiry
        },
    }
}
```

### Gap 2: VirtualPositionTracker Can't Track Contract Months

**Current:**
```python
class VirtualPosition:
    symbol: str  # "ES" only
    quantity: float
    avg_entry_price: float
    # Missing: contract_id or contract_month

tracker.positions["ES"] = VirtualPosition(...)
# Can't represent: "1 ESZ25 and 1 ESH26 simultaneously"
```

**Needed Option A: Minimal Change**
```python
class VirtualPosition:
    symbol: str  # "ES"
    contract_id: str  # "CON.F.US.EP.Z25" or shorthand "Z25"
    quantity: float
    avg_entry_price: float

tracker.positions[("ES", "Z25")] = VirtualPosition(...)
tracker.positions[("ES", "H26")] = VirtualPosition(...)
```

**Needed Option B: Full Separation**
```python
class VirtualPosition:
    symbol: str  # "ES"
    contract_month: str  # "Z", "H", "M", "U"
    contract_year: int  # 2025, 2026
    quantity: float
    avg_entry_price: float

tracker.positions[("ES", "Z25")] = VirtualPosition(...)
```

**Needed Option C: Contract-Agnostic (Keep Simple)**
```python
# Add a flag: "This position can't be auto-rolled"
class VirtualPosition:
    symbol: str
    quantity: float
    avg_entry_price: float
    is_locked_to_contract: bool = False  # If True, can't assume rollover
    physical_contract_id: str = None  # Last known ProjectX contract ID
```

### Gap 3: No Rollover Detection

**Current:**
```python
def repair_position_desync(self):
    sync_result = check_position_sync()
    # Only checks if qty matches
    # Doesn't know if qty matches because of rollover
```

**Needed:**
```python
def detect_rollover_event(symbol: str) -> Optional[RolloverEvent]:
    """Detect if exchange rolled position to new contract."""
    virtual = get_virtual_position(symbol)
    exchange = check_exchange_position(symbol)

    if virtual.contract_id != exchange.contract_id:
        return RolloverEvent(
            symbol=symbol,
            old_contract=virtual.contract_id,
            new_contract=exchange.contract_id,
            old_price=virtual.last_price,
            new_price=exchange.price,
            settlement_price=???,  # Unknown without broker API
            realized_pnl=???,  # Unknown without entry/exit tracking
        )
    return None

def on_rollover_detected(event: RolloverEvent):
    """Update virtual tracker when rollover detected."""
    # Update contract_id
    # Reset entry_time? (new contract = new entry semantically)
    # Capture settlement P&L?
    # Update bracket orders to new contract?
```

### Gap 4: No Contract Age Awareness

**Current:**
```python
def can_execute_order(symbol: str, side: str) -> bool:
    # Checks: trading hours, session, calendar rules
    # Missing: "Is this contract about to expire?"
    return True  # Assume OK
```

**Needed:**
```python
def is_contract_near_expiry(symbol: str, days_before: int = 7) -> bool:
    """Check if contract expires within N days."""
    metadata = FUTURES_METADATA[symbol]
    expiry_schedule = metadata.get("expiration_schedule", {})

    next_expiry = calculate_next_expiry_date(
        symbol,
        frequency=expiry_schedule.get("frequency"),
        day_of_month=expiry_schedule.get("day_of_month"),
    )
    days_until = (next_expiry - datetime.now()).days
    return days_until <= days_before

def get_contract_age_days(symbol: str, contract_month: str) -> int:
    """How many days until this specific contract expires?"""
    # Parse contract_month (e.g., "Z25" = Dec 2025)
    # Calculate days remaining
    pass

def can_enter_new_positions(symbol: str) -> bool:
    """Can we enter NEW positions in this symbol?"""
    if is_contract_near_expiry(symbol, days_before=14):
        return False  # Too close to expiry, avoid
    # Also check other rules...
    return True
```

### Gap 5: Position Sync Can't Distinguish Contracts

**Current:**
```python
def check_position_sync(self) -> SyncResult:
    for symbol in unique_symbols:
        virtual_qty = aggregate_virtual_positions(symbol)  # "ES" = +1
        exchange_qty = check_exchange(symbol)  # "ES" = +1

        if virtual_qty == exchange_qty:
            result["synced"] = True
        else:
            result["discrepancies"].append({
                "symbol": symbol,
                "virtual_qty": virtual_qty,
                "exchange_qty": exchange_qty,
            })
```

**Problem:**
```
Virtual: {("ES", "Z25"): +2, ("ES", "H26"): -1} = +1 net
Exchange: {("ES", "Z25"): 0, ("ES", "H26"): +1} = +1 net
Current Logic: ✓ Synced (both total +1)
Reality: WRONG! Z25 should have +2 but has 0
         H26 should have -1 but has +1
```

**Needed:**
```python
def check_position_sync_by_contract(self) -> SyncResult:
    """Compare positions at contract level, not just symbol level."""
    for symbol in unique_symbols:
        for contract_month in get_active_contracts(symbol):
            virtual_qty = get_virtual_qty(symbol, contract_month)
            exchange_qty = get_exchange_qty(symbol, contract_month)

            if virtual_qty != exchange_qty:
                result["discrepancies"].append({
                    "symbol": symbol,
                    "contract_month": contract_month,
                    "virtual_qty": virtual_qty,
                    "exchange_qty": exchange_qty,
                    "is_rollover": is_rollover_in_progress(symbol),
                })
    return result
```

### Gap 6: Bracket Order Recreation After Rollover

**Current:**
```python
class BracketOrderManager:
    # Has logic to cancel and recreate brackets
    # But doesn't know about rollovers

    def cancel_brackets_for_strategy(self, strategy_id):
        # Finds orders by tag
        # Cancels them
        # Missing: Are these on the right contract?
```

**Problem:**
```
Scenario:
  1. Enter LONG 1 ES at 5900
     Create brackets: TP=5910, SL=5890
     Brackets placed on ESZ25

  2. Exchange auto-rolls to ESH26

  3. Our code tries to cancel brackets
     "Cancel all with tag ES_1M_01-XXXXX"

  Problem: Brackets might be on:
    - Z25 (expired, can't cancel)
    - H26 (wrong contract, our bracket manager doesn't know)
    - Lost between contracts

  Can't recreate because:
    - We don't know new contract ID
    - We don't know new entry price (on H26)
    - We don't know if rollover actually happened
```

**Needed:**
```python
def update_brackets_on_rollover(self, rollover_event: RolloverEvent):
    """Update bracket orders when position rolls to new contract."""
    for strategy_id, state in self.strategy_states.items():
        if state.symbol != rollover_event.symbol:
            continue

        # Find existing brackets on old contract
        old_brackets = find_brackets(
            strategy_id=strategy_id,
            contract_id=rollover_event.old_contract,
        )

        if not old_brackets:
            # Already closed or never existed
            continue

        # Cancel brackets on old contract
        for bracket in old_brackets:
            try:
                self.cancel_order(bracket.order_id)
            except Exception:
                pass  # Already gone

        # Calculate new bracket prices based on new contract entry
        new_entry_price = rollover_event.new_price
        new_tp = calculate_tp(new_entry_price, state.entry_atr, state.entry_pt_mult)
        new_sl = calculate_sl(new_entry_price, state.entry_atr, state.entry_sl_mult)

        # Place new brackets on new contract
        self.create_brackets(
            strategy_id=strategy_id,
            contract_id=rollover_event.new_contract,
            entry_price=new_entry_price,
            tp=new_tp,
            sl=new_sl,
        )
```

### Gap 7: No Stale Entry Price Detection

**Current:**
```python
def calculate_pnl(self, current_price: float) -> float:
    if self.quantity == 0:
        return 0.0
    return self.quantity * (current_price - self.avg_entry_price) * multiplier
    # No validation that avg_entry_price is valid
```

**Problem:**
```
Entry: 5900 on ESZ25
Rollover: Contract moved, entry price now stale

Calc: (5905 - 5900) * 1 * 50 = $250
But: This includes the rollover slippage mixed with unrealized P&L

Can't separate:
  - P&L from Z25 (realized at rollover)
  - P&L from H26 (unrealized)
```

**Needed:**
```python
@dataclass
class VirtualPosition:
    symbol: str
    quantity: float
    avg_entry_price: float
    contract_id_at_entry: str  # What contract were we entered on?
    last_known_contract_id: str  # What contract are we on now?

    @property
    def has_crossed_contracts(self) -> bool:
        return self.contract_id_at_entry != self.last_known_contract_id

    def get_entry_price_validity(self) -> str:
        """Returns: "valid", "stale", "unknown"."""
        if not self.last_known_contract_id:
            return "unknown"
        if self.contract_id_at_entry == self.last_known_contract_id:
            return "valid"
        return "stale"  # Crossed contracts

    def calculate_pnl(self, current_price: float) -> float:
        pnl = self.quantity * (current_price - self.avg_entry_price) * multiplier

        if self.has_crossed_contracts:
            logger.warning(
                f"P&L calculation on cross-contract position "
                f"({self.symbol} {self.contract_id_at_entry} → {self.last_known_contract_id}). "
                f"P&L may not be accurate."
            )

        return pnl
```

---

## Recommended Solutions (In Priority Order)

### Solution 1: Contract Month Tracking in Metadata (QUICK WIN)

**Effort:** Low (1-2 hours)
**Risk:** Low
**Impact:** Enables contract lifecycle awareness

```python
# Add to futures_metadata.py

def get_contract_expiry_date(symbol: str, contract_month: str) -> datetime:
    """
    Calculate expiry date for a contract month.

    Args:
        symbol: "ES", "NQ", "GC", etc.
        contract_month: "Z25" for Dec 2025, "H26" for Mar 2026

    Returns:
        Expiry datetime (last trading day)
    """
    month_map = {"H": 3, "M": 6, "U": 9, "Z": 12}
    year = int(contract_month[1:]) + 2000
    month = month_map.get(contract_month[0], 12)

    # CME standard: 3rd Friday of expiry month
    first_day = datetime(year, month, 1)
    # Find first Friday
    friday_offset = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=friday_offset if friday_offset > 0 else 7)
    # Third Friday
    third_friday = first_friday + timedelta(weeks=2)

    return third_friday

def is_contract_expired(symbol: str, contract_month: str) -> bool:
    """Check if contract has expired."""
    expiry = get_contract_expiry_date(symbol, contract_month)
    return datetime.now() > expiry

def days_until_expiry(symbol: str, contract_month: str) -> int:
    """Days until contract expires."""
    expiry = get_contract_expiry_date(symbol, contract_month)
    delta = expiry - datetime.now()
    return max(0, delta.days)

def get_current_active_contract(symbol: str) -> str:
    """Which contract month is currently active/liquid?"""
    # Conservative: prefer contract with >30 days to expiry
    for month_code in ["H", "M", "U", "Z"]:
        for year in range(25, 30):  # 2025-2029
            contract = f"{month_code}{year}"
            if not is_contract_expired(symbol, contract) and days_until_expiry(symbol, contract) > 30:
                return contract
    return None  # All contracts expired or too close
```

### Solution 2: Extend VirtualPositionTracker to Track Contract Months (MEDIUM)

**Effort:** Medium (3-5 hours)
**Risk:** Medium (refactoring existing class)
**Impact:** Enables correct position sync across rollovers

**Option A (Recommended): Parallel tracking**
```python
class VirtualPositionTracker:
    # Keep existing positions dict for backward compatibility
    positions: Dict[str, VirtualPosition] = {}  # Old: symbol → position

    # Add new tracking
    contract_positions: Dict[tuple, VirtualPosition] = {}  # New: (symbol, contract) → position

    def execute_order(self, symbol: str, quantity: float, side: str,
                      price: float = None, order_id: str = None,
                      contract_id: str = None):  # NEW PARAM
        """Execute order with optional contract tracking."""

        # Determine contract to track under
        if contract_id:
            key = (symbol, contract_id)
        else:
            key = symbol  # Fallback to old behavior

        # Update new contract_positions if contract specified
        if contract_id:
            if key not in self.contract_positions:
                self.contract_positions[key] = VirtualPosition(...)
            # Update contract_positions[key]

        # Also update old positions dict for aggregation
        # This keeps backward compatibility
        super().execute_order(symbol, quantity, side, price, order_id)

    def get_position_by_contract(self, symbol: str, contract_id: str) -> Optional[VirtualPosition]:
        """Get position for specific contract."""
        return self.contract_positions.get((symbol, contract_id))

    def aggregate_by_symbol(self, symbol: str) -> float:
        """Sum all contract positions for a symbol."""
        total = 0.0
        for (sym, _contract), pos in self.contract_positions.items():
            if sym == symbol:
                total += pos.quantity
        return total
```

### Solution 3: Add Rollover Detection to BracketOrderManager (MEDIUM)

**Effort:** Medium (4-6 hours)
**Risk:** Medium (touches critical sync logic)
**Impact:** Enables automatic bracket recovery after rollover

```python
class BracketOrderManager:
    def detect_and_handle_rollover(self, symbol: str) -> Optional[RolloverEvent]:
        """Detect if a symbol's position rolled to new contract."""

        virtual_contract = self.get_virtual_contract(symbol)
        exchange_contract = self.get_exchange_contract(symbol)

        if virtual_contract == exchange_contract:
            return None  # No rollover

        # Rollover detected!
        event = RolloverEvent(
            symbol=symbol,
            old_contract=virtual_contract,
            new_contract=exchange_contract,
            timestamp=datetime.now(),
        )

        # Handle the rollover
        self._process_rollover(event)
        return event

    def _process_rollover(self, event: RolloverEvent):
        """Update virtual tracker and bracket orders for rollover."""

        # Update virtual tracker
        for strategy_id, state in self.strategy_states.items():
            if state.symbol != event.symbol:
                continue

            pos = state.tracker.get_position(event.symbol)
            if pos is None:
                continue

            # Mark position as having rolled
            pos.previous_contract_id = event.old_contract
            pos.current_contract_id = event.new_contract

            # DON'T reset entry_price or entry_time (preserve for P&L)
            # Just mark that we're on a new contract now

            # Update/recreate brackets if needed
            self._recreate_brackets_for_rollover(state, event)

    def _recreate_brackets_for_rollover(self, state, event: RolloverEvent):
        """Update bracket orders for the rolled position."""

        # Cancel old brackets (if they still exist on old contract)
        try:
            old_brackets = self._find_brackets_on_contract(
                state.strategy_id,
                event.old_contract,
            )
            for bracket in old_brackets:
                self._cancel_order_safe(bracket.order_id)
        except Exception as e:
            self.logger.warning(f"Failed to cancel old brackets: {e}")

        # Place new brackets on new contract
        # Use same TP/SL prices but on new contract
        if state.take_profit_price and state.stop_loss_price:
            self._place_brackets(
                strategy_id=state.strategy_id,
                symbol=event.symbol,
                contract_id=event.new_contract,
                position_qty=state.tracker.get_position(event.symbol).quantity,
                tp_price=state.take_profit_price,
                sl_price=state.stop_loss_price,
            )
```

### Solution 4: Add Contract Expiry Warnings to TradingCalendar (LOW)

**Effort:** Low (1-2 hours)
**Risk:** Low
**Impact:** Prevents trading near-expiry contracts

```python
class TradingCalendar:
    def get_status(self, symbol: str, current_time: datetime, ...) -> CalendarStatus:
        """Check if we can trade, now with contract expiry awareness."""

        status = super().get_status(symbol, current_time, ...)  # Existing logic

        # Add contract expiry check
        active_contract = get_current_active_contract(symbol)
        days_until_expiry = days_until_expiry(symbol, active_contract)

        if days_until_expiry < 7:
            status.can_enter_orders = False
            status.session_reason = f"Contract expiring in {days_until_expiry} days"

        return status
```

### Solution 5: Improve Repair Logic to Handle Multiple Contracts (HIGH)

**Effort:** High (8-12 hours)
**Risk:** High (refactors core logic)
**Impact:** Makes repair logic safe across rollovers

```python
def repair_position_desync(self, sync_result: Optional[Dict] = None) -> Dict[str, Any]:
    """NEW VERSION: Repair with contract awareness."""

    if not sync_result:
        sync_result = self.check_position_sync_by_contract()

    result = {"repairs_made": [], "warnings": []}

    for discrepancy in sync_result.get("discrepancies", []):
        symbol = discrepancy["symbol"]
        contract = discrepancy["contract"]  # NEW: per-contract
        virtual_qty = discrepancy["virtual_qty"]
        exchange_qty = discrepancy["exchange_qty"]
        is_rollover = discrepancy.get("is_rollover", False)  # NEW

        if is_rollover:
            # Don't repair! Rollover detection handles this
            self.logger.info(
                f"Skipping repair for {symbol}/{contract}: "
                f"Rollover in progress"
            )
            continue

        # Standard repair logic
        excess = virtual_qty - exchange_qty
        if abs(excess) < 0.001:
            continue

        # ... rest of repair logic, now per-contract
```

---

## Testing Strategy

### Test 1: Expiry Metadata
```python
def test_contract_expiry_dates():
    # ES Z25 should expire Dec 28, 2025
    expiry = get_contract_expiry_date("ES", "Z25")
    assert expiry.month == 12
    assert expiry.day == 26  # 3rd Friday

    # Should be expired on Dec 29
    assert is_contract_expired("ES", "Z25", check_date=datetime(2025, 12, 29))
```

### Test 2: Multi-Contract Sync
```python
def test_sync_distinguishes_contracts():
    # Virtual: Z25 +2, H26 -1
    # Exchange: Z25 0, H26 +1
    # Should detect TWO discrepancies, not "synced"

    virtual = {
        ("ES", "Z25"): 2,
        ("ES", "H26"): -1,
    }
    exchange = {
        ("ES", "Z25"): 0,
        ("ES", "H26"): 1,
    }

    sync = check_position_sync_by_contract(virtual, exchange)
    assert not sync["synced"]
    assert len(sync["discrepancies"]) == 2
```

### Test 3: Rollover Detection
```python
def test_rollover_event_detection():
    # Simulate exchange rolling Z25 → H26

    virtual = VirtualPositionTracker()
    virtual.execute_order("ES", 1, "buy", 5900, contract_id="Z25")

    # Simulate rollover by updating contract info
    exchange_contract = "H26"

    rollover = bracket_manager.detect_and_handle_rollover("ES")
    assert rollover is not None
    assert rollover.old_contract == "Z25"
    assert rollover.new_contract == "H26"
```

### Test 4: Bracket Survival Across Rollover
```python
def test_brackets_survive_rollover():
    # Open position with brackets on Z25
    # Rollover to H26
    # Brackets should be recreated on H26

    # ...setup...

    # Trigger rollover
    rollover_event = RolloverEvent(...)
    bracket_manager._process_rollover(rollover_event)

    # Check brackets
    new_brackets = bracket_manager.get_active_brackets("ES_1M_01")
    assert new_brackets[0].contract_id == "H26"
    assert new_brackets[0].tp_price == original_tp  # Prices preserved
```

### Test 5: P&L Accuracy Across Contracts
```python
def test_pnl_accuracy_on_cross_contract_position():
    # Enter on Z25, rollover to H26, exit on H26
    # P&L should be correct despite contract change

    # Entry: ES_1M_01 LONG 1 @ 5900 (on Z25)
    # Rollover: Z25 closes @ 5910, H26 opens @ 5905
    # Exit: H26 closes @ 5920

    # Expected P&L:
    # - From Z25: (5910 - 5900) = 10 points = $500
    # - From H26: (5920 - 5905) = 15 points = $750
    # - Total: $1250

    # Virtual calculation:
    # - Entry price: 5900 (original)
    # - Exit price: 5920
    # - Naive calc: (5920 - 5900) * 50 = $1000 ✗ WRONG

    # Correct:
    # - Must account for settlement @ 5910
    # - Or split P&L by contract

    # Test both approaches
```

---

## Rollout Plan

### Phase 1: Foundation (Week 1)
- [ ] Add contract expiry metadata functions
- [ ] Add contract month constants to FUTURES_METADATA
- [ ] Tests for contract age detection

### Phase 2: Tracking Enhancement (Week 2)
- [ ] Extend VirtualPositionTracker with optional contract tracking
- [ ] Add parallel contract_positions dict
- [ ] Update order execution to track contracts
- [ ] Tests for multi-contract aggregation

### Phase 3: Detection & Recovery (Week 3)
- [ ] Implement rollover detection in BracketOrderManager
- [ ] Add rollover event handler
- [ ] Implement bracket recreation on rollover
- [ ] Tests for rollover detection and handling

### Phase 4: Repair Safety (Week 4)
- [ ] Enhance sync logic to check per-contract
- [ ] Update repair logic to respect contracts
- [ ] Add warnings for stale entry prices
- [ ] Comprehensive integration tests

### Phase 5: Calendar Integration (Week 5)
- [ ] Add contract expiry checks to TradingCalendar
- [ ] Implement near-expiry trading restrictions
- [ ] Auto-close positions near expiry (optional)
- [ ] Safety test: prevents trades on expired contracts

---

## Risk Mitigation

### Risk: Repair Logic Acts on Stale Data
**Mitigation:** Add timestamp to sync results, skip repairs if > 5 minutes old

### Risk: Bracket Orphans During Rollover
**Mitigation:** Rollover detector cancels old brackets before opening new ones

### Risk: Entry Prices Wrong After Rollover
**Mitigation:** Log warning when P&L crosses contracts, add validation

### Risk: Backtest Results Don't Match Live
**Mitigation:** Backtest framework must simulate rollover events

### Risk: Repair Cascades During Multiple Expirations
**Mitigation:** Limit repair frequency (max once per 5 minutes per symbol)

---

## Questions to Answer First

Before implementing, clarify:

1. **ProjectX Auto-Rollover Behavior**
   - Does it auto-roll positions?
   - To which contract?
   - Can we detect it?

2. **Data Feed Behavior**
   - Does data automatically switch to new contract?
   - Or do we need explicit contract specification?
   - What if we request data for expired contract?

3. **Order Routing**
   - When we submit order for "ES", which contract?
   - Can we specify contract explicitly?
   - What if we submit for expired contract?

4. **Position Reporting**
   - Does ProjectX show positions separately by contract?
   - Or aggregated by symbol?
   - Can we query historical positions after expiry?

---
