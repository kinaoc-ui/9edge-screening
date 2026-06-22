# 9-Edge UI 部署指南

## 推薦：GitHub + Streamlit Cloud（俾朋友一條 link）

**朋友唔使連你部機** — 你 push code 上 GitHub，deploy Streamlit Cloud，俾 `https://xxx.streamlit.app` 就得。

👉 **逐步教學：`GITHUB部署.md`**

| 步驟 | 你做咩 |
|------|--------|
| 1 | `git init` → commit → push 上 GitHub |
| 2 | [share.streamlit.io](https://share.streamlit.io) → New app → Main file：`app_9edge_ui.py` |
| 3 | 將 Cloud link 俾朋友 |

朋友用法見 **`朋友使用指南.md`**。

### Cloud 功能

| 功能 | 支援 |
|------|------|
| 睇 batch screening 摘要（repo 內 `reports/batch/*_summary.md`） | ✅ |
| 上載 W1/D1/H1 → **CSV 重新分析** | ✅ |
| **分析 (TV)**（TradingView 拉數） | ❌ 僅本機 |

更新 batch：本機 **9edge.bat** → UI「Screener 選股」→ commit summary → push → Cloud 自動更新。

---

## 本機自己用

| bat | 用途 |
|-----|------|
| `launch_tv_debug.bat` + `開9edge_UI.bat` | TV 拉數 + 9-edge 分析 |
| `跑選股.bat` | Screener CSV 批量選股（零 token） |
| `batch分析CSV.bat` | 已有 CSV 批量分析 |

---

## 依賴

`requirements.txt` 已包含 Streamlit、yfinance。Cloud **唔需要** Node / tradingview-mcp。

---

## 常見問題

**朋友要等我開機？**  
用 **Streamlit Cloud link** 就唔使。

**Cloud 分析完 report 去邊？**  
Cloud 暫存；請 download。永久保存：本機 commit `reports/batch/` 再 push。

**想 Cloud 有最新 screening？**  
`跑選股.bat` → commit `reports/batch/*_summary.md` → push。
