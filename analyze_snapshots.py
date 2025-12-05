import pandas as pd

# Load equity curve
eq = pd.read_csv("snapshots/backtest_20251204_095452/equity_curve.csv")
eq['unrealized_pnl'] = eq['portfolio_value'] - eq['account_balance']

# Load snapshots
snap = pd.read_csv("snapshots/backtest_20251204_095452/snapshots.csv")

# HYPOTHESIS: The unrealized P&L is being calculated using the LAST price in the dataset
# instead of the price at current_time.

# Let's check: if the unrealized P&L is $3,353 at 09:30 on Oct 1,
# and the multiplier is 10, then the price difference is 335.3 points.

# If entry_price is 0 (default), then current_price would be 335.3 (too low for MGC)
# If entry_price is set to the LAST price in the dataset, then...

# Let's check the LAST price in the dataset
print("Checking if unrealized P&L correlates with price differences:")
print()

# The first unrealized P&L is $3,353
# $3,353 / 10 (multiplier) = 335.3 points
# If there are 21 strategies, each with 1 contract:
# $3,353 / 21 = $159.67 per strategy
# $159.67 / 10 = 15.97 points per strategy

# Let's check if the MGC price at 09:30 is ~16 points different from some reference price
print("MGC prices in snapshots:")
print(snap[['timestamp', 'last_price', 'entry_price']].head(20).to_string())
print()

# Let's check the equity curve more carefully
# The unrealized P&L is $3,353 at 09:30, then stays at $3,353 until 10:20
# At 10:20, it drops to $0
# At 10:42, it jumps to $3,487 (when a new position is opened)

print("Equity curve around 10:20 on Oct 1:")
oct1 = eq[eq['timestamp'].str.contains('2025-10-01')]
print(oct1[oct1['timestamp'].str.contains('10:')][['timestamp', 'account_balance', 'portfolio_value', 'unrealized_pnl']].head(30).to_string())
print()

# CRITICAL INSIGHT: The unrealized P&L drops to $0 at 10:20, then jumps to $3,487 at 10:42
# This suggests that the unrealized P&L is being calculated correctly AFTER the first trade
# But the INITIAL unrealized P&L of $3,353 is wrong

# Let's check if the $3,353 is related to the number of strategies
# 21 strategies × ~$160 per strategy = $3,360 (close to $3,353!)
print("Number of unique strategies in snapshots:")
print(snap['strategy_id'].nunique())
print()

# Let's check if the unrealized P&L is exactly 21 × some value
print(f"$3,353 / 21 strategies = ${3353/21:.2f} per strategy")
print(f"$3,353 / 10 (multiplier) / 21 strategies = {3353/10/21:.2f} points per strategy")
print()

# HYPOTHESIS: At the start of the backtest, all 21 strategies have phantom positions
# with entry_price set to some value, causing the unrealized P&L to be non-zero

# Let's check if the unrealized P&L is related to the MGC price at 09:30
# If MGC price at 09:30 is ~3888 and entry_price is ~3872:
# (3888 - 3872) * 1 * 10 = 160 (matches!)

# But wait - the snapshots show that entry_price is NaN when position_qty is 0
# So the entry_price should be None, not some value

# Let me check if there's a bug in how entry_price is being initialized
print("Checking entry_price values in snapshots:")
print(snap[['strategy_id', 'timestamp', 'position_qty', 'entry_price']].head(20).to_string())

