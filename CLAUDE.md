# Lumibot Fork - Development Guide

## Virtual Environment

This project uses a standard Python virtual environment (not Poetry).

**Activate the virtual environment:**
```bash
source venv/bin/activate
```

**If packages are missing, install with pip after activating:**
```bash
source venv/bin/activate
pip install <package-name>
```

## Running Tests

**Always activate the venv first, then run pytest:**
```bash
source venv/bin/activate && pytest tests/ -v
```

**Run a specific test file:**
```bash
source venv/bin/activate && pytest tests/test_trading_calendar_sessions.py -v
```

**Run a specific test class:**
```bash
source venv/bin/activate && pytest tests/test_trading_calendar_sessions.py::TestTradingCalendarSessions -v
```

**Run a specific test method:**
```bash
source venv/bin/activate && pytest tests/test_trading_calendar_sessions.py::TestTradingCalendarSessions::test_weekend_blackout_enforcement -v
```

## Environment Variables

### Trading Calendar Overrides

- **`ALLOW_TRADES_UNTIL_FORCE_FLAT`**: Bypass the 30-minute `stop_new_orders` buffer before session end
  - `true`: Allow trades up until `force_flat` time (bypasses `stop_new_orders`)
  - Unset or any other value: Normal behavior (blocks at `stop_new_orders`)
  - **Still enforces**: `force_flat`, platform maintenance (15:10-17:00 CT), weekend blackout
  - **Usage**: `export ALLOW_TRADES_UNTIL_FORCE_FLAT=true`

- **`CALENDAR_EMERGENCY_DISABLE`**: Completely disable the calendar system (NOT RECOMMENDED)

- **`ENFORCE_SESSIONS_IN_BACKTEST`**: Control session enforcement in backtest mode
  - `true` (default): Enforce sessions realistically
  - `false`: Skip session enforcement for signal exploration

## Key Files

- `lumibot/tools/trading_calendar.py` - Two-layer trading window manager
- `custom_portfolio/strategies/portfolio_manager.py` - TopStepX platform config
- `tests/test_trading_calendar_sessions.py` - Calendar session tests

## ProjectX API Reference

### Client Initialization

```python
from dotenv import load_dotenv
load_dotenv()

from lumibot.credentials import PROJECTX_CONFIG  # noqa: E402
from lumibot.tools.projectx_helpers import ProjectXClient  # noqa: E402

client = ProjectXClient(PROJECTX_CONFIG)
account_id = client.get_preferred_account_id()
```

### API Field Names (IMPORTANT)

The ProjectX API uses different field names than you might expect:

**Positions (`position_search_open`):**
- `size` - Always positive! Use `type` for direction
- `type` - **CRITICAL**: `1` = LONG, `2` = SHORT
- `averagePrice` (NOT `avgPrice`) - average entry price
- `contractId` - contract identifier
- No unrealized P&L field - must be calculated

```python
# Correct way to get signed quantity:
size = pos.get("size", 0)
pos_type = pos.get("type", 1)  # 1=LONG, 2=SHORT
quantity = size if pos_type == 1 else -size
```

**Orders (`order_search`):**
- `customTag` - may be `None`, use `order.get("customTag") or ""`

**Trades (`trade_search`):**
- `profitAndLoss` - realized P&L when trade closes a position
- `fees` - trading fees
- `price` - fill price

### API Error Codes

**Order Cancel (`order_cancel`):**
- `errorCode: 5` - Order doesn't exist (already filled or cancelled)

**Order Place (`order_place`):**
- `errorCode: 2` - "Invalid price. Price is outside allowed range." - Stop/limit price too close or too far from current market (exchange/broker rule)

### Order Status Codes

**Order status field values:**
- `status: 1` - Open (working order)
- `status: 2` - Filled
- `status: 3` - Cancelled
- `status: 5` - Rejected/Expired (needs confirmation)

### Contract ID Format

Contract IDs use different symbols than trading symbols:
- ES: `CON.F.US.EP.Z25` (EP → ES)
- MES: `CON.F.US.MES.Z25` (matches)
- NQ: `CON.F.US.ENQ.Z25` (ENQ → NQ)
- MNQ: `CON.F.US.MNQ.Z25` (matches)
- GC: `CON.F.US.GCE.Z25` (GCE → GC)
- MGC: `CON.F.US.MGC.Z25` (matches)

**Contract to Symbol Mapping:**
- `EP` → `ES`
- `ENQ` → `NQ`
- `GCE` → `GC`
- Others (MES, MNQ, MGC, etc.) match directly

### OCO / Bracket Orders (CRITICAL)

**`linkedOrderId` does NOT work!** The API accepts the parameter but does NOT create true OCO behavior. When one order fills, the linked order remains orphaned.

**Native brackets** (`stopLossBracket`/`takeProfitBracket` params) require account-level "Auto OCO Brackets" setting which conflicts with scaled virtual positions.

**Solution**: Use `/tools/bracket_order_manager.py` - polls order status and cancels orphans:
```python
from tools.bracket_order_manager import BracketOrderManager

manager = BracketOrderManager(client, account_id)
manager.register_bracket(base_tag, sl_order_id=111, tp_order_id=222, symbol="ES")
manager.poll_and_cleanup()  # Call every 2-5 seconds in live trading
```

See `/pnl_validation_workflow_notes.md` for full details.

## P&L Validation Workflow

Testing tool for bracket orders and P&L calculations:

```bash
# Run full 16-step validation:
python tools/pnl_validation_workflow.py run-to 16

# Check account status:
PYTHONPATH=/Users/marvin/repos/lumibot_fork python tools/test_oco_brackets.py status
```

See `/pnl_validation_workflow_notes.md` for detailed progress and findings.
