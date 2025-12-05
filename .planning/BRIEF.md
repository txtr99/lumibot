# Project Brief: Fix Backtest Data Lookahead Bugs

## Problem Statement
The multi-strategy backtesting system has multiple critical bugs causing **lookahead bias** - the backtest sees and uses future data when calculating current prices, P&L, and order fills.

## Evidence
- Unrealized P&L is CONSTANT at $3,353 from 09:30 to 10:19 (should vary with price)
- Same future price used repeatedly instead of current bar's price
- Equity curve shows phantom unrealized P&L before any trading occurs
- Backtest only iterates NYSE hours (09:30-16:00) but data shows trades at 04:26, 07:53, 08:08

## Root Cause Summary
1. `SharedDataManager.get_cached_data()` returns the ENTIRE prefetched dataset
2. Code uses `df["close"].iloc[-1]` on unfiltered data = future price
3. Market calendar defaults to NASDAQ (stocks) instead of us_futures (24h)
4. Fallback logic in order execution uses `df.index[-1]` = future timestamp

## Affected Files
- `custom_portfolio/tools/shared_data_manager.py` (lines 382-398, 434-461)
- `custom_portfolio/multi_strategy_executor_enhanced.py` (lines 1095-1133, 1544-1548, 1678)
- `custom_portfolio/strategies/run_portfolio.py` (PortfolioStrategy.initialize)
- `lumibot/brokers/broker.py` (line 119 - already has auto-detection)

## Success Criteria
- Unrealized P&L varies with price at each iteration
- No phantom P&L at first iteration (t=0 should have 0 unrealized P&L)
- Backtest iterates through futures market hours, not just NYSE hours
- Order fills use the bar closest to `current_time`, not last bar in dataset
