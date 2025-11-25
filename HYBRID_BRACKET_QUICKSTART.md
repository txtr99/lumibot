# Hybrid Bracket System - Quick Start Guide

## 5-Minute Overview

**Goal**: Broker handles TP/SL (fast), client monitors as backup (safe)

**Key Principle**: Client only acts if broker **provably failed**

**Result**: Zero duplicate closures + resilience to broker failures

---

## Quick Implementation Checklist

### Step 1: Copy Implementation Code
```bash
# Copy the implementation module
cp HYBRID_BRACKET_IMPLEMENTATION.py lumibot/brokers/hybrid_bracket.py
```

### Step 2: Modify ProjectX Broker

Add to `lumibot/brokers/projectx.py`:

```python
from lumibot.brokers.hybrid_bracket import HybridBracketManager, HybridBracketState

class ProjectX(Broker):
    def __init__(self, config, data_source, **kwargs):
        super().__init__(...)

        # Add hybrid bracket manager
        self.hybrid_bracket_manager = HybridBracketManager(
            broker=self,
            data_source=data_source,
            logger=self.logger
        )
```

### Step 3: Hook into Order Submission

Still in `projectx.py`, modify `_submit_order`:

```python
def _submit_order(self, order: Order) -> Order:
    # Existing bracket logic...
    if order.order_class == Order.OrderClass.BRACKET:
        submitted = self._submit_bracket_to_broker(order)  # Existing method

        # NEW: Register for hybrid monitoring
        if submitted and submitted.id and hasattr(self, 'hybrid_bracket_manager'):
            bracket_state = HybridBracketState(
                parent_order_id=submitted.id,
                position_asset=order.asset,
                position_side="long" if order.side == "buy" else "short",
                position_qty=order.quantity,
                entry_price=order.limit_price or 0,
                tp_price=getattr(order, 'secondary_limit_price', None),
                sl_price=getattr(order, 'secondary_stop_price', None),
                broker_tp_order_id=submitted._synthetic_bracket.get('children', {}).get('tp'),
                broker_sl_order_id=submitted._synthetic_bracket.get('children', {}).get('sl'),
                broker_last_seen=datetime.now()
            )
            self.hybrid_bracket_manager.register_bracket(bracket_state)

        return submitted
```

### Step 4: Hook into Stream Events

Still in `projectx.py`:

```python
def _handle_order_update(self, data):
    super()._handle_order_update(data)  # Existing logic

    # NEW: Update hybrid tracker
    if hasattr(self, 'hybrid_bracket_manager'):
        self.hybrid_bracket_manager.stream_handler.on_order_update(data)

def _handle_position_update(self, data):
    super()._handle_position_update(data)  # Existing logic

    # NEW: Update hybrid tracker
    if hasattr(self, 'hybrid_bracket_manager'):
        self.hybrid_bracket_manager.stream_handler.on_position_update(data)
```

### Step 5: Enable Monitoring in Strategy

Add to `custom_portfolio/strategies/portfolio_manager.py`:

```python
class PortfolioManager(Strategy):
    def on_trading_iteration(self):
        # ... your existing strategy logic ...

        # NEW: Monitor hybrid brackets
        if hasattr(self.broker, 'hybrid_bracket_manager'):
            self.broker.hybrid_bracket_manager.monitor_all_brackets()

        # NEW: Periodic reconciliation
        if self.iteration_count % 10 == 0:
            if hasattr(self.broker, 'hybrid_bracket_manager'):
                self.broker.hybrid_bracket_manager.reconcile_all_brackets()
```

---

## Verify It Works

### Test 1: Normal Broker Success

```python
# Submit bracket order
order = self.create_order(
    asset=self.mes,
    quantity=1,
    side="buy",
    limit_price=5000,
    order_class=Order.OrderClass.BRACKET,
    secondary_limit_price=5050,  # TP
    secondary_stop_price=4950     # SL
)
self.submit_order(order)

# Wait for TP to hit (price goes to 5050)
# Check logs: Should see "Bracket TP filled by broker"
# NO client backup execution should occur
```

### Test 2: Client Backup Takeover

```python
# Submit bracket order
order = self.create_order(...)
self.submit_order(order)

# Manually cancel broker orders (simulate failure)
self.broker.cancel_order(broker_tp_order)
self.broker.cancel_order(broker_sl_order)

# Wait 60+ seconds for failure detection
# Price hits SL level
# Check logs: Should see "🚨 CLIENT BACKUP EXECUTING"
```

### Test 3: Check Metrics

```python
# In strategy, log stats
stats = self.broker.hybrid_bracket_manager.get_bracket_stats()
self.log_message(f"Brackets: {stats}")
# Output: {'total_brackets': 5, 'broker_active': 4, 'broker_failed': 0, 'closed': 1}
```

---

## Configuration (Optional)

Create config file `config/hybrid_bracket_config.py`:

```python
HYBRID_BRACKET_CONFIG = {
    'enabled': True,

    # Timeouts (in seconds)
    'staleness_warning_seconds': 30,
    'broker_failure_timeout_seconds': 60,
    'grace_period_seconds': 15,
    'reconciliation_interval_seconds': 60,

    # Features
    'client_backup_enabled': True,
    'duplicate_prevention_enabled': True,
    'log_state_transitions': True,
}
```

Load in broker init:

```python
from config.hybrid_bracket_config import HYBRID_BRACKET_CONFIG

self.hybrid_bracket_manager = HybridBracketManager(
    broker=self,
    data_source=data_source,
    logger=self.logger,
    config=HYBRID_BRACKET_CONFIG  # Optional
)
```

---

## Understanding the Logs

### Normal Operation (Broker Success)

```
[INFO] Registered hybrid bracket: 12345
[DEBUG] Broker status for 12345: active
[DEBUG] Broker status for 12345: active
[INFO] ✅ Bracket TP filled by broker
```

### Client Takeover (Broker Failed)

```
[INFO] Registered hybrid bracket: 12345
[DEBUG] Broker status for 12345: active
[DEBUG] Broker status for 12345: active
[WARNING] ⚠️ Broker stale for 60.2s, verifying...
[ERROR] Broker orders confirmed dead after fresh check
[WARNING] ⚠️ Broker failed for 12345, client taking over
[INFO] ✅ All safety checks passed for 12345
[WARNING] 🚨 CLIENT BACKUP EXECUTING: client_sl at price=4950.00
[INFO] ✅ Client backup closed position successfully
```

### Duplicate Prevention

```
[INFO] Registered hybrid bracket: 12345
[WARNING] ⚠️ Broker failed for 12345, client taking over
[DEBUG] Client execution blocked: grace_period_active_12.5s
[DEBUG] Client execution blocked: grace_period_active_8.2s
[DEBUG] Client execution blocked: broker_orders_still_active_on_fresh_check
[INFO] ✅ Bracket TP filled by broker  # Broker recovered
```

---

## Common Issues & Solutions

### Issue: Client never taking over

**Symptom**: Broker orders fail, but client doesn't execute backup

**Check**:
1. Is monitoring enabled in strategy? (`monitor_all_brackets()` called?)
2. Has 60s passed? (Check `broker_failure_timeout_seconds`)
3. Are orders actually failed? (Check broker dashboard)
4. Is position still open? (Client won't act if closed)

**Solution**:
```python
# Add debug logging
bracket = self.broker.hybrid_bracket_manager.brackets[order_id]
print(f"State: {bracket.state}")
print(f"Last seen: {bracket.broker_last_seen}")
print(f"Client enabled: {bracket.client_enabled}")
```

### Issue: Duplicate closures

**Symptom**: Both broker and client close position

**Check**:
1. Are grace periods too short? (Increase `grace_period_seconds`)
2. Is fresh API check failing? (Network issues?)
3. Are stream events delayed? (Broker connectivity?)

**Solution**: This shouldn't happen! If it does, investigate logs:
```python
# Look for both closing events in logs
grep "Bracket.*filled by broker" logs.txt
grep "CLIENT BACKUP EXECUTING" logs.txt
```

### Issue: High latency in client backup

**Symptom**: Client takes 90+ seconds to act

**Expected**: Client should act within 75-80 seconds of broker failure

**Check**:
1. Is `broker_failure_timeout_seconds` set correctly? (Default: 60s)
2. Is `grace_period_seconds` too long? (Default: 15s)
3. Is fresh API check slow? (Network latency?)

**Solution**: Adjust timeouts (carefully):
```python
HYBRID_BRACKET_CONFIG = {
    'broker_failure_timeout_seconds': 45,  # Faster detection
    'grace_period_seconds': 10,            # Shorter grace period
}
```

---

## Monitoring Dashboard

Create simple monitoring view:

```python
def print_hybrid_dashboard(broker):
    """Print hybrid bracket status dashboard."""

    manager = broker.hybrid_bracket_manager
    stats = manager.get_bracket_stats()

    print("=" * 60)
    print("HYBRID BRACKET DASHBOARD")
    print("=" * 60)
    print(f"Total Brackets:   {stats['total_brackets']}")
    print(f"Broker Active:    {stats['broker_active']}")
    print(f"Broker Failed:    {stats['broker_failed']}")
    print(f"Closed:           {stats['closed']}")
    print()

    # Print active brackets
    print("Active Brackets:")
    for bracket_id, bracket in manager.brackets.items():
        if bracket.state != "CLOSED":
            age = (datetime.now() - bracket.broker_last_seen).total_seconds()
            print(f"  {bracket_id[:8]}: {bracket.state} (age: {age:.0f}s)")

    print("=" * 60)

# Call in strategy
if self.iteration_count % 100 == 0:
    print_hybrid_dashboard(self.broker)
```

Output:
```
============================================================
HYBRID BRACKET DASHBOARD
============================================================
Total Brackets:   3
Broker Active:    2
Broker Failed:    1
Closed:           0

Active Brackets:
  a1b2c3d4: BROKER_ACTIVE (age: 5s)
  e5f6g7h8: BROKER_ACTIVE (age: 12s)
  i9j0k1l2: BROKER_FAILED (age: 65s)
============================================================
```

---

## Performance Tuning

### For High-Frequency Trading

```python
HYBRID_BRACKET_CONFIG = {
    # Faster detection
    'staleness_warning_seconds': 15,
    'broker_failure_timeout_seconds': 30,
    'grace_period_seconds': 5,

    # Less frequent reconciliation
    'reconciliation_interval_seconds': 120,
}
```

### For Low-Frequency Trading

```python
HYBRID_BRACKET_CONFIG = {
    # More conservative
    'staleness_warning_seconds': 60,
    'broker_failure_timeout_seconds': 120,
    'grace_period_seconds': 30,

    # More frequent reconciliation
    'reconciliation_interval_seconds': 30,
}
```

---

## Rollout Plan

### Week 1: Paper Trading
- Deploy to paper/simulation environment
- Monitor logs for 1 week
- Verify no duplicates
- Tune timeouts if needed

### Week 2: Single Strategy
- Deploy to 1 live strategy (low stakes)
- Monitor closely
- Verify client backup works correctly
- Document any issues

### Week 3: Gradual Rollout
- Deploy to 10% of strategies
- Monitor metrics
- Increase to 50%
- Finally 100%

---

## Disabling Hybrid (Emergency)

If issues arise, quickly disable:

### Option 1: Feature Flag

```python
HYBRID_BRACKET_CONFIG = {
    'enabled': False,  # Disable entire system
}
```

### Option 2: Comment Out Monitoring

```python
def on_trading_iteration(self):
    # ... strategy logic ...

    # DISABLED: Monitor hybrid brackets
    # if hasattr(self.broker, 'hybrid_bracket_manager'):
    #     self.broker.hybrid_bracket_manager.monitor_all_brackets()
```

### Option 3: Remove from Broker Init

```python
def __init__(self, config, data_source, **kwargs):
    super().__init__(...)

    # DISABLED: Add hybrid bracket manager
    # self.hybrid_bracket_manager = HybridBracketManager(...)
```

System falls back to pure broker brackets (existing behavior).

---

## Support & Troubleshooting

**Full Documentation**:
- Design: `HYBRID_BRACKET_DESIGN.md`
- Diagrams: `HYBRID_BRACKET_FLOWCHART.md`
- Code: `HYBRID_BRACKET_IMPLEMENTATION.py`
- Summary: `HYBRID_BRACKET_SUMMARY.md`

**Debug Mode**:
```python
import logging
logging.getLogger('lumibot.brokers.hybrid_bracket').setLevel(logging.DEBUG)
```

**Health Check**:
```python
# Run after each trading session
def health_check(broker):
    manager = broker.hybrid_bracket_manager
    stats = manager.get_bracket_stats()

    # Alert if too many failures
    if stats['broker_failed'] > stats['total_brackets'] * 0.1:
        print("⚠️ WARNING: >10% broker failures detected")

    # Alert if client takeovers
    closed = [b for b in manager.brackets.values()
              if b.closed_by and b.closed_by.startswith('client_')]
    if closed:
        print(f"🚨 ALERT: {len(closed)} client backups executed")

    return stats
```

---

## Success Criteria

✅ **No duplicate closures** after 1 week of testing
✅ **Broker success rate** >95% (check metrics)
✅ **Client backup works** when broker fails (verify in logs)
✅ **No performance degradation** (check iteration timing)
✅ **Clean logs** (no errors, minimal warnings)

If all criteria met → proceed to gradual rollout
If any fail → investigate, fix, repeat testing

---

## That's It!

You now have:
- ✅ Broker-primary execution (fast)
- ✅ Client-backup monitoring (safe)
- ✅ Multi-layer duplicate prevention
- ✅ Position reconciliation
- ✅ Comprehensive logging
- ✅ Production-ready code

**Next**: Follow the 5-step implementation checklist above to get started!
