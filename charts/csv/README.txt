TradingView CSV 放這裡
======================

TradingView 匯出步驟：
1. 開圖（D1 或 H4）
2. 圖表下方或右鍵選單 -> Export chart data...（匯出圖表數據）
3. 存做：
   ETN_D1.csv
   ETN_H4.csv

注意：
- 免費帳戶可能有 bar 數量限制
- 需要登入 TradingView

分析命令：
  python analyze_tv_csv.py --symbol ETN

或雙擊：分析CSV.bat

比截圖準：價格、EMA、量都由數據計算，唔使 LLM 睇圖。
