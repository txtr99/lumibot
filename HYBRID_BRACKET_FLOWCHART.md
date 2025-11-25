# Hybrid Bracket Order System - Decision Flow Diagrams

## 1. High-Level Architecture Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                       BRACKET ORDER SUBMITTED                        │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│              BROKER: Submit Parent + TP/SL Children                  │
│              - Parent order to broker                                │
│              - Broker creates TP limit order                         │
│              - Broker creates SL stop order                          │
│              - Returns order IDs                                     │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│           HYBRID MANAGER: Register Bracket State                     │
│           - Store broker order IDs                                   │
│           - Set state = BROKER_ACTIVE                                │
│           - Enable monitoring                                        │
│           - Record entry details                                     │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      CONTINUOUS MONITORING                           │
│                                                                       │
│  ┌──────────────────┐                  ┌──────────────────┐         │
│  │  Broker Stream   │──────────────────▶│  State Updates  │         │
│  │  - Order updates │                  │  - Last seen     │         │
│  │  - Position      │                  │  - Order status  │         │
│  │  - Trades        │                  │  - Position qty  │         │
│  └──────────────────┘                  └──────────────────┘         │
│                                                                       │
│  ┌──────────────────┐                  ┌──────────────────┐         │
│  │  Client Monitor  │──────────────────▶│  Backup Logic   │         │
│  │  - Every iteration│                  │  - Check failure│         │
│  │  - Price checks  │                  │  - Ready to act │         │
│  └──────────────────┘                  └──────────────────┘         │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                   ┌──────────────┴──────────────┐
                   ▼                             ▼
         ┌──────────────────┐         ┌──────────────────┐
         │  BROKER SUCCESS  │         │  BROKER FAILURE  │
         │  (90%+ of time)  │         │  (rare cases)    │
         └──────────────────┘         └──────────────────┘
                   │                             │
                   ▼                             ▼
         ┌──────────────────┐         ┌──────────────────┐
         │  Position Closed │         │  CLIENT TAKEOVER │
         │  State = CLOSED  │         │  Market close    │
         │  closed_by=broker│         │  State = CLOSED  │
         └──────────────────┘         └──────────────────┘
```

---

## 2. Broker Status Detection Flow

```
START: Check Broker Status
         │
         ▼
┌──────────────────────────┐
│ Get Current Position     │
└──────────────────────────┘
         │
         ├─────────────┬─────────────┐
         ▼             ▼             ▼
    Position      Position      Position
    Open          Closed        Unknown
         │             │             │
         │             └─────────────┤
         │                           │
         ▼                           ▼
┌──────────────────────────┐  ┌──────────────────┐
│ Query Order Status       │  │ RETURN:          │
│ - TP order status        │  │ "triggered"      │
│ - SL order status        │  │ (Broker won)     │
│ - Last update time       │  └──────────────────┘
└──────────────────────────┘
         │
         ├─────────────┬─────────────┬──────────────┐
         ▼             ▼             ▼              ▼
    Both Active   Both Failed   One Active     Stale (>60s)
         │             │             │              │
         │             │             │              ▼
         │             │             │         ┌──────────────┐
         │             │             │         │ Fresh Check  │
         │             │             │         │ API query    │
         │             │             │         └──────────────┘
         │             │             │              │
         │             │             │              ├────────┬────────┐
         │             │             │              ▼        ▼        ▼
         │             │             │           Active   Failed   Gone
         │             │             │              │        │        │
         ▼             ▼             ▼              ▼        ▼        ▼
┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌────────┐ ┌────────┐
│RETURN:       │ │RETURN:   │ │RETURN:   │ │RETURN:  │ │RETURN: │ │RETURN: │
│"active"      │ │"failed"  │ │"active"  │ │"active" │ │"failed"│ │"failed"│
└──────────────┘ └──────────┘ └──────────┘ └─────────┘ └────────┘ └────────┘
```

---

## 3. Client Execution Decision Tree

```
START: Should Client Execute?
         │
         ▼
┌─────────────────────────────┐
│ CHECK 1: Broker Status      │
│ Is broker active?           │
└─────────────────────────────┘
         │
         ├────────YES───────────▶ ABORT: "broker_active"
         │
         NO
         │
         ▼
┌─────────────────────────────┐
│ CHECK 2: Broker Triggered   │
│ Did broker already close?   │
└─────────────────────────────┘
         │
         ├────────YES───────────▶ ABORT: "broker_already_closed"
         │
         NO
         │
         ▼
┌─────────────────────────────┐
│ CHECK 3: Broker Failed      │
│ Is status = "failed"?       │
└─────────────────────────────┘
         │
         ├────────NO────────────▶ ABORT: "broker_status_ambiguous"
         │
         YES
         │
         ▼
┌─────────────────────────────┐
│ CHECK 4: Position Exists    │
│ Is position still open?     │
└─────────────────────────────┘
         │
         ├────────NO────────────▶ ABORT: "position_already_closed"
         │
         YES
         │
         ▼
┌─────────────────────────────┐
│ CHECK 5: Grace Period       │
│ Has 15s passed since        │
│ last broker update?         │
└─────────────────────────────┘
         │
         ├────────NO────────────▶ ABORT: "grace_period_active"
         │
         YES
         │
         ▼
┌─────────────────────────────┐
│ CHECK 6: Fresh Status       │
│ Query broker one more time  │
│ Are orders still dead?      │
└─────────────────────────────┘
         │
         ├────────NO────────────▶ ABORT: "broker_orders_active"
         │
         YES
         │
         ▼
┌─────────────────────────────┐
│ ✅ ALL CHECKS PASSED        │
│ EXECUTE CLIENT CLOSE        │
│ - Create market order       │
│ - Tag as CLIENT_BACKUP      │
│ - Submit to broker          │
│ - Mark bracket CLOSED       │
└─────────────────────────────┘
```

---

## 4. State Machine Diagram

```
                    INITIAL STATE
                         │
                         ▼
                ┌─────────────────┐
                │ BROKER_ACTIVE   │◀─────┐
                │ (broker handling │      │
                │  everything)     │      │
                └─────────────────┘      │
                         │               │
              Broker     │     Order     │ Order
              silent     │     updates   │ active
              >60s       │     received  │
                         │               │
                         ▼               │
                ┌─────────────────┐     │
                │ BROKER_FAILED   │─────┘
                │ (client enabled  │
                │  as backup)      │
                └─────────────────┘
                         │
                         │ Client
                         │ executes
                         │ closure
                         ▼
                ┌─────────────────┐
                │     CLOSED      │
                │ (final state)   │
                └─────────────────┘
                         │
                         │ closed_by:
                         │ - broker_tp
                         │ - broker_sl
                         │ - client_tp
                         │ - client_sl
                         │ - manual
                         ▼
                    [TERMINAL]


Alternative Path (Broker Success):

    BROKER_ACTIVE
         │
         │ Broker fills
         │ TP or SL
         ▼
      CLOSED
    (closed_by=broker_tp/sl)
```

---

## 5. Race Condition Prevention

```
Timeline of Client Execution with Safety Checks:

T = 0s                T = 15s              T = 20s              T = 25s
│                     │                    │                    │
│ Broker orders       │ Grace period       │ Fresh API check    │ Submit order
│ confirmed dead      │ waiting            │ re-verify          │ atomic
│                     │                    │                    │
├─────────────────────┼────────────────────┼────────────────────┤
│                     │                    │                    │
│ CHECK 1-4 passed    │ CHECK 5 passed     │ CHECK 6 passed     │ EXECUTE
│ State: FAILED       │ No broker updates  │ Orders still dead  │ Close position
│                     │                    │                    │


Concurrent Broker Fill Scenario:

T = 0s                T = 5s               T = 15s              T = 20s
│                     │                    │                    │
│ Client checks       │ Broker fills       │ Client sees filled │ Client aborts
│ broker status       │ (stream delayed)   │ order in fresh     │ execution
│ = failed            │                    │ API check          │
│                     │                    │                    │
├─────────────────────┼────────────────────┼────────────────────┤
│                     │                    │                    │
│ Enter grace period  │ [BROKER FILLS]     │ CHECK 6 fails      │ ABORT ✓
│                     │                    │ Order = filled     │
│                     │                    │                    │


Double-Fill Prevention:

T = 0s                T = 1s
│                     │
│ Client submits      │ Broker order fills
│ close order         │ simultaneously
│                     │
├─────────────────────┤
│                     │
│ Order A: market     │ Order B: limit
│ close LONG 1        │ close LONG 1
│                     │
│ Position closed     │ Second order
│ by Order A          │ REJECTED ✓
│                     │ (no position)
```

---

## 6. Reconciliation Flow

```
Every N Iterations (default: 10)
         │
         ▼
┌─────────────────────────────────────┐
│ FOR EACH ACTIVE BRACKET:            │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ 1. Fetch Position from Broker       │
│    - Current quantity                │
│    - Avg price                       │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ 2. Fetch Order Status from Broker   │
│    - TP order status                 │
│    - SL order status                 │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ 3. Compare Expected vs Actual        │
└─────────────────────────────────────┘
         │
         ├──────────────┬──────────────┬──────────────┐
         ▼              ▼              ▼              ▼
    All Match      Qty Mismatch   Unprotected    Orphaned Orders
         │              │              │              │
         │              ▼              ▼              ▼
         │     ┌──────────────┐ ┌─────────────┐ ┌──────────────┐
         │     │ LOG WARNING  │ │ENABLE CLIENT│ │CANCEL ORDERS │
         │     │ Update qty   │ │ Set FAILED  │ │ Clean up     │
         │     └──────────────┘ └─────────────┘ └──────────────┘
         │              │              │              │
         └──────────────┴──────────────┴──────────────┘
                        │
                        ▼
               ┌────────────────┐
               │ Log Summary    │
               │ Update metrics │
               └────────────────┘


Reconciliation States:

┌────────────────┬──────────────┬──────────────┬─────────────────┐
│  Position      │  Orders      │  Diagnosis   │  Action         │
├────────────────┼──────────────┼──────────────┼─────────────────┤
│  EXISTS        │  ACTIVE      │  HEALTHY     │  None           │
│  EXISTS        │  NONE        │  UNPROTECTED │  Enable client  │
│  NONE          │  ACTIVE      │  ORPHANED    │  Cancel orders  │
│  NONE          │  NONE        │  CLOSED      │  Archive        │
│  QTY MISMATCH  │  ACTIVE      │  PARTIAL     │  Update & warn  │
└────────────────┴──────────────┴──────────────┴─────────────────┘
```

---

## 7. Event Flow Diagram

```
Stream Events ──────────────────────────────┐
                                            │
                                            ▼
                                  ┌──────────────────┐
                                  │ Event Handler    │
                                  └──────────────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    ▼                       ▼                       ▼
          ┌──────────────────┐    ┌──────────────────┐   ┌──────────────────┐
          │ Order Update     │    │ Position Update  │   │ Trade Update     │
          │ - Status change  │    │ - Qty change     │   │ - Fill confirm   │
          │ - Fill event     │    │ - Close detect   │   │ - Price info     │
          └──────────────────┘    └──────────────────┘   └──────────────────┘
                    │                       │                       │
                    └───────────────────────┼───────────────────────┘
                                            │
                                            ▼
                                  ┌──────────────────────┐
                                  │ Update Bracket State │
                                  │ - Last seen          │
                                  │ - Order status       │
                                  │ - Position qty       │
                                  └──────────────────────┘
                                            │
                                            ▼
                                  ┌──────────────────────┐
                                  │ Status Detection     │
                                  │ - Active/Failed/Done │
                                  └──────────────────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    ▼                       ▼                       ▼
          ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
          │ ACTIVE       │        │ CLOSED       │        │ FAILED       │
          │ Keep waiting │        │ Mark done    │        │ Enable client│
          └──────────────┘        └──────────────┘        └──────────────┘
```

---

## 8. Monitoring Loop Pseudocode

```python
def monitor_all_brackets():
    """Called every strategy iteration."""

    for bracket in active_brackets:

        # Step 1: Detect broker status
        status = detect_broker_status(bracket)

        # Step 2: State machine transition
        if status == "active":
            bracket.state = "BROKER_ACTIVE"
            bracket.broker_last_seen = now()
            continue  # Broker handling it

        elif status.startswith("triggered"):
            bracket.state = "CLOSED"
            bracket.closed_by = status
            log_success(bracket)
            archive(bracket)
            continue  # Done, broker won

        elif status == "failed":
            bracket.state = "BROKER_FAILED"
            bracket.client_enabled = True
            log_warning("Broker failed, client taking over")
            # Fall through to client check

        elif status == "unknown":
            # Ambiguous, keep monitoring
            pass

        # Step 3: If client enabled, check execution
        if bracket.client_enabled:

            # Get current price
            price = get_last_price(bracket.asset)

            # Check TP trigger
            tp_hit = (bracket.position_side == "long" and price >= bracket.tp_price) or \
                     (bracket.position_side == "short" and price <= bracket.tp_price)

            # Check SL trigger
            sl_hit = (bracket.position_side == "long" and price <= bracket.sl_price) or \
                     (bracket.position_side == "short" and price >= bracket.sl_price)

            # Determine trigger
            if sl_hit:
                trigger = "client_sl"
            elif tp_hit:
                trigger = "client_tp"
            else:
                continue  # Not triggered yet

            # Final safety checks
            should_execute, reason = should_client_execute(bracket, trigger)

            if should_execute:
                # EXECUTE CLIENT BACKUP
                log_warning(f"CLIENT EXECUTING: {trigger}")
                close_order = create_close_order(bracket, trigger)
                submit_order(close_order)
                bracket.state = "CLOSED"
                bracket.closed_by = trigger
                log_success(bracket)
            else:
                # Blocked by safety check
                log_info(f"Client blocked: {reason}")
```

---

## 9. Complete System Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                          HYBRID BRACKET SYSTEM                          │
├────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  INPUTS                   PROCESSING                    OUTPUTS         │
│                                                                         │
│  ┌──────────┐            ┌─────────────┐             ┌──────────┐     │
│  │ Strategy │───────────▶│   Bracket   │────────────▶│ Position │     │
│  │ Signal   │            │   Manager   │             │ Closed   │     │
│  └──────────┘            └─────────────┘             └──────────┘     │
│                                 │                                       │
│  ┌──────────┐                   ├──────────────┐                       │
│  │ Broker   │                   ▼              ▼                       │
│  │ Stream   │           ┌─────────────┐  ┌──────────┐                 │
│  └──────────┘           │   Broker    │  │  Client  │                 │
│       │                 │   Primary   │  │  Backup  │                 │
│       │                 └─────────────┘  └──────────┘                 │
│       │                         │              │                       │
│       └─────────────────────────┴──────────────┘                       │
│                                 │                                       │
│                    ┌────────────┴────────────┐                         │
│                    ▼                         ▼                         │
│            ┌───────────────┐        ┌───────────────┐                 │
│            │ State Tracker │        │ Reconciler    │                 │
│            │ - Last seen   │        │ - Position    │                 │
│            │ - Order status│        │ - Orders      │                 │
│            └───────────────┘        └───────────────┘                 │
│                                                                         │
│  SAFETY LAYERS:                                                        │
│  [1] Broker Status Check                                               │
│  [2] Position Verification                                             │
│  [3] Grace Period Wait                                                 │
│  [4] Fresh API Query                                                   │
│  [5] Atomic Execution                                                  │
│                                                                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Key Takeaways

1. **Broker is Primary**: 90%+ of cases handled by broker server-side
2. **Client is Safety Net**: Only acts on provable broker failure
3. **Multi-Layer Safety**: 6 checks prevent duplicate closures
4. **Absence Detection**: Don't act on price touch, act on confirmed failure
5. **Grace Periods**: Wait 15s after last broker update before acting
6. **Fresh Verification**: Final API check before client executes
7. **Reconciliation**: Periodic checks catch position/order mismatches
8. **Observability**: Comprehensive logging and metrics for debugging
