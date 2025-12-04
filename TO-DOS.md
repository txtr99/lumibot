# Outstanding Todos

## Fix GC Strategy Param Names for min_bars Detection - 2025-12-03 17:18

- **Rename EMA params in GC strategies** - Update param names to follow naming convention for auto min_bars detection. **Problem:** `ema_fast: 20` and `ema_slow: 50` in GC_1M_01.py (and likely all 10 GC strategies) won't be detected by executor's min_bars calculation because they lack `_length`, `_period`, or `_lookback` suffix. Results in min_bars ~64 instead of ~100. **Files:** `custom_portfolio/strategies/active_strategies/GC_1M_01.py:23-27`, `custom_portfolio/strategies/active_strategies/GC_1M_02.py` through `GC_1M_10.py` (same pattern). **Solution:** Rename `ema_fast` → `ema_fast_length` and `ema_slow` → `ema_slow_length` in STRATEGY_CONFIG params and update populate_indicators() to match.

## Update EasyLanguage Conversion Skill - 2025-12-03 17:22

- **Fix param naming convention in skill examples** - Update example code to use `_length`/`_period` suffixes. **Problem:** Line 145 shows `"fast": 20, "slow": 50` which won't be detected for min_bars calculation - same anti-pattern the template warns against. **Files:** `~/.claude/skills/convert-easylanguage-strategies/SKILL.md:143-146`. **Solution:** Change example to `"fast_length": 20, "slow_length": 50`.

- **Fix session handling guidance** - Clarify 24/7 session syntax. **Problem:** Line 185 says "omit allowed_sessions" for 24/7 markets, but template expects field to always exist with `["24/7"]` value. **Files:** `~/.claude/skills/convert-easylanguage-strategies/SKILL.md:185-188, 227-231`. **Solution:** Change to "use `['24/7']`" instead of "omit".

- **Clarify micro vs standard contract symbol handling** - Add guidance on symbol extraction. **Problem:** Example shows `"symbol": "GC"` but actual strategies use `"MGC"` (micro). Skill should clarify whether to preserve source symbol or allow user choice. **Files:** `~/.claude/skills/convert-easylanguage-strategies/SKILL.md:62, 142`. **Solution:** Add step to ask user preference for micro vs standard contracts, or detect from EL file if specified.
