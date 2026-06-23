First Screen — TradingView Watchlist Import
==========================================

入選條件：W1 或 D1 任一中晒 3/3（MA轉上 + 跌K量低 + 升K量高）

Files:
  hits_comma.txt              — 全部入選（EXCHANGE:SYMBOL comma）
  hits_lines.txt              — 全部入選（每行一隻）
  hits_by_sector_industry.txt — 入選按 Sector + Industry 分組（參考格式）
  hits_by_sector/*.txt        — 入選按 Sector 分組（逐個 import）
  hits_by_sector_industry/*.txt — 入選按 Sector+Industry 分組
  W1_only_comma.txt / D1_only_comma.txt — 只 W 或只 D 入選
  classified_full.csv         — 完整表（含 Sector / Industry）

Note: TV 官方 comma import 唔支援 sector header。
      要分 sector → import hits_by_sector/ 或 hits_by_sector_industry/ 入面每個檔。
