# Indicators Found in Strategy Files

Summary of all technical indicators discovered across 30 EasyLanguage strategy files (10 ES_1M, 10 NQ_1M, 10 GC_1M).

## Complete Indicator List

### Moving Averages
- `average(close, N)` - Simple Moving Average
- `xaverage(close, N)` - Exponential Moving Average

### Oscillators & Momentum
- `RSI(close, N)` - Relative Strength Index (14 most common)
- `rsi(c, 2)` - Short-term RSI (common: 2, 14)
- `Stochastics(N)` - Stochastic oscillator (simplified version)
- `KaufmanEfficiencyRatio(N)` - Efficiency ratio
- `momentum(close, N)` - Momentum indicator
- `rateOfChange(close, N)` - Rate of change

### Trend Indicators
- `ADX(N)` - Average Directional Index

### MACD Family
- `macdhist(c, fast, slow, signal)` - MACD histogram

### Volatility & Bands
- `BollingerBand(close, period, stddev)` - Bollinger Bands (returns array)

### Price Patterns
- `consecutive(series, length, direction)` - Consecutive up/down bars

### Common Parameters by Symbol

#### ES (S&P 500 E-mini)
- Max Time: 120-180 bars
- PT Multiple: 2-8x ATR
- SL Multiple: 5-8x ATR
- ATR Length: 20
- **Session Filter**: Time[0] >= 1000 and Time[0] <= 1500 (10 AM - 3 PM)
- Common strategy types: Mean reversion with RSI

#### NQ (Nasdaq E-mini)
- Max Time: 120-180 bars
- PT Multiple: 2-8x ATR
- SL Multiple: 5-8x ATR
- ATR Length: 20
- **Session Filter**: Time[0] >= 1000 and Time[0] <= 1500
- Common strategy types: Mean reversion, momentum

#### GC (Gold Futures)
- Max Time: 180 bars
- PT Multiple: 8x ATR
- SL Multiple: 8x ATR
- ATR Length: 20
- **No session filters** (24/7 market)
- Common strategy types: Trend following, mean reversion

## Common Condition Patterns

### Mean Reversion Patterns
```
rsi(c, 2)[0] >= 80 and rsi(c, 2)[0] <= 90
rsi(c, 14)[0] crosses below 70
rsi(c, 14)[0] <= rsi(c, 14)[4] and rsi(c, 14)[0] > 70
```

### Trend Following Patterns
```
xaverage(close, 20)[0] > xaverage(close, 20)[5]
average(close, 8)[0] > average(close, 8)[1]
ADX(20) > 30
```

### Efficiency/Momentum Patterns
```
KaufmanEfficiencyRatio(10)[0] crosses below .10
rateOfChange(close, 5)[0] < rateOfChange(close, 5)[3]
macdhist(c, 12, 26, 9)[0] > macdhist(c, 12, 26, 9)[3]
```

### Consecutive Bar Patterns
```
consecutive(close, 5, 0) = 1  // 5 consecutive down closes
consecutive(high, 5, 0) = 1   // 5 consecutive down highs
```

### Price vs MA Patterns
```
low[0] > xaverage(close, 100)
low[0] > average(close, 200)
close[0] > xaverage(close, 10)
high[0] < average(close, 50)
```

### Bollinger Band Patterns
```
low[0] > BollingerBand(c, 20, 2)[0]  // Price above lower band
```

## Time Filter Patterns

### Date Filters (All Strategies)
```
(Date[0]>1250202 or (Date[0]=1250202 and Time[0]>=1800))
```
**Action**: Ignore - handled by backtest framework

### Intraday Session Filters (ES/NQ only)
```
Time[0] >= 1000 and Time[0] <= 1500
```
**Action**: Ignore - use `allowed_sessions: ["New_York"]` in config

### No Time Filters (GC/24-7 markets)
**Action**: Omit `allowed_sessions` from config

## OHLC References Found

Strategies use direct OHLC comparisons:
- `low[0] > xaverage(close, 100)`
- `high[0] < average(close, 50)`
- `open[0] > xaverage(close, 100)`
- `close[0] > xaverage(close, 10)`

## Cross Detection Patterns

```
rsi(c, 2)[0] crosses below 95
rsi(c, 14)[0] crosses below 70
KaufmanEfficiencyRatio(10)[0] crosses below .50
```

## Strategy Type Classification

Based on indicators present:

**Mean Reversion**:
- RSI extreme levels (> 70, < 30)
- RSI crosses below/above thresholds
- Short-term RSI (2-period)

**Trend Following**:
- ADX > 30
- Moving average slopes (MA[0] > MA[N])
- Multiple timeframe MA alignments

**Momentum**:
- Rate of change comparisons
- MACD histogram trends
- Momentum indicator comparisons
- Consecutive bar patterns

## Implementation Notes

1. **All strategies are long-only** - No short entry logic found in any of the 30 files
2. **Bracket orders standardized** - All use ATR-based PT/SL
3. **Time-based exits** - Max bars in trade is consistent (120-180)
4. **Position sizing ignored** - All use 1 contract in Python template
5. **Exit logic standardized** - Only PT/SL/time exits, no signal-based exits
