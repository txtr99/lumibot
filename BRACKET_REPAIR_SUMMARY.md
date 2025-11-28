# Bracket Order Orphaning: Executive Summary

## Quick Overview

The `repair_position_desync()` method in `BracketOrderManager` (tools/bracket_order_manager.py) contains **10 critical edge cases** that can cause bracket orders to become orphaned while positions are zeroed. This creates data inconsistency, memory leaks, and potential P&L tracking errors.

**Core Risk**: Zeroing a virtual position that still has active stop loss / take profit orders on the exchange.

---

## The Three Critical Failure Modes

### 1. **Exception Silencing** (Lines 2204-2210)
```python
try:
    cancel_brackets_for_strategy(sid)  # ← Can fail with exception
except Exception as ce:
    logger.warning(...)  # ← Just logs, doesn't stop

state.tracker.reset()  # ← Still executes even if cancel failed!
```
**Outcome**: Position zeroed locally while brackets remain active on exchange.

### 2. **No Cancel Confirmation** (Lines 301-320)
```python
response = client.api.order_cancel(order_id)
if response["success"]:
    return True
else:
    return False
    # ← Never checks: is the order actually in TERMINAL state?
```
**Outcome**: Network ambiguity—cancel might have succeeded even if response fails.

### 3. **No State Cleanup** (Lines 368-390)
```python
def cancel_bracket(self, base_tag: str):
    # ... cancels orders ...
    # ← But never removes bracket from self.brackets dict!
```
**Outcome**: Brackets polled indefinitely, memory leak, polling overhead.

---

## Key Scenarios

| # | Scenario | Trigger | Impact | Severity |
|---|----------|---------|--------|----------|
| 1 | TP/SL fill during zero | Simultaneous fill + repair | Real fill not tracked | **CRITICAL** |
| 2 | SL fills while cancelling TP | Fast price movement | Both legs fill, wrong P&L | **CRITICAL** |
| 3 | Cancel fails, zero anyway | Network timeout | Orphaned bracket, untracked position | **HIGH** |
| 4 | Strategy vs Manager state divergence | Incomplete cleanup | Stale bracket pairs in memory | **MEDIUM** |
| 5 | Cancel response lost | Network interruption | Code thinks failed but order terminal | **MEDIUM** |
| 6 | Partial zero with mixed brackets | Multiple strategies | Some brackets orphaned | **HIGH** |
| 7 | Concurrent reset during fill | Threading race | Virtual position tracker corruption | **CRITICAL** |
| 8 | Streaming fill after cancel | WebSocket update | Fill not applied to zeroed position | **MEDIUM** |
| 9 | Bracket pair never cleaned | Normal operation | Memory leak, CPU waste | **LOW** |
| 10 | Multi-strategy concurrent access | Concurrent fills + repair | Data corruption in tracker | **CRITICAL** |

---

## Recommended Fixes (Priority Order)

### Phase 1: Immediate (Prevent Data Loss)

1. **Add transaction boundary** (2 hours):
   - Don't swallow exceptions in cancel phase
   - If cancel fails, skip position zero or abort repair
   - Return error to caller

2. **Verify cancel actually succeeded** (3 hours):
   - After cancel API call, poll order status
   - Confirm order is in TERMINAL state before proceeding
   - Handle both API success and failure paths

3. **Clean up bracket pairs** (2 hours):
   - After cancel succeeds, remove from `self.brackets` dict
   - Add timeout-based cleanup for orphaned pairs
   - Ensure no stale entries grow indefinitely

### Phase 2: Short-term (Prevent State Divergence)

4. **Synchronize repair with polling** (4 hours):
   - Don't run `repair_position_desync()` and `poll_cycle()` simultaneously
   - Add mutual exclusion or serial processing
   - Prevent concurrent position/bracket modification

5. **Add thread safety to VirtualPositionTracker** (2 hours):
   - Add `threading.RLock()` for all dict/list access
   - Protect `reset()` and `execute_order()` operations
   - Prevent concurrent modification corruption

6. **Verify position before applying fill** (2 hours):
   - Check position exists and is non-flat before fill
   - Detect and log orphaned fills
   - Prevent fills on zeroed positions

### Phase 3: Medium-term (Improve Reliability)

7. **Add bracket pair cleanup tracking** (3 hours):
   - Track bracket pair creation time
   - Remove truly orphaned pairs after timeout
   - Monitor memory growth

8. **Atomic position + bracket operations** (4 hours):
   - Design "position guardian" pattern
   - Single source of truth for position + brackets
   - Prevents state divergence

9. **Add audit trail** (2 hours):
   - Log all position changes (why, when, by whom)
   - Enable post-mortem analysis
   - Track repair history

### Phase 4: Long-term (Architectural)

10. **Refactor bracket lifecycle** (6 hours):
    - Define clear states: ACTIVE → PENDING_CANCEL → CANCELLED → DELETED
    - Or: ACTIVE → FILL_PROCESSED → DELETED
    - Prevent state confusion

---

## Code Changes Required

### Critical: Fix Pattern 1 (Exception Silencing)

**File**: `tools/bracket_order_manager.py`, lines 2204-2210

**Before**:
```python
try:
    cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
except Exception as ce:
    self.logger.warning(...)

state.tracker.reset()  # ALWAYS happens
```

**After**:
```python
try:
    cancel_result = self.cancel_brackets_for_strategy(sid, wait_seconds=0.3)
    if not cancel_result.get("all_cancelled"):
        raise Exception("Bracket cancel incomplete")
except Exception as ce:
    self.logger.error(...)
    result["warnings"].append({...})
    continue  # ← Skip this strategy's zero

state.tracker.reset()  # Only if cancel succeeded
```

### Critical: Fix Pattern 2 (No Cancel Confirmation)

**File**: `tools/bracket_order_manager.py`, lines 301-320

**Before**:
```python
response = self.client.api.order_cancel(self.account_id, order_id)
if response.get("success"):
    return True
else:
    return False
```

**After**:
```python
# Step 1: Send cancel request
response = self.client.api.order_cancel(self.account_id, order_id)

# Step 2: ALWAYS verify terminal state (regardless of response)
for attempt in range(3):
    orders = self.client.api.order_search(...).get("orders", [])
    for order in orders:
        if order.get("id") == order_id and order.get("status") in TERMINAL_STATUSES:
            return True  # Confirmed terminal
    time.sleep(0.1)

self.logger.error(f"Could not confirm cancel for {order_id}")
return False
```

### Critical: Fix Pattern 3 (No Cleanup)

**File**: `tools/bracket_order_manager.py`, lines 368-390

**Before**:
```python
def cancel_bracket(self, base_tag: str):
    if bracket.sl_order_id:
        self._cancel_order(bracket.sl_order_id, "SL", base_tag)
    if bracket.tp_order_id:
        self._cancel_order(bracket.tp_order_id, "TP", base_tag)
    # ← Never removes from self.brackets
    return result
```

**After**:
```python
def cancel_bracket(self, base_tag: str):
    if bracket.sl_order_id:
        self._cancel_order(bracket.sl_order_id, "SL", base_tag)
    if bracket.tp_order_id:
        self._cancel_order(bracket.tp_order_id, "TP", base_tag)

    # ← NEW: Remove from tracking
    if base_tag in self.brackets:
        del self.brackets[base_tag]

    return result
```

---

## Testing Strategy

Create test suite in `tests/test_bracket_repair_edge_cases.py`:

- **7 test groups** (40+ individual tests)
- Cover all 10 scenarios
- Include concurrency tests with threading
- Verify atomicity and isolation
- Performance baseline (cancel overhead <100ms)

See `BRACKET_REPAIR_TEST_SCENARIOS.md` for detailed test cases.

---

## Risk Assessment

### Before Fixes
- **Data Loss Risk**: HIGH (fills not tracked)
- **P&L Error Risk**: HIGH (wrong fills applied)
- **Memory Leak Risk**: MEDIUM (unbounded bracket dict)
- **System Instability**: MEDIUM (corruption can cascade)

### After Fixes
- **Data Loss Risk**: LOW (transactional boundaries)
- **P&L Error Risk**: LOW (fill validation)
- **Memory Leak Risk**: LOW (cleanup mechanism)
- **System Instability**: LOW (atomic operations)

---

## Document Map

1. **BRACKET_ORDER_ORPHANING_EDGE_CASES.md** (3300 lines)
   - 10 detailed scenarios
   - Vulnerability analysis
   - Root cause identification
   - Consequences and recommendations

2. **BRACKET_REPAIR_CODE_PATTERNS.md** (800 lines)
   - 7 vulnerable code patterns
   - Before/after code examples
   - Specific file locations and line numbers
   - Fix implementations

3. **BRACKET_REPAIR_TEST_SCENARIOS.md** (700 lines)
   - 7 test groups with 40+ tests
   - Concurrency tests
   - Integration tests
   - Performance benchmarks

4. **BRACKET_REPAIR_SUMMARY.md** (this file)
   - Executive summary
   - Quick reference
   - Priority-ordered fixes
   - Implementation timeline

---

## Implementation Timeline

**Total Effort**: ~22-30 hours (distributed across 3 weeks)

- **Week 1** (Phase 1): Critical fixes, 7 hours
  - Exception silencing, cancel confirmation, cleanup

- **Week 2** (Phase 2): Integration, 10 hours
  - Synchronization, thread safety, fill validation

- **Week 3** (Phase 3-4): Polish, 5-13 hours
  - Audit trail, lifecycle management, testing

---

## Validation Checklist

- [ ] All 10 scenarios have test coverage
- [ ] Exception handling prevents position zeroing on cancel failure
- [ ] Cancel confirmation polls order status
- [ ] Bracket pairs removed after successful cancel
- [ ] No stale entries in `self.brackets` after 5 minutes
- [ ] VirtualPositionTracker has thread-safe locking
- [ ] Fills validated before applying to positions
- [ ] Concurrent repair + polling don't corrupt data
- [ ] All tests pass in isolation and concurrently
- [ ] Performance overhead <100ms per operation

---

## Related Files

- `tools/bracket_order_manager.py` (2300+ lines) - Main repair logic
- `lumibot/tools/virtual_position_tracker.py` (294 lines) - Position tracking
- `custom_portfolio/multi_strategy_executor_enhanced.py` (100+ lines) - Strategy state
- `tests/test_bracket_order_manager_enhanced.py` (100+ lines) - Existing tests

---

## Questions for Development Team

1. **Concurrency Model**: Is repair_position_desync() expected to run in same thread as polling, or separate?
2. **Timeout Values**: What should be bracket pair cleanup timeout? (Suggested: 5 minutes)
3. **Rollback Strategy**: Should failed repairs attempt to restore brackets? Or fail fast?
4. **Logging Level**: Should orphaned fills log as WARNING or ERROR?
5. **Backward Compatibility**: Do existing strategies need migration before changes?

---

## Success Criteria

- [ ] No orphaned bracket orders after position repair
- [ ] No fills applied to zeroed positions
- [ ] No memory leaks from stale bracket pairs
- [ ] Repair failures don't corrupt position state
- [ ] All 10 scenarios have passing tests
- [ ] <1s overhead for 100 concurrent repairs
- [ ] Zero data corruption in stress tests

