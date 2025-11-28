# Recovery & Restart: Decision Trees

Visual decision trees for common recovery scenarios.

---

## DECISION TREE 1: Bot Startup Sequence

```
BOT STARTS
│
├─ Load VirtualPositionTracker
│  ├─ File exists and valid?
│  │  ├─ YES → Load positions (continue to next step)
│  │  └─ NO → Start with empty positions
│  │
│  └─ Any positions loaded?
│     ├─ YES → Continue
│     └─ NO → positions = {}
│
├─ Load OrderRegistry
│  ├─ File exists and valid?
│  │  ├─ YES → Load orders (continue)
│  │  └─ NO → Start with empty registry
│  │
│  └─ Any orders loaded?
│     ├─ YES → Continue
│     └─ NO → orders = {}
│
├─ Load BracketOrderManager state
│  ├─ File exists and valid?
│  │  ├─ YES → Load brackets
│  │  └─ NO → Start with empty brackets
│
├─ Query exchange for positions
│  │
│  └─ get_positions_from_exchange()
│     └─ exchange_positions = {...}
│
├─ RUN SYNC CHECK
│  │
│  └─ For each symbol in [ES, NQ, GC, ...]:
│     │
│     ├─ calc: virtual_qty = sum(all strategies)
│     ├─ calc: exchange_qty = exchange.position[symbol]
│     │
│     └─ Is virtual_qty == exchange_qty?
│        │
│        ├─ YES (synced)
│        │  │
│        │  └─ → Can proceed to normal trading
│        │
│        └─ NO (mismatch)
│           │
│           ├─ Is virtual_qty > exchange_qty?
│           │  │ "We think we have more than exchange shows"
│           │  │
│           │  └─ → FLATTEN_SYMBOL (nuclear option)
│           │
│           └─ Is virtual_qty < exchange_qty?
│              │ "Exchange has more than we think"
│              │
│              └─ → FLATTEN_SYMBOL (nuclear option)
│
├─ FLATTEN_SYMBOL (if mismatch)
│  │
│  ├─ Cancel all pending orders for symbol
│  ├─ Submit close order for exchange_qty
│  ├─ Mark virtual position flat
│  ├─ Wait for close confirmation
│  │
│  └─ On next sync check:
│     ├─ Is position now flat?
│     │  └─ YES → Proceed to trading
│     └─ NO → ALERT HUMAN (flatten failed)
│
└─ Trading starts
```

---

## DECISION TREE 2: Position Sync Check (Per Symbol)

```
SYNC CHECK: Check if virtual matches exchange

START
│
├─ Get virtual_qty
│  ├─ Query virtual tracker for symbol
│  ├─ Sum across ALL strategies
│  │
│  └─ virtual_qty = sum([strategy.position for each strategy])
│
├─ Get exchange_qty
│  ├─ Query exchange positions
│  ├─ Find contract matching symbol
│  ├─ Extract size + direction
│  │
│  └─ exchange_qty = size * (1 if LONG else -1)
│
├─ Compare
│  │
│  ├─ abs(virtual_qty - exchange_qty) < 1e-9?
│  │  │
│  │  ├─ YES (synced within floating point epsilon)
│  │  │  │
│  │  │  └─ RETURN: SyncResult(is_synced=True)
│  │  │
│  │  └─ NO (mismatch detected)
│  │     │
│  │     └─ Continue to diagnosis
│  │
│  └─ Diagnosis
│     │
│     ├─ Log error: "MISMATCH detected"
│     ├─ Log: "Exchange shows {exchange_qty}, Virtual shows {virtual_qty}"
│     ├─ Log: "Difference: {exchange_qty - virtual_qty}"
│     │
│     └─ RETURN: SyncResult(
│           is_synced=False,
│           action_needed="FLATTEN_SYMBOL",
│           details="..."
│        )
│
└─ END
```

---

## DECISION TREE 3: NUCLEAR Flatten Execution

```
FLATTEN_SYMBOL called

START
│
├─ Step 1: Cancel all pending orders for symbol
│  │
│  ├─ Query registry.get_pending_orders_for_symbol(symbol)
│  │
│  ├─ For each pending order:
│  │  ├─ Call client.order_cancel(order_id)
│  │  │  │
│  │  │  ├─ Success? → registry.register_cancellation()
│  │  │  │
│  │  │  └─ Failed? → log warning, continue
│  │  │
│  │  └─ Brief wait (0.3s)
│  │
│  └─ cancelled_count = {N}
│
├─ Step 2: Get current exchange position
│  │
│  ├─ Query client.position_search_open(account_id)
│  ├─ Find position for symbol
│  │
│  └─ exchange_qty = {current amount on exchange}
│
├─ Step 3: Close exchange position if needed
│  │
│  ├─ If exchange_qty == 0?
│  │  │
│  │  ├─ YES → Skip to step 4 (already flat)
│  │  │
│  │  └─ NO → Continue
│  │
│  ├─ Determine close side
│  │  └─ close_side = "SELL" if exchange_qty > 0 else "BUY"
│  │
│  ├─ Determine close qty
│  │  └─ close_qty = abs(exchange_qty)
│  │
│  ├─ Submit close order
│  │  │
│  │  ├─ Call client.order_place(...)
│  │  │  │
│  │  │  ├─ Success? → order_id = result["orderId"]
│  │  │  │
│  │  │  └─ Failed? → log error, continue anyway
│  │  │
│  │  └─ Log: "NUCLEAR close order: {close_side} {close_qty} {symbol}"
│  │
│  └─ BUG ZONE: Virtual marked flat BEFORE close confirms!
│     └─ tracker.close_position(strategy_id)
│
├─ Step 4: Mark virtual positions flat
│  │
│  ├─ Query tracker.get_all_positions()
│  │
│  ├─ For each position with matching symbol:
│  │  └─ tracker.close_position(strategy_id)
│  │
│  └─ closed_count = {N}
│
├─ Step 5: Wait and verify
│  │
│  ├─ Sleep briefly
│  │
│  ├─ Re-query exchange positions
│  │
│  ├─ Is exchange_qty now 0?
│  │  │
│  │  ├─ YES → Flatten successful
│  │  │
│  │  └─ NO → Flatten FAILED (order didn't execute)
│  │     │
│  │     └─ BUG ZONE: If close order failed but virtual is flat:
│  │        │  Next iteration:
│  │        │  - virtual_qty = 0 (marked flat in step 4)
│  │        │  - exchange_qty = X (close didn't execute)
│  │        │  - MISMATCH again → FLATTEN triggered AGAIN
│  │        │  → INFINITE LOOP RISK
│  │
│  └─ Log: "NUCLEAR flatten complete"
│
└─ END
```

---

## DECISION TREE 4: Bracket Resubmission Risk

```
STRATEGY ON_TRADING_ITERATION

START
│
├─ Fetch data for symbol
├─ Calculate indicators (ATR, RSI, etc.)
├─ Generate signal (BUY, SELL, HOLD)
│
├─ Current position check
│  │
│  └─ position_qty = virtual_tracker.get_position(symbol)
│
├─ If signal == "HOLD" and position_qty > 0
│  │
│  ├─ Check time-based exit
│  │  └─ bars_in_trade > max_bars?
│  │     │
│  │     ├─ YES → Close position (exit trade)
│  │     │
│  │     └─ NO → Keep position, check brackets
│  │
│  └─ Check bracket status
│     │
│     ├─ Do we have brackets?
│     │  │
│     │  └─ Check: state.brackets_submitted
│     │     │
│     │     ├─ YES (True) → Don't resubmit
│     │     │  │
│     │     │  └─ Brackets already on exchange
│     │     │
│     │     └─ NO (False) → Resubmit brackets
│     │        │
│     │        └─ BUG ZONE: On restart, this is ALWAYS False!
│     │           │
│     │           └─ Call submit_brackets()
│     │              │
│     │              ├─ TP order submitted (again)
│     │              ├─ SL order submitted (again)
│     │              │
│     │              └─ RESULT: 2 TP, 2 SL on exchange
│     │                 (Duplicates!)
│     │
│     └─ set state.brackets_submitted = True
│
└─ END
```

---

## DECISION TREE 5: State File Loading (Fallback Pattern)

```
LOAD_STATE(primary_path, backup1_path, backup2_path)

START
│
├─ Try primary file
│  │
│  ├─ Does primary_path exist?
│  │  │
│  │  ├─ NO → Skip to backup1
│  │  │
│  │  └─ YES → Try to load
│  │     │
│  │     ├─ json.load() succeeds?
│  │     │  │
│  │     │  ├─ YES → Return data (SUCCESS)
│  │     │  │
│  │     │  └─ NO → JSONDecodeError
│  │     │     │
│  │     │     └─ Log: "Primary file corrupt"
│  │     │        │
│  │     │        └─ Continue to backup1
│  │     │
│  │     └─ Is file completely empty?
│  │        │
│  │        ├─ YES → Empty data, valid but no state
│  │        │  │
│  │        │  └─ Return: {} (empty dict)
│  │        │
│  │        └─ NO → Continue to next step
│  │
│  └─ Checksum validation (if available)
│     │
│     ├─ Does stored_checksum == calculated_checksum?
│     │  │
│     │  ├─ YES → Data valid
│     │  │  │
│     │  │  └─ Return data (SUCCESS)
│     │  │
│     │  └─ NO → Corruption detected
│     │     │
│     │     └─ Continue to backup1
│
├─ Try backup1 file
│  │
│  ├─ Does backup1_path exist?
│  │  │
│  │  ├─ NO → Skip to backup2
│  │  │
│  │  └─ YES → Try to load (same validation as primary)
│  │     │
│  │     ├─ Valid? → Return data (SUCCESS)
│  │     │
│  │     └─ Invalid? → Continue to backup2
│  │
│  └─ Log: "Using backup1 (age: XX minutes)"
│
├─ Try backup2 file
│  │
│  ├─ Does backup2_path exist?
│  │  │
│  │  ├─ NO → No backups available
│  │  │
│  │  └─ YES → Try to load (same validation)
│  │     │
│  │     ├─ Valid? → Return data (SUCCESS)
│  │     │
│  │     └─ Invalid? → Continue
│  │
│  └─ Log: "Using backup2 (age: XX minutes)"
│
├─ No valid state found
│  │
│  └─ Log: "CRITICAL: No valid state found in primary or backups"
│     │
│     └─ Return: {} (empty dict)
│        │
│        └─ Bot will see all positions as flat
│           │
│           └─ If exchange has positions → FLATTEN_SYMBOL
│
└─ END
```

---

## DECISION TREE 6: Cold Start Detection

```
STARTUP_VALIDATION

START
│
├─ Load virtual state
│  └─ virtual_positions = load_tracker_state()
│
├─ Query exchange positions
│  └─ exchange_positions = client.position_search_open()
│
├─ Calculate aggregates
│  │
│  ├─ For each symbol:
│  │  ├─ virtual_qty = sum(all strategies)
│  │  └─ exchange_qty = exchange.position[symbol]
│  │
│  └─ Is there any mismatch?
│
├─ Cold start check
│  │
│  ├─ Is virtual_qty == 0 AND exchange_qty > 0?
│  │  │
│  │  ├─ YES → COLD START ANOMALY DETECTED
│  │  │  │
│  │  │  └─ Log: "CRITICAL: Exchange has {exchange_qty} {symbol}"
│  │  │     │      "but virtual tracker is empty!"
│  │  │     │
│  │  │     └─ Decision: What to do?
│  │  │        │
│  │  │        ├─ Option A: Auto-flatten (CURRENT BEHAVIOR)
│  │  │        │  │
│  │  │        │  ├─ Risk: Could be wrong if exchange stale
│  │  │        │  ├─ Risk: Could close profitable position
│  │  │        │  │
│  │  │        │  └─ Action: FLATTEN_SYMBOL
│  │  │        │
│  │  │        ├─ Option B: Require manual review (SAFER)
│  │  │        │  │
│  │  │        │  ├─ Risk: Human needs to respond
│  │  │        │  ├─ Benefit: Prevents auto-liquidation
│  │  │        │  │
│  │  │        │  └─ Action: Wait for human input
│  │  │        │
│  │  │        └─ Option C: Quarantine mode
│  │  │           │
│  │  │           ├─ Don't trade, only monitor
│  │  │           ├─ Log all exchange activity
│  │  │           └─ Wait for manual review
│  │  │
│  │  └─ NO → Proceed normally
│  │
│  └─ Are there other mismatches?
│     └─ Continue to normal sync check
│
└─ END
```

---

## DECISION TREE 7: Orphaned Order Detection

```
STARTUP_ORDER_ARCHAEOLOGY

START
│
├─ Calculate session boundaries
│  │
│  ├─ previous_session_start = yesterday at 17:00 CT (session close)
│  ├─ session_start = today at 09:30 CT (session open)
│  │
│  └─ stale_threshold = 24 hours ago
│
├─ Query all orders from exchange
│  │
│  ├─ Call client.order_search(
│  │        created_after = datetime.now() - timedelta(hours=24)
│  │     )
│  │
│  └─ all_orders = {...}
│
├─ Filter for stale OPEN orders
│  │
│  ├─ For each order in all_orders:
│  │  │
│  │  ├─ Is order.status == OPEN (1)?
│  │  │  │
│  │  │  ├─ NO → Skip (filled or cancelled, no risk)
│  │  │  │
│  │  │  └─ YES → Check age
│  │  │     │
│  │  │     └─ Is order.created_at < stale_threshold?
│  │  │        │
│  │  │        ├─ NO → Recent order, keep
│  │  │        │
│  │  │        └─ YES → STALE ORDER FOUND
│  │  │           │
│  │  │           └─ stale_orders[] = order
│  │  │
│  │  └─ Is order from previous session?
│  │     │
│  │     ├─ YES → High priority (carried over)
│  │     │  │
│  │  │  └─ Log: "CARRIED OVER ORDER"
│  │     │
│  │     └─ NO → Old but same session
│  │        │
│  │        └─ Log: "PENDING SINCE START OF SESSION"
│  │
│  └─ stale_count = len(stale_orders)
│
├─ Decision: What to do with stale orders?
│  │
│  ├─ If stale_count == 0?
│  │  │
│  │  └─ Proceed normally
│  │
│  └─ If stale_count > 0?
│     │
│     ├─ Log warning: Found {stale_count} stale open orders
│     │
│     ├─ For each stale order:
│     │  ├─ Log: tag, age, symbol, side, qty, price
│     │  │
│     │  └─ Decision:
│     │     │
│     │     ├─ Option A: Auto-cancel
│     │     │  │
│     │     │  ├─ Risk: Could cancel intended order
│     │     │  │
│     │     │  └─ Action: client.order_cancel(order_id)
│     │     │
│     │     ├─ Option B: Require human approval
│     │     │  │
│     │     │  ├─ Benefit: Prevents unexpected cancellation
│     │     │  │
│     │     │  └─ Action: Wait for human input
│     │     │
│     │     └─ Option C: Quarantine mode
│     │        │
│     │        ├─ Monitor, don't trade
│     │        │
│     │        └─ Wait for resolution
│     │
│     └─ If auto-cancel chosen:
│        ├─ Cancel each stale order
│        ├─ Log each cancellation with reason
│        └─ Audit trail for compliance
│
└─ END (proceed to normal startup or quarantine)
```

---

## Summary: Key Decision Points

| Decision Point | Good Path | Bad Path (Risk) |
|---|---|---|
| **Sync Check** | virtual == exchange → proceed | virtual != exchange → flatten immediately |
| **Flatten Execution** | Wait for close confirmation | Mark flat before close executes → loop |
| **Bracket Resubmit** | Check persisted flag | Reset flag on restart → duplicate |
| **State Loading** | Use primary, fallback to backup | All files lost → start empty |
| **Cold Start** | Require manual review | Auto-flatten without confirmation |
| **Orphaned Orders** | Detect and clean up | Ignore, let them fill → mismatch |

