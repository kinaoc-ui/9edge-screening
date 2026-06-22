# 上 GitHub + Streamlit Cloud（俾朋友一條 link 就用）

**呢個先係推薦做法**：朋友開 `https://xxx.streamlit.app` 就得，**唔使連你部機**，**唔使 Cursor token**。

---

## 你要做嘅（一次性）

### 1. 建立 GitHub repo

1. 去 [github.com/new](https://github.com/new) 開新 repo（例如 `9edge-screening`）
2. **唔好**加 README / .gitignore（本地已有）

### 2. Push 本機 code

喺 `screening` 資料夾開 PowerShell：

```powershell
cd "C:\Users\kinao\OneDrive\桌面\stock\screening"

git init
git add .
git commit -m "9-edge screening UI + batch reports"

git branch -M main
git remote add origin https://github.com/你的帳號/9edge-screening.git
git push -u origin main
```

> 已 commit 上 GitHub 嘅 batch 摘要：`reports/batch/*_summary.md`、`SCREENER_*`（大 CSV、tv_import 唔會 push）。  
> **`video/` 課程片唔會 push**（已在 `.gitignore`，約 5GB）。

若 push 出現 `5.44 GiB` / `HTTP 500`：雙擊 **`git_乾淨Push.bat`** 清理後重 push。

### 3. Deploy Streamlit Cloud

1. 登入 [share.streamlit.io](https://share.streamlit.io)（用 GitHub 帳號）
2. **New app**
3. **Repository**：揀你啱啱 push 嘅 repo
4. **Branch**：`main`
5. **Main file path**：`app_9edge_ui.py`
6. **Deploy**

等 1–2 分鐘，會有永久 link，例如：

`https://9edge-screening.streamlit.app`

### 4. 俾朋友

將條 **Streamlit Cloud link** 傳俾朋友 + 附 `朋友使用指南.md` 內容。

之後你改 code 或更新 batch report → `git push` → Cloud 自動 rebuild。

---

## 朋友喺 Cloud 可以做咩

| 功能 | Cloud |
|------|-------|
| 睇 batch screening 摘要（sidebar「最近報告」） | ✅（你已 push 嘅 md） |
| 上載 W1/D1/H1 CSV → **CSV 重新分析** | ✅ |
| 上載 .md 報告睇 | ✅ |
| **分析 (TV)**（TradingView 拉數） | ❌ 只限你本機 |

朋友要分析新股票：TradingView 匯出 W1/D1/H1 CSV → 上載 → 按 **CSV 重新分析**。

---

## 你本機仍然可以做（自己用）

| 用途 | 用咩 |
|------|------|
| 自己 + TV 拉數 | 雙擊 **9edge.bat** → UI 撳「開 TradingView」+「分析 (TV)」 |
| 跑 Screener batch（零 token） | **9edge.bat** → UI 撳「Screener 選股」 |
| 更新 Cloud 上嘅 batch 結果 | UI 跑 Screener → commit `reports/batch/*_summary.md` → push |

---

## 常見問題

**Q：朋友要等我開機？**  
A：**唔使**。Streamlit Cloud 24/7 行，同你部機無關。

**Q：Cloud 分析完 report 會唔見？**  
A：Cloud 係暫存；要永久保存請 download md，或你本機跑完 commit 上 GitHub。
