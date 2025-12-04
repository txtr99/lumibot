# Indicator Mappings: EasyLanguage → Python

Complete reference for converting EasyLanguage technical indicators to Python implementations using pandas-ta, pandas operations, or custom functions.

## Pandas-TA Available Indicators

Install: pandas-ta is already available in the lumibot venv at `venv/`

Import in generated file: `import pandas_ta as ta`

### Moving Averages

| EasyLanguage | Python (pandas-ta) | Notes |
|--------------|-------------------|-------|
| `average(close, N)` | `closes.rolling(N).mean()` | Simple Moving Average (SMA) |
| `xaverage(close, N)` | `closes.ewm(span=N).mean()` | Exponential Moving Average (EMA) |
| `WAverage(close, N)` | `ta.wma(closes, length=N)` | Weighted Moving Average |

### Oscillators

| EasyLanguage | Python (pandas-ta) | Notes |
|--------------|-------------------|-------|
| `RSI(close, N)` | `ta.rsi(closes, length=N)` | Relative Strength Index |
| `Stochastic(...)` | `ta.stoch(df['high'], df['low'], df['close'], k=14, d=3)` | Returns DataFrame with STOCHk and STOCHd |
| `Stochastics(N)` | `ta.stoch(df['high'], df['low'], df['close'], k=N, d=3)['STOCHk']` | Simpler stochastic with just %K |
| `CCI(N)` | `ta.cci(df['high'], df['low'], df['close'], length=N)` | Commodity Channel Index |
| `Williams%R(N)` | `ta.willr(df['high'], df['low'], df['close'], length=N)` | Williams %R |

### Trend Indicators

| EasyLanguage | Python (pandas-ta) | Notes |
|--------------|-------------------|-------|
| `ADX(N)` | `ta.adx(df['high'], df['low'], df['close'], length=N)['ADX']` | Average Directional Index |
| `DMI(N)` | `ta.adx(df['high'], df['low'], df['close'], length=N)` | Returns DataFrame with ADX, DMP, DMN |
| `ParabolicSAR(...)` | `ta.psar(df['high'], df['low'], df['close'])` | Parabolic SAR |

### MACD Indicators

| EasyLanguage | Python (pandas-ta) | Notes |
|--------------|-------------------|-------|
| `MACD(close, 12, 26, 9)` | `ta.macd(closes, fast=12, slow=26, signal=9)['MACD']` | MACD line |
| `MACDSignal(close, 12, 26, 9)` | `ta.macd(closes, fast=12, slow=26, signal=9)['MACDs']` | Signal line |
| `MACDHist(close, 12, 26, 9)` | `ta.macd(closes, fast=12, slow=26, signal=9)['MACDh']` | Histogram |

**Implementation pattern**:
```python
def populate_indicators(df, params):
    closes = df["close"]

    # Calculate MACD once, extract components
    macd_data = ta.macd(closes, fast=12, slow=26, signal=9)

    return {
        "macd": macd_data['MACD'].iloc[-1],
        "macd_signal": macd_data['MACDs'].iloc[-1],
        "macd_hist": macd_data['MACDh'].iloc[-1],
    }
```

### Volatility Indicators

| EasyLanguage | Python | Notes |
|--------------|--------|-------|
| `AvgTrueRange(N)` | `ta.atr(df['high'], df['low'], df['close'], length=N)` | Average True Range |
| `TrueRange` | `ta.true_range(df['high'], df['low'], df['close'])` | True Range |
| `BollingerBand(close, N, K)` | `ta.bbands(closes, length=N, std=K)` | Returns DataFrame with BBL, BBM, BBU |
| `BollingerBand(close, N, K)[0]` | `ta.bbands(closes, length=N, std=K)['BBL']` | Lower Bollinger Band |
| `BollingerBand(close, N, K)[1]` | `ta.bbands(closes, length=N, std=K)['BBM']` | Middle Bollinger Band |
| `BollingerBand(close, N, K)[2]` | `ta.bbands(closes, length=N, std=K)['BBU']` | Upper Bollinger Band |

### Volume Indicators

| EasyLanguage | Python (pandas-ta) | Notes |
|--------------|-------------------|-------|
| `OBV` | `ta.obv(df['close'], df['volume'])` | On Balance Volume |
| `VolumeMA(N)` | `df['volume'].rolling(N).mean()` | Volume Moving Average |

### Price Patterns

| EasyLanguage | Python | Notes |
|--------------|--------|-------|
| `Highest(High, N)` | `df['high'].rolling(N).max()` | Highest high in N bars |
| `Lowest(Low, N)` | `df['low'].rolling(N).min()` | Lowest low in N bars |
| `Range` | `df['high'] - df['low']` | Bar range |

## Custom Indicator Implementations

These indicators require custom implementation as they're not in pandas-ta or need special handling.

### KaufmanEfficiencyRatio

```python
def kaufman_efficiency_ratio(closes, length=10):
    """
    Calculate Kaufman Efficiency Ratio
    """
    change = abs(closes.iloc[-1] - closes.iloc[-1 - length])
    volatility = closes.diff().abs().rolling(length).sum().iloc[-1]

    if volatility == 0:
        return 0

    return change / volatility
```

Usage in populate_indicators:
```python
def populate_indicators(df, params):
    closes = df["close"]
    ker_length = params.get("ker_length", 10)

    return {
        "ker": kaufman_efficiency_ratio(closes, length=ker_length),
    }
```

### Rate of Change (ROC)

```python
def rate_of_change(closes, length=1):
    """
    Calculate Rate of Change
    """
    return ((closes.iloc[-1] - closes.iloc[-1 - length]) / closes.iloc[-1 - length]) * 100
```

### Momentum

```python
def momentum(closes, length=10):
    """
    Calculate Momentum (difference from N bars ago)
    """
    return closes.iloc[-1] - closes.iloc[-1 - length]
```

### Consecutive Bars

```python
def consecutive(series, length, direction):
    """
    Count consecutive up/down bars

    Args:
        series: Price series (close, high, low, etc.)
        length: Number of bars to check
        direction: 0 for down, 1 for up

    Returns:
        1 if condition met, 0 otherwise
    """
    if direction == 1:
        # Check if all bars in length are increasing
        for i in range(1, length):
            if series.iloc[-i] <= series.iloc[-i - 1]:
                return 0
        return 1
    else:  # direction == 0
        # Check if all bars in length are decreasing
        for i in range(1, length):
            if series.iloc[-i] >= series.iloc[-i - 1]:
                return 0
        return 1
```

Usage example:
```python
def populate_indicators(df, params):
    closes = df["close"]
    highs = df["high"]

    return {
        "consecutive_up": consecutive(closes, 5, 1),  # 5 consecutive up closes
        "consecutive_down_high": consecutive(highs, 5, 0),  # 5 consecutive down highs
    }
```

### Custom Moving Average Differences

```python
def ma_difference(closes, fast=10, slow=20):
    """
    Difference between fast and slow moving averages
    """
    fast_ma = closes.rolling(fast).mean().iloc[-1]
    slow_ma = closes.rolling(slow).mean().iloc[-1]
    return fast_ma - slow_ma
```

## Array Indexing Conversions

EasyLanguage uses `[N]` for N bars ago. Python pandas uses `.iloc[-1 - N]` for current bar minus N.

| EasyLanguage | Python | Meaning |
|--------------|--------|---------|
| `close[0]` | `closes.iloc[-1]` | Current (most recent) close |
| `close[1]` | `closes.iloc[-2]` | Previous close (1 bar ago) |
| `close[2]` | `closes.iloc[-3]` | 2 bars ago |
| `close[N]` | `closes.iloc[-1 - N]` | N bars ago |

**For indicators**:
```python
# EasyLanguage: rsi(c, 14)[0] > rsi(c, 14)[2]
# Python:
rsi_series = ta.rsi(closes, length=14)
condition = rsi_series.iloc[-1] > rsi_series.iloc[-3]
```

## Crosses Conversion

EasyLanguage cross operators → Python comparison logic.

### Crosses Above

```python
# EasyLanguage: indicator1 crosses above indicator2
# Python:
def crosses_above(series1, series2):
    """Check if series1 crosses above series2"""
    was_below = series1.iloc[-2] <= series2.iloc[-2]
    now_above = series1.iloc[-1] > series2.iloc[-1]
    return was_below and now_above
```

Usage:
```python
def go_long(state, df):
    closes = df["close"]
    fast_ma = closes.rolling(10).mean()
    slow_ma = closes.rolling(20).mean()

    return crosses_above(fast_ma, slow_ma)
```

### Crosses Below

```python
def crosses_below(series1, series2):
    """Check if series1 crosses below series2"""
    was_above = series1.iloc[-2] >= series2.iloc[-2]
    now_below = series1.iloc[-1] < series2.iloc[-1]
    return was_above and now_below
```

## Comparison Operators

| EasyLanguage | Python | Notes |
|--------------|--------|-------|
| `>` | `>` | Greater than |
| `<` | `<` | Less than |
| `>=` | `>=` | Greater than or equal |
| `<=` | `<=` | Less than or equal |
| `=` | `==` | Equal (note double equals in Python) |
| `<>` | `!=` | Not equal |
| `and` | `and` | Logical AND |
| `or` | `or` | Logical OR |

## Complete Example: Complex Condition Conversion

**EasyLanguage**:
```
condition1 = (Date[0]>1250202 or (Date[0]=1250202 and Time[0]>=1800)) and
    KaufmanEfficiencyRatio(10)[0] crosses below .10 and
    average(close,8)[0] < average(close,8)[2] and
    xaverage(close,20)[0] > xaverage(close,20)[5] and
    ADX(20) > 30;
```

**Python** (omit date/time filter):
```python
def populate_indicators(df, params):
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]

    # Calculate all indicators
    ker = ta.ker(closes, length=10)  # or custom implementation
    sma_8 = closes.rolling(8).mean()
    ema_20 = closes.ewm(span=20).mean()
    adx = ta.adx(highs, lows, closes, length=20)['ADX']

    return {
        "ker_current": ker.iloc[-1],
        "ker_prev": ker.iloc[-2],
        "sma_8_current": sma_8.iloc[-1],
        "sma_8_2ago": sma_8.iloc[-3],
        "ema_20_current": ema_20.iloc[-1],
        "ema_20_5ago": ema_20.iloc[-6],
        "adx": adx.iloc[-1],
    }

def go_long(state, df):
    indicators = populate_indicators(df, state.params)

    # KER crosses below 0.10
    ker_cross = (indicators["ker_prev"] >= 0.10 and
                 indicators["ker_current"] < 0.10)

    # SMA(8) current < SMA(8) 2 bars ago
    sma_declining = indicators["sma_8_current"] < indicators["sma_8_2ago"]

    # EMA(20) current > EMA(20) 5 bars ago
    ema_rising = indicators["ema_20_current"] > indicators["ema_20_5ago"]

    # ADX > 30
    adx_strong = indicators["adx"] > 30

    return ker_cross and sma_declining and ema_rising and adx_strong
```

## Notes on Implementation Strategy

1. **Calculate indicators once**: In `populate_indicators()`, calculate each indicator series once and extract needed values
2. **Store historical values**: If comparing to previous bars, store both current and historical values in returned dict
3. **Use pandas-ta when available**: Prefer pandas-ta implementations for reliability and performance
4. **Custom only when necessary**: Implement custom functions only for indicators not in pandas-ta
5. **Test with synthetic data**: Validation script will test all indicators calculate without errors
