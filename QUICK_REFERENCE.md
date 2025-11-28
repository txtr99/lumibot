# Edge Cases Quick Reference Card

## The System in 60 Seconds

```
Multiple strategies (S1, S2, S3...)
         ↓
Virtual Position Tracker (assumes instant fills)
         ↓
    Aggregate to net
         ↓
Compare with Exchange Position API (10-100ms lag)
         ↓
If virtual > exchange:
   ← REPAIR: Zero phantom positions ←
         ↓
        ???
```

**Problem:** "Phantom" might be a real fill in-flight. Repair can create disasters.

---

## Edge Cases at a Glance

| # | Name | Trigger | Symptom | Fix |
|---|------|---------|---------|-----|
| 1 | **SL Fill During Sync** | SL fills @ exact sync moment | Position inverted, exit invisible | Flight window + cooldown |
| 2 | **Double Fill** | Both SL & TP fill simultaneously | Position becomes short | Validate single exit per position |
| 3 | **In-Flight Order** | Sync before order confirms | Entry zeroed, double-entry | Flight window blocks sync |
| 4 | **Concurrent Write** | Repair thread + fill thread race | Position math corrupted | Atomic repair with lock |
| 5 | **Wrong Direction Repair** | Repair triggers opposite divergence | Oscillating repairs | Conservative policy |
| 6 | **Bracket During Sync** | TP fills while sync running | P&L lost, multiple entries | Sync cooldown |
| 7 | **Lost Attribution** | Multiple strategies per symbol | Wrong strategy zeroed | Strategy-level repair |
| 8 | **Cascading Repair** | Repair triggers opposite divergence | Worse than original | Policy with threshold |
| 9 | **Cancel Race** | Repair severs entry-exit link | TP cancelled when not needed | Atomic bracket handling |
| 10 | **Stale Data** | API returns old snapshot | Repair based on outdated data | Cooldown + timestamp check |

---

## Specific Scenarios (What Actually Happens)

### Scenario A: SL Fills While Syncing (Most Common)

```
T0:00    SL fills at 4990
T0:10ms  Exchange records: +2 (S1 flat, S2/S3 still long)
T0:15ms  Sync reads exchange: +2
T0:15ms  Sync reads virtual: +3 (S1 not yet aware of SL fill)
T0:20ms  REPAIR: Zero S1 (assumes phantom)
T0:25ms  SL fill processed: S1.qty = +1 - 1 = -1 (SHORT!)
```

**Result:** Exit becomes invisible, position inverts

**Fix:** Wait 2s after orders before syncing

---

### Scenario B: In-Flight Order Repaired

```
T0:00    Buy 1 ES sent to exchange
T0:02ms  Virtual tracker assumes filled: qty = +1
T0:10ms  SYNC FIRES (too early!)
T0:15ms  Exchange has 0 (fill not propagated yet)
T0:15ms  REPAIR: Zero virtual position
T0:20ms  Real fill arrives, but virtual is already zeroed
T0:25ms  Next signal: place another buy (thinks flat)
```

**Result:** Entry invisible, double-entry

**Fix:** Don't sync within 2s of placing orders

---

### Scenario C: Both SL and TP Fill

```
T0:00    Price jumps, both bracket legs triggered
T0:05ms  Exchange fills SL: sell 1
T0:10ms  Exchange fills TP: sell 1 (no true OCO!)
T0:15ms  Virtual processes: qty = +1 - 1 = 0, then 0 - 1 = -1
T0:20ms  Position is SHORT (should be flat)
```

**Result:** Position inverted, double-exit recorded

**Fix:** Validate that exits ≤ position size

---

### Scenario D: Concurrent Repair vs Fill

```
Thread A                          Thread B
─────────────────────────────────────────────
repair.force_flat():
  qty = 0
                                  fill.execute_order():
                                    old_qty = qty (reads 0)
qty = 0 ✓
                                    new_qty = 0 - 1 = -1
                                    qty = -1
Final: qty = -1 (SHORT!)
```

**Result:** Position math corrupted

**Fix:** Lock during repair + fill processing

---

## One-Liner Fixes

```python
# 1. Prevent in-flight order repairs
flight_window = OrderFlightWindow(2)  # 2-second window
flight_window.register_pending(order_id, symbol, qty, side)

# 2. Block early syncs
cooldown = SyncCooldown(2)  # 2-second cooldown
if not cooldown.can_sync(symbol): return True

# 3. Atomic repair
atomic_repair = AtomicPositionRepair(tracker)
with atomic_repair._repair_lock:
    tracker.force_flat(symbol)

# 4. Target single strategy
repaired_id, _ = repair.repair_most_suspicious(symbol, exchange_qty)

# 5. Conservative policy
if should_repair(symbol, delta) and abs(delta) >= 2.0 and elapsed >= 3:
    execute_repair()
```

---

## Diagnosis Checklist

Run this to identify if you have these issues:

```python
# Check 1: How often are repairs firing?
LOG_REPAIR_EVENTS = True
# If > 2 per hour → PROBLEM

# Check 2: Do repairs always reduce virtual (safe)?
if delta >= 0:
    logger.warning("Repair tried to ADD virtual qty")
# If > 0 → DANGEROUS

# Check 3: Do repairs happen immediately after orders?
time_since_last_order = now() - last_order_time
if time_since_last_order < 1.0:
    logger.warning("Repair within 1s of order")
# If happens → IN-FLIGHT RISK

# Check 4: Do multiple repairs happen consecutively?
if repair_count > 1 and repairs_within_10s > 1:
    logger.warning("Cascading repairs detected")
# If > 1 in 10s → CASCADING RISK

# Check 5: Do position divergences change direction?
deltas_last_hour = []
if len(set(sign(d) for d in deltas_last_hour)) > 1:
    logger.warning("Divergence direction changed")
# If both + and - → OSCILLATION RISK
```

---

## Red Flags (See These = Problem)

```
🚨 Repair fires > 1 time per hour
🚨 Position becomes negative when should be positive
🚨 Same symbol repaired twice within 10 seconds
🚨 P&L tracking loses trades
🚨 Exit orders invisible in order history
🚨 Strategy thinks position is flat but exchange shows open
🚨 Exchange position changes without corresponding order
```

---

## Next Steps (Priority Order)

1. **TODAY:** Read EDGE_CASES_POSITION_SYNC.md (30 min)
2. **TOMORROW:** Implement OrderFlightWindow + SyncCooldown (2-3 hours)
3. **THIS WEEK:** Add AtomicPositionRepair locking (2 hours)
4. **NEXT WEEK:** Add StrategyAwareRepair + policy (4 hours)
5. **VALIDATE:** Run edge case tests (6 hours)

**Total time investment: ~1 week for production safety**

---

## Key Insight

**The core issue:** Your system optimistically assumes orders fill instantly, but brokers lag 10-100ms in confirming positions. This 100ms window is where edge cases live.

**The solution:** Explicitly track "in-flight" orders separately, don't sync during flight windows, and lock critical sections.

**The payoff:** Repair becomes rare (< 1x/week), P&L tracking stays accurate, and you can trust virtual positions.

