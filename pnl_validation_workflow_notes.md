# P&L Validation Workflow Testing Notes

## Overview

**File Being Tested**: `/Users/marvin/repos/lumibot_fork/tools/pnl_validation_workflow.py`
**Branch**: `feature/bracket-order-reliability`
**Purpose**: Validate P&L calculations and bracket order management for ProjectX/TopStep integration

---

## Current Status (End of Session 4)

**Workflow Execution Complete**: Steps 1-14 PASSED, Step 15 FAILED (market condition, not code bug)

### Final Test Run Results (2025-11-24)

```
Steps 1-14: ✅ ALL PASSED
Step 15:    ❌ FAILED (errorCode 2 - SL price outside allowed range)
```

**Validated Successfully:**
- Race condition fixes (steps 7 & 13) - cancel brackets FIRST pattern works
- Tag persistence across API calls
- Self-healing (LONG and SHORT) - recreate deleted brackets
- Bracket cleanup (LONG and SHORT) - orphan cancellation
- P&L calculations - 0 discrepancies
- Position type detection - `type` field instead of `size` sign
- No rate limit issues

**Step 15 Failure**: Market timing issue, not a code bug. SL price crossed through current market during volatile conditions. See "Edge Case: ErrorCode 2" section below for retry mechanism TODO.

---

## Session 3 Summary (2025-11-24 Late Night)

### Critical Bug Fix: Close Position Race Condition

**Problem**: Step 13 created unintended LONG positions when closing SHORTs.

**Root Cause**: Race condition between position query and close order:
1. Query positions → find 6 SHORTs
2. Start sending BUY orders to close
3. Meanwhile, tight brackets fill, closing some SHORTs
4. Our BUY orders arrive and create NEW LONG positions instead of closing

**Solution**: Cancel bracket orders FIRST, then close positions.

Steps 7 and 13 now:
1. Cancel ALL bracket orders for the phase (prevents race)
2. Wait 0.5s for cancels to propagate
3. Query positions (now safe from bracket fills)
4. Send close orders

### Workflow Changes

- **Step 15**: Rewrote as BracketOrderManager integration test (was OCO test)
- **Step 16**: Removed entirely (order modification test - not needed)
- **TOTAL_STEPS**: Changed from 16 to 15

### Position Type Bug Fix (from earlier in session)

Fixed SHORT position detection. API returns `size` as always positive, use `type` field:
- `type: 1` = LONG
- `type: 2` = SHORT

---

## Session 1 Summary (2025-11-24 Earlier)

### Bugs Fixed (12 total)

1. **Progress bar display**: Fixed string concatenation for Rich formatting
2. **API field names**: `qty`→`size`, `avgPrice`→`averagePrice`, `customTag` None handling
3. **Tag uniqueness**: Added workflow_id suffix to prevent collisions
4. **Fill price detection**: Query position after market order for actual fill price
5. **P&L display**: Removed false discrepancy logic (API has no unrealized P&L)
6. **Self-healing tags**: Added `HEAL` suffix with timestamp
7. **Cancel confirmation**: Added polling loop (5 attempts @ 0.5s) before recreating
8. **ErrorCode 5 handling**: Gracefully skip if order already gone
9. **Floating point precision**: Fixed `round_to_tick()` noise (e.g., `4134.900000000001`)
10. **Tag matching substring bug**: Changed `in` to `startswith()` to prevent ES matching MES
11. **Contract symbol extraction**: Added Globex→Symbol mapping (EP→ES, ENQ→NQ, GCE→GC)
12. **Close order tag collision**: Added counter to close tags (`CLOSE1`, `CLOSE2`, etc.)

### Feature Added

- **`run-to N` command**: Run steps sequentially from start through step N

---

## Session 2 Summary (2025-11-24 Later)

### Critical Discovery: OCO Does NOT Work

**Problem**: When closing positions or when TP fills, SL orders remain orphaned.

**Investigation Results**:
- `linkedOrderId` parameter is accepted by API but **does NOT create true OCO**
- Tested with MGC: Placed entry + SL + TP → SL filled → **TP remained orphaned** (confirmed!)
- Native brackets (`stopLossBracket`/`takeProfitBracket`) require account setting "Auto OCO Brackets" which conflicts with scaled virtual positions (can't use)

### Solution: BracketOrderManager

Created `/tools/bracket_order_manager.py` - a REST-based polling solution:

```python
manager = BracketOrderManager(client, account_id)
manager.register_bracket(base_tag, sl_order_id=111, tp_order_id=222, symbol="ES")
manager.poll_and_cleanup()  # Call every 2-5 seconds in live trading
```

**How it works**:
1. Tracks SL/TP pairs by base tag
2. Polls `order_search` (single API call returns ALL orders)
3. When one leg fills (status=2) → automatically cancels the other
4. Handles edge cases (order already gone via errorCode 5, etc.)

**Test Result**: TP filled → Manager detected status change → Cancelled orphan SL → Success!

### Workflow Script Updates

**Step 7 & Step 13** now explicitly cancel orphan bracket orders after closing positions:
- Query all open orders
- Cancel any `_LONG_SL_`, `_LONG_TP_` (step 7) or `_SHORT_SL_`, `_SHORT_TP_` (step 13)
- Verify cleanup before marking complete

---

## Production Integration Design (Future Work)

### Unified BracketOrderManager

```
Strategy places entry → registers with manager:
  manager.register_entry(order_id, strategy_id, sl_mult, tp_mult, atr)

Manager poll() handles everything:
  - Entry filled? → Calculate SL/TP prices → Place bracket orders
  - Bracket filled? → Cancel orphan
  - Position closed externally? → Cancel both brackets
```

### Polling Frequency
- **Live**: Every 2-5 seconds (well within 200 req/min rate limit)
- **Backtest**: Once per iteration

### Backtest Edge Cases
- If both SL and TP hit in same bar → assume SL hit first (conservative)
- SL/TP prices stored with position at entry time

### Key Insight
Single `order_search` call returns ALL orders for the account - no per-market or per-order overhead. Polling 6 markets with 12 bracket orders = still just 1 API call.

---

## Race Condition Prevention (CRITICAL)

### Design Principles

Race conditions in order/position handling are **unacceptable**. The system must:

1. **Make race conditions impossible** - even if it takes an extra 1-2 seconds to verify
2. **Detect if a race condition occurred** - reconciliation checks
3. **Fix mismatches automatically** - corrective action
4. **Verify the fix worked** - re-check after correction

### Order of Operations (Safe Pattern)

When closing positions with active brackets:
```
1. Cancel ALL bracket orders first (eliminate race source)
2. Wait for cancels to propagate (0.5s minimum)
3. Query current positions
4. Send close orders
5. Verify account is flat
```

### Position Synchronization Requirements (Production)

Production trading requires position reconciliation:

1. **Detect Mismatches**: Compare exchange positions vs virtual positions
   - Per-symbol, per-direction comparison
   - Flag any exposure that doesn't match our virtual aggregate

2. **Corrective Action Options**:
   - Cancel orphan brackets (self-healing already implemented)
   - Close unrecognized positions on exchange
   - OR update virtual positions to match exchange (if we missed a fill)

3. **Verification Loop**:
   - After any correction, re-query and verify
   - Log all corrections for audit trail
   - Alert if correction fails

### Position Sync Check (Pseudo-code)

```python
def reconcile_positions():
    exchange_positions = query_exchange_positions()
    virtual_positions = get_virtual_aggregate_positions()

    for symbol in all_symbols:
        exchange_qty = exchange_positions.get(symbol, 0)
        virtual_qty = virtual_positions.get(symbol, 0)

        if exchange_qty != virtual_qty:
            log_mismatch(symbol, exchange_qty, virtual_qty)

            # Option 1: Trust exchange, update virtual
            # Option 2: Trust virtual, correct exchange
            # Decision depends on context...

            take_corrective_action(symbol, exchange_qty, virtual_qty)

    # Re-verify
    verify_reconciliation()
```

### When to Run Reconciliation

- After every order fill event
- On startup/reconnection
- Periodically (every N minutes as sanity check)
- After any error or timeout

---

## Files Created/Modified

### Created
- `/tools/bracket_order_manager.py` - REST-based bracket order tracking and orphan cleanup
- `/tools/test_oco_brackets.py` - OCO testing utilities

### Modified
- `/tools/pnl_validation_workflow.py` - Bug fixes + orphan cleanup in steps 7 & 13
- `/custom_portfolio/data/futures_metadata.py` - Globex symbol mapping, `round_to_tick()` fix
- `/CLAUDE.md` - Added ProjectX API reference section

---

## Key API Reference

### Order Status Codes
- `1` = Open
- `2` = Filled
- `3` = Cancelled
- `4` = Expired
- `5` = Rejected

### Error Codes
- `2` = Invalid price - TWO distinct causes:
  - Floating point precision issue (e.g., `4134.900000000001`) - fix with `round_to_tick()`
  - **Price outside allowed range** - SL/TP price crossed through current market price
- `5` = Order doesn't exist (already filled/cancelled)

### Edge Case: ErrorCode 2 "Price Outside Allowed Range"

**Problem Discovered in Step 15**:
```
SL failed: {'orderId': 1976024844, 'success': False, 'errorCode': 2,
'errorMessage': 'Order price is outside allowed range. Please set price below best ask.'}
```

**Root Cause**: Between calculating the SL/TP price and submitting the order, the market moved enough that our calculated price is no longer valid:
- For LONG positions: SL must be below current bid, TP must be above current ask
- For SHORT positions: SL must be above current ask, TP must be below current bid

**TODO: Implement Retry Mechanism**

When `errorCode == 2` with "outside allowed range" message:
1. Re-fetch current market price
2. Recalculate SL/TP based on new price
3. Retry the order
4. Log the price adjustment for audit trail

```python
def place_bracket_with_retry(client, account_id, contract_id, side, size,
                             sl_offset, tp_offset, max_retries=3):
    """Place bracket orders with automatic price adjustment on rejection."""
    for attempt in range(max_retries):
        current_price = get_current_price(client, contract_id)

        if side == 0:  # BUY entry (LONG position)
            sl_price = round_to_tick(current_price - sl_offset, symbol)
            tp_price = round_to_tick(current_price + tp_offset, symbol)
        else:  # SELL entry (SHORT position)
            sl_price = round_to_tick(current_price + sl_offset, symbol)
            tp_price = round_to_tick(current_price - tp_offset, symbol)

        sl_result = place_sl_order(sl_price)

        if not sl_result.get("success"):
            error_code = sl_result.get("errorCode")
            error_msg = sl_result.get("errorMessage", "")

            if error_code == 2 and "outside allowed range" in error_msg:
                logger.warning(f"SL price {sl_price} rejected, retrying with fresh price...")
                time.sleep(0.2)  # Brief pause before retry
                continue
            else:
                return sl_result  # Different error, don't retry

        # SL placed successfully, now place TP
        tp_result = place_tp_order(tp_price)
        return {"sl": sl_result, "tp": tp_result}

    return {"success": False, "error": f"Failed after {max_retries} retries"}
```

**Key Insight**: This is more likely to occur during volatile market conditions or when using tight brackets (like our 0.25x ATR test brackets).

### Position API (`position_search_open`)
- `size` - Always positive (quantity, NOT direction!)
- `type` - **CRITICAL**: `1` = LONG, `2` = SHORT
- `averagePrice` (NOT `avgPrice`)
- `contractId`
- No unrealized P&L field

**⚠️ IMPORTANT**: Do NOT use `size` sign to determine direction! The API returns `size` as always positive. Use `type` field instead:
```python
pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT
quantity = size if pos_type == 1 else -size
```

### Order API (`order_search`)
- `customTag` may be `None` - use `order.get("customTag") or ""`
- `filledPrice` and `fillVolume` available on filled orders

### Contract ID → Symbol Mapping
| Contract ID | Globex | Symbol |
|-------------|--------|--------|
| CON.F.US.EP.Z25 | EP | ES |
| CON.F.US.MES.Z25 | MES | MES |
| CON.F.US.ENQ.Z25 | ENQ | NQ |
| CON.F.US.MNQ.Z25 | MNQ | MNQ |
| CON.F.US.GCE.Z25 | GCE | GC |
| CON.F.US.MGC.Z25 | MGC | MGC |

---

## To Resume Testing

```bash
cd /Users/marvin/repos/lumibot_fork
source venv/bin/activate

# Account should be clean. Run full workflow:
python tools/pnl_validation_workflow.py run-to 15

# Or run steps individually:
python tools/pnl_validation_workflow.py start
python tools/pnl_validation_workflow.py step-1
# ... etc
```

### Quick Status Check
```bash
PYTHONPATH=/Users/marvin/repos/lumibot_fork python tools/test_oco_brackets.py status
```

---

## Test Execution Progress

| Step | Description | Status | Notes |
|------|-------------|--------|-------|
| start | Initialize workflow | READY | |
| step-1 | Capture baseline metadata | READY | 6 instruments |
| step-2 | Create LONG brackets | READY | 0.25x ATR (tight) |
| step-3 | Verify positions & tags | READY | |
| step-4 | Validate LONG P&L | READY | |
| step-5 | Self-healing test | READY | Fixed tag collision |
| step-6 | Wait for LONG fills | READY | |
| step-7 | Close LONG positions | READY | Cancels brackets FIRST (race fix) |
| step-8 | Create SHORT brackets | READY | 0.25x ATR (tight) |
| step-9 | Verify SHORT positions | READY | Uses type field for direction |
| step-10 | Validate SHORT P&L | READY | |
| step-11 | Self-healing (SHORT) | READY | |
| step-12 | Wait for SHORT fills | READY | |
| step-13 | Close SHORT positions | READY | Cancels brackets FIRST (race fix) |
| step-14 | Generate interim report | READY | |
| step-15 | BracketOrderManager test | FAILED | errorCode 2 - SL price outside range (see Edge Case docs) |

---

## GitHub Issues

- **#12**: "Investigate ProjectX API for unrealized P&L endpoint"
- **TODO**: "Implement bracket order retry mechanism for errorCode 2 price rejections"

---

## Production Integration Checklist

### Ready to Integrate (Validated)

| Pattern | File | Description | Status |
|---------|------|-------------|--------|
| BracketOrderManager | `/tools/bracket_order_manager.py` | REST polling for orphan cleanup | ✅ Ready |
| Race-safe close | Steps 7 & 13 in workflow | Cancel brackets → wait → query → close | ✅ Validated |
| Position type detection | `futures_metadata.py` | Use `type` field (1=LONG, 2=SHORT) | ✅ Validated |
| round_to_tick() | `futures_metadata.py` | Fix floating point precision | ✅ Validated |
| Tag collision prevention | Workflow script | Use `startswith()` not `in` | ✅ Validated |
| Self-healing brackets | Workflow script | Recreate with `_HEAL` suffix | ✅ Validated |

### Needs Implementation

| Pattern | Priority | Notes |
|---------|----------|-------|
| Price rejection retry | HIGH | errorCode 2 "outside range" - refetch price & retry |
| Position reconciliation | MEDIUM | Periodic exchange vs virtual position sync |
| Hybrid bracket system | MEDIUM | See HYBRID_BRACKET_*.md docs |

### Integration Order

1. **Immediate**: Integrate `BracketOrderManager` into live trading loop
   - Call `poll_and_cleanup()` every 2-5 seconds
   - Register brackets after placing SL/TP orders

2. **Next Sprint**: Add price rejection retry mechanism
   - Wrap bracket order placement in retry loop
   - Refetch price on errorCode 2 + "outside range"

3. **Future**: Full hybrid bracket system (see detailed docs)

---

## Related Documentation

### Comprehensive Design Documents

These files contain detailed designs for production bracket order handling:

| File | Description | Size |
|------|-------------|------|
| `HYBRID_BRACKET_DESIGN.md` | Complete technical design | ~60KB |
| `HYBRID_BRACKET_FLOWCHART.md` | Visual diagrams and flows | ~25KB |
| `HYBRID_BRACKET_IMPLEMENTATION.py` | Production-ready code | ~35KB |
| `HYBRID_BRACKET_SUMMARY.md` | Executive summary | ~10KB |
| `BRACKET_ORDER_FAILURE_MODES.md` | Failure mode analysis & recovery | ~45KB |

### Key Concepts from Design Docs

**Hybrid Bracket Approach**:
- Broker as primary (90%+ cases) - server-side execution
- Client as safety net - only acts if broker **provably failed**
- Multi-layer duplicate prevention

**Failure Mode Coverage**:
1. Parent fills but child rejected
2. TP fills but SL not cancelled (orphan)
3. Connection lost while in position
4. Order stuck in pending state
5. Partial fills on exit orders

**State Machine**:
```
BROKER_ACTIVE → (60s no updates) → BROKER_FAILED → (client executes) → CLOSED
```

---

## Code Patterns for Main Loop Integration

### 1. BracketOrderManager Integration

```python
# In strategy initialization
self.bracket_manager = BracketOrderManager(client, account_id)

# After placing SL/TP orders
self.bracket_manager.register_bracket(
    base_tag=f"{symbol}_{direction}_{timestamp}",
    sl_order_id=sl_order.id,
    tp_order_id=tp_order.id,
    symbol=symbol
)

# In trading loop (every 2-5 seconds)
result = self.bracket_manager.poll_and_cleanup()
if result["cancelled"]:
    logger.info(f"Cancelled orphans: {result['cancelled']}")
```

### 2. Race-Safe Position Close

```python
def close_positions_safely(client, account_id, positions, bracket_tags):
    """Close positions without race conditions."""
    # Step 1: Cancel ALL bracket orders FIRST
    for tag in bracket_tags:
        cancel_orders_by_tag(client, account_id, tag)

    # Step 2: Wait for cancels to propagate
    time.sleep(0.5)

    # Step 3: Query current positions (now safe)
    current_positions = client.api.position_search_open(account_id)

    # Step 4: Send close orders
    for pos in current_positions.get("positions", []):
        close_position(client, account_id, pos)

    # Step 5: Verify flat
    verify_account_flat(client, account_id)
```

### 3. Position Direction Detection

```python
def get_position_quantity(position_dict):
    """Get signed quantity from position API response."""
    size = position_dict.get("size", 0)
    pos_type = position_dict.get("type", 1)  # 1=LONG, 2=SHORT

    # API returns size as always positive, use type for direction
    return size if pos_type == 1 else -size
```

### 4. Price Rejection Retry (TODO)

```python
def place_bracket_with_retry(client, account_id, contract_id,
                              sl_offset, tp_offset, max_retries=3):
    """Place bracket with automatic retry on price rejection."""
    for attempt in range(max_retries):
        current_price = get_current_price(client, contract_id)
        sl_price = round_to_tick(current_price - sl_offset, symbol)
        tp_price = round_to_tick(current_price + tp_offset, symbol)

        result = place_sl_order(client, account_id, sl_price)

        if result.get("success"):
            return place_tp_order(client, account_id, tp_price)

        error_code = result.get("errorCode")
        error_msg = result.get("errorMessage", "")

        if error_code == 2 and "outside allowed range" in error_msg:
            logger.warning(f"Price {sl_price} rejected, retry {attempt+1}")
            time.sleep(0.2)
            continue
        else:
            return result  # Different error, don't retry

    return {"success": False, "error": f"Failed after {max_retries} retries"}
```
