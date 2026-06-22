# 9-Edge 主流程（CSV only）

唔使截圖、唔使 LLM、唔使 Cursor token、唔使理 chart 畫線。

## 路徑 A — Screener 批量選股（推薦）

TradingView Screener 匯出 CSV → 本機一次過跑晒全部代號。

1. TradingView Screener 設好 filter → **Export** → 存成 `new_日期_xxxx.csv`（建議放 `stock\` 資料夾）
2. 雙擊 **`跑選股.bat`**
   - 可 **拖放 CSV** 到 bat 上
   - 或 Enter 用預設（`..\new_*.csv` 最新一份）
3. 等完成（約 5–15 分鐘 / 400 隻）

輸出：
- `reports/batch/SCREENER_檔名_日期_summary.md` — A/B 級、潛力榜、完整排名
- `reports/batch/tv_import/檔名_日期/` — TV Watchlist `.txt`、A 級按 sector、`classified_full.csv`

命令列（等同 bat）：
```bat
python screen_screener_csv.py "..\new_2026-06-18_68630.csv"
python screen_screener_csv.py "你的.csv" --limit 10   REM 試跑 10 隻
```

> **注意**：呢條路徑用 **yfinance** 拉 W1/D1/H1，唔使逐隻 export chart CSV。  
> 之前 Cursor 入面跑嘅 batch 都係同一個 script — 你部機自己 double-click 就得，**零 token**。

---

## 路徑 B — 每隻股 export chart CSV（更準）

1. TradingView 開 W1 -> Export chart data -> `charts/csv/ETN_W1.csv`
2. 開 D1 -> Export -> `charts/csv/ETN_D1.csv`
3. 開 H1 -> Export -> `charts/csv/ETN_H1.csv`
4. 重複 30 隻

## 一次分析全部

雙擊 **`batch分析CSV.bat`**

輸出：
- `reports/batch/BATCH_日期_summary.md`  一覽表
- `reports/batch/SYMBOL_日期_9edge_csv.md`  每隻詳細（W1/D1/H1 分開）

## 9 edge 全部由 CSV + yfinance 計算

| Edge | 數據來源 |
|------|----------|
| 1 趨勢 | 各 TF 獨立：5/10/20 MA |
| 2 S&R | 各 TF swing / 突破阻力 |
| 3 CSR | 各 TF 形態 |
| 4 MTF | W1→D1→H1 跨週期對齊 |
| 5 RS | vs SPY 3M（symbol 級） |
| 6 R&R&S | D1 swing stop, RR>=2 |
| 7 板塊 | sector ETF vs SPY（symbol 級） |
| 8 F.T. | 各 TF 近3K 跟進 |
| 9 M.I. | 各 TF 量 + OBV |

---

## 邊啲要 token / AI？

| 功能 | 腳本 / bat | 要 Cursor token？ |
|------|------------|-------------------|
| **Screener 批量選股** | `跑選股.bat` / `screen_screener_csv.py` | ❌ 唔使 |
| **Chart CSV 分析** | `batch分析CSV.bat` / `analyze_tv_csv.py` | ❌ 唔使 |
| **TV 拉數 + 分析** | `一鍵分析TV.bat` / `開9edge_UI.bat` | ❌ 唔使（要 TradingView CDP） |
| **睇報告 UI** | `開9edge_UI.bat` | ❌ 唔使 |
| **截圖 + LLM 睇圖** | `貼圖分析.bat` / `check_chart.py` | ⚠️ 本機 Ollama 或 OpenAI（舊流程） |
| **Chat 叫 agent 幫手跑** | Cursor 對話 | ✅ 用 token（可選，唔必要） |
