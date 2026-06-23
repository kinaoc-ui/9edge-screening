First Screen — TradingView Watchlist Import
==========================================

格式：### Sector — Industry 標題 + 每行 EXCHANGE:SYMBOL
所有 *_comma.txt / *_lines.txt 都含 Sector / Industry 分組。

Files:
  FirstScreen_YYYY-MM-DD_comma.txt — 入選全部（建議 import 呢個）
  hits_comma.txt                   — 同上
  hits_by_sector_industry.txt      — 同上（備份檔名）
  hits_by_sector/*.txt             — 按 Sector 分檔
  hits_by_sector_industry/*.txt    — 按 Sector+Industry 分檔
  W1_only_comma.txt / D1_only_comma.txt — 子集
  classified_full.csv              — 完整表（含 Sector / Industry）

Import: TV Watchlist → ⋯ → Import list → 揀 FirstScreen_YYYY-MM-DD_comma.txt
