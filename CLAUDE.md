# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Virtual Environment

This project uses a standard Python virtual environment (not Poetry).

```bash
source venv/bin/activate
pip install <package-name>  # If packages are missing
```

## Common Commands

```bash
# Run all tests
source venv/bin/activate && pytest tests/ -v

# Run specific test file/class/method
pytest tests/test_trading_calendar_sessions.py -v
pytest tests/test_trading_calendar_sessions.py::TestTradingCalendarSessions -v
pytest tests/test_trading_calendar_sessions.py::TestTradingCalendarSessions::test_weekend_blackout_enforcement -v

# Run backtest example
python -m lumibot.example_strategies.stock_buy_and_hold

# Code coverage
coverage run; coverage report; coverage html

# Run portfolio strategies (live/backtest)
python custom_portfolio/strategies/run_portfolio.py
```

## Architecture Overview

### Core Lumibot (upstream library)
- `lumibot/strategies/strategy.py` - Base Strategy class with lifecycle methods (`initialize`, `on_trading_iteration`, `before_market_opens`, etc.)
- `lumibot/brokers/` - Broker implementations (Alpaca, Interactive Brokers, ProjectX, Tradier, etc.)
- `lumibot/data_sources/` - Data source implementations (Polygon, Yahoo, Databento, etc.)
- `lumibot/entities/` - Core entities: Asset, Order, Position, Bars, Quote
- `lumibot/backtesting/` - Backtesting infrastructure for each data source

### Custom Portfolio System (multi-strategy trading)
This fork adds a complete multi-strategy trading system for futures:

```
custom_portfolio/
├── strategies/
│   ├── portfolio_manager.py      # Auto-discovers strategies from active_strategies/
│   ├── run_portfolio.py          # Entry point for running the portfolio
│   ├── active_strategies/        # Drop strategy files here (auto-loaded)
│   │   ├── ES_1M_01.py          # E-mini S&P strategies
│   │   ├── NQ_1M_01.py          # E-mini Nasdaq strategies
│   │   └── GC_1M_01.py          # Gold strategies
│   └── templates/
│       └── strategy_template.py  # Template for new strategies
├── multi_strategy_executor.py    # Runs 30+ strategies with shared resources
├── multi_strategy_executor_enhanced.py  # Enhanced version with brackets
└── tools/
    ├── shared_data_manager.py    # Caches market data (95%+ API reduction)
    ├── global_rate_limiter.py    # 2-second order delays
    ├── strategy_attribution.py   # Per-strategy P&L tracking
    └── strategy_state.py         # Per-strategy state management
```

### Virtual Position Tracking
`lumibot/tools/virtual_position_tracker.py` - Critical for brokers with unreliable position APIs (like TopStepX). Tracks positions locally independent of broker, assuming market orders fill immediately.

### Trading Calendar System
`lumibot/tools/trading_calendar.py` - Two-layer restriction system:
- Layer 1 (Platform): Broker-specific restrictions (TopStepX maintenance windows)
- Layer 2 (Session): Instrument-specific trading windows (NY session, London session, etc.)

### Bracket Order Management
`tools/bracket_order_manager.py` - Workaround for ProjectX API's broken OCO. Polls order status and cancels orphaned stop/limit orders when one side fills.

## Strategy Template Pattern

Each strategy in `active_strategies/` must define:
```python
STRATEGY_CONFIG = {
    "strategy_id": "",  # Empty = use filename
    "symbol": "ES",
    "contracts": 1,
    "params": {"sma_length": 200},  # Use _length/_period suffixes for auto min_bars
    "bracket_orders": {"atr_period": 20, "pt_mult": 2.0, "sl_mult": 1.0},
    "allowed_sessions": ["New_York"],
}

def populate_indicators(df, params) -> dict: ...
def go_long(state, df) -> bool: ...
def go_short(state, df) -> bool: ...
def generate_signal(state, df) -> str: ...  # "BUY", "SELL", "HOLD"
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
