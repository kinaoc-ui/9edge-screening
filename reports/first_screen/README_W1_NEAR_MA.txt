W1_NEAR_MA — Luk near-MA pullback shortlist (not the old "W1 圖" checkbox)
==============================================================================

Every First Screen run (UI, CLI, or run_first_screen.bat / Task Scheduler)
writes these next to the normal PASS list:

  FirstScreen_YYYY-MM-DD_HHMMSS_comma.txt
      Full FS PASS — ### Sector — Industry + EXCHANGE:TICKER

  FirstScreen_YYYY-MM-DD_HHMMSS_W1_NEAR_MA_comma.txt
      Subset of PASS where (D1 OHLCV):
        vs_sma20   in [-1.5%, +3.5%]
        vs_5d_high <= 0.5%
      Same ### Sector — Industry TV Import format.

  FirstScreen_YYYY-MM-DD_HHMMSS_W1_NEAR_MA.csv
      symbol, sector, industry, vs_sma20, vs_5d_high, as_of

  FirstScreen_YYYY-MM-DD_HHMMSS_W1_NEAR_MA_symbols.txt
      Flat symbols (one per line)

Task Scheduler (same FS job — no second schedule):
  Program/script : <repo>\run_first_screen.bat
  Arguments      : "C:\path\to\screener.csv"
  Start in       : <repo>

  or: python first_screen.py --csv "C:\path\to\screener.csv"

UI: First Screen page → after run → download W1_NEAR_MA TV list.
