# EasyLanguage Syntax Patterns

Reference for parsing and understanding EasyLanguage trading strategy files.

## File Structure

Typical EasyLanguage strategy file structure:

```
{
    Strategy Details:
    Symbol: GC_1M
    Start Date: 20250202
    Stop Date: 20251108
    Profit Target On: Yes
    Profit Multiple: 8
    Stop Loss On: Yes
    Stop Loss Multiple: 8
    Max Time: 180
}

inputs:
    parameter1(value1),
    parameter2(value2),
    ...
    ;

vars:
    variable1(init_value),
    variable2(init_value),
    ...
    ;

// Calculation code
if condition then begin
    // code
end;

// Exit logic
if mp = +1 then begin
    // long exit code
end else if mp = -1 then begin
    // short exit code
end;

// Entry logic
if isClose then begin
    condition1 = (...);
end;

if isClose and condition1[delay] then begin
    Buy ("Entry") PS contract this bar close;
end;
```

## Header Section

Enclosed in `{ }`, contains strategy metadata:

| Field | Meaning | Usage |
|-------|---------|-------|
| `Symbol` | Trading symbol/market | Extract for STRATEGY_CONFIG.symbol |
| `Profit Multiple` | PT multiplier | Map to bracket_orders.pt_mult |
| `Stop Loss Multiple` | SL multiplier | Map to bracket_orders.sl_mult |
| `Max Time` | Max bars in trade | Map to time_exit.max_bars |
| `Start Date`, `Stop Date` | Backtest date range | Ignore (handled by framework) |

## Inputs Section

Parameters that can be optimized:

```
inputs:
    atr_length(20),
    pt_mult(8.00),
    sl_mult(8.00),
    Max_Time(180),
    delay(0)
    ;
```

**Mapping to Python**:
- `atr_length` → `bracket_orders.atr_period`
- `pt_mult` → `bracket_orders.pt_mult`
- `sl_mult` → `bracket_orders.sl_mult`
- `Max_Time` → `time_exit.max_bars`
- Other parameters used in entry logic → `params` dict

**Parameters to ignore**:
- `CombineSignals`, `Required4Entry`, `Required4Exit` (multi-chart logic)
- `ReadFiles` (file I/O flag)
- `PT_ON`, `SL_ON`, `TL_ON`, `HH_ON`, `LL_ON` (control flags, always use PT/SL in template)
- `delay` (bar delay, not used in Python template)

## Variables Section

Internal variables for strategy state:

```
vars:
    mp(0),
    mpPrev(0),
    atr(0),
    hh(0),
    ll(0),
    ...
    ;
```

**Ignore in conversion**:
- `mp`, `mpPrev` (market position tracking, handled by framework)
- `isClose`, `isOpen` (bar status flags, not needed)
- `intrabarpersist` keyword (intrabar state, not applicable)
- Internal calculation variables (atr, hh, ll, PT, SL, TL)

## Entry Logic Patterns

### Long Entry (Buy)

```
if isClose then begin
    condition1 = (...technical indicators...);
end;

if isClose and condition1[delay] then begin
    Buy ("Entry") PS contract this bar close;
    // bracket order setup (ignore, handled by template)
end;
```

**Key patterns**:
- `Buy (...)` → Indicates long entry logic
- `condition1 = (...)` → The actual entry condition to convert
- `isClose` → Bar closed check (not needed in Python, framework handles this)
- `[delay]` → Bar delay, usually 0, ignore
- `PS` or `PS contract` → Position size variable (always 1 in Python template)

**Extract**: Everything in `condition1 = (...)` for go_long() logic

### Short Entry (SellShort)

```
if isClose then begin
    condition2 = (...technical indicators...);
end;

if isClose and condition2[delay] then begin
    SellShort ("Entry") PS contract this bar close;
    // bracket order setup
end;
```

**Key patterns**:
- `SellShort (...)` → Indicates short entry logic
- Similar structure to long entry
- Extract condition for go_short() logic

### Direction Detection

If strategy contains:
- `Buy` command only → `direction = "long"`
- `SellShort` command only → `direction = "short"`
- Both `Buy` and `SellShort` → `direction = "both"`

## Exit Logic Patterns

**Ignore exit logic section entirely**. Template handles exits via:
- `bracket_orders` (PT/SL based on ATR multiples)
- `time_exit` (max bars in trade)

Common exit patterns to ignore:
```
// Time-based exit
if BarsSinceEntry >= Max_Time then begin
    Sell ("TimeX") all contracts this bar close;
end;

// Profit target
if PT_ON = 1 then
    Sell ("PTx") all contracts next bar PT limit;

// Stop loss
if SL_ON = 1 then
    Sell ("SLx") all contracts next bar SL stop;
```

**Why ignore**: These are standardized in the Python template framework and controlled via STRATEGY_CONFIG.

## Condition Syntax Patterns

### Boolean Operators

| EasyLanguage | Python | Example |
|--------------|--------|---------|
| `and` | `and` | `A > 0 and B < 10` |
| `or` | `or` | `A > 0 or B < 10` |
| `not` | `not` | `not (A > 0)` |

### Comparison Operators

| EasyLanguage | Python | Notes |
|--------------|--------|-------|
| `=` | `==` | Equality (Python uses double equals) |
| `<>` | `!=` | Not equal |
| `>`, `<` | `>`, `<` | Greater/less than |
| `>=`, `<=` | `>=`, `<=` | Greater/less or equal |

### Cross Operators

| EasyLanguage | Python Equivalent |
|--------------|-------------------|
| `A crosses above B` | `(A.iloc[-2] <= B.iloc[-2]) and (A.iloc[-1] > B.iloc[-1])` |
| `A crosses below B` | `(A.iloc[-2] >= B.iloc[-2]) and (A.iloc[-1] < B.iloc[-1])` |

## Date/Time Filters

Common patterns:

### Date Filters
```
(Date[0]>1250202 or (Date[0]=1250202 and Time[0]>=1800))
```
**Handling**: **Ignore completely** - Framework handles backtest date ranges

### Intraday Time Filters (Session Filters)
```
Time[0] >= 1000 and Time[0] <= 1500
```
Common in ES/NQ strategies to limit trading to specific hours (10:00 AM to 3:00 PM).

**Handling**: **Ignore these as well**. The Python template uses `allowed_sessions` config to handle session filtering at the framework level. If time filters are present:
- For 24/7 markets (GC, CL, crypto): Omit `allowed_sessions`
- For session-based markets (ES, NQ): Set `allowed_sessions` to appropriate session (e.g., `["New_York"]`)

**What to extract**: Only the technical indicator logic that comes AFTER these time filters in the condition.

## OHLC References

EasyLanguage uses lowercase for OHLC data:

| EasyLanguage | Python Access | Notes |
|--------------|---------------|-------|
| `close`, `close[0]`, `c` | `df["close"]` series, `.iloc[-1]` for current | Most recent close price |
| `open`, `open[0]`, `o` | `df["open"]` series, `.iloc[-1]` for current | Most recent open price |
| `high`, `high[0]`, `h` | `df["high"]` series, `.iloc[-1]` for current | Most recent high price |
| `low`, `low[0]`, `l` | `df["low"]` series, `.iloc[-1]` for current | Most recent low price |
| `volume`, `volume[0]` | `df["volume"]` series, `.iloc[-1]` for current | Most recent volume |

**Examples**:
```python
# EasyLanguage: low[0] > xaverage(close, 100)
# Python:
lows = df["low"]
closes = df["close"]
ema_100 = closes.ewm(span=100).mean()
condition = lows.iloc[-1] > ema_100.iloc[-1]

# EasyLanguage: open[0] > close[0]
# Python:
opens = df["open"]
closes = df["close"]
condition = opens.iloc[-1] > closes.iloc[-1]
```

## Array Indexing

EasyLanguage uses `[N]` for N bars ago:

| Syntax | Meaning | Python Equivalent |
|--------|---------|-------------------|
| `close[0]` | Current close | `closes.iloc[-1]` |
| `close[1]` | Previous close | `closes.iloc[-2]` |
| `close[2]` | 2 bars ago | `closes.iloc[-3]` |
| `indicator[N]` | Indicator N bars ago | `indicator_series.iloc[-1 - N]` |

**Pattern**: `[N]` → `.iloc[-1 - N]` (except `[0]` → `.iloc[-1]`)

## Common Patterns and Their Meanings

### Position Sizing

```
PS = position_size("Other", "Default", 50000, 0, atr, SL_ON * 8.00);
```

**Ignore**: Python template always uses 1 contract, controlled by STRATEGY_CONFIG.contracts

### Bar Status Checks

```
isClose = (BarStatus(1) <> 1);
if isClose then begin
    // calculations
end;
```

**Ignore**: Python framework handles bar timing. Only extract logic inside the `if isClose` blocks.

### Market Position

```
mp = MarketPosition;
mpPrev = mp;
if mp = +1 then begin
    // in long position
end else if mp = -1 then begin
    // in short position
end;
```

**Ignore**: Position tracking handled by Python framework. Don't convert position management code.

### Bracket Order Setup

```
PT = Round2Fraction(Close[0] + atr[0] * pt_mult);
SL = Round2Fraction(Close[0] - atr[0] * sl_mult);
```

**Ignore**: Template handles bracket orders via bracket_orders config with ATR-based distances.

## Strategy Type Detection

Analyze entry condition patterns to classify strategy type:

### Trend Following Indicators
- `ADX > threshold` (directional trend)
- Moving average trends: `MA[0] > MA[N]` (rising MA)
- MACD histogram trends
- Directional indicators (DMI)

If primarily trend indicators → `strategy_type = "trend_following"`

### Mean Reversion Indicators
- `RSI < 30` or `RSI > 70` (oversold/overbought)
- `RSI crosses below 90` (extreme levels)
- Bollinger Band touches
- Price vs MA comparisons: `close < MA * 0.98`

If primarily mean reversion patterns → `strategy_type = "mean_reversion"`

### Breakout Indicators
- `close > Highest(high, N)` (breakout above range)
- `close < Lowest(low, N)` (breakdown below range)
- Volatility expansion patterns

If primarily breakout patterns → `strategy_type = "breakout"`

## Parsing Strategy

1. **Read header** (`{ }` section) for metadata
2. **Parse inputs** section for parameters
3. **Skip vars** section (internal state)
4. **Skip exit logic** (if mp = +1 / mp = -1 blocks)
5. **Find entry conditions**:
   - Look for `condition1 = (...)`
   - Check for `Buy` commands → long entry
   - Check for `SellShort` commands → short entry
6. **Extract indicator logic** from conditions
7. **Classify strategy type** based on indicators used
8. **Ignore**: date/time filters, position management, bracket order setup, bar status checks
