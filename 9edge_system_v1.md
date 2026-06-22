# 9-Edge Stock Screening System (V1 → V2)

Market: US stocks  
Style: Swing (2 days to 4 weeks)  
Primary timeframe: W1 + D1 + H1  
Risk per trade: 1% of account  
Core principle: Only enter with multiple edge confluence.

---

## 0) Multiple Edge Trading Area（核心目標）

所有 9-edge 分析嘅目的，係搵 **有潛力** 嘅股 — 唔止睇**現價**有幾多 edge，更要問：

- **如果升上去 $X**，會多啲咩 edge？（例如升破前浪頂 → 阻力變支持 → +S&R）
- **如果跌下去 $Y**，short 邊會中幾多 edge？
- **Long edge vs Short edge** — 就算做 long，都要計 short 有幾強；Long > Short 先值得偏多

| 層次 | 意思 |
|------|------|
| **S&R 價區** | 前浪頂/底 + MA + 填補裂口 等相近價 **group 成 area**（anchor ±3.5%；**單一 area 闊度上限 ~5%**）；≥2 源 = Multiple edge |
| **+ 趨勢** | Edge #1 升勢確立（5/10/20MA 同向 + 大成交量） |
| **+ 形態/量** | CSR 反轉 K 線 + 其他 edge 匯聚 |
| **+ 確認** | MTF、RS、R&R&S 等 |

**A 級入場** = 核心 #1–#5 全過 + 價位喺 valid trading area（唔係中間位/追價）。

報告會顯示：

1. **Multiple Edge Trading Area**（現價支持匯聚區）
2. **Long vs Short Edge 對比**（現價 9/9 vs 9/9）
3. **價格情景表** — 升到/跌到 **S/R area**（group 後）會中咩 edge；表內 **匯聚來源** 列列出 cluster 組成（首 3 源 +N），方便核對 grouping 是否正確

實作：`analyze_tv_csv.py` → `build_edge_scenarios()`

---

## 1) Universe / Screener (TradingView — user export, NOT 9-edge)

Universe screening is **not** part of 9-edge grading. Apply filters in your TradingView screener before export; the engine only scores the 9 edges on symbols you provide.

Example user screener filters (replace with your own):

- Price > SMA200
- Market Cap > 2B
- Beta > 1
- Price × Volume (1M) > 900M

## 2) 9-Edge Score Model

Score each stock from 0 to 9 (one point per edge).  
Only **A setups** are tradable.

---

### Edge #1 — Momentum & Trend（J LAW 強勁動能 + 趨勢）✅ 已實作

Long bias — **5 子項至少 4/5**，且 **MA 同向** 同 **大成交量（硬性）** 先 pass。  
**同時計跌勢 5 子項**（Short edge 對比用）。**唔計波幅。**

| 子項 | 升勢 pass 條件 |
|------|----------------|
| 均線同向 | 5MA > 10MA > 20MA 且 20MA 斜向上 |
| 20MA 界線 | 近 10 日收市 ≤1 次跌穿 20MA，現價在 20MA 上 |
| 浪型/突破 | 一浪高於一浪，或強 K 升破前 60 日浪頂 |
| 大 K 線 | 近 10 日 ≥3 根 body > 1.3× 近 20 日均 body |
| **大成交量** | 今日量 ≥1.25×均量，或近 5 日均量 ≥1.15×，或大 K 配大成交量 ≥2 次 |

催化劑（評級、合作協議等）需人手補充，CSV 無法偵測。  
實作：`analyze_tv_csv.py` → `analyze_momentum_trend()`

---

### Edge #2 — S&R（J LAW 支持與阻力 → Multiple Edge Trading Area）✅ 已實作

**S&R 來源（4 類）：**

1. **前浪頂 / 前浪底** — 歷史 swing high / low  
2. **趨勢線 / 平行通道** — W1 平行通道（UTL/DTL **必須兩點定斜率** p1→p2，再延伸）；圖表參考，**唔做 Setup TP**  
3. **移動平均線** — 接近 / 到達 / **輕微穿越** MA；MA 向上 = 支持，向下 = 阻力  
4. **填補裂口** — gap 被回補後嘅價位  

**突破規則：**

- 輕微或短暫穿越前浪頂/底 → **唔代表 S&R 消失**  
- **大幅穿越**，或穿越後**維持一段時間** → 視作突破  
- 前浪頂/底一旦被 **sustained break** 突破 → **支持/阻力力量反轉**（阻力變支持，支持變阻力）  
- **輕微/短暫** 升穿或跌穿 **唔計** breakthrough — S&R 仍有效  
- MA、填補裂口同前浪頂/底一樣適用 role reversal；注意 MA **方向**  
- **Area 層面**：整個 S/R area 被 sustained break 穿過後，該 area 角色反轉；未確認突破嘅 area 仍保留原角色  

**Pass 條件（long）：**

- 價位喺 **Multiple edge area**（≥2 個 S&R 來源匯聚，距現價 ≤4%）  
- 或 **突破前浪頂 + 回踩企穩**（role reversal）  
- 或 升勢中 **輕微穿越 MA/前浪底** 但收市企穩  

**Fail：** 中間位（距支撐 5–12% 且未近阻力突破）、追價（距支撐 >10%）

**Timeframe priority (swing):** W1 > D1 > H1 for key wave levels — `merge_swing_sr()` in `score_symbol()`.

**CSV 自動評分：**

| 子項 | Pass |
|------|------|
| 多源匯聚 | ≥2 源（前浪底 / MA / 填補裂口 / 20日低）喺 ±3.5% 帶內 |
| 距離 | 現價距 area 中心 **≤4%**（唯一允許固定 % 規則） |
| 突破回踩 | 前浪頂 sustained break + 結構式回踩企穩（阻力→支持） |
| MA 角色 | MA 向上=支持；輕微穿越 + 收市企穩仍有效 |
| 突破判定 | 結構式（燭身站穩或 2+ 收市），**唔用**固定 % offset |
| Role reversal | MA / 裂口 / 前浪頂底 / **area** — sustained break 後反轉；短暫 pierce 唔計 |
| Area 過濾 | `build_support_areas()` / `build_resistance_areas()` 用 bars 剔除已失效 area，並加入已反轉 area |

實作：`analyze_sr()`, `merge_swing_sr()`, `key_price_levels()`, `build_support_sources()`, `classify_ma_level()`, `area_*_valid()`

---

### Edge #3 — CSR 陰陽燭價格行為 ✅ 五大類 + 大前提

**五大類：** Reversal ✅ | Gap ✅ | Long Body ✅ | Engulfing ✅ | Screw-over ✅

#### CSR 大前提（所有子類必須遵守）

1. 陰陽燭反映**買賣意圖**，唔只睇升跌結果  
2. **同形態、不同背景 → 解讀完全不同**（course 核心）  
3. 必須結合 Edge #1 趨勢 + Edge #2 S&R 價位  
4. Reversal = 1–4 根 K 內**強勁反向**；長影線 = 曾去該位但收市返回  
5. 必須喺**正確價位**（Multiple edge area / 前浪頂底 / MA）  
6. Gap 唔好當日判斷 — 等 **1–3 日跟進性**  
7. 永遠以**收市價**作準（愚弄 / 吞噬 / 失敗反向）  
8. 輕微穿越 MA 但收市企穩 → 唔算做錯  

#### Natural Pullback（背景判斷）✅ 已實作

拉回支持區時要問：**係 Natural Pullback 定真反转？**

| Pass（Natural Pullback） | Fail（同形態但背景唔同） |
|--------------------------|--------------------------|
| 短中期 MA 向上（5>10，20MA 未跌） | 中期 MA 向下 + 長期 MA 向下 |
| 長期 MA（EMA200）**趨平或向上**（打平仍可作支持） | 有 Pin Bar 但背景不配合 → **唔 pass Reversal** |
| Support Area 保持（未跌穿前浪底） | 只 note「非 Natural Pullback」 |
| 前文有拉回 | |

實作：`assess_csr_background()` → 影響 Reversal pass + report note

#### Gap + Outside Bar 裂口反轉 ✅ 已實作

- 向上裂口 @ **前浪頂/阻力** + 1–2 日內 **Outside Bar 陰線** → Short  
- 實作：`scan_gap_outside_reversal()`

#### Reversal 反轉 — 總原則

- 陰陽燭反映**買賣雙方意圖**，唔只睇結果
- Reversal = **強勁反向動能**；1 根 K 或最多 **3–4 根 K** 內快速反轉
- 影線代表價格曾去到該位，但**收市全部回到上方/跌回下方**（唔會停留喺錯位太久）
- **共同特徵：長長嘅影線**（Pin Bar / Hammer / Shooting Star）
- **必須喺正確價位**（關鍵支持/阻力、Multiple edge area、MA）— 錯位嘅 Reversal 會快速失效
- 要結合**前文後理** + 當前市場環境（同 Edge #1/#2 一齊睇）
- 反過來亦一樣（支持→ bullish；阻力→ bearish）

#### Long Pass（CSV 自動）

| 子項 | Pass |
|------|------|
| 價位 | 喺關鍵**支持**（S&R area / 前浪底 / MA / 20日低，±3.5%） |
| 前文 | 跌了一段 / 拉回（近 5K ≥2 陰線 或 距 10K 高點 ≥3%） |
| 形態 | 近 4K 內：**Pin Bar/錘子**（下影 ≥2× body 且 ≥50% range） |
| 加分 | Reversal K 配放量 |

#### Fail

- 有 Reversal 形態但**唔喺關鍵支持**（錯位）
- 支持區但**未有** Reversal K 線

實作：`analyze_tv_csv.py` → `analyze_csr()`

#### Gap 裂口 ✅

**總原則**

- 裂口 = 兩根 K 線之間**冇重疊**嘅價格差
- 長期升/跌勢中嘅**延續裂口**先係大機會
- **唔好喺裂口當日**就判斷成功/失敗 → 要等**翌日或之後 2–3 日**價格行為確認
- 正常行為 = **跟進性**（gap up 應繼續升；gap down 應繼續跌）
- 異常 = 翌日出現**反向大 K**（gap down 後大陽燭 / gap up 後大陰燭）

**裂口高開體 / 低開體**

- 開、高、低、收**近乎相同**；極高/極低開，收喺極端

**Long Pass**

| 子項 | Pass |
|------|------|
| 延續裂口↑ | 升勢中 gap up + 1–3 日跟進確認 + 現價唔跌穿裂口日開盤 |
| 裂口↓失敗反向 | gap down 失敗（升穿裂口日開盤/最高）→ 反向買入信號 |

**Short Pass**

| 子項 | Pass |
|------|------|
| 延續裂口↓ | 跌勢中 gap down + 跟進確認 + 現價唔升穿裂口日開盤 |
| 裂口↑失敗 | gap up 失敗跌穿裂口日開盤 |

**Fail**

- 跌穿 gap up 開盤價（向上裂口失敗）
- 升穿 gap down 開盤價（向下裂口失敗，除非做反向）
- 裂口後無跟進性

**Short CSR 總覽（Reversal / Gap / 大燭 / 吞噬 / 愚弄）**

| 子項 | Short edge |
|------|------------|
| Reversal@阻力 | Shooting Star @ 阻力 + 前文上升 |
| Gap↓延續 | 跌勢中 gap down + 跟進確認 |
| Gap↑失敗反向 | gap up 失敗跌穿開盤 |
| 大陰燭 | body ≥1.8× 均 body |
| 大陽燭延伸失敗反向 | 延伸區大陽燭後跌穿開盤 → Short |
| 陰吞噬 | Bearish Engulfing @ 阻力 |
| 陽吞噬失敗反向 | 陽吞噬後跌穿開盤 → Short |
| 反轉再反轉↓ | Bear Trap + 收市跌穿前K low @ 阻力 |
| Trap↑愚弄失敗反向 | 陽愚弄後跌穿Trap低 → Short |
| **Gap+Outside 裂口反轉** | 向上裂口@前浪頂 + Outside Bar 陰線 |

報告會分開顯示 **Long CSR note** 同 **Short CSR note**；**Short Edge 明細** 逐 edge 計分。

#### Long Body 大燭 ✅

**總原則**

- 大陽燭 / 大陰燭 = **燭身幅度**大（開→收），**唔用**收市價絕對升跌幅
- **燭身越長，動能越大**；收市升/跌方向係次要考慮
- 就算成交量唔高，只要價格行為夠強烈都算大燭（放量係加分）

**位置分類（策略提示）**

| 位置 | 意思 | 策略 |
|------|------|------|
| 趨勢開端 / 轉折 | 突破前 consolidation 或 @S&R 轉折 | 後續動能最大 ✅ |
| 趨勢中途 | 已有升/跌勢中再出大燭 | 仍有動能，但時間較短、過程較唔順 |
| 延伸區 | 已升/跌一段後再出大燭 | 股價已延伸，失敗率↑；**唔代表唔可以做**，策略宜短線 |

**Long Pass**

| 子項 | Pass |
|------|------|
| 大陽燭 | body ≥1.8× 近 20 日均 body，body 佔 range ≥55% |
| 趨勢開端/轉折 | 突破前高 / @支持轉折 → 最佳 edge |
| 中途/延伸 | 仍 pass，但 note 提示短線 |
| 大陰燭延伸失敗反向 | 延伸區大陰燭後升穿開盤 → Long 反向 |

**Short Pass**

| 子項 | Pass |
|------|------|
| 大陰燭 | 同上，方向相反 |
| 大陽燭延伸失敗反向 | 延伸區大陽燭後跌穿開盤 → Short |

#### Engulfing 吞噬 ✅

**總原則**

- **Bullish / Bearish Engulfing Bar**：當日 body **完全包住**前一日 body，且幅度更大
- **破腳穿頭**：當日 high/low 完全包住前日 high/low + 收市突破前日 high/low
- **單 K 等價吞噬**：未必見到兩根 body 全包或破腳穿頭，但**行為同意圖**同 Bullish/Bearish Engulfing（洗盤 + 收市突破）
- **兩日反映**：分兩根 K 反映同一吞噬意圖（前 K 小陰/陽 + 當日大 K 突破）
- 陽吞噬要**收市升穿前日最高價**；陰吞噬要**收市跌穿前日最低價**
- 必須喺**關鍵水平**（前浪頂/底、Multiple edge area、突破回踩）
- **Shake Out**：前一日小陰線 + 翌日陽吞噬 @ 突破位
- **輕微微穿越 MA**（低點穿、收市企穩）→ 唔算做錯
- 裂口低開 + 陽吞噬 = 180° Reversal（大戶推價）

**Long Pass**

| 子項 | Pass |
|------|------|
| 陽吞噬 | body 全包前 K + 升穿前日 high + @關鍵水平 |
| 破腳穿頭 | outside bar + 收市突破 |
| 單K等價 | 洗盤低點 + 大陽收市升穿前日 high @ 關鍵水平 |
| 兩日反映 | 前K小陰 + 大陽突破（未必 body 全包） |
| Shake Out | 前 K 陰線清洗 + 陽吞噬突破 |
| 前浪頂回踩 | 阻力→支持 + Bullish Engulfing |
| 陰吞噬失敗反向 | 陰吞噬後升穿開盤 → Long |
| 加注 | 現價回到早前吞噬位 ±3% |

**Short Pass**

| 子項 | Pass |
|------|------|
| 陰吞噬 | body 全包前 K + 跌穿前日 low + @阻力 |
| 破腳穿頭 | outside bar + 收市跌穿前日 low |
| 單K等價 | 洗盤上影 + 大陰收市跌穿前日 low @ 阻力 |
| 兩日反映 | 前K小陽 + 大陰跌破（未必 body 全包） |
| 陽吞噬失敗反向 | 陽吞噬後跌穿開盤 → Short |

#### Screw-over 愚弄 ✅

**總原則**

- **永遠以收市價作準** — 盤中見到嘅嘢唔代表最終方向
- **反轉再反轉** = 市場先 Trap 一次，再 180° 反向 screw  trapped 嘅人
- 市場可以 Trap 你**不止一次**；每次都要等**收市確認**
- 同吞噬類似，但強調 **收市升穿/跌穿 Trap K 嘅 high/low**（唔只 body 全包）

**Long Pass**

| 子項 | Pass |
|------|------|
| 反轉再反轉↑ | 前K陰線Trap + 當日陽線收市 **> 前K最高價** @ 關鍵支持 |
| 180°反向 | 收市升穿幅度 ≥1.5% |
| Bear Trap愚弄失敗反向 | 陰線愚弄後升穿Trap高 → Long |

**Short Pass**

| 子項 | Pass |
|------|------|
| 反轉再反轉↓ | 前K陽線Trap + 當日陰線收市 **< 前K最低價** @ 阻力 |
| 180°反向 | 收市跌穿幅度 ≥1.5% |
| Bull Trap愚弄失敗反向 | 陽線愚弄後跌穿Trap低 → Short |

---

### Edge #4 MTF（W1 → D1 → H1）

**Stack:** W1 (HTF) → D1 (mid, primary setup) → H1 (LTF entry timing).  
Valid separation: W1→D1 ~5×, D1→H1 ~6.5× (≥4× apart per course).

**Course principles (implemented in `analyze_mtf_cross()`):**

- LTF can show a big reaction at S/R while HTF only pulls back slightly, then continues the HTF trend.
- LTF counter-trend move may still be HTF with-trend (and vice versa).
- LTF parabolic run → HTF brief pullback = precise entry technique.
- MA parameters (5/10/20) are the same on every timeframe.

**Long MTF pass when:**

1. W1 is not bearish against a D1 long setup (HTF supports or is neutral).
2. D1 has a long setup (momentum pass, or price above 20MA with W1 not bearish).
3. H1 is not structurally fighting the D1 long — a bearish H1 pullback in an W1/D1 uptrend counts as entry timing.

**Short:** mirror logic.

Each timeframe gets its own 9-edge table in the report; MTF (#4) is the cross-stack score (same value shown per TF, labeled W1→D1→H1). Overall tradable grade uses **D1** edges + cross-TF MTF + symbol-level RS/Board.

---

### Edge #5 — R.S.（相對強度）✅ V2

**三特徵**（唔使齊晒，**≥2/3** 即過；前文後理判斷有 RS 可果斷進場）：

1. **反向走勢** — 跑贏 SPY；跌市時最易見（大盤跌、個股平/升）。
2. **領先移動平均線** — 價在 5/10/20MA 之上，短均線領先（D1）。
3. **RS 線向上** — 股價/SPY 比率上升或近 40 日新高。

> 強勢領導股往往有 RS；但有 RS 的股票不一定是強勢領導股。

Implemented: `assess_relative_strength()` — yfinance SPY + D1 CSV bars。

---

### Edge #6 — R&R&S（Risk, Reward & Stop）✅ V2

**M.E.T.A.** — **M**oney **E**ntry **T**arget **A**lignment：入場、止損、目標、RR 四者必須一齊合理先入場（唔係淨睇圖形）。

**止損（結構式，取最清晰）：**
1. Trading area low（Multiple edge 支持區下沿 — **W1+D1+H1** 匯聚，含 MA、填補裂口）
2. 前浪底 / 前浪頂 role reversal（各 TF；**W1 > D1 > H1** 影響力）
3. 20 日低（D1）
4. **現價 / 突破 setup**：止損用 **W1+D1** 結構支持（唔用 H1 微支持做 swing stop）；H1 僅 fallback
5. **回踩 setup B**：Entry 用 zone **中位**（闊區）或 **下沿**（窄區），唔用 zone 頂

**目標（取最合理、RR 最高者）：**
- **前浪頂** / **60 日阻力** / **MA（向下=阻力）** / **填補裂口** — 由 W1+D1+H1 合併池揀
- **UTL** 上升通道：下軌連 **兩個遞升 swing low**（p1→p2），上軌平行（同樣兩點）；虛線由 p2 向前 +12W — **圖表參考 only**
- **DTL** 下降通道：上軌連 **兩個遞降 swing high**（p1→p2），下軌平行；虛線由 p2 向前 — **圖表參考 only**

**Multi-TF key level 規則（Watch Setup A/B）：**
- **睇齊** W1、D1、H1：浪頂/浪底、MA（向上=支持/向下=阻力）、填補裂口
- **揀位** 時 W1 > D1 > H1 影響力；label 必須標示來源 TF（例：`D1 20MA（向上=支持）`）
- Entry / Stop / TP 各自獨立計；≤4% 只用於匯聚 proximity，唔用公式 % offset 做價位

**RR 規則：**
- 目標 **5:1** 最理想
- **≥2:1** 即過 edge（實盤 execution 會「打折」）
- Pass：`raw_rr >= 2` 且 stop / target 均有結構依據

Implemented: `build_rr_plan()` — `pick_structure_stop()`, `collect_reward_targets_*()`, `compute_trendline_levels()`。

---

### Edge #7 — Broad Market Edge（大盤 Long/Short Edge）✅ V2

**Trade What We See** — 分析大盤（**SPY** 主；QQQ 次參考）係 **Long Edge** 定 **Short Edge**，再決定搵長倉定短倉。

- **Long Edge** = 大盤處於多重優勢進場區間（Multiple Edge support）或明確升勢結構 → **積極搵長倉**（強勢股）
- **Short Edge** = 大盤處於阻力匯聚 / 跌勢結構 → **積極搵短倉**
- 大盤 + 個股 **同時 Long Edge** = 最高勝率做多環境

**三支柱（≥2/3 通過）：**

1. **S&R** — Multiple Edge support zone（≥2 源）或突破回踩；Short 鏡像阻力區
2. **動能/趨勢** — MA stack 或 higher lows（升）/ lower highs（跌）
3. **MTF** — W1→D1 對齊做多/做空（無 CSV 時 fallback yfinance D1）

**SPY 報價：** 用 **即時 / 盤前 / 盤後**（yfinance `marketState`）；**唔用昨日收市**。無當前時段報價 → BME **參考·不計分**（同 MI 模式，從總分剔除）。

**同 Sector 升跌（子項）：** 報告顯示同板塊 peer 方向 — `升` / `跌` / `混合` / `無數據`。
- Screener batch：同 Sector 內 peer 當日升跌統計（>0.15% 計升，<-0.15% 計跌）
- 單股：fallback 該 Sector ETF（XLK/XLF…）即時升跌

**評分：** `board_edge` long=1 當大盤 Long Edge；short=1 當大盤 Short Edge（全市場共用）。Sector peer 為參考子項，唔改 pillar 2/3 計分。

Implemented: `assess_broad_market_edge()` + `yf_live_quote()` — SPY hist from `charts/csv/SPY_*.csv` or yfinance D1，現價 patch 最後一根 K。

---

### Edge #8 — F.T.（First Touch / META 進場）✅ V2

META 進場策略：**盡量做第1或第2次 touch**（力量最強）；第2次仍理想；第3/4次要小心、偏短線；第4次後易多次穿越、力量減弱。

**Touch 定義（Long）：**
- 升勢 MA 排列（5>10>20）
- 若 S&R 已 group 成 **trading area** → 以 **area 下沿–上沿** 計 touch（同一 area 內觸及多條 MA **只算 1 次**）
- 否則：low 觸及 **10MA**（強勢）或 **20MA**（較深回踩）~1.8% 帶內
- 收市反彈（陽線或收在支持之上）
- 兩次 touch 之間必須 **離開 area**（價格回升離 zone）先至計下一次

**Pass Long F.T.：** 本趨勢段內第 **1–2 次**有效 touch + 近期反彈；或 **突破後近3K follow-through** + 放量。

**Short** — 跌勢 mirror（觸及 MA 後回落）。

Implemented: `assess_first_touch()` in `analyze_tv_csv.py`。

---

### Edge #9 — M.I.（MACD breakout only）✅ V2

只用輔助指標 **MACD**，而且**只喺突破交易情境**先啟用（平時唔用嚟判斷）。

**時間框架：** 以 **W1（周線）為準**；高時間框架 MACD 訊號優先。

**Pass Long M.I.：**
- 價格處於突破位（收市突破前高/近幾支 K 破前高）
- MACD 出現多頭確認（近期黃金交叉，或 line > signal + 柱體轉強）

**Pass Short M.I.（鏡像）：**
- 價格跌破關鍵低位
- MACD 空頭確認（近期死亡交叉，或 line < signal + 柱體轉弱）

Implemented: `assess_mi_macd_breakout()` in `analyze_tv_csv.py`（並由 W1 統一覆蓋 D1/H1 的 M.I. 分數）。

## 3) Trade Class Rules

- A setup: score >= 7 and Edge #1-#5 all pass.
- B setup: score = 6, can watch but no entry by default.
- C setup: score <= 5, skip.

Hard entry rule:

- Must pass #1, #2, #3, #4, #5
- Plus any 2 of #6 to #9
- Reward/risk must be >= 2 before order placement

## 4) Entry, Stop, Target

## Entry Trigger (choose one)

- Breakout entry: break above trigger level with volume expansion
- Retest entry: reclaim key level and hold on H1

## Stop Placement

- Place stop below invalidation structure (trading area low / wave bottom / 20-day low / multi-TF MA or gap)
- Breakout setup: stop at nearest valid support below entry (any TF; W1 > D1 > H1 tiebreak)
- Do not use arbitrary fixed cents stop

## Target and Management

- Reward targets (pick best logical): prior wave top, UTL channel extension, DTL measured move
- Aim **5:1** RR when structure allows; **minimum 2:1** to pass Edge #6
- TP1: 1R (scale out 30% to 50%) — label as **1R 量度目標** (not a structure level)
- TP2: primary structure target (not fixed 2R) — report must name the key level (e.g. W1 前浪頂, UTL 通道延伸, DTL 突破量度)
- Stop / TP rows in Watch Setup A/B show **price + key level type** (W1/D1/H1 where relevant)
- Runner: trail by H1 swing low or D1 EMA20

If price closes back below key reclaim level quickly, reduce risk early.

## 5) Position Sizing (1% model)

Let:

- Account size = A
- Risk percentage = 1%
- Entry price = E
- Stop price = S
- Risk per share = E - S

Then:

- Dollar risk per trade = A * 0.01
- Position size (shares) = floor((A * 0.01) / (E - S))

Never exceed predefined max risk.

## 6) Daily Workflow (30 to 45 min)

1. Market context:
  - Check SPY and QQQ trend (W1 + D1 + H1).
  - Check sector strength map (XLK, XLF, XLE, XLV, etc.).
2. Run screener (TradingView export — universe handled outside 9-edge):
  - Apply your TV filters (e.g. cap, beta, dollar volume); export CSV.
3. Build shortlist:
  - Keep 20 to 50 names.
4. Score candidates:
  - Fill 9-edge scorecard.
5. Execute only A setups:
  - Pre-calc entry, stop, shares, TP1, TP2.
6. Journal:
  - Save screenshot + reasons + post-trade review.

## 7) No-Trade Conditions

Skip new entries when:

- Major index is choppy/range-bound with failed breakouts
- News risk event is imminent and setup quality is marginal
- Reward/risk below 2
- Setup depends on a single edge only

## 8) Weekly Review

Track these metrics:

- Win rate
- Average R
- Expectancy
- A/B/C setup count
- Which edge failed most often

Goal: Improve edge quality and remove recurring weak patterns.

## 9) Upgrade Notes (V2 after course review)

**Done (V2 partial):**

- ✅ Edge #1 Momentum & Trend — J LAW 5-sub-check（無波幅）+ Short 對比  
- ✅ Edge #2 S&R — Multiple Edge Trading Area + breakout/role reversal rules  
- ✅ Edge #3 CSR — 五大類全部實作（Reversal / Gap / Long Body / Engulfing / Screw-over + 失敗反向 + Short edge）
- ✅ 價格情景分析 — Long vs Short edge projection
- ✅ Edge #6 R&R&S — M.E.T.A. structure stop + wave top/UTL/DTL targets

**Still to refine:**

- CSR 五大類 ✅ 全部完成
- R&R&S ✅  
- Board Market Edge ✅  
- F.T.（First Touch）✅  
- M.I.（W1 MACD breakout-only）✅

Bind each into stricter objective pass/fail criteria and update `analyze_tv_csv.py`.