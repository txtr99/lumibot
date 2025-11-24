Agent Guidelines
================

Pull requests
-------------
- Do not create a PR unless the user explicitly requests it **and** confirms the work is ready. Keep changes local/branch-only until then.

Backtest QA tolerance
---------------------
- Daily missing tolerance: allow up to 10 minutes of unexpected missing data per day (beyond scheduled maintenance/holidays/weekends) before flagging a day.

Backtest runtime defaults
-------------------------
- When running any backtest, set `SHOW_PLOT=false`, `SHOW_TEARSHEET=false`, `SHOW_INDICATORS=false` to keep runs fast and non-interactive.
