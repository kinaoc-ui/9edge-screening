# 30 隻股 Batch 9-Edge 分析

## Step 1 — 放圖（最方便）

**方法 A — 連續貼 30 隻（推薦）**

1. 雙擊 **`連續貼圖30隻.bat`**
2. 每隻股 **只輸入一次代號**，例如 `ETN`：
   - **第 1 次**：Win+Shift+S 截 **D1** → Enter → 存 `ETN_D1.png`
   - **第 2 次**：Win+Shift+S 截 **H4** → Enter → 存 `ETN_H4.png`
3. 下一隻 repeat；搞掂按 **Enter 空行** 或輸入 `q` 退出

**方法 B — 貼一張**

1. Win+Shift+S 截圖
2. 雙擊 **`貼圖到charts.bat`** → 選 1 → 輸入代號

圖會自動存入 `charts\`（例如 `ETN_D1.png`）。  
Windows folder **唔支援 Ctrl+V 貼圖**，要用上面 bat。

雙擊 **`列出已放圖表.bat`** 檢查進度。

## Step 2 — 話我開始

Chat 寫例如：

> 圖放好了，幫我 batch 分析 charts 入面全部（或：分析 ETN WOLF NVDA ...）

## Step 3 — 我俾你 report

輸出喺 `reports/batch/`：

| 檔案 | 內容 |
|------|------|
| `BATCH_日期_summary.md` | 30 隻一覽：代號、分數、Grade、Decision |
| `SYMBOL_日期_9edge.md` | 每隻詳細 9-edge + Entry Plan |

## 評分標準（9-edge skill）

- **A**：≥7 分且 #1–#5 全過 → 可交易
- **B**：6 分 → watch
- **C**：≤5 分 → skip

## 提示

- 每張圖左上角要見到 **代號 + 價格 + 量**
- D1 必須；H4 有就 MTF 準啲
- 唔使再跑 Ollama，我直接睇圖分析
