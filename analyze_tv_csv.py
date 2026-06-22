#!/usr/bin/env python3
"""
9-Edge scoring from TradingView CSV only (no chart images needed).

Export from TV: Export chart data -> charts/csv/SYMBOL_W1.csv + SYMBOL_D1.csv + SYMBOL_H1.csv

  python analyze_tv_csv.py --symbol ETN
  python analyze_tv_csv.py --batch          # all symbols in charts/csv/
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_DIR = ROOT / "charts" / "csv"
REPORTS = ROOT / "reports" / "batch"

EDGES = [
    "momentum_trend", "sr", "csp_pa_vol", "mtf", "rs",
    "rrs", "board_edge", "ft", "mi",
]

# MI 暫時只得 MACD — 計分關閉，報告仍顯示作參考（用戶自行喺 TV 睇）
MI_EDGE_SCORING_ENABLED = False
EDGE_SCORE_KEYS = list(EDGES) if MI_EDGE_SCORING_ENABLED else [k for k in EDGES if k != "mi"]
EDGE_SCORE_MAX = len(EDGE_SCORE_KEYS)


def edge_score_fmt(n: int | float) -> str:
    return f"{int(n)}/{EDGE_SCORE_MAX}"


def sum_edge_scores(edges: dict) -> int:
    total = 0
    for k in EDGE_SCORE_KEYS:
        v = edges.get(k)
        if isinstance(v, dict):
            total += int(v.get("score", 0) or 0)
        else:
            total += int(v or 0)
    return total

TF_ORDER = ("W1", "D1", "H1")
TF_MIN_BARS = {"W1": 20, "D1": 30, "H1": 20}
TF_PRIORITY = {"W1": 0, "D1": 1, "H1": 2}
SWING_STOP_TFS = frozenset({"W1", "D1"})

SECTOR_ETF = {
    "technology": "XLK",
    "consumer cyclical": "XLY",
    "consumer defensive": "XLP",
    "healthcare": "XLV",
    "financial": "XLF",
    "financial services": "XLF",
    "industrials": "XLI",
    "basic materials": "XLB",
    "energy": "XLE",
    "utilities": "XLU",
    "real estate": "XLRE",
    "communication services": "XLC",
}


def load_tv_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k.strip().lower(): v.strip() for k, v in row.items() if k})
    if not rows:
        raise ValueError(f"Empty CSV: {path}")
    return rows


def parse_row(r: dict) -> dict | None:
    keys = list(r.keys())

    def find_val(*parts: str) -> float | None:
        for k in keys:
            kn = k.replace(" ", "").lower()
            if any(p in kn for p in parts):
                try:
                    v = (r.get(k) or "").strip()
                    if v and v.lower() not in ("nan", ""):
                        return float(v.replace(",", ""))
                except ValueError:
                    pass
        return None

    o, h, l, c, v = find_val("open"), find_val("high"), find_val("low"), find_val("close"), find_val("volume", "vol")
    if None in (o, h, l, c, v):
        return None
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def parse_bars(rows: list[dict], min_bars: int = 30) -> list[dict]:
    bars = []
    for r in rows:
        b = parse_row(r)
        if b:
            bars.append(b)
    if len(bars) < min_bars:
        raise ValueError(f"Need >={min_bars} bars, got {len(bars)}")
    return bars


def ema(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float], list[float]]:
    """Classic MACD series: line, signal, histogram."""
    if len(values) < 2:
        base = [0.0 for _ in values]
        return base, base, base
    ef = ema(values, fast)
    es = ema(values, slow)
    line = [a - b for a, b in zip(ef, es)]
    sig = ema(line, signal)
    hist = [m - s for m, s in zip(line, sig)]
    return line, sig, hist


def assess_mi_macd_breakout(bars: list[dict]) -> dict:
    """
    Edge #9 M.I. (V2): only MACD, and only for breakout context.
    Higher timeframe is preferred; caller may override by W1 result.
    """
    base = {
        "long_pass": False,
        "short_pass": False,
        "breakout_up": False,
        "breakout_down": False,
        "macd_line": 0.0,
        "macd_signal": 0.0,
        "macd_hist": 0.0,
        "long_note": "MACD（breakout only）：未有突破情境",
        "short_note": "MACD（breakout only）：未有突破情境",
    }
    if len(bars) < 35:
        base["long_note"] = "MACD 數據不足"
        base["short_note"] = "MACD 數據不足"
        return base

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    line, sig, hist = macd(closes)
    n = len(closes)

    top = max(highs[-30:-3])
    bot = min(lows[-30:-3])
    breakout_up = closes[-1] >= top * 1.003 or max(highs[-3:]) > top
    breakout_down = closes[-1] <= bot * 0.997 or min(lows[-3:]) < bot

    bull_cross_recent = any(
        line[i - 1] <= sig[i - 1] and line[i] > sig[i]
        for i in range(max(1, n - 4), n)
    )
    bear_cross_recent = any(
        line[i - 1] >= sig[i - 1] and line[i] < sig[i]
        for i in range(max(1, n - 4), n)
    )
    bull_momentum = hist[-1] > 0 and hist[-1] >= hist[-2]
    bear_momentum = hist[-1] < 0 and hist[-1] <= hist[-2]

    long_pass = breakout_up and (bull_cross_recent or (line[-1] > sig[-1] and bull_momentum))
    short_pass = breakout_down and (bear_cross_recent or (line[-1] < sig[-1] and bear_momentum))

    if breakout_up:
        long_note = (
            f"Breakout + MACD確認（line {line[-1]:.3f} / signal {sig[-1]:.3f} / hist {hist[-1]:.3f}）"
            if long_pass
            else f"有 Breakout 但 MACD 未確認（line {line[-1]:.3f} / signal {sig[-1]:.3f}）"
        )
    else:
        long_note = "MACD（breakout only）：目前唔係突破位"

    if breakout_down:
        short_note = (
            f"Breakdown + MACD確認（line {line[-1]:.3f} / signal {sig[-1]:.3f} / hist {hist[-1]:.3f}）"
            if short_pass
            else f"有 Breakdown 但 MACD 未確認（line {line[-1]:.3f} / signal {sig[-1]:.3f}）"
        )
    else:
        short_note = "MACD（breakout only）：目前唔係跌破位"

    return {
        "long_pass": long_pass,
        "short_pass": short_pass,
        "breakout_up": breakout_up,
        "breakout_down": breakout_down,
        "macd_line": round(line[-1], 4),
        "macd_signal": round(sig[-1], 4),
        "macd_hist": round(hist[-1], 4),
        "long_note": long_note,
        "short_note": short_note,
    }


def obv_trend(bars: list[dict]) -> bool:
    obv = [0.0]
    for i in range(1, len(bars)):
        if bars[i]["close"] > bars[i - 1]["close"]:
            obv.append(obv[-1] + bars[i]["volume"])
        elif bars[i]["close"] < bars[i - 1]["close"]:
            obv.append(obv[-1] - bars[i]["volume"])
        else:
            obv.append(obv[-1])
    return obv[-1] > obv[-10]


def range_pct(bars: list[dict], n: int) -> float:
    seg = bars[-n:]
    c = seg[-1]["close"]
    return (max(b["high"] for b in seg) - min(b["low"] for b in seg)) / c * 100


def find_swing_low(bars: list[dict], lookback: int = 20) -> float:
    return min(b["low"] for b in bars[-lookback:])


def find_resistance(bars: list[dict], lookback: int = 60) -> float:
    return max(b["high"] for b in bars[-lookback:])


def sma(values: list[float], period: int) -> list[float]:
    out = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(sum(values[: i + 1]) / (i + 1))
        else:
            out.append(sum(values[i - period + 1 : i + 1]) / period)
    return out


def candle_body_pct(bar: dict) -> float:
    return abs(bar["close"] - bar["open"]) / bar["close"] * 100 if bar["close"] else 0


def find_swing_points(bars: list[dict], lookback: int = 60) -> tuple[list[float], list[float]]:
    """Local swing highs and lows for wave structure."""
    seg = bars[-lookback:]
    highs, lows = [], []
    for i in range(1, len(seg) - 1):
        if seg[i]["high"] >= seg[i - 1]["high"] and seg[i]["high"] >= seg[i + 1]["high"]:
            highs.append(seg[i]["high"])
        if seg[i]["low"] <= seg[i - 1]["low"] and seg[i]["low"] <= seg[i + 1]["low"]:
            lows.append(seg[i]["low"])
    return highs, lows


# Edge #2 S&R — only confluence clustering / price-to-zone use fixed bands
SR_CONFLUENCE_BAND_PCT = 0.035
SR_CONFLUENCE_DIST_PCT = 0.04
SCENARIO_AREA_BAND_PCT = 0.035
SCENARIO_AREA_MAX_SPAN_PCT = 0.05  # max zone width — ~±2.5% from anchor cluster
SUPPORT_AREA_MAX_DIST_PCT = 0.22
RESISTANCE_AREA_MAX_DIST_PCT = 0.40
MAX_SUPPORT_AREAS = 5
MAX_RESISTANCE_AREAS = 4


def find_gap_zones(bars: list[dict], min_gap_pct: float = 1.0) -> list[dict]:
    """填補裂口：偵測 gap 及是否已被回補；filled gap 價位作 S&R。"""
    zones = []
    for i in range(1, len(bars)):
        prev, curr = bars[i - 1], bars[i]
        if curr["low"] > prev["high"] * (1 + min_gap_pct / 100):
            gap_lo, gap_hi = prev["high"], curr["low"]
            filled = any(b["low"] <= gap_hi for b in bars[i:])
            fill_price = gap_lo if filled else (gap_lo + gap_hi) / 2
            zones.append({
                "type": "up_gap", "low": gap_lo, "high": gap_hi,
                "level": fill_price, "filled": filled, "idx": i,
            })
        elif curr["high"] < prev["low"] * (1 - min_gap_pct / 100):
            gap_lo, gap_hi = curr["high"], prev["low"]
            filled = any(b["high"] >= gap_lo for b in bars[i:])
            fill_price = gap_hi if filled else (gap_lo + gap_hi) / 2
            zones.append({
                "type": "down_gap", "low": gap_lo, "high": gap_hi,
                "level": fill_price, "filled": filled, "idx": i,
            })
    return zones


def ma_rising(series: list[float], lookback: int = 6) -> bool:
    return len(series) > lookback and series[-1] > series[-lookback]


def ma_falling(series: list[float], lookback: int = 6) -> bool:
    return len(series) > lookback and series[-1] < series[-lookback]


def minor_penetration_holds_support(bar: dict, level: float) -> bool:
    """輕微穿越支持但收市企穩 — S&R 仍有效。"""
    return level > 0 and bar["low"] < level and bar["close"] >= level


def minor_penetration_holds_resistance(bar: dict, level: float) -> bool:
    return level > 0 and bar["high"] > level and bar["close"] <= level


def classify_ma_level(
    bars: list[dict], price: float, label: str, series: list[float],
) -> tuple[str | None, str]:
    """
    MA as S/R with role reversal after sustained break.
    輕微穿越 ≠ 突破；sustained_break 後阻力↔支持。
    Returns (side, display_label) or (None, '') if level inactive.
    """
    val = series[-1]
    if val <= 0:
        return None, ""
    last = bars[-1]
    broke_up = sustained_break(bars, val, "up")
    broke_down = sustained_break(bars, val, "down")

    if broke_up and price >= val:
        if last["close"] >= val or minor_penetration_holds_support(last, val):
            return "support", f"{label}（阻力→支持）"
    if broke_down and price <= val:
        if last["close"] <= val or minor_penetration_holds_resistance(last, val):
            return "resistance", f"{label}（支持→阻力）"

    if ma_rising(series):
        if val <= price or minor_penetration_holds_support(last, val):
            return "support", label
    elif ma_falling(series):
        if val >= price or minor_penetration_holds_resistance(last, val):
            return "resistance", label
    elif val <= price:
        return "support", f"{label}（平）"
    elif val >= price:
        return "resistance", label
    return None, ""


def classify_gap_level(
    bars: list[dict], price: float, gap: dict, prefix: str = "",
) -> tuple[str | None, str, float]:
    """Gap fill level with role reversal after sustained break."""
    if not gap.get("filled"):
        return None, "", 0.0
    lvl = gap["level"]
    if lvl <= 0:
        return None, "", 0.0
    tag = f"{prefix}填補裂口"
    broke_up = sustained_break(bars, lvl, "up")
    broke_down = sustained_break(bars, lvl, "down")
    if broke_up and price >= lvl:
        return "support", f"{tag}（阻力→支持）", lvl
    if broke_down and price <= lvl:
        return "resistance", f"{tag}（支持→阻力）", lvl
    if lvl <= price and not broke_down:
        return "support", tag, lvl
    if lvl >= price and not broke_up:
        return "resistance", tag, lvl
    return None, "", lvl


def sustained_break(bars: list[dict], level: float, direction: str = "up") -> bool:
    """
    突破判定（結構式，無固定 % offset）：
    - 大幅穿越 = 收市站穩在 level 另一邊且燭身主體越過 level
    - 或 2+ 根收市維持在 level 另一邊
    輕微/短暫影線穿越不算。
    """
    if level <= 0 or len(bars) < 2:
        return False
    seg = bars[-5:]
    last3 = seg[-3:]

    if direction == "up":
        def body_committed_above(b: dict) -> bool:
            if b["close"] <= level:
                return False
            body_lo = min(b["open"], b["close"])
            return body_lo >= level or b["close"] >= (b["high"] + b["low"]) / 2

        if any(body_committed_above(b) for b in last3):
            return True
        return sum(1 for b in seg if b["close"] > level) >= 2

    def body_committed_below(b: dict) -> bool:
        if b["close"] >= level:
            return False
        body_hi = max(b["open"], b["close"])
        return body_hi <= level or b["close"] <= (b["high"] + b["low"]) / 2

    if any(body_committed_below(b) for b in last3):
        return True
    return sum(1 for b in seg if b["close"] < level) >= 2


def retest_holds_as_support(bars: list[dict], level: float) -> bool:
    """突破前浪頂後回踩企穩（阻力→支持）。"""
    if level <= 0 or not sustained_break(bars, level, "up"):
        return False
    last = bars[-1]
    if minor_penetration_holds_support(last, level):
        return True
    if last["close"] >= level:
        return any(b["low"] <= level for b in bars[-3:])
    return False


def retest_holds_as_resistance(bars: list[dict], level: float) -> bool:
    """跌穿前浪底後反彈受阻（支持→阻力）。"""
    if level <= 0 or not sustained_break(bars, level, "down"):
        return False
    last = bars[-1]
    if minor_penetration_holds_resistance(last, level):
        return True
    if last["close"] <= level:
        return any(b["high"] >= level for b in bars[-3:])
    return False


def cluster_support_zone(
    sources: list[tuple[str, float]], price: float, tol_pct: float = SR_CONFLUENCE_BAND_PCT,
) -> dict | None:
    """Multiple edge area：多個 S&R 來源匯聚同一價區（±tol_pct 帶）。"""
    valid = [(n, v) for n, v in sources if v > 0 and v <= price]
    if not valid:
        return None
    best = None
    for _, anchor in valid:
        band_lo = anchor * (1 - tol_pct)
        band_hi = anchor * (1 + tol_pct)
        in_band = [(n, v) for n, v in valid if band_lo <= v <= band_hi]
        if not in_band:
            continue
        zone_lo = min(v for _, v in in_band)
        zone_hi = max(v for _, v in in_band)
        mid = (zone_lo + zone_hi) / 2
        dist_pct = abs(price - mid) / price if price else 0
        names = list(dict.fromkeys(n for n, _ in in_band))
        score = len(names)
        if best is None or score > best["edge_count"] or (score == best["edge_count"] and dist_pct < best["dist_pct"]):
            best = {
                "zone_lo": round(zone_lo, 2),
                "zone_hi": round(zone_hi, 2),
                "edge_count": score,
                "sources": names,
                "dist_pct": dist_pct,
                "anchor": anchor,
            }
    return best


def cluster_resistance_zone(
    sources: list[tuple[str, float]], price: float, tol_pct: float = SR_CONFLUENCE_BAND_PCT,
) -> dict | None:
    """Short bias：阻力匯聚區（價位接近阻力從下方）。"""
    valid = [(n, v) for n, v in sources if v > 0 and v >= price]
    if not valid:
        return None
    best = None
    for _, anchor in valid:
        band_lo = anchor * (1 - tol_pct)
        band_hi = anchor * (1 + tol_pct)
        in_band = [(n, v) for n, v in valid if band_lo <= v <= band_hi]
        if not in_band:
            continue
        zone_lo = min(v for _, v in in_band)
        zone_hi = max(v for _, v in in_band)
        mid = (zone_lo + zone_hi) / 2
        dist_pct = abs(price - mid) / price if price else 0
        names = list(dict.fromkeys(n for n, _ in in_band))
        score = len(names)
        if best is None or score > best["edge_count"] or (score == best["edge_count"] and dist_pct < best["dist_pct"]):
            best = {
                "zone_lo": round(zone_lo, 2),
                "zone_hi": round(zone_hi, 2),
                "edge_count": score,
                "sources": names,
                "dist_pct": dist_pct,
                "anchor": anchor,
            }
    return best


def build_support_sources(
    bars: list[dict],
    s5: list[float],
    s10: list[float],
    s20: list[float],
    e20: list[float],
    wave_top: float,
    wave_bot: float,
    swing_20: float,
    gaps: list[dict],
    price: float,
    tf_prefix: str = "",
) -> tuple[list[tuple[str, float]], bool, bool]:
    """Long S&R sources: 前浪底 + MA(向上=支持) + 填補裂口 + 突破後前浪頂 role reversal。"""
    prefix = f"{tf_prefix}:" if tf_prefix else ""
    sources: list[tuple[str, float]] = []
    broke_top = sustained_break(bars, wave_top, "up")
    broke_bot = sustained_break(bars, wave_bot, "down")

    if not broke_bot:
        sources.append((f"{prefix}前浪底", wave_bot))
    sources.append((f"{prefix}20日低", swing_20))

    if broke_top:
        sources.append((f"{prefix}前浪頂(阻力→支持)", wave_top))

    for label, series in [("5MA", s5), ("10MA", s10), ("20MA", s20), ("EMA20", e20)]:
        side, disp = classify_ma_level(bars, price, label, series)
        if side == "support":
            sources.append((f"{prefix}{disp}", series[-1]))

    for g in gaps:
        side, disp, lvl = classify_gap_level(bars, price, g, prefix)
        if side == "support":
            sources.append((disp, lvl))

    ma_bull = s5[-1] > s10[-1] > s20[-1]
    return sources, broke_top, ma_bull


def build_resistance_sources(
    bars: list[dict],
    s5: list[float],
    s10: list[float],
    s20: list[float],
    e20: list[float],
    wave_top: float,
    wave_bot: float,
    resist_60: float,
    gaps: list[dict],
    price: float,
    tf_prefix: str = "",
) -> tuple[list[tuple[str, float]], bool]:
    prefix = f"{tf_prefix}:" if tf_prefix else ""
    sources: list[tuple[str, float]] = []
    broke_top = sustained_break(bars, wave_top, "up")
    broke_bot = sustained_break(bars, wave_bot, "down")

    if not broke_top:
        sources.append((f"{prefix}前浪頂", wave_top))
    sources.append((f"{prefix}60日高", resist_60))

    if broke_bot:
        sources.append((f"{prefix}前浪底(支持→阻力)", wave_bot))

    for label, series in [("5MA", s5), ("10MA", s10), ("20MA", s20), ("EMA20", e20)]:
        side, disp = classify_ma_level(bars, price, label, series)
        if side == "resistance":
            sources.append((f"{prefix}{disp}", series[-1]))

    for g in gaps:
        side, disp, lvl = classify_gap_level(bars, price, g, prefix)
        if side == "resistance":
            sources.append((disp, lvl))

    ma_bear = s5[-1] < s10[-1] < s20[-1]
    return sources, ma_bear


def bars_at_price(bars: list[dict], price: float) -> list[dict]:
    """Simulate last bar at hypothetical close (for scenario projection)."""
    out = [dict(b) for b in bars]
    last = dict(out[-1])
    last["close"] = price
    last["high"] = max(last["high"], price)
    last["low"] = min(last["low"], price)
    out[-1] = last
    return out


def analyze_sr(bars: list[dict], tf_label: str = "") -> dict:
    """
    J LAW 支持與阻力 (Edge #2) → Multiple Edge Trading Area
    來源：前浪頂/底、MA（方向決定 S/R）、填補裂口（趨勢線/通道 = 人手）
    輕微穿越 ≠ 突破；大幅/ sustained 穿越 → 突破，S/R 反轉
    """
    closes = [b["close"] for b in bars]
    c = closes[-1]
    s5 = sma(closes, 5)
    s10 = sma(closes, 10)
    s20 = sma(closes, 20)
    e20 = ema(closes, 20)

    highs, lows = find_swing_points(bars, 60)
    wave_top = highs[-1] if highs else find_resistance(bars, 60)
    wave_bot = lows[-1] if lows else find_swing_low(bars, 20)
    swing_20 = find_swing_low(bars, 20)
    resist_60 = find_resistance(bars, 60)
    gaps = find_gap_zones(bars)

    support_sources, broke_top, ma_bull = build_support_sources(
        bars, s5, s10, s20, e20, wave_top, wave_bot, swing_20, gaps, c, tf_label,
    )
    resistance_sources, _ = build_resistance_sources(
        bars, s5, s10, s20, e20, wave_top, wave_bot, resist_60, gaps, c, tf_label,
    )
    zone = cluster_support_zone(support_sources, c)
    retest_as_support = retest_holds_as_support(bars, wave_top)

    last = bars[-1]
    minor_cross = False
    if zone:
        minor_cross = minor_penetration_holds_support(last, zone["anchor"])
    if not minor_cross and ma_bull:
        for _, val in support_sources:
            if minor_penetration_holds_support(last, val):
                minor_cross = True
                break

    dist_support = (c - swing_20) / c if c else 0
    dist_resist = (resist_60 - c) / c if c else 0
    mid_range = (
        dist_support > 0.05 and dist_resist > 0.05
        and not retest_as_support
        and not (zone and zone["edge_count"] >= 2 and zone["dist_pct"] <= SR_CONFLUENCE_DIST_PCT)
    )
    extended = dist_support > 0.10 and not retest_as_support and not zone

    at_confluence = zone and zone["edge_count"] >= 2 and zone["dist_pct"] <= SR_CONFLUENCE_DIST_PCT
    in_zone = zone and zone["zone_lo"] <= c <= zone["zone_hi"]

    sr_pass = at_confluence or retest_as_support or (ma_bull and minor_cross)

    notes = []
    tf_note = f"[{tf_label}] " if tf_label else ""
    if at_confluence:
        src = "+".join(zone["sources"])
        notes.append(f"{tf_note}Multiple edge area ${zone['zone_lo']}-{zone['zone_hi']}（{zone['edge_count']}源:{src}）")
    elif retest_as_support:
        notes.append(f"{tf_note}突破前浪頂{wave_top:.2f}後回踩（阻力→支持）")
    elif in_zone and zone:
        notes.append(f"{tf_note}近{zone['sources'][0]}${zone['zone_lo']:.2f}")
    elif minor_cross:
        notes.append(f"{tf_note}輕微穿越，收市企穩，S&R仍有效")
    elif extended:
        notes.append(f"{tf_note}追價：距支撐{dist_support*100:.1f}%")
    elif mid_range:
        notes.append(f"{tf_note}中間位：支撐{swing_20:.2f} 阻力{resist_60:.2f}")
    else:
        notes.append(f"{tf_note}等支撐{swing_20:.2f}或破{resist_60:.2f}")

    if ma_bull:
        notes.append("MA向上=支持")
    elif s5[-1] < s10[-1] < s20[-1]:
        notes.append("MA向下=阻力")

    area_type = "confluence" if at_confluence else ("retest" if retest_as_support else ("mid_range" if mid_range else "wait"))
    trading_area = {
        "type": area_type,
        "zone_lo": zone["zone_lo"] if zone else swing_20,
        "zone_hi": zone["zone_hi"] if zone else swing_20,
        "edge_count": zone["edge_count"] if zone else 0,
        "sources": zone["sources"] if zone else [],
        "wave_top": round(wave_top, 2),
        "wave_bottom": round(wave_bot, 2),
        "tf": tf_label or None,
    }

    return {
        "pass": 1 if sr_pass else 0,
        "note": "；".join(notes),
        "swing_low": round(swing_20, 2),
        "resistance": round(resist_60, 2),
        "wave_top": round(wave_top, 2),
        "wave_bottom": round(wave_bot, 2),
        "trading_area": trading_area,
        "retest_as_support": retest_as_support,
        "support_sources": support_sources,
        "resistance_sources": resistance_sources,
        "broke_top": broke_top,
        "broke_bottom": sustained_break(bars, wave_bot, "down"),
        "tf": tf_label or None,
    }


def analyze_sr_short(bars: list[dict], tf_label: str = "") -> dict:
    """Short bias S&R：阻力匯聚、跌穿前浪底後反彈受阻（支持→阻力）。"""
    closes = [b["close"] for b in bars]
    c = closes[-1]
    s5 = sma(closes, 5)
    s10 = sma(closes, 10)
    s20 = sma(closes, 20)
    e20 = ema(closes, 20)

    highs, lows = find_swing_points(bars, 60)
    wave_top = highs[-1] if highs else find_resistance(bars, 60)
    wave_bot = lows[-1] if lows else find_swing_low(bars, 20)
    resist_60 = find_resistance(bars, 60)
    gaps = find_gap_zones(bars)

    resistance_sources, ma_bear = build_resistance_sources(
        bars, s5, s10, s20, e20, wave_top, wave_bot, resist_60, gaps, c, tf_label,
    )
    zone = cluster_resistance_zone(resistance_sources, c)
    retest_as_resistance = retest_holds_as_resistance(bars, wave_bot)

    last = bars[-1]
    minor_cross = False
    if zone:
        minor_cross = minor_penetration_holds_resistance(last, zone["anchor"])
    if not minor_cross and ma_bear:
        for _, val in resistance_sources:
            if minor_penetration_holds_resistance(last, val):
                minor_cross = True
                break

    dist_resist = (resist_60 - c) / c if c else 0
    dist_support = (c - wave_bot) / c if c else 0
    mid_range = dist_resist > 0.05 and dist_support > 0.05 and not retest_as_resistance
    extended = dist_resist > 0.10 and not retest_as_resistance and not zone

    at_confluence = zone and zone["edge_count"] >= 2 and zone["dist_pct"] <= SR_CONFLUENCE_DIST_PCT

    sr_pass = at_confluence or retest_as_resistance or (ma_bear and minor_cross)

    notes = []
    tf_note = f"[{tf_label}] " if tf_label else ""
    if at_confluence:
        src = "+".join(zone["sources"])
        notes.append(f"{tf_note}阻力匯聚 ${zone['zone_lo']}-{zone['zone_hi']}（{zone['edge_count']}源:{src}）")
    elif retest_as_resistance:
        notes.append(f"{tf_note}跌穿前浪底{wave_bot:.2f}後反彈受阻（支持→阻力）")
    elif zone:
        notes.append(f"{tf_note}近阻力{zone['sources'][0]}${zone['zone_hi']:.2f}")
    elif extended:
        notes.append(f"{tf_note}追空：距阻力{dist_resist*100:.1f}%")
    elif mid_range:
        notes.append(f"{tf_note}中間位")
    else:
        notes.append(f"{tf_note}等阻力{wave_top:.2f}或跌穿{wave_bot:.2f}")

    return {"pass": 1 if sr_pass else 0, "note": "；".join(notes), "tf": tf_label or None}


def merge_swing_sr(
    d1: dict,
    d1_bars: list[dict],
    w1: dict | None = None,
    w1_bars: list[dict] | None = None,
    h1: dict | None = None,
    h1_bars: list[dict] | None = None,
) -> dict:
    """
    Swing trade S&R: W1 > D1 > H1 priority for key levels.
    Re-cluster support at D1 price using merged multi-TF sources.
    """
    merged = dict(d1)
    price = d1["close"]
    tf_stack: list[tuple[str, dict, list[dict]]] = []
    for label, analysis, bars in (
        ("W1", w1, w1_bars),
        ("D1", d1, d1_bars),
        ("H1", h1, h1_bars),
    ):
        if analysis and bars:
            tf_stack.append((label, analysis, bars))

    if not tf_stack:
        return merged

    for label, analysis, _ in tf_stack:
        if analysis.get("wave_top"):
            merged["wave_top"] = analysis["wave_top"]
            merged["wave_top_tf"] = label
            break
    for label, analysis, _ in tf_stack:
        if analysis.get("wave_bottom"):
            merged["wave_bottom"] = analysis["wave_bottom"]
            merged["wave_bottom_tf"] = label
            break

    all_support: list[tuple[str, float]] = []
    all_resistance: list[tuple[str, float]] = []
    tf_wave_tops: dict[str, float] = {}
    tf_wave_bottoms: dict[str, float] = {}
    any_retest = False
    any_pass = False
    notes: list[str] = []
    for label, analysis, bars in tf_stack:
        sr_tf = analyze_sr(bars, tf_label=label)
        all_support.extend(sr_tf.get("support_sources") or [])
        all_resistance.extend(sr_tf.get("resistance_sources") or [])
        if analysis.get("wave_top"):
            tf_wave_tops[label] = analysis["wave_top"]
        if analysis.get("wave_bottom"):
            tf_wave_bottoms[label] = analysis["wave_bottom"]
        if sr_tf.get("retest_as_support"):
            any_retest = True
            notes.append(sr_tf["note"])
        if sr_tf.get("pass"):
            any_pass = True

    pivot_bands: list[dict] = []
    if w1_bars:
        pivot_bands = compute_pivot_sr_bands(w1_bars)
        merged["pivot_sr_bands"] = pivot_bands
        for band in pivot_bands:
            tag = f"W1:{band['label']}"
            if band["side"] == "support":
                all_support.append((tag, band["price"]))
            else:
                all_resistance.append((tag, band["price"]))
    else:
        merged["pivot_sr_bands"] = []

    zone = cluster_support_zone(all_support, price)
    d1_sr = analyze_sr(d1_bars, tf_label="D1")
    at_confluence = zone and zone["edge_count"] >= 2 and zone["dist_pct"] <= SR_CONFLUENCE_DIST_PCT
    near_pivot = nearest_pivot_band_at_price(pivot_bands, price)
    at_pivot_support = (
        near_pivot is not None
        and near_pivot.get("side") == "support"
        and price_near_pivot_zone(price, near_pivot)
    )
    pivot_sr_pass = at_pivot_support and near_pivot.get("touches", 0) >= SR_MIN_TOUCHES
    sr_pass = at_confluence or any_retest or d1_sr["pass"] or pivot_sr_pass

    primary_tf = merged.get("wave_bottom_tf") or "D1"
    if pivot_sr_pass and near_pivot:
        merge_note = (
            f"Swing S&R W1 {near_pivot['label']} "
            f"${near_pivot['zone_lo']:.2f}–${near_pivot['zone_hi']:.2f}"
        )
    elif at_confluence:
        src = "+".join(zone["sources"][:5])
        merge_note = f"Swing S&R（{primary_tf}主導）Multiple edge ${zone['zone_lo']}-{zone['zone_hi']}（{zone['edge_count']}源:{src}）"
    elif any_retest:
        merge_note = notes[0] if notes else d1_sr["note"]
    elif d1_sr["pass"]:
        merge_note = d1_sr["note"]
    else:
        merge_note = f"Swing S&R（{primary_tf}前浪底${merged.get('wave_bottom', d1.get('wave_bottom', 0)):.2f}）| {d1_sr['note']}"

    area = dict(d1.get("trading_area") or {})
    if pivot_sr_pass and near_pivot:
        area.update({
            "type": "pivot_sr",
            "zone_lo": near_pivot["zone_lo"],
            "zone_hi": near_pivot["zone_hi"],
            "edge_count": near_pivot.get("touches", SR_MIN_TOUCHES),
            "sources": [f"W1 {near_pivot['label']}"],
            "primary_tf": "W1",
        })
    elif zone and at_confluence:
        area.update({
            "type": "confluence",
            "zone_lo": zone["zone_lo"],
            "zone_hi": zone["zone_hi"],
            "edge_count": zone["edge_count"],
            "sources": zone["sources"],
            "primary_tf": primary_tf,
        })
    else:
        area["primary_tf"] = primary_tf
        area["wave_top"] = merged.get("wave_top")
        area["wave_bottom"] = merged.get("wave_bottom")

    merged["sr_pass"] = 1 if sr_pass else 0
    merged["sr_note"] = merge_note
    merged["trading_area"] = area
    merged["retest_as_support"] = any_retest or d1.get("retest_as_support", False)
    merged["sr_merged"] = True
    merged["sr_tf_priority"] = "W1>D1>H1"
    merged["all_support_sources"] = all_support
    merged["all_resistance_sources"] = all_resistance
    merged["tf_wave_tops"] = tf_wave_tops
    merged["tf_wave_bottoms"] = tf_wave_bottoms
    merged["broke_top"] = d1_sr.get("broke_top", False)
    merged["broke_bottom"] = d1_sr.get("broke_bottom", False)
    return merged


def analyze_momentum_trend(bars: list[dict]) -> dict:
    """
    J LAW 強勁動能 + 趨勢 (Edge #1) — 5 子項（無波幅）:
    升/跌兩邊都計，用於 long vs short 對比。
    """
    closes = [b["close"] for b in bars]
    c = closes[-1]
    s5 = sma(closes, 5)
    s10 = sma(closes, 10)
    s20 = sma(closes, 20)
    e20 = ema(closes, 20)

    # --- 4) MA 同向 (5/10/20，以 20MA 為界) ---
    ma_bull_aligned = s5[-1] > s10[-1] > s20[-1]
    ma_bear_aligned = s5[-1] < s10[-1] < s20[-1]
    ma20_slope_up = s20[-1] > s20[-6]
    ma20_slope_down = s20[-1] < s20[-6]
    n = len(bars)
    closes_below_s20 = sum(
        1 for i, b in enumerate(bars[-10:])
        if b["close"] < s20[n - min(10, n) + i]
    )
    ma20_respect_up = closes_below_s20 <= 1
    closes_above_s20 = sum(
        1 for i, b in enumerate(bars[-10:])
        if b["close"] > s20[n - min(10, n) + i]
    )
    ma20_respect_down = closes_above_s20 <= 1

    # --- 5) 浪型結構 ---
    highs, lows = find_swing_points(bars, 60)
    hh_hl = len(highs) >= 2 and highs[-1] > highs[-2] and len(lows) >= 2 and lows[-1] > lows[-2]
    ll_lh = len(highs) >= 2 and highs[-1] < highs[-2] and len(lows) >= 2 and lows[-1] < lows[-2]

    # --- 1) 升破前浪頂 / 跌破前浪底 + 動能 ---
    prior_top = max(b["high"] for b in bars[-60:-5]) if len(bars) > 10 else bars[-1]["high"]
    prior_bot = min(b["low"] for b in bars[-60:-5]) if len(bars) > 10 else bars[-1]["low"]
    avg_body = sum(candle_body_pct(b) for b in bars[-20:]) / 20
    last_body = candle_body_pct(bars[-1])
    avg_vol = sum(b["volume"] for b in bars[-20:]) / 20
    strong_candle = last_body > avg_body * 1.4 and bars[-1]["volume"] > avg_vol * 1.1

    broke_top = c > prior_top * 1.005 or max(b["high"] for b in bars[-3:]) > prior_top
    broke_bot = c < prior_bot * 0.995 or min(b["low"] for b in bars[-3:]) < prior_bot
    momentum_break_up = broke_top and strong_candle
    momentum_break_down = broke_bot and strong_candle

    big_count = sum(1 for b in bars[-10:] if candle_body_pct(b) > avg_body * 1.3)
    frequent_big = big_count >= 3

    vol_last = bars[-1]["volume"]
    vol_last5 = sum(b["volume"] for b in bars[-5:]) / 5
    big_vol_count = sum(
        1 for b in bars[-10:]
        if candle_body_pct(b) > avg_body * 1.2 and b["volume"] > avg_vol * 1.2
    )
    large_volume = (
        vol_last >= avg_vol * 1.25
        or vol_last5 >= avg_vol * 1.15
        or big_vol_count >= 2
    )
    vol_ratio = vol_last / avg_vol if avg_vol else 0

    # --- 升勢動能綜合 (long system) ---
    bull_checks = {
        "ma_aligned": ma_bull_aligned and ma20_slope_up,
        "ma20_respect": ma20_respect_up and c > s20[-1],
        "wave_structure": hh_hl or momentum_break_up,
        "big_candles": frequent_big,
        "large_volume": large_volume,
    }
    bull_score = sum(bull_checks.values())

    bear_checks = {
        "ma_aligned": ma_bear_aligned and ma20_slope_down,
        "ma20_respect": ma20_respect_down and c < s20[-1],
        "wave_structure": ll_lh or momentum_break_down,
        "big_candles": frequent_big,
        "large_volume": large_volume,
    }
    bear_score = sum(bear_checks.values())

    momentum_pass = bull_score >= 4 and bull_checks["ma_aligned"] and bull_checks["large_volume"]
    bear_pass = bear_score >= 4 and bear_checks["ma_aligned"] and bear_checks["large_volume"]
    trend_dir = "升勢" if bull_score >= bear_score else "跌勢"

    notes = []
    if bull_checks["ma_aligned"]:
        notes.append(f"5/10/20MA 同向向上({s5[-1]:.2f}/{s10[-1]:.2f}/{s20[-1]:.2f})")
    else:
        notes.append("均線未完全同向")
    if bull_checks["wave_structure"]:
        notes.append("一浪高於一浪" if hh_hl else f"升破前浪頂{prior_top:.2f}")
    else:
        notes.append("未見明確升浪結構")
    if bull_checks["ma20_respect"]:
        notes.append("少跌穿20MA")
    if frequent_big:
        notes.append(f"大K線頻繁({big_count}/10)")
    if large_volume:
        notes.append(f"大成交量({vol_ratio:.1f}x均量)")
    else:
        notes.append("成交量未配合")

    return {
        "pass": momentum_pass,
        "bear_pass": bear_pass,
        "note": "；".join(notes),
        "bear_note": f"跌勢{bear_score}/5" + ("✓" if bear_pass else ""),
        "trend_dir": trend_dir,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "sma5": round(s5[-1], 2),
        "sma10": round(s10[-1], 2),
        "sma20": round(s20[-1], 2),
        "ema20": round(e20[-1], 2),
        "prior_wave_top": round(prior_top, 2),
        "prior_wave_bottom": round(prior_bot, 2),
    }


def wick_metrics(bar: dict) -> dict | None:
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    return {
        "range": rng,
        "body": body,
        "upper": h - max(o, c),
        "lower": min(o, c) - l,
        "bullish": c >= o,
    }


def is_bullish_reversal_pin(bar: dict) -> bool:
    """Reversal：長下影線，收市回到上方（Pin Bar / Hammer）。"""
    m = wick_metrics(bar)
    if not m:
        return False
    return (
        m["lower"] >= max(m["body"] * 2, m["range"] * 0.5)
        and m["upper"] <= m["range"] * 0.3
    )


def is_bearish_reversal_pin(bar: dict) -> bool:
    """Reversal：長上影線，收市跌回下方（Shooting Star）。"""
    m = wick_metrics(bar)
    if not m:
        return False
    return (
        m["upper"] >= max(m["body"] * 2, m["range"] * 0.5)
        and m["lower"] <= m["range"] * 0.3
    )


def is_bullish_engulfing(bars: list[dict], idx: int) -> bool:
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if prev["close"] >= prev["open"] or curr["close"] <= curr["open"]:
        return False
    return curr["open"] <= prev["close"] and curr["close"] >= prev["open"]


def is_bearish_engulfing(bars: list[dict], idx: int) -> bool:
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if prev["close"] <= prev["open"] or curr["close"] >= curr["open"]:
        return False
    return curr["open"] >= prev["close"] and curr["close"] <= prev["open"]


def body_fully_engulfs(prev: dict, curr: dict) -> bool:
    """吞噬：當日 body 完全包住前一日 body，且幅度更大。"""
    prev_lo = min(prev["open"], prev["close"])
    prev_hi = max(prev["open"], prev["close"])
    curr_lo = min(curr["open"], curr["close"])
    curr_hi = max(curr["open"], curr["close"])
    prev_body = prev_hi - prev_lo
    curr_body = curr_hi - curr_lo
    if prev_body <= 0 or curr_body <= prev_body * 1.05:
        return False
    return curr_lo <= prev_lo and curr_hi >= prev_hi


def is_bullish_engulfing_bar(bars: list[dict], idx: int) -> bool:
    """Bullish Engulfing Bar：吞噬 + 收市升穿前日最高價。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] <= curr["open"]:
        return False
    if not body_fully_engulfs(prev, curr):
        return False
    if curr["close"] < prev["high"] * 0.997:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def is_bearish_engulfing_bar(bars: list[dict], idx: int) -> bool:
    """Bearish Engulfing Bar：吞噬 + 收市跌穿前日最低價。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] >= curr["open"]:
        return False
    if not body_fully_engulfs(prev, curr):
        return False
    if curr["close"] > prev["low"] * 1.003:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def is_pojiao_chuantou(prev: dict, curr: dict) -> bool:
    """破腳穿頭：當日 high/low 完全包住前日 high/low。"""
    return curr["high"] > prev["high"] and curr["low"] < prev["low"]


def is_bullish_engulfing_pojiao(bars: list[dict], idx: int) -> bool:
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] <= curr["open"] or not is_pojiao_chuantou(prev, curr):
        return False
    if curr["close"] < prev["high"] * 0.997:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def is_bearish_engulfing_pojiao(bars: list[dict], idx: int) -> bool:
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] >= curr["open"] or not is_pojiao_chuantou(prev, curr):
        return False
    if curr["close"] > prev["low"] * 1.003:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def is_bullish_engulfing_single(bars: list[dict], idx: int) -> bool:
    """單 K 等價吞噬：未必見到兩根 body 全包，但洗盤+收市突破前日 high，意圖同 Bullish Engulfing。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] <= curr["open"]:
        return False
    if body_fully_engulfs(prev, curr) or is_pojiao_chuantou(prev, curr):
        return False
    if curr["close"] < prev["high"] * 0.997:
        return False
    avg_b = avg_body_size(bars, idx)
    if avg_b <= 0 or candle_body_pct(curr) < avg_b * 1.5:
        return False
    prev_lo = min(prev["open"], prev["close"])
    shakeout = curr["low"] < prev_lo or curr["low"] < prev["low"] * 1.002
    if not shakeout:
        return False
    if curr["open"] > prev["close"] * 1.012:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.4)


def is_bearish_engulfing_single(bars: list[dict], idx: int) -> bool:
    """單 K 等價吞噬（空）：洗盤上影 + 收市跌穿前日 low。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] >= curr["open"]:
        return False
    if body_fully_engulfs(prev, curr) or is_pojiao_chuantou(prev, curr):
        return False
    if curr["close"] > prev["low"] * 1.003:
        return False
    avg_b = avg_body_size(bars, idx)
    if avg_b <= 0 or candle_body_pct(curr) < avg_b * 1.5:
        return False
    prev_hi = max(prev["open"], prev["close"])
    shakeout = curr["high"] > prev_hi or curr["high"] > prev["high"] * 0.998
    if not shakeout:
        return False
    if curr["open"] < prev["close"] * 0.988:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.4)


def is_bullish_engulfing_two_day(bars: list[dict], idx: int) -> bool:
    """兩日反映吞噬：前 K 小陰 + 當日大陽，行為同吞噬但未必見到 body 全包。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] <= curr["open"] or prev["close"] >= prev["open"]:
        return False
    if body_fully_engulfs(prev, curr):
        return False
    if curr["close"] < prev["high"] * 0.997 or curr["close"] <= prev["open"]:
        return False
    if curr["open"] > prev["close"] * 1.008:
        return False
    avg_b = avg_body_size(bars, idx)
    prev_body = candle_body_pct(prev)
    curr_body = candle_body_pct(curr)
    if avg_b <= 0 or curr_body < avg_b * 1.5:
        return False
    if prev_body > 0 and curr_body < prev_body * 1.6:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def is_bearish_engulfing_two_day(bars: list[dict], idx: int) -> bool:
    """兩日反映吞噬（空）：前 K 小陽 + 當日大陰。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] >= curr["open"] or prev["close"] <= prev["open"]:
        return False
    if body_fully_engulfs(prev, curr):
        return False
    if curr["close"] > prev["low"] * 1.003 or curr["close"] >= prev["open"]:
        return False
    if curr["open"] < prev["close"] * 0.992:
        return False
    avg_b = avg_body_size(bars, idx)
    prev_body = candle_body_pct(prev)
    curr_body = candle_body_pct(curr)
    if avg_b <= 0 or curr_body < avg_b * 1.5:
        return False
    if prev_body > 0 and curr_body < prev_body * 1.6:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def detect_bullish_engulfing(bars: list[dict], idx: int) -> tuple[bool, str]:
    if is_bullish_engulfing_bar(bars, idx):
        return True, "Bullish Engulfing Bar"
    if is_bullish_engulfing_pojiao(bars, idx):
        return True, "破腳穿頭"
    if is_bullish_engulfing_single(bars, idx):
        return True, "單K等價吞噬"
    if is_bullish_engulfing_two_day(bars, idx):
        return True, "兩日反映"
    return False, ""


def detect_bearish_engulfing(bars: list[dict], idx: int) -> tuple[bool, str]:
    if is_bearish_engulfing_bar(bars, idx):
        return True, "Bearish Engulfing Bar"
    if is_bearish_engulfing_pojiao(bars, idx):
        return True, "破腳穿頭"
    if is_bearish_engulfing_single(bars, idx):
        return True, "單K等價吞噬"
    if is_bearish_engulfing_two_day(bars, idx):
        return True, "兩日反映"
    return False, ""


def minor_ma_cross_only(bar: dict, ma: float) -> bool:
    """輕微微穿越 MA：低點穿過但收市企穩 → 唔算做錯。"""
    if ma <= 0:
        return False
    return bar["low"] < ma * 0.997 and bar["close"] >= ma * 0.995


def engulfing_at_key_level_bull(bar: dict, sr: dict, mom: dict, tol: float = 0.04) -> tuple[bool, str]:
    wave_top = sr.get("wave_top", 0)
    c, lo = bar["close"], bar["low"]
    if sr.get("retest_as_support"):
        return True, f"前浪頂{wave_top:.2f}回踩支持"
    if wave_top and (near_price_level(lo, wave_top, tol) or near_price_level(c, wave_top, tol)):
        return True, f"關鍵水平${wave_top:.2f}"
    if at_key_support(bar, sr, mom, tol):
        return True, "Multiple edge 支持區"
    if wave_top and c > wave_top * 1.003:
        return True, f"突破前浪頂{wave_top:.2f}"
    return False, ""


def engulfing_at_key_level_bear(bar: dict, sr: dict, mom: dict, tol: float = 0.04) -> tuple[bool, str]:
    wave_bot = sr.get("wave_bottom", 0)
    wave_top = sr.get("wave_top", 0)
    c, hi = bar["close"], bar["high"]
    if wave_top and near_price_level(hi, wave_top, tol):
        return True, f"前浪頂阻力${wave_top:.2f}"
    if at_key_resistance(bar, sr, mom, tol):
        return True, "阻力匯聚區"
    if wave_bot and c < wave_bot * 0.997:
        return True, f"跌穿前浪底{wave_bot:.2f}"
    return False, ""


def classify_engulfing_bull(bars: list[dict], idx: int, sr: dict, mom: dict, level_hint: str, subtype: str = "") -> str:
    prev = bars[idx - 1]
    bar = bars[idx]
    parts = [p for p in (subtype, level_hint) if p]
    if prev["close"] < prev["open"]:
        parts.append("Shake Out")
    if subtype == "單K等價吞噬":
        parts.append("洗盤後Reversal")
    elif subtype == "兩日反映":
        parts.append("分兩日反映意圖")
    wave_top = sr.get("wave_top", 0)
    if wave_top and bar["close"] > wave_top * 1.005:
        parts.append("180°反轉突破")
    elif sr.get("retest_as_support"):
        parts.append("可加注回踩")
    ma10 = mom.get("sma10", 0)
    if minor_ma_cross_only(bar, ma10):
        parts.append("輕微穿越10MA無碍")
    return "；".join(parts)


def classify_engulfing_bear(bars: list[dict], idx: int, sr: dict, mom: dict, level_hint: str, subtype: str = "") -> str:
    prev = bars[idx - 1]
    bar = bars[idx]
    parts = [p for p in (subtype, level_hint) if p]
    if prev["close"] > prev["open"]:
        parts.append("Shake Out")
    if subtype == "單K等價吞噬":
        parts.append("洗盤後Reversal")
    elif subtype == "兩日反映":
        parts.append("分兩日反映意圖")
    ma10 = mom.get("sma10", 0)
    if minor_ma_cross_only(bar, ma10):
        parts.append("輕微穿越10MA無碍")
    if bar["close"] < sr.get("wave_bottom", bar["close"]) * 0.995:
        parts.append("大戶推低")
    return "；".join(parts)


def engulfing_bull_fail_reverse(bars: list[dict], idx: int) -> dict:
    """陽吞噬失敗：跌穿吞噬日開盤 → Short 反向。"""
    eng_open = bars[idx]["open"]
    after = bars[idx + 1 :]
    if not after:
        return {"pass": False, "reason": ""}
    if any(b["close"] < eng_open for b in after[:3]) or bars[-1]["close"] < eng_open:
        return {
            "pass": True,
            "reason": f"陽吞噬失敗跌穿開盤({eng_open:.2f})→考慮Short",
            "level": eng_open,
        }
    return {"pass": False, "reason": ""}


def engulfing_bear_fail_reverse(bars: list[dict], idx: int) -> dict:
    """陰吞噬失敗：升穿吞噬日開盤 → Long 反向。"""
    eng_open = bars[idx]["open"]
    after = bars[idx + 1 :]
    if not after:
        return {"pass": False, "reason": ""}
    if any(b["close"] > eng_open for b in after[:3]) or bars[-1]["close"] > eng_open:
        return {
            "pass": True,
            "reason": f"陰吞噬失敗升穿開盤({eng_open:.2f})→考慮Long",
            "level": eng_open,
        }
    return {"pass": False, "reason": ""}


def scan_engulfing_patterns(
    bars: list[dict], sr: dict, mom: dict, lookback: int = 10,
) -> dict:
    """掃描 Bullish/Bearish Engulfing Bar @ 關鍵水平 + 失敗反向。"""
    start = max(1, len(bars) - lookback)
    bull_eng = None
    bear_eng = None
    bull_fail_rev = None
    bear_fail_rev = None
    avg_vol = sum(b["volume"] for b in bars[-20:]) / 20

    for i in range(start, len(bars)):
        bar = bars[i]
        vol_note = "；放量" if bar["volume"] >= avg_vol * 1.15 else ""

        bull_ok, bull_sub = detect_bullish_engulfing(bars, i)
        if bull_ok:
            ok, hint = engulfing_at_key_level_bull(bar, sr, mom)
            if ok:
                reason = classify_engulfing_bull(bars, i, sr, mom, hint, bull_sub) + vol_note
                bull_eng = {"idx": i, "reason": reason, "level": round(bar["open"], 2), "subtype": bull_sub}
            rev = engulfing_bull_fail_reverse(bars, i)
            if rev["pass"]:
                bear_fail_rev = {"idx": i, "reason": rev["reason"], "level": rev["level"]}

        bear_ok, bear_sub = detect_bearish_engulfing(bars, i)
        if bear_ok:
            ok, hint = engulfing_at_key_level_bear(bar, sr, mom)
            if ok:
                reason = classify_engulfing_bear(bars, i, sr, mom, hint, bear_sub) + vol_note
                bear_eng = {"idx": i, "reason": reason, "level": round(bar["open"], 2), "subtype": bear_sub}
            rev = engulfing_bear_fail_reverse(bars, i)
            if rev["pass"]:
                bull_fail_rev = {"idx": i, "reason": rev["reason"], "level": rev["level"]}

    # 加注：現價回到早前吞噬 K 水平
    if bull_eng is None:
        for i in range(max(1, len(bars) - 20), len(bars) - 1):
            bull_ok, _ = detect_bullish_engulfing(bars, i)
            if not bull_ok:
                continue
            ok, hint = engulfing_at_key_level_bull(bars[i], sr, mom)
            if not ok:
                continue
            level = (bars[i]["open"] + bars[i]["close"]) / 2
            c = bars[-1]["close"]
            if abs(c - level) / c <= 0.03 and c >= level * 0.99:
                bull_eng = {
                    "idx": i,
                    "reason": f"回到吞噬位${level:.2f}可加注（{hint}）",
                    "level": round(level, 2),
                }
                break

    return {
        "bull_eng": bull_eng,
        "bear_eng": bear_eng,
        "bull_fail_rev": bull_fail_rev,
        "bear_fail_rev": bear_fail_rev,
    }


def is_bullish_screw_over_bar(bars: list[dict], idx: int) -> bool:
    """Bullish 愚弄：前K陰線Trap + 收市升穿前K最高價（以收市價作準）。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] <= curr["open"] or prev["close"] >= prev["open"]:
        return False
    if curr["close"] <= prev["high"] * 0.997:
        return False
    avg_b = avg_body_size(bars, idx)
    if avg_b <= 0 or candle_body_pct(curr) < avg_b * 1.2:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def is_bearish_screw_over_bar(bars: list[dict], idx: int) -> bool:
    """Bearish 愚弄：前K陽線Trap + 收市跌穿前K最低價。"""
    if idx < 1:
        return False
    prev, curr = bars[idx - 1], bars[idx]
    if curr["close"] >= curr["open"] or prev["close"] <= prev["open"]:
        return False
    if curr["close"] >= prev["low"] * 1.003:
        return False
    avg_b = avg_body_size(bars, idx)
    if avg_b <= 0 or candle_body_pct(curr) < avg_b * 1.2:
        return False
    m = wick_metrics(curr)
    return bool(m and m["body"] >= m["range"] * 0.35)


def screw_over_at_key_level_bull(bar: dict, trap: dict, sr: dict, mom: dict, tol: float = 0.04) -> tuple[bool, str]:
    if at_key_support(bar, sr, mom, tol):
        return True, "Multiple edge 支持區"
    wave_top = sr.get("wave_top", 0)
    if sr.get("retest_as_support"):
        return True, f"前浪頂{wave_top:.2f}回踩"
    if wave_top and bar["close"] > wave_top * 1.003:
        return True, f"突破前浪頂{wave_top:.2f}"
    ma10 = mom.get("sma10", 0)
    if ma10 and trap["low"] < ma10 * 0.997 and bar["close"] >= ma10 * 0.995:
        return True, "Trap後企穩10MA"
    if near_price_level(trap["low"], sr.get("wave_bottom", 0), tol):
        return True, "前浪底/support Trap"
    return False, ""


def screw_over_at_key_level_bear(bar: dict, trap: dict, sr: dict, mom: dict, tol: float = 0.04) -> tuple[bool, str]:
    if at_key_resistance(bar, sr, mom, tol):
        return True, "阻力匯聚區"
    wave_top = sr.get("wave_top", 0)
    if wave_top and near_price_level(trap["high"], wave_top, tol):
        return True, f"前浪頂阻力${wave_top:.2f}"
    ma10 = mom.get("sma10", 0)
    if ma10 and trap["high"] > ma10 * 1.003 and bar["close"] <= ma10 * 1.005:
        return True, "Trap後跌穿10MA"
    return False, ""


def classify_screw_over_bull(bars: list[dict], idx: int, sr: dict, mom: dict, hint: str) -> str:
    prev, curr = bars[idx - 1], bars[idx]
    parts = ["反轉再反轉", hint]
    move = (curr["close"] - prev["high"]) / prev["high"] * 100 if prev["high"] else 0
    if move >= 1.5:
        parts.append("180°反向")
    parts.append("Trap沽空者")
    if sr.get("retest_as_support"):
        parts.append("收市價確認")
    return "；".join(parts)


def classify_screw_over_bear(bars: list[dict], idx: int, sr: dict, mom: dict, hint: str) -> str:
    prev, curr = bars[idx - 1], bars[idx]
    parts = ["反轉再反轉", hint]
    move = (prev["low"] - curr["close"]) / prev["low"] * 100 if prev["low"] else 0
    if move >= 1.5:
        parts.append("180°反向")
    parts.append("Trap追多者")
    return "；".join(parts)


def screw_over_bull_fail_reverse(bars: list[dict], idx: int) -> dict:
    """Bullish 愚弄失敗：跌穿 Trap K 最低價 → Short。"""
    trap_low = bars[idx - 1]["low"]
    after = bars[idx + 1 :]
    if not after:
        return {"pass": False, "reason": ""}
    if any(b["close"] < trap_low for b in after[:3]) or bars[-1]["close"] < trap_low:
        return {
            "pass": True,
            "reason": f"愚弄失敗跌穿Trap低({trap_low:.2f})→考慮Short",
            "level": trap_low,
        }
    return {"pass": False, "reason": ""}


def screw_over_bear_fail_reverse(bars: list[dict], idx: int) -> dict:
    """Bearish 愚弄失敗：升穿 Trap K 最高價 → Long。"""
    trap_high = bars[idx - 1]["high"]
    after = bars[idx + 1 :]
    if not after:
        return {"pass": False, "reason": ""}
    if any(b["close"] > trap_high for b in after[:3]) or bars[-1]["close"] > trap_high:
        return {
            "pass": True,
            "reason": f"愚弄失敗升穿Trap高({trap_high:.2f})→考慮Long",
            "level": trap_high,
        }
    return {"pass": False, "reason": ""}


def scan_screw_over_patterns(
    bars: list[dict], sr: dict, mom: dict, lookback: int = 10,
) -> dict:
    """掃描 Screw-over 愚弄 / 反轉再反轉 @ 關鍵水平 + 失敗反向。"""
    start = max(1, len(bars) - lookback)
    bull_screw = None
    bear_screw = None
    bull_fail_rev = None
    bear_fail_rev = None
    avg_vol = sum(b["volume"] for b in bars[-20:]) / 20

    for i in range(start, len(bars)):
        bar = bars[i]
        trap = bars[i - 1]
        vol_note = "；放量" if bar["volume"] >= avg_vol * 1.15 else ""

        if is_bullish_screw_over_bar(bars, i):
            ok, hint = screw_over_at_key_level_bull(bar, trap, sr, mom)
            if ok:
                reason = classify_screw_over_bull(bars, i, sr, mom, hint) + vol_note
                bull_screw = {"idx": i, "reason": reason, "level": round(trap["low"], 2)}
            rev = screw_over_bull_fail_reverse(bars, i)
            if rev["pass"]:
                bear_fail_rev = {"idx": i, "reason": rev["reason"], "level": rev["level"]}

        if is_bearish_screw_over_bar(bars, i):
            ok, hint = screw_over_at_key_level_bear(bar, trap, sr, mom)
            if ok:
                reason = classify_screw_over_bear(bars, i, sr, mom, hint) + vol_note
                bear_screw = {"idx": i, "reason": reason, "level": round(trap["high"], 2)}
            rev = screw_over_bear_fail_reverse(bars, i)
            if rev["pass"]:
                bull_fail_rev = {"idx": i, "reason": rev["reason"], "level": rev["level"]}

    return {
        "bull_screw": bull_screw,
        "bear_screw": bear_screw,
        "bull_fail_rev": bull_fail_rev,
        "bear_fail_rev": bear_fail_rev,
    }


def near_price_level(price: float, level: float, tol_pct: float = 0.035) -> bool:
    return level > 0 and abs(price - level) / price <= tol_pct


def at_key_support(bar: dict, sr: dict, mom: dict, tol: float = 0.035) -> bool:
    """Reversal 必須喺正確價位（支持區），錯位唔算 edge。"""
    c, lo = bar["close"], bar["low"]
    area = sr.get("trading_area") or {}
    levels = [
        sr.get("wave_bottom", 0),
        sr.get("swing_low", 0),
        mom.get("sma20", 0),
        mom.get("sma10", 0),
        mom.get("ema20", 0),
    ]
    if area.get("zone_lo"):
        levels.extend([area["zone_lo"], area["zone_hi"], (area["zone_lo"] + area["zone_hi"]) / 2])
    if any(near_price_level(lo, lv, tol) or near_price_level(c, lv, tol) for lv in levels if lv):
        return True
    return bool(sr.get("retest_as_support")) or area.get("type") in ("confluence", "retest")


def at_key_resistance(bar: dict, sr: dict, mom: dict, tol: float = 0.035) -> bool:
    c, hi = bar["close"], bar["high"]
    area = sr.get("trading_area") or {}
    levels = [
        sr.get("wave_top", 0),
        sr.get("resistance", 0),
        mom.get("sma20", 0),
        mom.get("sma10", 0),
        mom.get("ema20", 0),
    ]
    return any(near_price_level(hi, lv, tol) or near_price_level(c, lv, tol) for lv in levels if lv)


def prior_pullback(bars: list[dict]) -> bool:
    """前文：跌了一段 / 拉回，先至有 bullish reversal 意義。"""
    closes = [b["close"] for b in bars]
    reds = sum(1 for b in bars[-5:] if b["close"] < b["open"])
    peak = max(closes[-10:])
    drop = (peak - closes[-1]) / closes[-1] if closes[-1] else 0
    return reds >= 2 or drop >= 0.03 or closes[-1] < closes[-6]


def prior_rally(bars: list[dict]) -> bool:
    closes = [b["close"] for b in bars]
    greens = sum(1 for b in bars[-5:] if b["close"] > b["open"])
    trough = min(closes[-10:])
    rise = (closes[-1] - trough) / trough if trough else 0
    return greens >= 2 or rise >= 0.03 or closes[-1] > closes[-6]


def ma_slope_pct(values: list[float], lookback: int = 10) -> float:
    if len(values) <= lookback:
        return 0.0
    old = values[-lookback - 1]
    if not old:
        return 0.0
    return (values[-1] - old) / old


def is_bearish_outside_bar(prev: dict, curr: dict) -> bool:
    """Outside Bar 空：高低完全包住前K + 陰線收市。"""
    return is_pojiao_chuantou(prev, curr) and curr["close"] < curr["open"]


def is_bullish_outside_bar(prev: dict, curr: dict) -> bool:
    return is_pojiao_chuantou(prev, curr) and curr["close"] > curr["open"]


def assess_csr_background(bars: list[dict], sr: dict, mom: dict) -> dict:
    """
    CSR 大前提 — 同形態、不同背景 → 解讀完全不同。
    Natural Pullback：短中期 MA 向上、長期 MA 趨平/向上、Support Area 保持。
    """
    closes = [b["close"] for b in bars]
    s5 = sma(closes, 5)
    s10 = sma(closes, 10)
    s20 = sma(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200) if len(closes) >= 200 else e50

    mid_mas_up = s5[-1] > s10[-1] and (s10[-1] >= s20[-1] * 0.998 or s20[-1] > s20[-6])
    mid_mas_down = s5[-1] < s10[-1] < s20[-1] and s20[-1] < s20[-6]

    slope200 = ma_slope_pct(e200, min(20, len(e200) - 2))
    long_ma_flat = abs(slope200) <= 0.015
    long_ma_up = slope200 > 0.005
    long_ma_down = slope200 < -0.015
    long_ma_not_bearish = not long_ma_down

    ta = sr.get("trading_area") or {}
    last = bars[-1]
    at_sup = at_key_support(last, sr, mom)
    support_area = ta.get("edge_count", 0) >= 1 and ta.get("type") in ("confluence", "retest", "wait")
    wave_bot = sr.get("wave_bottom", 0)
    area_holds = wave_bot <= 0 or last["close"] >= wave_bot * 0.97

    natural_pullback = (
        prior_pullback(bars)
        and mid_mas_up
        and long_ma_not_bearish
        and (at_sup or support_area)
        and area_holds
    )

    natural_rally = (
        prior_rally(bars)
        and mid_mas_down
        and (at_key_resistance(last, sr, mom) or last["close"] >= sr.get("wave_top", 0) * 0.98)
    )

    notes = []
    if natural_pullback:
        long200 = "200EMA趨平" if long_ma_flat else ("200EMA向上" if long_ma_up else "200EMA未向下")
        notes.append(f"Natural Pullback（短中期MA向上、{long200}、Support Area保持）")
    elif at_sup and prior_pullback(bars) and (mid_mas_down or long_ma_down):
        notes.append("同形態但背景唔同：中期/長期MA向下→唔係Natural Pullback")
    if natural_rally:
        notes.append("Natural Rally（中期MA向下@阻力）")

    return {
        "natural_pullback": natural_pullback,
        "natural_rally": natural_rally,
        "mid_mas_up": mid_mas_up,
        "mid_mas_down": mid_mas_down,
        "long_ma_flat": long_ma_flat,
        "long_ma_not_bearish": long_ma_not_bearish,
        "long_ma_down": long_ma_down,
        "support_area_holds": (at_sup or support_area) and area_holds,
        "background_note": "；".join(notes),
        "ema200": round(e200[-1], 2) if len(closes) >= 50 else None,
    }


def scan_gap_outside_reversal(
    bars: list[dict], sr: dict, mom: dict, lookback: int = 15,
) -> dict | None:
    """
    Gap + Outside Bar 裂口反轉 @ 前浪頂/阻力：
    向上裂口後 1–2 日內出現陰線 Outside Bar → Short。
    """
    wave_top = sr.get("wave_top", 0)
    start = max(2, len(bars) - lookback)
    best = None
    for i in range(start, len(bars)):
        if not is_gap_up_bar(bars[i - 1], bars[i]):
            continue
        gap_bar = bars[i]
        near_top = wave_top and gap_bar["low"] >= wave_top * 0.975
        at_res = at_key_resistance(gap_bar, sr, mom)
        if not near_top and not at_res:
            continue
        for j in range(i, min(i + 3, len(bars))):
            if j < 1:
                continue
            if not is_bearish_outside_bar(bars[j - 1], bars[j]):
                continue
            ob = bars[j]
            if ob["close"] < gap_bar["open"]:
                hint = f"前浪頂${wave_top:.2f}" if near_top else "阻力區"
                entry = {
                    "idx": j,
                    "reason": f"Gap+Outside Bar 裂口反轉@{hint}（跌穿裂口開盤{gap_bar['open']:.2f}）",
                }
                if best is None or j >= best["idx"]:
                    best = entry
    return best


def scan_reversal_patterns(bars: list[dict], lookback: int = 4) -> dict:
    """掃描最近 1–4 根 K 嘅 Reversal 形態。"""
    start = max(1, len(bars) - lookback)
    bull_pin, bear_pin, bull_eng, bear_eng = False, False, False, False
    bull_idx, bear_idx = -1, -1
    for i in range(start, len(bars)):
        if is_bullish_reversal_pin(bars[i]):
            bull_pin, bull_idx = True, i
        if is_bearish_reversal_pin(bars[i]):
            bear_pin, bear_idx = True, i
        if is_bullish_engulfing(bars, i):
            bull_eng, bull_idx = True, i
        if is_bearish_engulfing(bars, i):
            bear_eng, bear_idx = True, i
    return {
        "bull_pin": bull_pin,
        "bear_pin": bear_pin,
        "bull_engulf": bull_eng,
        "bear_engulf": bear_eng,
        "bull_idx": bull_idx,
        "bear_idx": bear_idx,
    }


def is_gap_up_bar(prev: dict, curr: dict, min_pct: float = 0.005) -> bool:
    if prev["high"] <= 0:
        return False
    return curr["low"] > prev["high"] and (curr["low"] - prev["high"]) / prev["high"] >= min_pct


def is_gap_down_bar(prev: dict, curr: dict, min_pct: float = 0.005) -> bool:
    if prev["low"] <= 0:
        return False
    return curr["high"] < prev["low"] and (prev["low"] - curr["high"]) / prev["low"] >= min_pct


def is_high_gap_candle(bar: dict, prev: dict) -> bool:
    """裂口高開：開高低收近乎相同，極高開。"""
    m = wick_metrics(bar)
    if not m or prev["close"] <= 0:
        return False
    gap_open = (bar["open"] - prev["close"]) / prev["close"] >= 0.008
    tiny_body = m["body"] <= m["range"] * 0.3
    closes_high = bar["close"] >= bar["high"] - m["range"] * 0.2
    return gap_open and tiny_body and closes_high and bar["close"] >= bar["open"]


def is_low_gap_candle(bar: dict, prev: dict) -> bool:
    """裂口低開：開高低收近乎相同，極低開。"""
    m = wick_metrics(bar)
    if not m or prev["close"] <= 0:
        return False
    gap_open = (prev["close"] - bar["open"]) / prev["close"] >= 0.008
    tiny_body = m["body"] <= m["range"] * 0.3
    closes_low = bar["close"] <= bar["low"] + m["range"] * 0.2
    return gap_open and tiny_body and closes_low and bar["close"] <= bar["open"]


def evaluate_gap_up(bars: list[dict], idx: int) -> dict:
    """向上裂口：要睇之後 1–3 日跟進性；跌穿裂口日開盤 = 失敗。"""
    gap_bar = bars[idx]
    gap_open = gap_bar["open"]
    after = bars[idx + 1 :]
    if not after:
        return {"verdict": "pending", "reason": "等翌日確認跟進性"}
    check = after[:3]
    if any(b["close"] < gap_open for b in check):
        return {"verdict": "failed", "reason": "跌穿裂口日開盤價", "gap_open": gap_open}
    d1 = after[0]
    if d1["close"] < d1["open"] and d1["close"] < gap_bar["close"] * 0.98:
        return {"verdict": "failed", "reason": "翌日大陰燭（應跟進上升）", "gap_open": gap_open}
    follow = any(b["close"] >= gap_bar["close"] * 0.995 for b in check)
    follow = follow or (d1["close"] > d1["open"] and d1["close"] >= gap_open)
    if len(check) >= 2:
        follow = follow or check[1]["close"] > check[0]["close"]
    if follow and bars[-1]["close"] >= gap_open:
        return {"verdict": "success", "reason": "裂口跟進確認", "gap_open": gap_open}
    if len(check) >= 3:
        return {"verdict": "failed", "reason": "無跟進性", "gap_open": gap_open}
    return {"verdict": "pending", "reason": "等2-3日確認", "gap_open": gap_open}


def evaluate_gap_down(bars: list[dict], idx: int) -> dict:
    """向下裂口：應繼續下跌；升穿裂口日開盤 = 失敗。"""
    gap_bar = bars[idx]
    gap_open = gap_bar["open"]
    after = bars[idx + 1 :]
    if not after:
        return {"verdict": "pending", "reason": "等翌日確認跟進性"}
    check = after[:3]
    if any(b["close"] > gap_open for b in check):
        return {"verdict": "failed", "reason": "升穿裂口日開盤價", "gap_open": gap_open}
    d1 = after[0]
    if d1["close"] > d1["open"] and d1["close"] > gap_bar["close"] * 1.02:
        return {"verdict": "failed", "reason": "翌日大陽燭（應跟進下跌）", "gap_open": gap_open}
    follow = any(b["close"] <= gap_bar["close"] * 1.005 for b in check)
    follow = follow or (d1["close"] < d1["open"] and d1["close"] <= gap_open)
    if len(check) >= 2:
        follow = follow or check[1]["close"] < check[0]["close"]
    if follow and bars[-1]["close"] <= gap_open:
        return {"verdict": "success", "reason": "裂口跟進確認", "gap_open": gap_open}
    if len(check) >= 3:
        return {"verdict": "failed", "reason": "無跟進性", "gap_open": gap_open}
    return {"verdict": "pending", "reason": "等2-3日確認", "gap_open": gap_open}


def gap_down_fail_reverse(bars: list[dict], idx: int) -> dict:
    """裂口低開失敗：升穿裂口日開盤（甚至最高）後先買入。"""
    ev = evaluate_gap_down(bars, idx)
    if ev["verdict"] != "failed":
        return {"pass": False, "reason": ""}
    gap_bar = bars[idx]
    gap_open, gap_high = gap_bar["open"], gap_bar["high"]
    c = bars[-1]["close"]
    if c > gap_open:
        tag = "升穿最高" if c > gap_high else "升穿開盤"
        return {
            "pass": True,
            "reason": f"裂口低開失敗後{tag}({gap_open:.2f})",
            "gap_open": gap_open,
        }
    return {"pass": False, "reason": ""}


def gap_up_fail_reverse(bars: list[dict], idx: int) -> dict:
    """裂口高開失敗：跌穿裂口日開盤 → Short 信號。"""
    ev = evaluate_gap_up(bars, idx)
    if ev["verdict"] != "failed":
        return {"pass": False, "reason": ""}
    gap_open = bars[idx]["open"]
    if bars[-1]["close"] < gap_open:
        return {"pass": True, "reason": f"裂口高開失敗跌穿開盤({gap_open:.2f})", "gap_open": gap_open}
    return {"pass": False, "reason": ""}


def avg_body_size(bars: list[dict], end_idx: int, period: int = 20) -> float:
    start = max(0, end_idx - period)
    seg = bars[start:end_idx]
    if not seg:
        return 0.0
    return sum(candle_body_pct(b) for b in seg) / len(seg)


def is_big_bullish_body(bar: dict, avg_body: float) -> bool:
    """大陽燭：燭身幅度（非收市絕對升跌幅）；body 明顯大於近均。"""
    if bar["close"] <= bar["open"] or avg_body <= 0:
        return False
    body = candle_body_pct(bar)
    m = wick_metrics(bar)
    if not m or m["range"] <= 0:
        return False
    body_dominant = m["body"] >= m["range"] * 0.55
    return body >= avg_body * 1.8 and body_dominant


def is_big_bearish_body(bar: dict, avg_body: float) -> bool:
    """大陰燭：同上，方向相反。"""
    if bar["close"] >= bar["open"] or avg_body <= 0:
        return False
    body = candle_body_pct(bar)
    m = wick_metrics(bar)
    if not m or m["range"] <= 0:
        return False
    body_dominant = m["body"] >= m["range"] * 0.55
    return body >= avg_body * 1.8 and body_dominant


def classify_long_body_context(
    bars: list[dict], idx: int, sr: dict, mom: dict, direction: str,
) -> tuple[str, str]:
    """
    大燭位置分類：
    - trend_start / turning：趨勢開端或轉折 → 後續動能最大
    - mid_trend：趨勢中途 → 仍有動能但時間較短、過程較不順
    - extended：已延伸 → 失敗率↑，策略宜短線
    """
    bar = bars[idx]
    seg = bars[max(0, idx - 15):idx]
    swing_lo = min(b["low"] for b in bars[max(0, idx - 20): idx + 1])
    swing_hi = max(b["high"] for b in bars[max(0, idx - 20): idx + 1])
    rise = (bar["close"] - swing_lo) / swing_lo if swing_lo else 0
    drop = (swing_hi - bar["close"]) / swing_hi if swing_hi else 0

    if direction == "bull":
        if rise >= 0.12 or (sr.get("swing_low") and (bar["close"] - sr["swing_low"]) / bar["close"] > 0.10):
            return "extended", "延伸區大陽燭：失敗率↑，策略宜短線"
        if len(seg) >= 5:
            prior_closes = [b["close"] for b in seg]
            prior_high = max(b["high"] for b in seg)
            flat_or_down = prior_closes[-1] <= prior_closes[0] * 1.03
            breakout = bar["close"] > prior_high * 1.005
            if flat_or_down and breakout:
                return "trend_start", "趨勢開端大陽燭：後續動能大"
        if at_key_support(bar, sr, mom) and prior_pullback(bars[: idx + 1]):
            return "turning", "轉折大陽燭@支持：後續動能大"
        if mom.get("bull_score", 0) >= 3:
            return "mid_trend", "中途大陽燭：動能仍有但持倉宜短線"
        return "mid_trend", "大陽燭：價格行為強烈"

    if drop >= 0.12 or (sr.get("resistance") and (sr["resistance"] - bar["close"]) / bar["close"] > 0.10):
        return "extended", "延伸區大陰燭：失敗率↑，策略宜短線"
    if len(seg) >= 5:
        prior_closes = [b["close"] for b in seg]
        prior_low = min(b["low"] for b in seg)
        flat_or_up = prior_closes[-1] >= prior_closes[0] * 0.97
        breakdown = bar["close"] < prior_low * 0.995
        if flat_or_up and breakdown:
            return "trend_start", "趨勢開端大陰燭：後續動能大"
    if at_key_resistance(bar, sr, mom) and prior_rally(bars[: idx + 1]):
        return "turning", "轉折大陰燭@阻力：後續動能大"
    if mom.get("bear_score", 0) >= 3:
        return "mid_trend", "中途大陰燭：動能仍有但持倉宜短線"
    return "mid_trend", "大陰燭：價格行為強烈"


def big_bull_fail_reverse(bars: list[dict], idx: int, context: str) -> dict:
    """延伸區大陽燭失敗：跌穿大陽燭開盤 → 考慮反向 Short。"""
    if context != "extended":
        return {"pass": False, "reason": ""}
    bull_bar = bars[idx]
    bull_open = bull_bar["open"]
    after = bars[idx + 1:]
    if not after:
        return {"pass": False, "reason": ""}
    if any(b["close"] < bull_open for b in after[:3]) or bars[-1]["close"] < bull_open:
        return {
            "pass": True,
            "reason": f"大陽燭延伸失敗跌穿開盤({bull_open:.2f})→考慮Short",
            "ref_open": bull_open,
        }
    return {"pass": False, "reason": ""}


def big_bear_fail_reverse(bars: list[dict], idx: int, context: str) -> dict:
    """延伸區大陰燭失敗：升穿大陰燭開盤 → 考慮反向 Long。"""
    if context != "extended":
        return {"pass": False, "reason": ""}
    bear_bar = bars[idx]
    bear_open = bear_bar["open"]
    after = bars[idx + 1:]
    if not after:
        return {"pass": False, "reason": ""}
    if any(b["close"] > bear_open for b in after[:3]) or bars[-1]["close"] > bear_open:
        return {
            "pass": True,
            "reason": f"大陰燭延伸失敗升穿開盤({bear_open:.2f})→考慮Long",
            "ref_open": bear_open,
        }
    return {"pass": False, "reason": ""}


def scan_long_body_patterns(
    bars: list[dict], sr: dict, mom: dict, lookback: int = 10,
) -> dict:
    """掃描近 N 日大燭：趨勢開端/轉折/中途/延伸 + 延伸失敗反向。"""
    start = max(1, len(bars) - lookback)
    bull_lb = None
    bear_lb = None
    bull_fail_rev = None
    bear_fail_rev = None

    for i in range(start, len(bars)):
        avg_b = avg_body_size(bars, i)
        bar = bars[i]
        vol_note = ""
        avg_vol = sum(b["volume"] for b in bars[max(0, i - 20):i]) / max(1, min(20, i))
        if avg_vol and bar["volume"] >= avg_vol * 1.2:
            vol_note = "；放量"

        if is_big_bullish_body(bar, avg_b):
            ctx, hint = classify_long_body_context(bars, i, sr, mom, "bull")
            entry = {
                "idx": i,
                "context": ctx,
                "reason": hint + vol_note,
                "body_pct": round(candle_body_pct(bar), 2),
            }
            if bull_lb is None or ctx in ("trend_start", "turning"):
                bull_lb = entry
            rev = big_bull_fail_reverse(bars, i, ctx)
            if rev["pass"]:
                bear_fail_rev = {"idx": i, "reason": rev["reason"]}

        if is_big_bearish_body(bar, avg_b):
            ctx, hint = classify_long_body_context(bars, i, sr, mom, "bear")
            entry = {
                "idx": i,
                "context": ctx,
                "reason": hint + vol_note,
                "body_pct": round(candle_body_pct(bar), 2),
            }
            if bear_lb is None or ctx in ("trend_start", "turning"):
                bear_lb = entry
            rev = big_bear_fail_reverse(bars, i, ctx)
            if rev["pass"]:
                bull_fail_rev = {"idx": i, "reason": rev["reason"]}

    return {
        "bull_lb": bull_lb,
        "bear_lb": bear_lb,
        "bull_fail_rev": bull_fail_rev,
        "bear_fail_rev": bear_fail_rev,
    }


def scan_gap_patterns(bars: list[dict], mom: dict, lookback: int = 20) -> dict:
    """掃描近 N 日裂口：延續裂口 / 裂口失敗反向。"""
    start = max(1, len(bars) - lookback)
    bull_score = mom.get("bull_score", 0)
    bear_score = mom.get("bear_score", 0)
    uptrend = bull_score >= bear_score + 1
    downtrend = bear_score >= bull_score + 1

    bull_gap = None
    bear_gap = None
    bull_fail_rev = None
    bear_fail_rev = None

    for i in range(start, len(bars)):
        prev = bars[i - 1]
        curr = bars[i]
        if is_gap_up_bar(prev, curr):
            ev = evaluate_gap_up(bars, i)
            high_gap = is_high_gap_candle(curr, prev)
            if ev["verdict"] == "success" and uptrend:
                bull_gap = {
                    "idx": i,
                    "high_gap": high_gap,
                    "reason": ev["reason"] + ("；裂口高開體" if high_gap else ""),
                }
            rev = gap_up_fail_reverse(bars, i)
            if rev["pass"]:
                bear_fail_rev = {"idx": i, "reason": rev["reason"]}
        if is_gap_down_bar(prev, curr):
            ev = evaluate_gap_down(bars, i)
            low_gap = is_low_gap_candle(curr, prev)
            if ev["verdict"] == "success" and downtrend:
                bear_gap = {
                    "idx": i,
                    "low_gap": low_gap,
                    "reason": ev["reason"] + ("；裂口低開體" if low_gap else ""),
                }
            rev = gap_down_fail_reverse(bars, i)
            if rev["pass"]:
                bull_fail_rev = {"idx": i, "reason": rev["reason"]}

    return {
        "bull_gap": bull_gap,
        "bear_gap": bear_gap,
        "bull_fail_rev": bull_fail_rev,
        "bear_fail_rev": bear_fail_rev,
    }


def analyze_csr(bars: list[dict], sr: dict, mom: dict) -> dict:
    """
    Edge #3 CSR — 陰陽燭價格行為
    Reversal | Gap | Long Body | Engulfing | Screw-over 愚弄（已實作）
    """
    last = bars[-1]
    avg_vol = sum(b["volume"] for b in bars[-20:]) / 20
    pat = scan_reversal_patterns(bars)
    gaps = scan_gap_patterns(bars, mom)
    lbs = scan_long_body_patterns(bars, sr, mom)
    engs = scan_engulfing_patterns(bars, sr, mom)
    screws = scan_screw_over_patterns(bars, sr, mom)
    bg = assess_csr_background(bars, sr, mom)
    gap_outside = scan_gap_outside_reversal(bars, sr, mom)
    pull = prior_pullback(bars)
    rally = prior_rally(bars)
    sup = at_key_support(last, sr, mom)
    res = at_key_resistance(last, sr, mom)

    bull_pin = pat["bull_pin"]
    bear_pin = pat["bear_pin"]
    bull_pat = bull_pin
    bear_pat = bear_pin

    bull_names = []
    if bull_pin:
        bull_names.append("Pin Bar/錘子")

    bear_names = []
    if bear_pin:
        bear_names.append("Shooting Star")

    vol_ok = last["volume"] >= avg_vol * 1.1
    idx = pat["bull_idx"] if pat["bull_idx"] >= 0 else len(bars) - 1
    if idx >= 0 and bars[idx]["volume"] >= avg_vol * 1.1:
        vol_ok = True

    bearish_bg = bg["mid_mas_down"] and bg["long_ma_down"]
    rev_pass = sup and bull_pin and (bg["natural_pullback"] or (pull and not bearish_bg))
    gap_pass = bool(gaps["bull_gap"])
    gap_fail_rev_pass = bool(gaps["bull_fail_rev"])
    lb_pass = bool(lbs["bull_lb"])
    lb_fail_rev_pass = bool(lbs["bull_fail_rev"])
    eng_pass = bool(engs["bull_eng"])
    eng_fail_rev_pass = bool(engs["bull_fail_rev"])
    screw_pass = bool(screws["bull_screw"])
    screw_fail_rev_pass = bool(screws["bull_fail_rev"])
    csr_pass = (
        rev_pass or gap_pass or gap_fail_rev_pass or lb_pass or lb_fail_rev_pass
        or eng_pass or eng_fail_rev_pass or screw_pass or screw_fail_rev_pass
    )

    bear_rev = bear_pin and res and rally
    bear_gap = bool(gaps["bear_gap"])
    bear_gap_fail = bool(gaps["bear_fail_rev"])
    bear_lb = bool(lbs["bear_lb"])
    bear_lb_fail = bool(lbs["bear_fail_rev"])
    bear_eng = bool(engs["bear_eng"])
    bear_eng_fail = bool(engs["bear_fail_rev"])
    bear_screw = bool(screws["bear_screw"])
    bear_screw_fail = bool(screws["bear_fail_rev"])
    gap_outside_pass = bool(gap_outside)
    bear_signal = (
        bear_rev or bear_gap or bear_gap_fail or bear_lb or bear_lb_fail
        or bear_eng or bear_eng_fail or bear_screw or bear_screw_fail or gap_outside_pass
    )

    long_notes = []
    if bg["background_note"] and bg["natural_pullback"]:
        long_notes.append(bg["background_note"])
    if rev_pass:
        tag = "Natural Pullback+" if bg["natural_pullback"] else ""
        long_notes.append(f"{tag}Reversal({'/'.join(bull_names)})@支持區")
        if vol_ok:
            long_notes.append("放量確認")
    elif bull_pin and not sup:
        long_notes.append(f"有{'/'.join(bull_names)}但唔喺關鍵支持（錯位）")
    elif bull_pin and bearish_bg:
        long_notes.append(f"有{'/'.join(bull_names)}@支持但背景唔配合（非Natural Pullback）")
    elif bull_pin and not pull:
        long_notes.append(f"有{'/'.join(bull_names)}但前文未充分拉回")
    elif sup and not bull_pin and not gap_pass and not gap_fail_rev_pass and not lb_pass and not lb_fail_rev_pass and not eng_pass and not eng_fail_rev_pass and not screw_pass and not screw_fail_rev_pass:
        long_notes.append("支持區但未有 Reversal K 線")

    if gap_pass:
        long_notes.append(f"Gap↑延續：{gaps['bull_gap']['reason']}")
    elif gaps["bull_gap"] is None:
        for i in range(max(1, len(bars) - 20), len(bars)):
            if is_gap_up_bar(bars[i - 1], bars[i]):
                ev = evaluate_gap_up(bars, i)
                if ev["verdict"] == "pending":
                    long_notes.append(f"Gap↑待確認：{ev['reason']}")
                    break
                if ev["verdict"] == "failed":
                    long_notes.append(f"Gap↑失敗：{ev['reason']}")
                    break

    if gap_fail_rev_pass:
        long_notes.append(f"Gap↓失敗反向：{gaps['bull_fail_rev']['reason']}")

    if lb_pass:
        lb = lbs["bull_lb"]
        long_notes.append(f"大陽燭({lb['body_pct']}% body)：{lb['reason']}")
    if lb_fail_rev_pass:
        long_notes.append(f"大陰燭失敗反向：{lbs['bull_fail_rev']['reason']}")

    if eng_pass:
        long_notes.append(f"陽吞噬：{engs['bull_eng']['reason']}")
    if eng_fail_rev_pass:
        long_notes.append(f"陰吞噬失敗反向：{engs['bull_fail_rev']['reason']}")

    if screw_pass:
        long_notes.append(f"愚弄↑：{screws['bull_screw']['reason']}")
    if screw_fail_rev_pass:
        long_notes.append(f"Trap↓愚弄失敗反向：{screws['bull_fail_rev']['reason']}")

    short_notes = []
    if bear_rev:
        short_notes.append(f"Reversal({'/'.join(bear_names)})@阻力")
    if bear_gap:
        short_notes.append(f"Gap↓延續：{gaps['bear_gap']['reason']}")
    if bear_gap_fail:
        short_notes.append(f"Gap↑失敗反向：{gaps['bear_fail_rev']['reason']}")
    if bear_lb:
        lb = lbs["bear_lb"]
        short_notes.append(f"大陰燭({lb['body_pct']}% body)：{lb['reason']}")
    if bear_lb_fail:
        short_notes.append(f"大陽燭延伸失敗反向：{lbs['bear_fail_rev']['reason']}")
    if bear_eng:
        short_notes.append(f"陰吞噬：{engs['bear_eng']['reason']}")
    if bear_eng_fail:
        short_notes.append(f"陽吞噬失敗反向：{engs['bear_fail_rev']['reason']}")
    if bear_screw:
        short_notes.append(f"愚弄↓：{screws['bear_screw']['reason']}")
    if bear_screw_fail:
        short_notes.append(f"Trap↑愚弄失敗反向：{screws['bear_fail_rev']['reason']}")
    if gap_outside_pass:
        short_notes.append(gap_outside["reason"])

    csr_type = "—"
    if rev_pass:
        csr_type = "reversal"
    elif gap_pass:
        csr_type = "gap"
    elif gap_fail_rev_pass:
        csr_type = "gap_fail_rev"
    elif lb_pass:
        csr_type = "long_body"
    elif lb_fail_rev_pass:
        csr_type = "long_body_fail_rev"
    elif eng_pass:
        csr_type = "engulfing"
    elif eng_fail_rev_pass:
        csr_type = "engulfing_fail_rev"
    elif screw_pass:
        csr_type = "screw_over"
    elif screw_fail_rev_pass:
        csr_type = "screw_over_fail_rev"

    short_type = "—"
    if bear_rev:
        short_type = "reversal"
    elif gap_outside_pass:
        short_type = "gap_outside_rev"
    elif bear_gap:
        short_type = "gap"
    elif bear_gap_fail:
        short_type = "gap_fail_rev"
    elif bear_lb:
        short_type = "long_body"
    elif bear_lb_fail:
        short_type = "long_body_fail_rev"
    elif bear_eng:
        short_type = "engulfing"
    elif bear_eng_fail:
        short_type = "engulfing_fail_rev"
    elif bear_screw:
        short_type = "screw_over"
    elif bear_screw_fail:
        short_type = "screw_over_fail_rev"

    pattern = "/".join(bull_names) if bull_names else "—"
    if gap_pass:
        pattern = "Gap↑" + ("高開體" if gaps["bull_gap"].get("high_gap") else "延續")
    elif gap_fail_rev_pass:
        pattern = "Gap↓失敗反向"
    elif lb_pass:
        ctx = lbs["bull_lb"]["context"]
        pattern = {"trend_start": "大陽燭·開端", "turning": "大陽燭·轉折"}.get(ctx, "大陽燭")
    elif lb_fail_rev_pass:
        pattern = "大陰燭失敗反向"
    elif eng_pass:
        pattern = engs["bull_eng"].get("subtype", "Bullish Engulfing")
    elif eng_fail_rev_pass:
        pattern = "陰吞噬失敗反向"
    elif screw_pass:
        pattern = "反轉再反轉↑"
    elif screw_fail_rev_pass:
        pattern = "Trap↓愚弄失敗反向"

    short_pattern = "/".join(bear_names) if bear_names else "—"
    if gap_outside_pass:
        short_pattern = "Gap+Outside 裂口反轉"
    elif bear_gap:
        short_pattern = "Gap↓" + ("低開體" if gaps["bear_gap"].get("low_gap") else "延續")
    elif bear_gap_fail:
        short_pattern = "Gap↑失敗反向"
    elif bear_lb:
        ctx = lbs["bear_lb"]["context"]
        short_pattern = {"trend_start": "大陰燭·開端", "turning": "大陰燭·轉折"}.get(ctx, "大陰燭")
    elif bear_lb_fail:
        short_pattern = "大陽燭延伸失敗反向"
    elif bear_eng:
        short_pattern = engs["bear_eng"].get("subtype", "Bearish Engulfing")
    elif bear_eng_fail:
        short_pattern = "陽吞噬失敗反向"
    elif bear_screw:
        short_pattern = "反轉再反轉↓"
    elif bear_screw_fail:
        short_pattern = "Trap↑愚弄失敗反向"

    return {
        "pass": csr_pass,
        "note": "；".join(long_notes) if long_notes else "未見 Long CSR 形態",
        "short_pass": bear_signal,
        "short_note": "；".join(short_notes) if short_notes else "未見 Short CSR 形態",
        "pattern": pattern,
        "short_pattern": short_pattern,
        "csr_type": csr_type,
        "short_type": short_type,
        "bear_pattern": "/".join(bear_names) if bear_names else "—",
        "at_key_support": sup,
        "at_key_resistance": res,
        "bull_reversal": rev_pass,
        "bear_reversal": bear_rev,
        "bear_gap": bear_gap,
        "bear_gap_fail": bear_gap_fail,
        "bear_long_body": bear_lb,
        "bear_long_body_fail": bear_lb_fail,
        "bear_engulfing": bear_eng,
        "bear_engulfing_fail": bear_eng_fail,
        "bear_screw_over": bear_screw,
        "bear_screw_over_fail": bear_screw_fail,
        "gap_outside_rev": gap_outside_pass,
        "natural_pullback": bg["natural_pullback"],
        "background_note": bg["background_note"],
        "wrong_place": bull_pin and not sup,
        "wrong_background": bull_pin and sup and bearish_bg,
    }


def assess_first_touch(
    bars: list[dict],
    mom: dict | None = None,
    sr: dict | None = None,
) -> dict:
    """
    Edge #8 F.T. — First Touch（META 進場）.
    盡量第1/2次 MA touch + 即時反彈；第3/4次要小心；第4次後易穿越失效。
    突破後近3K follow-through 亦算 Long F.T.
    """
    empty = {
        "long_pass": False,
        "short_pass": False,
        "touch_number_long": 0,
        "touch_number_short": 0,
        "touch_ma_long": "",
        "touch_ma_short": "",
        "quality_long": "none",
        "quality_short": "none",
        "rebound": False,
        "breakout_ft": False,
        "long_note": "未見 1st/2nd touch",
        "short_note": "未見 1st/2nd touch",
    }
    n = len(bars)
    if n < 25:
        return empty

    closes = [b["close"] for b in bars]
    s5, s10, s20 = sma(closes, 5), sma(closes, 10), sma(closes, 20)
    mas = {"s5": s5, "s10": s10, "s20": s20}
    tol = 0.018
    ma_bull = s5[-1] > s10[-1] > s20[-1]
    ma_bear = s5[-1] < s10[-1] < s20[-1]
    last = bars[-1]
    avg20 = sum(b["volume"] for b in bars[-20:]) / 20

    def leg_start(direction: str, ma_key: str) -> int:
        series = mas[ma_key]
        start = max(0, n - 55)
        if direction == "long":
            for i in range(n - 2, start, -1):
                if bars[i]["close"] < series[i] * 0.992:
                    return min(i + 1, n - 1)
            return start
        for i in range(n - 2, start, -1):
            if bars[i]["close"] > series[i] * 1.008:
                return min(i + 1, n - 1)
        return start

    def distinct(prev: int, curr: int, series: list[float], direction: str) -> bool:
        if curr - prev < 2:
            return False
        if curr - prev >= 5:
            return True
        seg = bars[prev + 1:curr]
        if not seg:
            return curr - prev >= 3
        if direction == "long":
            return max(b["high"] for b in seg) > series[curr] * 1.015
        return min(b["low"] for b in seg) < series[curr] * 0.985

    def count_touches(direction: str, ma_key: str) -> tuple[list[int], str]:
        series = mas[ma_key]
        start = leg_start(direction, ma_key)
        idxs: list[int] = []
        for i in range(start + 1, n):
            ma = series[i]
            if ma <= 0:
                continue
            b = bars[i]
            if direction == "long":
                if b["close"] < series[i] * 0.96:
                    continue
                near = abs(b["low"] - ma) / ma <= tol
                rebound = b["close"] >= ma * 0.993 or (
                    b["close"] > b["open"] and b["low"] <= ma * (1 + tol)
                )
                if near and rebound:
                    if not idxs or distinct(idxs[-1], i, series, direction):
                        idxs.append(i)
            else:
                if b["close"] > series[i] * 1.04:
                    continue
                near = abs(b["high"] - ma) / ma <= tol
                rebound = b["close"] <= ma * 1.007 or (
                    b["close"] < b["open"] and b["high"] >= ma * (1 - tol)
                )
                if near and rebound:
                    if not idxs or distinct(idxs[-1], i, series, direction):
                        idxs.append(i)
        label = {"s5": "5MA", "s10": "10MA", "s20": "20MA"}[ma_key]
        return idxs, label

    long_ma_key = "s10" if ma_bull and closes[-1] > s20[-1] else "s20"
    short_ma_key = "s10" if ma_bear and closes[-1] < s20[-1] else "s20"

    def count_area_touches(
        direction: str, zone_lo: float, zone_hi: float,
    ) -> tuple[list[int], str]:
        """Count touches on S/R area — multiple MAs in same band = one touch."""
        if zone_lo <= 0 or zone_hi <= 0:
            return [], "支持區"
        start = max(0, n - 55)
        leg_start = start
        if direction == "long":
            for i in range(n - 2, start, -1):
                if bars[i]["close"] < zone_lo * 0.992:
                    leg_start = min(i + 1, n - 1)
                    break
        else:
            for i in range(n - 2, start, -1):
                if bars[i]["close"] > zone_hi * 1.008:
                    leg_start = min(i + 1, n - 1)
                    break

        def area_distinct(prev: int, curr: int) -> bool:
            if curr - prev < 2:
                return False
            if curr - prev >= 5:
                return True
            seg = bars[prev + 1:curr]
            if not seg:
                return curr - prev >= 3
            if direction == "long":
                return max(b["high"] for b in seg) > zone_hi * 1.015
            return min(b["low"] for b in seg) < zone_lo * 0.985

        idxs: list[int] = []
        for i in range(leg_start + 1, n):
            b = bars[i]
            if direction == "long":
                if b["close"] < zone_lo * 0.96:
                    continue
                near = zone_lo * (1 - tol) <= b["low"] <= zone_hi * (1 + tol)
                rebound = b["close"] >= zone_lo * 0.993 or (
                    b["close"] > b["open"] and b["low"] <= zone_hi * (1 + tol)
                )
                if near and rebound and (not idxs or area_distinct(idxs[-1], i)):
                    idxs.append(i)
            else:
                if b["close"] > zone_hi * 1.04:
                    continue
                near = zone_lo * (1 - tol) <= b["high"] <= zone_hi * (1 + tol)
                rebound = b["close"] <= zone_hi * 1.007 or (
                    b["close"] < b["open"] and b["high"] >= zone_lo * (1 - tol)
                )
                if near and rebound and (not idxs or area_distinct(idxs[-1], i)):
                    idxs.append(i)
        label = f"支持區 {format_area_range(zone_lo, zone_hi)}"
        return idxs, label

    use_area_long = False
    use_area_short = False
    long_idx: list[int] = []
    short_idx: list[int] = []
    long_label = "10MA"
    short_label = "10MA"

    if sr:
        area = sr.get("trading_area") or {}
        zone_lo = area.get("zone_lo") or 0
        zone_hi = area.get("zone_hi") or zone_lo
        if zone_lo > 0 and area.get("edge_count", 0) >= 1:
            if ma_bull and zone_hi <= closes[-1] * 1.002:
                long_idx, long_label = count_area_touches("long", zone_lo, zone_hi)
                use_area_long = True
            if ma_bear and zone_lo >= closes[-1] * 0.998:
                short_idx, short_label = count_area_touches("short", zone_lo, zone_hi)
                use_area_short = True

    if not use_area_long:
        long_idx, long_label = count_touches("long", long_ma_key) if ma_bull else ([], "10MA")
    if not use_area_short:
        short_idx, short_label = count_touches("short", short_ma_key) if ma_bear else ([], "10MA")

    if use_area_long and sr:
        zone_lo = (sr.get("trading_area") or {}).get("zone_lo") or 0
        zone_hi = (sr.get("trading_area") or {}).get("zone_hi") or zone_lo
        ma_l = (zone_lo + zone_hi) / 2 if zone_hi else mas[long_ma_key][-1]
    else:
        ma_l = mas[long_ma_key][-1]
    if use_area_short and sr:
        zone_lo = (sr.get("trading_area") or {}).get("zone_lo") or 0
        zone_hi = (sr.get("trading_area") or {}).get("zone_hi") or zone_lo
        ma_s = (zone_lo + zone_hi) / 2 if zone_hi else mas[short_ma_key][-1]
    else:
        ma_s = mas[short_ma_key][-1]

    if use_area_long and sr:
        zone_lo = (sr.get("trading_area") or {}).get("zone_lo") or 0
        zone_hi = (sr.get("trading_area") or {}).get("zone_hi") or zone_lo
        at_touch_long = (
            ma_bull and zone_lo > 0
            and zone_lo * (1 - tol) <= last["low"] <= zone_hi * (1 + tol)
            and last["close"] >= zone_lo * 0.99
        )
    else:
        at_touch_long = (
            ma_bull and ma_l > 0 and abs(last["low"] - ma_l) / ma_l <= tol and last["close"] >= ma_l * 0.99
        )
    if use_area_short and sr:
        zone_lo = (sr.get("trading_area") or {}).get("zone_lo") or 0
        zone_hi = (sr.get("trading_area") or {}).get("zone_hi") or zone_lo
        at_touch_short = (
            ma_bear and zone_hi > 0
            and zone_lo * (1 - tol) <= last["high"] <= zone_hi * (1 + tol)
            and last["close"] <= zone_hi * 1.01
        )
    else:
        at_touch_short = (
            ma_bear and ma_s > 0 and abs(last["high"] - ma_s) / ma_s <= tol and last["close"] <= ma_s * 1.01
        )
    rebound_long = last["close"] > last["open"] or last["close"] >= ma_l * 0.995
    rebound_short = last["close"] < last["open"] or last["close"] <= ma_s * 1.005

    def effective_count(idxs: list[int], at_now: bool) -> int:
        cnt = len(idxs)
        if at_now and (not idxs or idxs[-1] != n - 1):
            return cnt + 1
        return cnt

    eff_long = effective_count(long_idx, at_touch_long)
    eff_short = effective_count(short_idx, at_touch_short)

    def touch_quality(eff: int) -> str:
        if eff <= 0:
            return "none"
        if eff == 1:
            return "ideal"
        if eff == 2:
            return "ok"
        if eff <= 4:
            return "caution"
        return "stale"

    qual_long = touch_quality(eff_long)
    qual_short = touch_quality(eff_short)

    recent_long = at_touch_long or (long_idx and n - 1 - long_idx[-1] <= 2)
    recent_short = at_touch_short or (short_idx and n - 1 - short_idx[-1] <= 2)

    ft_up = sum(1 for b in bars[-3:] if b["close"] > b["open"]) >= 2
    ft_vol = sum(b["volume"] for b in bars[-3:]) / 3 > avg20
    breakout_ft = ft_up and ft_vol and last["close"] >= max(b["close"] for b in bars[-5:-1])

    long_pass = (
        ma_bull and qual_long in ("ideal", "ok") and recent_long and rebound_long
    ) or breakout_ft
    short_pass = (
        ma_bear and qual_short in ("ideal", "ok") and recent_short and rebound_short
    ) or (
        sum(1 for b in bars[-3:] if b["close"] < b["open"]) >= 2
        and ft_vol and last["close"] <= min(b["close"] for b in bars[-5:-1])
        and ma_bear
    )

    def fmt_note(eff: int, qual: str, label: str, ma: float, direction: str) -> str:
        if qual == "ideal":
            return f"第1次 {label} touch @ ${ma:.2f}（力量最強）"
        if qual == "ok":
            return f"第2次 {label} touch @ ${ma:.2f}（仍理想）"
        if qual == "caution":
            return f"第{eff}次 {label} touch——要小心/偏短線"
        if qual == "stale":
            return f"第{eff}次 touch——多次穿越 MA，已過最佳位"
        if direction == "long" and breakout_ft:
            return "突破後近3K follow-through"
        return "未見 1st/2nd touch"

    long_note = fmt_note(eff_long, qual_long, long_label, ma_l, "long")
    if not long_pass and breakout_ft:
        long_note = "突破後近3K follow-through"
    elif long_pass and breakout_ft and qual_long == "none":
        long_note = "突破後近3K follow-through"

    short_note = fmt_note(eff_short, qual_short, short_label, ma_s, "short")

    return {
        "long_pass": long_pass,
        "short_pass": short_pass,
        "touch_number_long": eff_long,
        "touch_number_short": eff_short,
        "touch_ma_long": long_label,
        "touch_ma_short": short_label,
        "quality_long": qual_long,
        "quality_short": qual_short,
        "rebound": rebound_long or rebound_short,
        "breakout_ft": breakout_ft,
        "long_note": long_note,
        "short_note": short_note,
        "at_touch_long": at_touch_long,
        "at_touch_short": at_touch_short,
    }


def analyze_bars(bars: list[dict]) -> dict:
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    last = bars[-1]
    c, v = last["close"], last["volume"]
    avg20 = sum(volumes[-20:]) / 20

    r5 = range_pct(bars, 5)
    r10 = range_pct(bars, 10)
    r20 = range_pct(bars, 20)

    sr = analyze_sr(bars)
    sr_pass = sr["pass"]
    sr_note = sr["note"]
    swing = sr["swing_low"]

    mi = assess_mi_macd_breakout(bars)
    mi_pass = mi["long_pass"]
    mom = analyze_momentum_trend(bars)
    ft = assess_first_touch(bars, mom, sr=sr)
    ft_pass = ft["long_pass"]
    momentum_pass = mom["pass"]
    csr = analyze_csr(bars, sr, mom)

    return {
        "close": round(c, 2),
        "volume": int(v),
        "avg_volume_20": int(avg20),
        "ema20": mom["ema20"],
        "ema50": round(e50[-1], 2),
        "sma5": mom["sma5"],
        "sma10": mom["sma10"],
        "sma20": mom["sma20"],
        "momentum_pass": momentum_pass,
        "momentum_note": mom["note"],
        "momentum_bear_pass": mom["bear_pass"],
        "trend_dir": mom["trend_dir"],
        "sr_pass": sr_pass,
        "sr_note": sr_note,
        "trading_area": sr["trading_area"],
        "wave_top": sr["wave_top"],
        "wave_bottom": sr["wave_bottom"],
        "retest_as_support": sr["retest_as_support"],
        "csr_pass": csr["pass"],
        "csr_note": csr["note"],
        "csr_pattern": csr["pattern"],
        "csr_short_pass": csr["short_pass"],
        "csr_short_note": csr["short_note"],
        "csr_short_pattern": csr["short_pattern"],
        "csr_bear_reversal": csr["short_pass"],
        "csp_pass": csr["pass"],
        "csp_note": csr["note"],
        "csp_short_pass": csr["short_pass"],
        "csp_short_note": csr["short_note"],
        "ft_pass": ft_pass,
        "ft_short_pass": ft["short_pass"],
        "ft_detail": ft,
        "mi_short_pass": mi["short_pass"],
        "mi_detail": mi,
        "mi_pass": mi_pass,
        "swing_low": round(swing, 2),
        "resistance": round(find_resistance(bars), 2),
    }


def yf_history(ticker: str, period: str = "3mo"):
    import yfinance as yf
    h = yf.Ticker(ticker).history(period=period)
    if h.empty or len(h) < 5:
        return None
    ret = (h["Close"].iloc[-1] / h["Close"].iloc[0] - 1) * 100
    return ret


def yf_daily_closes(ticker: str, period: str = "6mo") -> list[float] | None:
    import yfinance as yf
    h = yf.Ticker(ticker).history(period=period)
    if h.empty or len(h) < 30:
        return None
    return [float(x) for x in h["Close"].tolist()]


def pct_return(closes: list[float], lookback: int | None = None) -> float | None:
    if lookback is None:
        lookback = len(closes) - 1
    if len(closes) < lookback + 1:
        return None
    return (closes[-1] / closes[-1 - lookback] - 1) * 100


def assess_rs_leading_ma(bars: list[dict]) -> tuple[bool, bool, str, str]:
    """Feature 2: leading / weak moving averages."""
    if len(bars) < 25:
        return False, False, "領先MA：數據不足", "弱勢MA：數據不足"
    closes = [b["close"] for b in bars]
    c = closes[-1]
    s5 = sma(closes, 5)[-1]
    s10 = sma(closes, 10)[-1]
    s20 = sma(closes, 20)[-1]
    s20_prev = sma(closes, 20)[-6]
    long_pass = c > s5 > s10 > s20 and s20 > s20_prev
    if not long_pass:
        long_pass = c > s20 and s5 > s10 > s20 and c > s5
    short_pass = c < s5 < s10 < s20 and s20 < s20_prev
    if not short_pass:
        short_pass = c < s20 and s5 < s10 < s20 and c < s5
    long_note = (
        f"領先MA：價>{s5:.2f}>{s10:.2f}>{s20:.2f}"
        if long_pass
        else f"領先MA未過：價{c:.2f} MA={s5:.2f}/{s10:.2f}/{s20:.2f}"
    )
    short_note = (
        f"弱勢MA：價<{s5:.2f}<{s10:.2f}<{s20:.2f}"
        if short_pass
        else f"弱勢MA未過：價{c:.2f}"
    )
    return long_pass, short_pass, long_note, short_note


def assess_rs_leading_ma_from_closes(closes: list[float]) -> tuple[bool, bool, str, str]:
    bars = [{"close": c, "open": c, "high": c, "low": c, "volume": 0} for c in closes]
    return assess_rs_leading_ma(bars)


def assess_rs_line(
    stock_closes: list[float], spy_closes: list[float]
) -> tuple[bool, bool, str, str]:
    """Feature 3: RS line (stock/SPY ratio) trending up or down."""
    n = min(len(stock_closes), len(spy_closes))
    if n < 40:
        return False, False, "RS線：數據不足", "RS線：數據不足"
    stock = stock_closes[-n:]
    spy = spy_closes[-n:]
    ratios = [s / p for s, p in zip(stock, spy) if p > 0]
    if len(ratios) < 40:
        return False, False, "RS線：數據不足", "RS線：數據不足"
    r_now = ratios[-1]
    r_20ago = ratios[-21]
    window = ratios[-40:]
    recent_high = max(window)
    recent_low = min(window)
    long_pass = r_now > r_20ago * 1.005 or r_now >= recent_high * 0.995
    short_pass = r_now < r_20ago * 0.995 or r_now <= recent_low * 1.005
    long_note = f"RS線向上：{r_20ago:.4f}→{r_now:.4f}" + (" 近40日新高" if r_now >= recent_high * 0.995 else "")
    short_note = f"RS線向下：{r_20ago:.4f}→{r_now:.4f}" if short_pass else f"RS線未跌：{r_now:.4f}"
    return long_pass, short_pass, long_note, short_note


def assess_rs_counter(
    stock_ret: float, spy_ret: float, spy_bearish: bool
) -> tuple[bool, bool, str, str]:
    """Feature 1: counter-trend vs market (most visible in bear markets)."""
    outperform = stock_ret > spy_ret
    underperform = stock_ret < spy_ret
    if spy_bearish:
        long_pass = outperform or (stock_ret > 0 and spy_ret < 0)
        long_note = f"跌市反向：3M {stock_ret:.1f}% vs SPY {spy_ret:.1f}%"
    else:
        long_pass = outperform and stock_ret > -2
        long_note = f"跑贏大盤：3M {stock_ret:.1f}% vs SPY {spy_ret:.1f}%"
    if not long_pass:
        long_note = f"未跑贏：3M {stock_ret:.1f}% vs SPY {spy_ret:.1f}%"
    if not spy_bearish:
        short_pass = underperform and spy_ret > 0
    else:
        short_pass = underperform and stock_ret < spy_ret
    short_note = (
        f"跑輸大盤：3M {stock_ret:.1f}% vs SPY {spy_ret:.1f}%"
        if short_pass
        else f"仍強於SPY：3M {stock_ret:.1f}% vs SPY {spy_ret:.1f}%"
    )
    return long_pass, short_pass, long_note, short_note


def _rs_fail(msg: str) -> dict:
    return {
        "long_pass": 0,
        "short_pass": 0,
        "long_note": msg,
        "short_note": msg,
        "features": {},
        "long_count": 0,
        "short_count": 0,
    }


def assess_relative_strength(symbol: str, d1_bars: list[dict] | None = None) -> dict:
    """
    Edge #5 RS — three features (course):
    1) Counter-trend vs SPY (especially in bear market)
    2) Leading moving averages (price above rising MAs)
    3) RS line (stock/SPY ratio) pointing up
    Pass Long/Short when >= 2/3 features (don't need all three).
    """
    try:
        stock_closes = yf_daily_closes(symbol, "6mo")
        spy_closes = yf_daily_closes("SPY", "6mo")
        if stock_closes is None or spy_closes is None:
            return _rs_fail("RS 數據不足")

        lb = min(63, len(stock_closes) - 1)
        stock_ret = pct_return(stock_closes, lb) or 0.0
        spy_ret = pct_return(spy_closes, min(63, len(spy_closes) - 1)) or 0.0
        spy_s20 = sma(spy_closes, 20)[-1] if len(spy_closes) >= 20 else spy_closes[-1]
        spy_bearish = spy_ret < 0 or spy_closes[-1] < spy_s20

        c_long, c_short, c_ln, c_sn = assess_rs_counter(stock_ret, spy_ret, spy_bearish)
        if d1_bars and len(d1_bars) >= 25:
            m_long, m_short, m_ln, m_sn = assess_rs_leading_ma(d1_bars)
        else:
            m_long, m_short, m_ln, m_sn = assess_rs_leading_ma_from_closes(stock_closes)
        l_long, l_short, l_ln, l_sn = assess_rs_line(stock_closes, spy_closes)

        long_count = sum((c_long, m_long, l_long))
        short_count = sum((c_short, m_short, l_short))
        long_pass = int(long_count >= 2)
        short_pass = int(short_count >= 2)

        icon = lambda ok: "✓" if ok else "—"
        long_note = (
            f"RS {long_count}/3：反向{icon(c_long)} 領先MA{icon(m_long)} RS線{icon(l_long)} "
            f"| 3M {stock_ret:.1f}% vs SPY {spy_ret:.1f}%"
        )
        short_note = (
            f"弱RS {short_count}/3：跑輸{icon(c_short)} 弱MA{icon(m_short)} RS跌{icon(l_short)}"
        )

        return {
            "long_pass": long_pass,
            "short_pass": short_pass,
            "long_note": long_note,
            "short_note": short_note,
            "long_count": long_count,
            "short_count": short_count,
            "stock_ret_3m": round(stock_ret, 1),
            "spy_ret_3m": round(spy_ret, 1),
            "spy_bearish": spy_bearish,
            "features": {
                "counter_trend": {"long": c_long, "short": c_short, "long_note": c_ln, "short_note": c_sn},
                "leading_ma": {"long": m_long, "short": m_short, "long_note": m_ln, "short_note": m_sn},
                "rs_line": {"long": l_long, "short": l_short, "long_note": l_ln, "short_note": l_sn},
            },
        }
    except Exception as e:
        return _rs_fail(f"RS 失敗: {e}")


def fetch_rs(symbol: str, d1_bars: list[dict] | None = None) -> tuple[int, str]:
    """Backward-compatible wrapper (long pass + note)."""
    rs = assess_relative_strength(symbol, d1_bars)
    return rs["long_pass"], rs["long_note"]


def yf_daily_bars(ticker: str, period: str = "1y") -> list[dict] | None:
    import yfinance as yf
    h = yf.Ticker(ticker).history(period=period)
    if h.empty or len(h) < 30:
        return None
    bars = []
    for _, row in h.iterrows():
        bars.append({
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume") or 0),
        })
    return bars


def load_spy_market_data() -> dict:
    """SPY W1/D1/H1 from CSV if present; else yfinance daily D1."""
    sym = "SPY"
    d1_path = CSV_DIR / f"{sym}_D1.csv"
    if d1_path.exists():
        d1_bars = parse_bars(load_tv_csv(d1_path))
        source = "CSV"
    else:
        d1_bars = yf_daily_bars(sym, "1y")
        source = "yfinance D1"
    if not d1_bars:
        return {"error": "SPY 數據不足"}
    d1 = analyze_bars(d1_bars)
    w1, h1 = None, None
    w1_path = CSV_DIR / f"{sym}_W1.csv"
    if w1_path.exists():
        w1_bars = parse_bars(load_tv_csv(w1_path), min_bars=TF_MIN_BARS["W1"])
        w1 = analyze_bars(w1_bars)
    h1_path = CSV_DIR / f"{sym}_H1.csv"
    if h1_path.exists():
        h1_bars = parse_bars(load_tv_csv(h1_path), min_bars=TF_MIN_BARS["H1"])
        h1 = analyze_bars(h1_bars)

    # Edge #9 M.I.: user rule = MACD breakout, W1 as primary source.
    mi_source_tf = "W1" if w1 else "D1"
    mi_canonical = (w1 or d1).get("mi_detail") or {
        "long_pass": bool((w1 or d1).get("mi_pass")),
        "short_pass": bool((w1 or d1).get("mi_short_pass")),
        "long_note": "MACD breakout 未確認",
        "short_note": "MACD breakout 未確認",
    }
    d1 = apply_mi_override(d1, mi_canonical, mi_source_tf)
    if w1:
        w1 = apply_mi_override(w1, mi_canonical, mi_source_tf)
    if h1:
        h1 = apply_mi_override(h1, mi_canonical, mi_source_tf)
    return {"d1": d1, "d1_bars": d1_bars, "w1": w1, "h1": h1, "source": source}


def _broad_market_fail(msg: str) -> dict:
    return {
        "long_pass": 0,
        "short_pass": 0,
        "long_note": msg,
        "short_note": msg,
        "bias": "neutral",
        "directive": "大盤數據不足 → 謹慎觀望",
        "long_count": 0,
        "short_count": 0,
        "close": None,
        "source": "—",
        "features": {},
    }


def assess_bm_pillar_sr_long(d1: dict) -> tuple[int, str]:
    area = d1.get("trading_area") or {}
    if d1.get("sr_pass"):
        return 1, d1.get("sr_note", "S&R Long Edge zone")
    if area.get("edge_count", 0) >= 2:
        src = "+".join(area.get("sources", [])[:3]) or "—"
        return 1, f"Multiple edge support（{area['edge_count']}源:{src}）"
    if d1.get("retest_as_support"):
        return 1, "突破回踩（阻力→支持）"
    if d1.get("trend_dir") == "升勢" and d1["close"] > d1["ema20"]:
        return 1, "升勢結構：價在 EMA20 上"
    return 0, "未在近支持匯聚區"


def assess_bm_pillar_sr_short(d1: dict, bars: list[dict]) -> tuple[int, str]:
    bands = d1.get("pivot_sr_bands") or []
    near_res = nearest_pivot_band_at_price(bands, d1["close"], side="resistance")
    if near_res and price_near_pivot_zone(d1["close"], near_res):
        return 1, (
            f"W1 {near_res['label']} "
            f"${near_res['zone_lo']:.2f}–${near_res['zone_hi']:.2f}"
        )
    sr_s = analyze_sr_short(bars)
    if sr_s["pass"]:
        return 1, sr_s["note"]
    if d1.get("trend_dir") == "跌勢" and d1["close"] < d1["ema20"]:
        return 1, "跌勢結構：價在 EMA20 下"
    return 0, "未在近阻力匯聚區"


def assess_bm_pillar_momentum_long(bars: list[dict]) -> tuple[int, str]:
    mom = analyze_momentum_trend(bars)
    if mom["pass"]:
        return 1, mom["note"]
    closes = [b["close"] for b in bars]
    s5, s10, s20 = sma(closes, 5)[-1], sma(closes, 10)[-1], sma(closes, 20)[-1]
    if s5 > s10 > s20:
        return 1, f"MA stack 向上 {s5:.2f}>{s10:.2f}>{s20:.2f}"
    highs, lows = find_swing_points(bars, 60)
    if len(highs) >= 2 and highs[-1] > highs[-2] and len(lows) >= 2 and lows[-1] > lows[-2]:
        return 1, "一浪高於一浪（higher lows）"
    return 0, "動能/趨勢未支持做多"


def assess_bm_pillar_momentum_short(bars: list[dict]) -> tuple[int, str]:
    mom = analyze_momentum_trend(bars)
    if mom.get("bear_pass"):
        return 1, mom.get("bear_note", "跌勢動能達標")
    closes = [b["close"] for b in bars]
    s5, s10, s20 = sma(closes, 5)[-1], sma(closes, 10)[-1], sma(closes, 20)[-1]
    if s5 < s10 < s20:
        return 1, f"MA stack 向下 {s5:.2f}<{s10:.2f}<{s20:.2f}"
    highs, lows = find_swing_points(bars, 60)
    if len(highs) >= 2 and highs[-1] < highs[-2] and len(lows) >= 2 and lows[-1] < lows[-2]:
        return 1, "一浪低於一浪（lower highs）"
    return 0, "動能/趨勢未支持做空"


def assess_bm_pillar_mtf_long(
    w1: dict | None, d1: dict, h1: dict | None, source: str,
) -> tuple[int, str]:
    if w1 is not None and h1 is not None:
        mtf_long, _, note = analyze_mtf_cross(w1, d1, h1)
        return int(mtf_long), note
    d_dir = tf_direction(d1)
    w_dir = tf_direction(w1) if w1 else "neutral"
    h_dir = tf_direction(h1) if h1 else "neutral"
    if w1 and not h1:
        ok = d_dir == "long" and w_dir in ("long", "neutral")
        return int(ok), f"部分 MTF（缺 H1）：W1={w_dir} D1={d_dir}"
    if h1 and not w1:
        ok = d_dir == "long" and h_dir in ("long", "neutral")
        return int(ok), f"部分 MTF（缺 W1）：D1={d_dir} H1={h_dir}"
    ok = d_dir in ("long", "neutral") and d1["close"] > d1["ema20"]
    return int(ok), f"僅 {source}：{d1.get('trend_dir', '—')} close ${d1['close']:.2f}"


def assess_bm_pillar_mtf_short(
    w1: dict | None, d1: dict, h1: dict | None, source: str,
) -> tuple[int, str]:
    if w1 is not None and h1 is not None:
        _, mtf_short, note = analyze_mtf_cross(w1, d1, h1)
        return int(mtf_short), note
    d_dir = tf_direction(d1)
    w_dir = tf_direction(w1) if w1 else "neutral"
    h_dir = tf_direction(h1) if h1 else "neutral"
    if w1 and not h1:
        ok = d_dir == "short" and w_dir in ("short", "neutral")
        return int(ok), f"部分 MTF（缺 H1）：W1={w_dir} D1={d_dir}"
    if h1 and not w1:
        ok = d_dir == "short" and h_dir in ("short", "neutral")
        return int(ok), f"部分 MTF（缺 W1）：D1={d_dir} H1={h_dir}"
    ok = d_dir in ("short", "neutral") and d1["close"] < d1["ema20"]
    return int(ok), f"僅 {source}：{d1.get('trend_dir', '—')} close ${d1['close']:.2f}"


def assess_broad_market_edge() -> dict:
    """
    Edge #7 — Broad Market Edge（大盤 Long/Short Edge）on SPY.
    Pass Long/Short when >= 2 of 3 pillars:
      ① S&R long-edge zone / bear mirror
      ② Momentum/trend supportive
      ③ MTF W1->D1 aligned (fallback when no CSV)
    """
    try:
        raw = load_spy_market_data()
        if raw.get("error"):
            return _broad_market_fail(raw["error"])
        d1, bars = raw["d1"], raw["d1_bars"]
        w1, h1, source = raw.get("w1"), raw.get("h1"), raw["source"]

        sr_l, sr_ln = assess_bm_pillar_sr_long(d1)
        mom_l, mom_ln = assess_bm_pillar_momentum_long(bars)
        mtf_l, mtf_ln = assess_bm_pillar_mtf_long(w1, d1, h1, source)
        sr_s, sr_sn = assess_bm_pillar_sr_short(d1, bars)
        mom_s, mom_sn = assess_bm_pillar_momentum_short(bars)
        mtf_s, mtf_sn = assess_bm_pillar_mtf_short(w1, d1, h1, source)

        long_count = sr_l + mom_l + mtf_l
        short_count = sr_s + mom_s + mtf_s
        long_pass = int(long_count >= 2 and long_count >= short_count)
        short_pass = int(short_count >= 2 and short_count > long_count)

        if long_pass:
            bias, directive = "long", "大盤 Long Edge → 積極搵長倉"
        elif short_pass:
            bias, directive = "short", "大盤 Short Edge → 積極搵短倉"
        else:
            bias, directive = "neutral", "大盤無明確 Edge → 觀望或減倉"

        icon = lambda ok: "✓" if ok else "—"
        long_note = (
            f"大盤 Long Edge {long_count}/3：S&R{icon(sr_l)} 動能{icon(mom_l)} MTF{icon(mtf_l)} "
            f"| SPY ${d1['close']:.2f}（{source}）"
        )
        short_note = (
            f"大盤 Short Edge {short_count}/3：S&R{icon(sr_s)} 動能{icon(mom_s)} MTF{icon(mtf_s)} "
            f"| SPY ${d1['close']:.2f}"
        )

        return {
            "long_pass": long_pass,
            "short_pass": short_pass,
            "long_note": long_note,
            "short_note": short_note,
            "bias": bias,
            "directive": directive,
            "long_count": long_count,
            "short_count": short_count,
            "close": d1["close"],
            "source": source,
            "qqq_note": _qqq_secondary_note(),
            "features": {
                "sr_zone": {"long": sr_l, "short": sr_s, "long_note": sr_ln, "short_note": sr_sn},
                "momentum": {"long": mom_l, "short": mom_s, "long_note": mom_ln, "short_note": mom_sn},
                "mtf": {"long": mtf_l, "short": mtf_s, "long_note": mtf_ln, "short_note": mtf_sn},
            },
        }
    except Exception as e:
        return _broad_market_fail(f"大盤分析失敗: {e}")


def _qqq_secondary_note() -> str:
    try:
        qqq = yf_history("QQQ")
        spy = yf_history("SPY")
        if qqq is None or spy is None:
            return ""
        return f"QQQ 3M {qqq:.1f}% vs SPY {spy:.1f}%（次參考）"
    except Exception:
        return ""


_MARKET_EDGE_CACHE: dict | None = None


def get_broad_market_edge(force: bool = False) -> dict:
    global _MARKET_EDGE_CACHE
    if force or _MARKET_EDGE_CACHE is None:
        _MARKET_EDGE_CACHE = assess_broad_market_edge()
    return _MARKET_EDGE_CACHE


def fetch_sector_footnote(symbol: str) -> str:
    """Sector vs SPY — display only; does not affect Edge #7."""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        sector = (info.get("sector") or "").lower()
        etf = None
        for key, tick in SECTOR_ETF.items():
            if key in sector:
                etf = tick
                break
        if not etf:
            return ""
        sec_ret = yf_history(etf)
        spy_ret = yf_history("SPY")
        if sec_ret is None or spy_ret is None:
            return ""
        lead = "領先" if sec_ret > spy_ret else "落後"
        return f"板塊參考：{info.get('sector', etf)} {etf} 3M {sec_ret:.1f}% vs SPY {spy_ret:.1f}%（{lead}）"
    except Exception:
        return ""


def fetch_board_edge(symbol: str) -> tuple[int, str]:
    """Backward-compatible wrapper — returns broad market long pass + note."""
    m = get_broad_market_edge()
    return m["long_pass"], m["long_note"]


def find_swing_points_indexed(
    bars: list[dict], lookback: int = 60,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Swing highs/lows as (bar_index, price) for trendline projection."""
    start = max(0, len(bars) - lookback)
    seg = bars[start:]
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(1, len(seg) - 1):
        idx = start + i
        if seg[i]["high"] >= seg[i - 1]["high"] and seg[i]["high"] >= seg[i + 1]["high"]:
            highs.append((idx, seg[i]["high"]))
        if seg[i]["low"] <= seg[i - 1]["low"] and seg[i]["low"] <= seg[i + 1]["low"]:
            lows.append((idx, seg[i]["low"]))
    return highs, lows


def project_trendline(p1: tuple[int, float], p2: tuple[int, float], target_idx: int) -> float:
    i1, v1 = p1
    i2, v2 = p2
    if i2 == i1:
        return v2
    slope = (v2 - v1) / (i2 - i1)
    return v1 + slope * (target_idx - i1)


CHANNEL_LOOKBACK_W1 = 80
CHANNEL_RECENT_W1 = 50
CHANNEL_PROJECT_W1 = 12

PIVOT_LEFT_BARS = 10
PIVOT_RIGHT_BARS = 10
ATR_PERIOD = 14
SR_ATR_BAND_MULT = 0.2
SR_MIN_TOUCHES = 3


def compute_atr_series(bars: list[dict], period: int = ATR_PERIOD) -> list[float]:
    """Wilder-style ATR (SMA of true range for each bar)."""
    if not bars:
        return []
    trs: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            tr = bar["high"] - bar["low"]
        else:
            prev = bars[i - 1]
            tr = max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev["close"]),
                abs(bar["low"] - prev["close"]),
            )
        trs.append(tr)
    return sma(trs, period)


def find_pivot_points_indexed(
    bars: list[dict],
    *,
    left: int = PIVOT_LEFT_BARS,
    right: int = PIVOT_RIGHT_BARS,
    use_close: bool = False,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    TradingView-style pivot highs / lows (Left Bars + Right Bars confirmation).
    Near the chart end, Right Bars shrinks so recent swings can still confirm.
    use_close=True: pivot on close (UTL/DTL); False: high/low wicks (S/R).
    """
    n = len(bars)
    if n < left + 2:
        return [], []
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(left, n):
        eff_right = min(right, n - 1 - i)
        seg = bars[i - left : i + eff_right + 1]
        if use_close:
            px = bars[i]["close"]
            closes = [b["close"] for b in seg]
            if px >= max(closes):
                highs.append((i, px))
            if px <= min(closes):
                lows.append((i, px))
        else:
            hi = bars[i]["high"]
            lo = bars[i]["low"]
            if hi >= max(b["high"] for b in seg):
                highs.append((i, hi))
            if lo <= min(b["low"] for b in seg):
                lows.append((i, lo))
    return highs, lows


def linear_regression_at(points: list[tuple[int, float]], target_idx: int) -> float:
    """Project price at target_idx through pivot chain (2-point line or OLS)."""
    if not points:
        return 0.0
    if len(points) == 1:
        return points[0][1]
    if len(points) == 2:
        return project_trendline(points[0], points[1], target_idx)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n = len(points)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[k] - x_mean) * (ys[k] - y_mean) for k in range(n))
    den = sum((xs[k] - x_mean) ** 2 for k in range(n))
    if den == 0:
        return y_mean
    slope = num / den
    intercept = y_mean - slope * x_mean
    return intercept + slope * target_idx


def _best_consecutive_chain(
    points: list[tuple[int, float]],
    *,
    rising: bool,
    min_len: int = 2,
) -> list[tuple[int, float]] | None:
    """Longest time-ordered chain of higher lows (UTL) or lower highs (DTL)."""
    if len(points) < min_len:
        return None
    sorted_pts = sorted(points, key=lambda p: p[0])
    best: list[tuple[int, float]] = []
    for start in range(len(sorted_pts)):
        chain = [sorted_pts[start]]
        for j in range(start + 1, len(sorted_pts)):
            nxt = sorted_pts[j]
            if rising and nxt[1] > chain[-1][1]:
                chain.append(nxt)
            elif not rising and nxt[1] < chain[-1][1]:
                chain.append(nxt)
        if len(chain) >= min_len and (
            len(chain) > len(best)
            or (
                len(chain) == len(best)
                and (
                    chain[-1][0] > best[-1][0]
                    or (
                        chain[-1][0] == best[-1][0]
                        and (chain[0][1] < best[0][1] if rising else chain[0][1] > best[0][1])
                    )
                )
            )
        ):
            best = chain
    return best if len(best) >= min_len else None


def _trendline_from_pivot_chain(
    chain: list[tuple[int, float]],
    *,
    last_idx: int,
    project_bars: int,
    times: list[int] | None,
    line_type: str,
) -> dict:
    fut_idx = last_idx + project_bars
    p1, p2 = chain[0], chain[-1]
    line_now = project_trendline(p1, p2, last_idx)
    line_future = project_trendline(p1, p2, fut_idx)
    span = p2[0] - p1[0]
    slope = (p2[1] - p1[1]) / span if span else 0.0
    return {
        "type": line_type,
        "p1": p1,
        "p2": p2,
        "chain": chain,
        "chain_len": len(chain),
        "slope_per_bar": round(slope, 4),
        "line_now": round(line_now, 2),
        "line_future": round(line_future, 2),
        "project_bars": project_bars,
        "future_idx": fut_idx,
        "future_date": _bar_date_label(fut_idx, times),
        "p1_date": _bar_date_label(p1[0], times),
        "p2_date": _bar_date_label(p2[0], times),
    }


def _cluster_pivot_prices(
    prices: list[float], tol: float,
) -> list[list[float]]:
    """Greedy cluster pivot prices within ATR tolerance."""
    if not prices or tol <= 0:
        return []
    clusters: list[list[float]] = []
    for price in sorted(prices):
        placed = False
        for cluster in clusters:
            center = sum(cluster) / len(cluster)
            if abs(price - center) <= tol:
                cluster.append(price)
                placed = True
                break
        if not placed:
            clusters.append([price])
    return clusters


def _count_bar_touches(
    bars: list[dict], center: float, tol: float, *, kind: str,
) -> int:
    count = 0
    for bar in bars:
        if kind == "resistance" and abs(bar["high"] - center) <= tol:
            count += 1
        elif kind == "support" and abs(bar["low"] - center) <= tol:
            count += 1
    return count


def compute_pivot_sr_bands(
    bars: list[dict],
    *,
    left: int = PIVOT_LEFT_BARS,
    right: int = PIVOT_RIGHT_BARS,
    atr_mult: float = SR_ATR_BAND_MULT,
    min_touches: int = SR_MIN_TOUCHES,
    max_bands: int = 8,
) -> list[dict]:
    """
    Strong horizontal S/R bands: pivot highs/lows (L/R bars) clustered within
    ATR×atr_mult; band valid when pivot count or bar touches >= min_touches.
    """
    if len(bars) < left + right + 3:
        return []
    atr_series = compute_atr_series(bars)
    atr = atr_series[-1] if atr_series else bars[-1]["high"] - bars[-1]["low"]
    tol = max(atr * atr_mult, bars[-1]["close"] * 0.002)
    close = bars[-1]["close"]

    pivot_highs, pivot_lows = find_pivot_points_indexed(bars, left=left, right=right)
    bands: list[dict] = []

    for kind, pivots in (("resistance", pivot_highs), ("support", pivot_lows)):
        prices = [p[1] for p in pivots]
        for cluster in _cluster_pivot_prices(prices, tol):
            if len(cluster) < min_touches:
                center = sum(cluster) / len(cluster)
                touches = _count_bar_touches(bars, center, tol, kind=kind)
                if touches < min_touches:
                    continue
            else:
                center = sum(cluster) / len(cluster)
                touches = max(len(cluster), _count_bar_touches(bars, center, tol, kind=kind))
            zlo = min(cluster) - tol * 0.15
            zhi = max(cluster) + tol * 0.15
            label = (
                f"{'R' if kind == 'resistance' else 'S'} band "
                f"({touches} touches, ATRx{atr_mult})"
            )
            bands.append({
                "kind": "zone",
                "side": kind,
                "price": round(center, 2),
                "zone_lo": round(zlo, 2),
                "zone_hi": round(zhi, 2),
                "touches": touches,
                "pivot_count": len(cluster),
                "label": label,
                "tf": "W1",
                "rank": 0 if kind == "resistance" else 1,
            })

    bands.sort(key=lambda b: abs(b["price"] - close))
    # De-overlap nearby bands
    kept: list[dict] = []
    for band in bands:
        if len(kept) >= max_bands:
            break
        zlo, zhi = band["zone_lo"], band["zone_hi"]
        if any(
            not (zhi + zlo * 0.02 < k["zone_lo"] or zlo - zlo * 0.02 > k["zone_hi"])
            for k in kept
        ):
            continue
        kept.append(band)
    return kept


def pivot_sr_bands_to_tv_shapes(
    bands: list[dict],
    times: list[int],
    *,
    close: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """Convert pivot S/R bands to TradingView rectangle specs."""
    if not bands or not times:
        return [], []
    ref_close = close if close is not None else bands[0]["price"]
    green = '{"linecolor": "#26a69a", "linewidth": 2, "backgroundColor": "rgba(38,166,154,0.28)"}'
    red = '{"linecolor": "#ef5350", "linewidth": 2, "backgroundColor": "rgba(239,83,80,0.28)"}'

    def pt(idx: int, price: float) -> dict:
        if idx < 0:
            idx = len(times) + idx
        i = max(0, min(idx, len(times) - 1))
        return {"time": times[i], "price": round(price, 2)}

    shapes: list[dict] = []
    for band in bands:
        zlo, zhi = band["zone_lo"], band["zone_hi"]
        style = green if zhi < ref_close else red
        shapes.append({
            "shape": "rectangle",
            "point": pt(0, zlo),
            "point2": pt(-1, zhi),
            "overrides": style,
            "label": band["label"],
        })
    return shapes, bands


def price_near_pivot_zone(
    price: float,
    band: dict,
    dist_pct: float = SR_CONFLUENCE_DIST_PCT,
) -> bool:
    """True if price inside band or within dist_pct of zone edge."""
    zlo, zhi = band["zone_lo"], band["zone_hi"]
    if zlo <= price <= zhi:
        return True
    if price <= 0:
        return False
    if price < zlo:
        return (zlo - price) / price <= dist_pct
    return (price - zhi) / price <= dist_pct


def nearest_pivot_band_at_price(
    bands: list[dict],
    price: float,
    *,
    side: str | None = None,
    dist_pct: float = SR_CONFLUENCE_DIST_PCT,
) -> dict | None:
    best: dict | None = None
    best_dist = float("inf")
    for band in bands:
        if side and band.get("side") != side:
            continue
        zlo, zhi = band["zone_lo"], band["zone_hi"]
        if price < zlo:
            d = zlo - price
        elif price > zhi:
            d = price - zhi
        else:
            d = 0.0
        rel = d / price if price else 0.0
        if rel <= dist_pct and rel < best_dist:
            best_dist = rel
            best = band
    return best


def pivot_band_to_area(band: dict, role: str) -> dict:
    zlo, zhi = band["zone_lo"], band["zone_hi"]
    mid = band["price"]
    touches = band.get("touches", SR_MIN_TOUCHES)
    side_label = "支持" if band.get("side") == "support" else "阻力"
    label = band.get("label") or f"{side_label} band"
    return {
        "zone_lo": zlo,
        "zone_hi": zhi,
        "mid": mid,
        "edge_count": touches,
        "sources": [f"W1 {label}"],
        "levels": [{"price": mid, "label": f"W1 {label}", "tf": "W1"}],
        "anchor": mid,
        "pivot_band": True,
        "role": role,
        "area_label": (
            f"{side_label}區 ${zlo:.2f}–${zhi:.2f}"
            f"（W1 pivot {touches} touches）"
        ),
    }


def pivot_bands_to_areas(
    bands: list[dict],
    close: float,
    role: str,
) -> list[dict]:
    """W1 pivot S/R bands as scenario areas (below/above close)."""
    out: list[dict] = []
    for band in bands:
        if role == "support" and band.get("side") != "support":
            continue
        if role == "resistance" and band.get("side") != "resistance":
            continue
        zlo, zhi = band["zone_lo"], band["zone_hi"]
        if role == "support" and zhi > close * 0.999:
            continue
        if role == "resistance" and zlo < close * 1.001:
            continue
        if role == "support" and (close - zhi) / close > SUPPORT_AREA_MAX_DIST_PCT:
            continue
        if role == "resistance" and (zlo - close) / close > RESISTANCE_AREA_MAX_DIST_PCT:
            continue
        out.append(pivot_band_to_area(band, role))
    return sorted(out, key=lambda a: abs(a["mid"] - close))


def compute_trendline_levels(bars: list[dict], lookback: int = 60) -> dict:
    """Legacy single-line UTL/DTL at last bar (kept for backward compat)."""
    if len(bars) < 10:
        return {}
    highs, lows = find_swing_points_indexed(bars, lookback)
    last_idx = len(bars) - 1
    out: dict = {}
    if len(lows) >= 2 and lows[-1][1] > lows[-2][1]:
        out["utl"] = round(project_trendline(lows[-2], lows[-1], last_idx), 2)
        out["utl_pts"] = (lows[-2], lows[-1])
    if len(highs) >= 2 and highs[-1][1] < highs[-2][1]:
        out["dtl"] = round(project_trendline(highs[-2], highs[-1], last_idx), 2)
        out["dtl_anchor"] = highs[-2][1]
        out["dtl_pts"] = (highs[-2], highs[-1])
    return out


def _bar_date_label(idx: int, times: list[int] | None) -> str:
    if times and 0 <= idx < len(times):
        return datetime.fromtimestamp(times[idx]).strftime("%Y-%m-%d")
    return f"bar {idx}"


def _bar_time_at_index(idx: int, times: list[int]) -> int:
    """Map bar index to unix time; extrapolate forward when idx >= len(times)."""
    if not times:
        return 0
    if 0 <= idx < len(times):
        return times[idx]
    step = times[-1] - times[-2] if len(times) >= 2 else 7 * 24 * 3600
    return times[-1] + step * (idx - (len(times) - 1))


def _parallel_rail(lower_p1: tuple[int, float], lower_p2: tuple[int, float], target_idx: int) -> float:
    return project_trendline(lower_p1, lower_p2, target_idx)


def compute_ascending_channel(
    bars: list[dict],
    *,
    lookback: int = CHANNEL_LOOKBACK_W1,
    recent_bars: int = CHANNEL_RECENT_W1,
    project_bars: int = CHANNEL_PROJECT_W1,
    times: list[int] | None = None,
) -> dict | None:
    """
    W1-style ascending parallel channel:
    - UTL (lower rail) through two rising swing lows
    - Upper rail parallel through the best swing high after the second low
    - Extension = upper rail projected forward by project_bars (time, not 2× measured move)
    """
    if len(bars) < 10:
        return None
    highs, lows = find_swing_points_indexed(bars, lookback)
    if not highs or not lows:
        return None

    last_idx = len(bars) - 1
    cutoff = max(0, last_idx - recent_bars)
    recent_highs = [hv for hv in highs if hv[0] >= cutoff]
    recent_lows = [lv for lv in lows if lv[0] >= cutoff]
    if len(recent_lows) < 2:
        return None

    anchor_hi = max(recent_highs, key=lambda hv: hv[1]) if recent_highs else None
    if anchor_hi:
        pre_peak_lows = [lv for lv in recent_lows if lv[0] < anchor_hi[0]]
        if len(pre_peak_lows) >= 2:
            p2 = max(pre_peak_lows, key=lambda lv: lv[0])
            candidates = [lv for lv in pre_peak_lows if lv[0] < p2[0] and lv[1] < p2[1]]
            in_range = [lv for lv in candidates if 6 <= p2[0] - lv[0] <= 15]
            if in_range:
                p1 = min(in_range, key=lambda lv: lv[0])
            elif candidates:
                p1 = min(candidates, key=lambda lv: lv[0])
            else:
                p1 = None
            if p1:
                slope = (p2[1] - p1[1]) / (p2[0] - p1[0])
                utl_at_hi = p1[1] + slope * (anchor_hi[0] - p1[0])
                width = anchor_hi[1] - utl_at_hi
                if width > 0:
                    fut_idx = last_idx + project_bars
                    utl_now = _parallel_rail(p1, p2, last_idx)
                    upper_now = utl_now + width
                    utl_future = _parallel_rail(p1, p2, fut_idx)
                    upper_future = utl_future + width
                    return {
                        "type": "ascending",
                        "utl_p1": p1,
                        "utl_p2": p2,
                        "upper_anchor": anchor_hi,
                        "channel_width": round(width, 2),
                        "slope_per_bar": round(slope, 4),
                        "utl_now": round(utl_now, 2),
                        "upper_now": round(upper_now, 2),
                        "utl_future": round(utl_future, 2),
                        "upper_future": round(upper_future, 2),
                        "project_bars": project_bars,
                        "future_idx": fut_idx,
                        "future_date": _bar_date_label(fut_idx, times),
                        "utl_p1_date": _bar_date_label(p1[0], times),
                        "utl_p2_date": _bar_date_label(p2[0], times),
                        "upper_anchor_date": _bar_date_label(anchor_hi[0], times),
                    }

    best: dict | None = None
    best_key: tuple = ()
    for i in range(len(recent_lows)):
        for j in range(i + 1, len(recent_lows)):
            p1, p2 = recent_lows[i], recent_lows[j]
            if p2[1] <= p1[1]:
                continue
            slope = (p2[1] - p1[1]) / (p2[0] - p1[0])
            hi_pool = [h for h in highs if h[0] >= p2[0]]
            if not hi_pool:
                continue
            anchor_hi = max(hi_pool, key=lambda h: h[1])
            utl_at_hi = p1[1] + slope * (anchor_hi[0] - p1[0])
            width = anchor_hi[1] - utl_at_hi
            if width <= 0:
                continue

            fut_idx = last_idx + project_bars
            utl_now = _parallel_rail(p1, p2, last_idx)
            upper_now = utl_now + width
            utl_future = _parallel_rail(p1, p2, fut_idx)
            upper_future = utl_future + width

            key = (anchor_hi[1], p2[0] - p1[0], -p1[0])
            if key > best_key:
                best_key = key
                best = {
                    "type": "ascending",
                    "utl_p1": p1,
                    "utl_p2": p2,
                    "upper_anchor": anchor_hi,
                    "channel_width": round(width, 2),
                    "slope_per_bar": round(slope, 4),
                    "utl_now": round(utl_now, 2),
                    "upper_now": round(upper_now, 2),
                    "utl_future": round(utl_future, 2),
                    "upper_future": round(upper_future, 2),
                    "project_bars": project_bars,
                    "future_idx": fut_idx,
                    "future_date": _bar_date_label(fut_idx, times),
                    "utl_p1_date": _bar_date_label(p1[0], times),
                    "utl_p2_date": _bar_date_label(p2[0], times),
                    "upper_anchor_date": _bar_date_label(anchor_hi[0], times),
                }
    return best


def compute_descending_channel(
    bars: list[dict],
    *,
    lookback: int = CHANNEL_LOOKBACK_W1,
    recent_bars: int = CHANNEL_RECENT_W1,
    project_bars: int = CHANNEL_PROJECT_W1,
    times: list[int] | None = None,
) -> dict | None:
    """
    Descending parallel channel (DTL reference):
    - DTL (upper rail) through two falling swing highs
    - Lower rail parallel through the deepest swing low after the second high
    - Extension = lower rail projected forward by project_bars
    """
    if len(bars) < 10:
        return None
    highs, lows = find_swing_points_indexed(bars, lookback)
    if not highs or not lows:
        return None

    last_idx = len(bars) - 1
    cutoff = max(0, last_idx - recent_bars)
    recent_highs = [hv for hv in highs if hv[0] >= cutoff]
    if len(recent_highs) < 2:
        return None

    def _build_descending(p1: tuple[int, float], p2: tuple[int, float]) -> dict | None:
        if p2[1] >= p1[1]:
            return None
        slope = (p2[1] - p1[1]) / (p2[0] - p1[0])
        lo_pool = [lv for lv in lows if lv[0] >= p2[0]]
        if not lo_pool:
            return None
        anchor_lo = min(lo_pool, key=lambda lv: lv[1])
        dtl_at_lo = p1[1] + slope * (anchor_lo[0] - p1[0])
        width = dtl_at_lo - anchor_lo[1]
        if width <= 0:
            return None
        fut_idx = last_idx + project_bars
        dtl_now = _parallel_rail(p1, p2, last_idx)
        lower_now = dtl_now - width
        dtl_future = _parallel_rail(p1, p2, fut_idx)
        lower_future = dtl_future - width
        return {
            "type": "descending",
            "dtl_p1": p1,
            "dtl_p2": p2,
            "lower_anchor": anchor_lo,
            "channel_width": round(width, 2),
            "slope_per_bar": round(slope, 4),
            "dtl_now": round(dtl_now, 2),
            "lower_now": round(lower_now, 2),
            "dtl_future": round(dtl_future, 2),
            "lower_future": round(lower_future, 2),
            "project_bars": project_bars,
            "future_idx": fut_idx,
            "future_date": _bar_date_label(fut_idx, times),
            "dtl_p1_date": _bar_date_label(p1[0], times),
            "dtl_p2_date": _bar_date_label(p2[0], times),
            "lower_anchor_date": _bar_date_label(anchor_lo[0], times),
        }

    # Prefer peak → lower-high DTL (post-crash bearish structure)
    peak = max(recent_highs, key=lambda hv: hv[1])
    after_peak = [hv for hv in recent_highs if hv[0] > peak[0] and hv[1] < peak[1]]
    if after_peak:
        p2 = max(after_peak, key=lambda hv: hv[0])
        built = _build_descending(peak, p2)
        if built:
            return built

    best: dict | None = None
    best_key: tuple = ()
    for i in range(len(recent_highs)):
        for j in range(i + 1, len(recent_highs)):
            p1, p2 = recent_highs[i], recent_highs[j]
            built = _build_descending(p1, p2)
            if not built:
                continue
            anchor_lo = built["lower_anchor"]
            key = (p1[1] - anchor_lo[1], p2[0] - p1[0], -p1[0])
            if key > best_key:
                best_key = key
                best = built
    return best


def _local_swing_highs_after(
    bars: list[dict], after_idx: int, *, use_close: bool = False,
) -> list[tuple[int, float]]:
    """3-bar swing highs strictly after after_idx (for post-peak DTL anchors)."""
    out: list[tuple[int, float]] = []
    key = "close" if use_close else "high"
    for i in range(after_idx + 1, len(bars) - 1):
        px = bars[i][key]
        if px >= bars[i - 1][key] and px >= bars[i + 1][key]:
            out.append((i, px))
    return out


def _local_swing_lows_after(
    bars: list[dict], after_idx: int, *, use_close: bool = False,
) -> list[tuple[int, float]]:
    """3-bar swing lows strictly after after_idx."""
    out: list[tuple[int, float]] = []
    key = "close" if use_close else "low"
    for i in range(after_idx + 1, len(bars) - 1):
        px = bars[i][key]
        if px <= bars[i - 1][key] and px <= bars[i + 1][key]:
            out.append((i, px))
    return out


def _post_peak_crash_trough_idx(
    bars: list[dict], peak_idx: int, *, window: int = 15,
) -> int | None:
    """Deepest close in the first `window` bars after peak (immediate crash, not later pullbacks)."""
    start = peak_idx + 1
    end = min(len(bars), start + window)
    if start >= end:
        return None
    return min(range(start, end), key=lambda i: bars[i]["close"])


def _scope_utl_pool(
    pool: list[tuple[int, float]], bars: list[dict] | None = None,
) -> list[tuple[int, float]]:
    """UTL: only pivots from the recent structural base (lowest pivot) forward."""
    if not pool:
        return pool
    base = min(pool, key=lambda p: p[1])
    scoped = [p for p in pool if p[0] >= base[0]]
    if bars and len(scoped) < 2:
        extra = _local_swing_lows_after(bars, base[0], use_close=True)
        merged = {p[0]: p for p in scoped}
        for pt in extra:
            merged[pt[0]] = pt
        scoped = sorted(merged.values(), key=lambda p: p[0])
    return scoped


def _chain_from_peak_lower_highs(
    pivot_highs: list[tuple[int, float]],
    bars: list[dict] | None = None,
    *,
    use_close: bool = False,
) -> list[tuple[int, float]] | None:
    """DTL: highest close pivot peak, then lower highs after post-crash trough."""
    if len(pivot_highs) < 1:
        return None
    peak = max(pivot_highs, key=lambda h: h[1])
    crash_idx = _post_peak_crash_trough_idx(bars, peak[0]) if bars else None
    after: list[tuple[int, float]] = [
        h for h in pivot_highs if h[0] > peak[0]
    ]
    if bars:
        after.extend(_local_swing_highs_after(bars, peak[0], use_close=use_close))
    by_idx: dict[int, float] = {}
    for idx, price in after:
        by_idx[idx] = max(by_idx.get(idx, 0), price)
    after_sorted = sorted(by_idx.items(), key=lambda x: x[0])
    chain = [peak]
    for idx, price in after_sorted:
        if crash_idx is not None and idx < crash_idx:
            continue
        if price < chain[-1][1]:
            chain.append((idx, price))
    return chain if len(chain) >= 2 else None


def _chain_ending_latest(
    points: list[tuple[int, float]], *, rising: bool, min_len: int = 2,
) -> list[tuple[int, float]] | None:
    """UTL/DTL fallback: walk back from the latest pivot to build a valid chain."""
    if len(points) < min_len:
        return None
    ordered = sorted(points, key=lambda p: p[0])
    end = ordered[-1]
    chain = [end]
    for pt in reversed(ordered[:-1]):
        if rising and pt[1] < chain[-1][1]:
            chain.append(pt)
        elif not rising and pt[1] > chain[-1][1]:
            chain.append(pt)
    chain.reverse()
    return chain if len(chain) >= min_len else None


def compute_utl_trendline(
    bars: list[dict],
    *,
    lookback: int = CHANNEL_LOOKBACK_W1,
    recent_bars: int = CHANNEL_RECENT_W1,
    project_bars: int = CHANNEL_PROJECT_W1,
    times: list[int] | None = None,
    pivot_left: int = PIVOT_LEFT_BARS,
    pivot_right: int = PIVOT_RIGHT_BARS,
) -> dict | None:
    """
    UTL: consecutive higher pivot lows on close (L/R bars), extended via line or OLS.
    """
    if len(bars) < pivot_left + pivot_right + 3:
        return None
    _, pivot_lows = find_pivot_points_indexed(
        bars, left=pivot_left, right=pivot_right, use_close=True,
    )
    if len(pivot_lows) < 2:
        return None

    last_idx = len(bars) - 1
    cutoff = max(0, last_idx - max(recent_bars, lookback))
    recent = [lv for lv in pivot_lows if lv[0] >= cutoff]
    pool = recent if len(recent) >= 2 else pivot_lows
    scoped = _scope_utl_pool(pool, bars)

    chain = _chain_ending_latest(scoped, rising=True, min_len=2)
    if not chain:
        chain = _best_consecutive_chain(scoped, rising=True, min_len=2)
    if not chain:
        return None
    return _trendline_from_pivot_chain(
        chain, last_idx=last_idx, project_bars=project_bars,
        times=times, line_type="utl",
    )


def compute_dtl_trendline(
    bars: list[dict],
    *,
    lookback: int = CHANNEL_LOOKBACK_W1,
    recent_bars: int = CHANNEL_RECENT_W1,
    project_bars: int = CHANNEL_PROJECT_W1,
    times: list[int] | None = None,
    pivot_left: int = PIVOT_LEFT_BARS,
    pivot_right: int = PIVOT_RIGHT_BARS,
) -> dict | None:
    """
    DTL: consecutive lower pivot highs on close (L/R bars), extended via line or OLS.
    """
    if len(bars) < pivot_left + pivot_right + 3:
        return None
    pivot_highs, _ = find_pivot_points_indexed(
        bars, left=pivot_left, right=pivot_right, use_close=True,
    )
    if len(pivot_highs) < 2:
        return None

    last_idx = len(bars) - 1
    cutoff = max(0, last_idx - max(recent_bars, lookback))
    recent = [hv for hv in pivot_highs if hv[0] >= cutoff]
    pool = recent if len(recent) >= 2 else pivot_highs

    chain = _chain_from_peak_lower_highs(pool, bars, use_close=True)
    if not chain:
        chain = _chain_ending_latest(pool, rising=False, min_len=2)
    if not chain:
        chain = _best_consecutive_chain(pool, rising=False, min_len=2)
    if not chain:
        return None
    return _trendline_from_pivot_chain(
        chain, last_idx=last_idx, project_bars=project_bars,
        times=times, line_type="dtl",
    )


def _pick_channel_primary(
    w1_bars: list[dict],
    ascending: dict | None,
    descending: dict | None,
) -> str:
    """Pick UTL vs DTL from recent swing structure (lower highs → DTL)."""
    if descending and not ascending:
        return "descending"
    if ascending and not descending:
        return "ascending"
    if not ascending and not descending:
        return "none"

    highs, lows = find_swing_points_indexed(w1_bars, CHANNEL_LOOKBACK_W1)
    last_idx = len(w1_bars) - 1
    cutoff = max(0, last_idx - CHANNEL_RECENT_W1)
    recent_highs = sorted([h for h in highs if h[0] >= cutoff], key=lambda h: h[0])
    recent_lows = sorted([lv for lv in lows if lv[0] >= cutoff], key=lambda lv: lv[0])
    close = w1_bars[-1]["close"]

    if len(recent_highs) >= 2 and recent_highs[-1][1] < recent_highs[-2][1] * 0.995:
        return "descending"

    if recent_highs:
        peak = max(recent_highs, key=lambda h: h[1])
        if peak[1] > close * 1.12 and peak[0] < last_idx - 3:
            return "descending"

    if len(recent_lows) >= 2 and recent_lows[-1][1] < recent_lows[-2][1] * 0.995:
        return "descending"

    return "ascending"


def compute_w1_channel_reference(
    w1_bars: list[dict],
    *,
    times: list[int] | None = None,
    project_bars: int = CHANNEL_PROJECT_W1,
) -> dict:
    """W1 parallel channel reference for chart display (not Setup TP)."""
    ascending = compute_ascending_channel(
        w1_bars, times=times, project_bars=project_bars,
    )
    descending = compute_descending_channel(
        w1_bars, times=times, project_bars=project_bars,
    )
    primary = _pick_channel_primary(w1_bars, ascending, descending)
    utl_line = compute_utl_trendline(
        w1_bars, times=times, project_bars=project_bars,
    )
    dtl_line = compute_dtl_trendline(
        w1_bars, times=times, project_bars=project_bars,
    )
    return {
        "tf": "W1",
        "primary": primary,
        "ascending": ascending,
        "descending": descending,
        "utl_line": utl_line,
        "dtl_line": dtl_line,
    }


def channel_ref_to_tv_shapes(
    channel_ref: dict,
    times: list[int],
    *,
    draw_parallel_channels: bool = False,
) -> list[dict]:
    """
    Convert W1 channel reference to TradingView draw_shape specs.

    Default: simple UTL (green) + DTL (orange/red) trendlines — each 2 segments
    (solid p1→today, dashed forward extension). Parallel channel rails only when
    draw_parallel_channels=True.
    """
    if not channel_ref or not times:
        return []

    green = '{"linecolor": "#26A69A", "linewidth": 2, "linestyle": 0}'
    green_dash = '{"linecolor": "#26A69A", "linewidth": 2, "linestyle": 2}'
    red = '{"linecolor": "#F44336", "linewidth": 2, "linestyle": 0}'
    red_dash = '{"linecolor": "#F44336", "linewidth": 2, "linestyle": 2}'
    blue = '{"linecolor": "#2962FF", "linewidth": 2, "linestyle": 0}'
    blue_dash = '{"linecolor": "#2962FF", "linewidth": 2, "linestyle": 2}'
    orange = '{"linecolor": "#FF9800", "linewidth": 2, "linestyle": 0}'
    orange_dash = '{"linecolor": "#FF9800", "linewidth": 2, "linestyle": 2}'

    def pt(idx: int, price: float) -> dict:
        return {"time": _bar_time_at_index(idx, times), "price": round(price, 2)}

    def trendline_segments(
        p1: tuple[int, float],
        p2: tuple[int, float],
        fut_idx: int,
        fut_price: float,
        *,
        solid_style: str,
        dash_style: str,
        label_prefix: str,
        chain: list[tuple[int, float]] | None = None,
    ) -> list[dict]:
        """Solid p1→today (through p2), dashed extension forward in time."""
        last_idx = len(times) - 1
        solid_end = max(p2[0], last_idx)
        y_solid_end = project_trendline(p1, p2, solid_end)
        return [
            {
                "shape": "trend_line",
                "point": pt(p1[0], p1[1]),
                "point2": pt(solid_end, y_solid_end),
                "overrides": solid_style,
                "label": f"{label_prefix} p1→today",
            },
            {
                "shape": "trend_line",
                "point": pt(solid_end, y_solid_end),
                "point2": pt(fut_idx, fut_price),
                "overrides": dash_style,
                "label": f"{label_prefix} extend",
            },
        ]

    shapes: list[dict] = []

    utl = channel_ref.get("utl_line")
    if utl:
        p1, p2 = utl["p1"], utl["p2"]
        shapes.extend(
            trendline_segments(
                p1, p2, utl["future_idx"], utl["line_future"],
                solid_style=green, dash_style=green_dash,
                label_prefix="UTL",
                chain=utl.get("chain"),
            )
        )

    dtl = channel_ref.get("dtl_line")
    if dtl:
        p1, p2 = dtl["p1"], dtl["p2"]
        shapes.extend(
            trendline_segments(
                p1, p2, dtl["future_idx"], dtl["line_future"],
                solid_style=red, dash_style=red_dash,
                label_prefix="DTL",
                chain=dtl.get("chain"),
            )
        )

    if not draw_parallel_channels:
        return shapes

    asc = channel_ref.get("ascending")
    desc = channel_ref.get("descending")

    if asc:
        p1, p2 = asc["utl_p1"], asc["utl_p2"]
        width = asc["channel_width"]
        fut = asc["future_idx"]
        upper_p1 = p1[1] + width
        upper_p2 = p2[1] + width
        solid_end = max(p2[0], len(times) - 1)
        upper_solid_end = project_trendline(
            (p1[0], upper_p1), (p2[0], upper_p2), solid_end,
        )
        shapes.extend(
            trendline_segments(
                p1, p2, fut, asc["utl_future"],
                solid_style=blue, dash_style=blue_dash,
                label_prefix="UTL lower",
            )
        )
        shapes.extend([
            {
                "shape": "trend_line",
                "point": pt(p1[0], upper_p1),
                "point2": pt(solid_end, upper_solid_end),
                "overrides": blue,
                "label": "UTL upper p1→today",
            },
            {
                "shape": "trend_line",
                "point": pt(solid_end, upper_solid_end),
                "point2": pt(fut, asc["upper_future"]),
                "overrides": blue_dash,
                "label": "UTL upper extend",
            },
        ])

    if desc:
        p1, p2 = desc["dtl_p1"], desc["dtl_p2"]
        width = desc["channel_width"]
        fut = desc["future_idx"]
        lower_p1 = p1[1] - width
        lower_p2 = p2[1] - width
        solid_end = max(p2[0], len(times) - 1)
        lower_solid_end = project_trendline(
            (p1[0], lower_p1), (p2[0], lower_p2), solid_end,
        )
        shapes.extend(
            trendline_segments(
                p1, p2, fut, desc["dtl_future"],
                solid_style=orange, dash_style=orange_dash,
                label_prefix="DTL upper",
            )
        )
        shapes.extend([
            {
                "shape": "trend_line",
                "point": pt(p1[0], lower_p1),
                "point2": pt(solid_end, lower_solid_end),
                "overrides": orange,
                "label": "DTL lower p1→today",
            },
            {
                "shape": "trend_line",
                "point": pt(solid_end, lower_solid_end),
                "point2": pt(fut, desc["lower_future"]),
                "overrides": orange_dash,
                "label": "DTL lower extend",
            },
        ])

    return shapes


SR_DRAW_DEDUP_PCT = 0.035
SR_DRAW_MAX_ZONES = 8


def _zones_overlap(
    zlo: float, zhi: float, blo: float, bhi: float, tol_pct: float = SR_DRAW_DEDUP_PCT,
) -> bool:
    pad_lo = min(zlo, blo) * tol_pct
    return not (zhi + pad_lo < blo or zlo - pad_lo > bhi)


def collect_sr_draw_levels(
    d1: dict,
    bars: list[dict] | None = None,
    *,
    max_levels: int = SR_DRAW_MAX_ZONES,
) -> list[dict]:
    """
    S/R for TradingView — nearby levels merged into areas (zones), not many单线.
    """
    c = d1["close"]
    items: list[dict] = []
    spans: list[tuple[float, float]] = []

    def try_add(item: dict) -> bool:
        zlo = round(item.get("zone_lo") or item["price"], 2)
        zhi = round(item.get("zone_hi") or item["price"], 2)
        if zhi < zlo:
            zlo, zhi = zhi, zlo
        if zhi <= zlo * 1.0005:
            zhi = zlo * 1.005
        for blo, bhi in spans:
            if _zones_overlap(zlo, zhi, blo, bhi):
                return False
        spans.append((zlo, zhi))
        item = {**item, "zone_lo": zlo, "zone_hi": zhi, "kind": "zone"}
        items.append(item)
        return True

    # Primary: W1 pivot S/R bands (same logic as Pine / draw_utl_dtl_tv)
    for band in sorted(
        d1.get("pivot_sr_bands") or [],
        key=lambda b: abs(b["price"] - c),
    ):
        if len(items) >= max_levels:
            break
        side = "R" if band.get("side") == "resistance" else "S"
        touches = band.get("touches", SR_MIN_TOUCHES)
        try_add({
            "price": band["price"],
            "zone_lo": band["zone_lo"],
            "zone_hi": band["zone_hi"],
            "label": f"W1 {side} band ({touches} touches)",
            "tf": "W1",
            "rank": 0,
        })

    if len(items) >= max_levels:
        return items[:max_levels]

    area = d1.get("trading_area") or {}
    zlo = area.get("zone_lo") or 0
    zhi = area.get("zone_hi") or zlo
    if zlo > 0:
        ptf = area.get("primary_tf") or "D1"
        ec = area.get("edge_count") or 0
        src = " / ".join((area.get("sources") or [])[:2])
        lbl = f"{ptf} Multiple edge ({ec}源:{src})" if ec >= 2 else f"{ptf} Trading area"
        try_add({"price": (zlo + zhi) / 2, "label": lbl, "tf": ptf, "rank": 0})

    if len(items) >= max_levels:
        return items[:max_levels]

    c = d1["close"]
    sup_levels = [
        {"price": p, "label": _format_sr_keylevel_label(src, "support"), "tf": _parse_tf_source(src)[0] or "D1"}
        for src, p in (d1.get("all_support_sources") or [])
        if p and p < c * 0.999
    ]
    res_levels = [
        {"price": p, "label": _format_sr_keylevel_label(src, "resistance"), "tf": _parse_tf_source(src)[0] or "D1"}
        for src, p in (d1.get("all_resistance_sources") or [])
        if p and p > c * 1.001
    ]
    for area in cluster_levels_to_areas(sup_levels):
        if len(items) >= max_levels:
            break
        try_add({
            "price": area["mid"],
            "label": area.get("area_label") or f"支持區 {format_area_range(area['zone_lo'], area['zone_hi'])}",
            "tf": "D1",
            "rank": 1,
        })
    for area in cluster_levels_to_areas(res_levels):
        if len(items) >= max_levels:
            break
        try_add({
            "price": area["mid"],
            "label": area.get("area_label") or f"阻力區 {format_area_range(area['zone_lo'], area['zone_hi'])}",
            "tf": "D1",
            "rank": 1,
        })

    for tf_key, px_key, base_lbl in (
        ("wave_top_tf", "wave_top", "前浪頂"),
        ("wave_bottom_tf", "wave_bottom", "前浪底"),
    ):
        if len(items) >= max_levels:
            break
        ptf = d1.get(tf_key) or "D1"
        px = d1.get(px_key) or 0
        if px and ptf == "W1":
            try_add({
                "price": px,
                "label": f"{ptf} {base_lbl}",
                "tf": ptf,
                "rank": 2,
            })

    resist_60 = d1.get("resistance") or 0
    if resist_60 and len(items) < max_levels:
        try_add({
            "price": resist_60,
            "label": "W1 60日高",
            "tf": "W1",
            "rank": 2,
        })

    return items[:max_levels]


def key_sr_zones(
    d1: dict,
    bars: list[dict] | None = None,
    *,
    max_levels: int = SR_DRAW_MAX_ZONES,
) -> list[dict]:
    """Canonical 關鍵 S/R zones — pivot bands + merged sources (all analysis uses this)."""
    return collect_sr_draw_levels(d1, bars=bars, max_levels=max_levels)


def _key_sr_zone_as_level(zone: dict, *, side: str) -> dict:
    zlo = round(zone["zone_lo"], 2)
    zhi = round(zone["zone_hi"], 2)
    tf = zone.get("tf") or "W1"
    price = zlo if side == "support" else zhi
    return {
        "price": price,
        "zone_lo": zlo,
        "zone_hi": zhi,
        "label": zone["label"],
        "tf": tf,
        "priority": TF_PRIORITY.get(tf, 9),
    }


def key_sr_levels_below(
    d1: dict,
    bars: list[dict] | None,
    ref: float,
) -> list[dict]:
    """Key S/R support levels below ref (nearest first)."""
    out = [
        _key_sr_zone_as_level(z, side="support")
        for z in key_sr_zones(d1, bars)
        if z["zone_hi"] < ref * 0.997
    ]
    return sorted(out, key=lambda lv: (-lv["price"], lv["priority"]))


def key_sr_levels_above(
    d1: dict,
    bars: list[dict] | None,
    ref: float,
) -> list[dict]:
    """Key S/R resistance levels above ref (nearest first)."""
    out = [
        _key_sr_zone_as_level(z, side="resistance")
        for z in key_sr_zones(d1, bars)
        if z["zone_lo"] > ref * 1.003
    ]
    return sorted(out, key=lambda lv: (lv["price"], lv["priority"]))


def find_key_sr_zone_for_price(
    d1: dict,
    bars: list[dict] | None,
    price: float,
    *,
    tol: float = 0.03,
) -> dict | None:
    for z in key_sr_zones(d1, bars):
        if z["zone_lo"] - tol <= price <= z["zone_hi"] + tol:
            return z
    return None


def analyze_sr_merged(
    d1_bars: list[dict],
    *,
    w1_bars: list[dict] | None = None,
    h1_bars: list[dict] | None = None,
) -> tuple[dict, dict | None, dict | None]:
    """Minimal analyze_bars + merge_swing_sr path for S/R drawing (no RS/MTF)."""
    d1 = analyze_bars(d1_bars)
    w1 = analyze_bars(w1_bars) if w1_bars else None
    h1 = analyze_bars(h1_bars) if h1_bars else None
    d1 = merge_swing_sr(d1, d1_bars, w1=w1, w1_bars=w1_bars, h1=h1, h1_bars=h1_bars)
    return d1, w1, h1


def _prices_near_pct(a: float, b: float, tol_pct: float = SR_DRAW_DEDUP_PCT) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / min(a, b) <= tol_pct


def sr_levels_to_tv_shapes(
    d1: dict,
    times: list[int],
    *,
    bars: list[dict] | None = None,
    max_levels: int = 15,
) -> tuple[list[dict], list[dict]]:
    """Convert merged D1 S/R analysis to TradingView draw_shape specs + level list."""
    if not times:
        return [], []

    levels = collect_sr_draw_levels(d1, bars=bars, max_levels=max_levels)
    if not levels:
        return [], []

    green = '{"linecolor": "#26a69a", "linewidth": 1, "backgroundColor": "rgba(38,166,154,0.12)"}'
    red = '{"linecolor": "#ef5350", "linewidth": 1, "backgroundColor": "rgba(239,83,80,0.12)"}'

    def pt(idx: int, price: float) -> dict:
        if idx < 0:
            idx = len(times) + idx
        i = max(0, min(idx, len(times) - 1))
        return {"time": times[i], "price": round(price, 2)}

    shapes: list[dict] = []
    for lv in levels:
        zlo, zhi = lv["zone_lo"], lv["zone_hi"]
        style = green if zhi < d1["close"] else red
        shapes.append({
            "shape": "rectangle",
            "point": pt(0, zlo),
            "point2": pt(-1, zhi),
            "overrides": style,
            "label": lv["label"],
        })

    return shapes, levels


def format_sr_key_levels_section(d1: dict, bars: list[dict] | None = None) -> list[str]:
    """Key S/R levels used for analysis + chart draw."""
    levels = key_sr_zones(d1, bars=bars, max_levels=10)
    if not levels:
        return []
    lines = ["### 關鍵 S/R 水平", ""]
    for lv in levels:
        if lv["kind"] == "zone":
            lines.append(f"- **{lv['label']}**：{format_area_range(lv['zone_lo'], lv['zone_hi'])}")
        else:
            lines.append(f"- **{lv['label']}**：${lv['price']:.2f}")
    lines.append("")
    return lines


def _price_matches(a: float, b: float, tol: float = 0.02) -> bool:
    return a > 0 and b > 0 and abs(a - b) <= tol


def _normalize_source_label(src: str) -> str:
    if ":" in src:
        return src.split(":", 1)[1]
    return src


def _parse_tf_source(src: str) -> tuple[str, str]:
    if ":" in src:
        tf, label = src.split(":", 1)
        return tf, label
    return "", src


def _format_sr_keylevel_label(src: str, role: str = "support", default_tf: str = "") -> str:
    """Human-readable key level with TF prefix and MA direction."""
    tf, label = _parse_tf_source(src)
    if not tf and default_tf:
        tf = default_tf
    if "(阻力→支持)" in label:
        out = label.replace("(阻力→支持)", "（阻力→支持）")
    elif "(支持→阻力)" in label:
        out = label.replace("(支持→阻力)", "（支持→阻力）")
    elif "(平)" in label:
        out = label.replace("(平)", "（平=支持）" if role == "support" else "（平=阻力）")
    elif any(x in label for x in ("MA", "EMA")) and "（" not in label:
        out = f"{label}（向上=支持）" if role == "support" else f"{label}（向下=阻力）"
    else:
        out = label
    return f"{tf} {out}" if tf else out


def collect_structural_support_levels(d1: dict) -> list[dict]:
    """Structural supports — pivot bands + merged support sources (feeds key S/R)."""
    levels: list[dict] = []
    seen: set[float] = set()
    c = d1.get("close") or 0

    def add(price: float, label: str, tf: str) -> None:
        p = round(price, 2)
        if p <= 0 or p in seen:
            return
        if c and p >= c * 0.999:
            return
        seen.add(p)
        levels.append({
            "price": p,
            "label": label,
            "tf": tf,
            "priority": TF_PRIORITY.get(tf, 9),
        })

    for band in d1.get("pivot_sr_bands") or []:
        if band.get("side") != "support":
            continue
        add(band.get("zone_lo") or band["price"], f"W1 {band['label']}", "W1")

    for src, price in d1.get("all_support_sources") or []:
        tf, _ = _parse_tf_source(src)
        add(price, _format_sr_keylevel_label(src, "support"), tf or "D1")

    return levels


def collect_structural_resistance_levels(d1: dict) -> list[dict]:
    """Structural resistances — pivot bands + merged resistance sources (feeds key S/R)."""
    levels: list[dict] = []
    seen: set[float] = set()
    c = d1.get("close") or 0

    def add(price: float, label: str, tf: str) -> None:
        p = round(price, 2)
        if p <= 0 or p in seen:
            return
        if c and p <= c * 1.001:
            return
        seen.add(p)
        levels.append({
            "price": p,
            "label": label,
            "tf": tf,
            "priority": TF_PRIORITY.get(tf, 9),
        })

    for band in d1.get("pivot_sr_bands") or []:
        if band.get("side") != "resistance":
            continue
        add(band.get("zone_hi") or band["price"], f"W1 {band['label']}", "W1")

    for src, price in d1.get("all_resistance_sources") or []:
        tf, _ = _parse_tf_source(src)
        add(price, _format_sr_keylevel_label(src, "resistance"), tf or "D1")

    return levels


def format_area_range(zone_lo: float, zone_hi: float) -> str:
    """Format S/R area as range or single price."""
    lo, hi = round(zone_lo, 2), round(zone_hi, 2)
    if hi <= lo * 1.002:
        return f"${lo:.2f}"
    return f"${lo:.2f}–${hi:.2f}"


def cluster_levels_to_areas(
    levels: list[dict],
    tol_pct: float = SCENARIO_AREA_BAND_PCT,
    max_span_pct: float = SCENARIO_AREA_MAX_SPAN_PCT,
) -> list[dict]:
    """
    Group structural levels into S/R areas — anchor ±tol band (same as Edge #2).
    No chain-merge across distant levels; each area span capped at max_span_pct.
    """
    if not levels:
        return []
    pool = sorted(levels, key=lambda x: x["price"])
    candidates: list[dict] = []

    for lv in pool:
        anchor = lv["price"]
        band_lo = anchor * (1 - tol_pct)
        band_hi = anchor * (1 + tol_pct)
        in_band = [x for x in pool if band_lo <= x["price"] <= band_hi]
        if not in_band:
            continue
        zone_lo = min(x["price"] for x in in_band)
        zone_hi = max(x["price"] for x in in_band)
        if zone_lo <= 0 or (zone_hi - zone_lo) / zone_lo > max_span_pct:
            continue
        sources = list(dict.fromkeys(x["label"] for x in in_band))
        candidates.append({
            "zone_lo": round(zone_lo, 2),
            "zone_hi": round(zone_hi, 2),
            "mid": round((zone_lo + zone_hi) / 2, 2),
            "edge_count": len(in_band),
            "sources": sources,
            "levels": in_band,
            "anchor": anchor,
        })

    if not candidates:
        return []

    deduped: dict[tuple[float, float], dict] = {}
    for area in sorted(
        candidates,
        key=lambda a: (-a["edge_count"], a["zone_hi"] - a["zone_lo"]),
    ):
        key = (area["zone_lo"], area["zone_hi"])
        if key in deduped:
            continue
        dominated = False
        for kept in deduped.values():
            if (
                area["zone_lo"] >= kept["zone_lo"]
                and area["zone_hi"] <= kept["zone_hi"]
                and kept["edge_count"] >= area["edge_count"]
            ):
                dominated = True
                break
        if not dominated:
            deduped[key] = area

    return sorted(deduped.values(), key=lambda a: a["zone_hi"], reverse=True)


def area_support_valid(bars: list[dict] | None, area: dict) -> bool:
    """Support area still active — brief pierce OK, sustained break down = invalid."""
    if not bars:
        return True
    return not sustained_break(bars, area["zone_lo"], "down")


def area_resistance_valid(bars: list[dict] | None, area: dict) -> bool:
    """Resistance area still active — brief pierce OK, sustained break up = invalid."""
    if not bars:
        return True
    return not sustained_break(bars, area["zone_hi"], "up")


def area_flipped_to_support(bars: list[dict] | None, area: dict) -> bool:
    """Former resistance area acting as support after sustained break up + hold."""
    if not bars:
        return False
    last = bars[-1]
    return (
        sustained_break(bars, area["zone_hi"], "up")
        and last["close"] >= area["zone_lo"]
    )


def area_flipped_to_resistance(bars: list[dict] | None, area: dict) -> bool:
    """Former support area acting as resistance after sustained break down + hold."""
    if not bars:
        return False
    last = bars[-1]
    return (
        sustained_break(bars, area["zone_lo"], "down")
        and last["close"] <= area["zone_hi"]
    )


def _dedupe_areas(areas: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[float, float]] = set()
    for area in areas:
        key = (area["zone_lo"], area["zone_hi"])
        if key in seen:
            continue
        seen.add(key)
        out.append(area)
    return out


def _select_non_overlapping_areas(
    areas: list[dict],
    max_n: int,
    reference: float,
) -> list[dict]:
    """Pick distinct S/R areas for scenarios — no overlapping bands."""
    ranked = sorted(
        areas,
        key=lambda a: (reference - a["zone_hi"], -a["edge_count"], a["zone_hi"] - a["zone_lo"]),
    )
    picked: list[dict] = []
    for area in ranked:
        if any(
            area["zone_lo"] <= kept["zone_hi"] and kept["zone_lo"] <= area["zone_hi"]
            for kept in picked
        ):
            continue
        picked.append(area)
        if len(picked) >= max_n:
            break
    return sorted(picked, key=lambda a: a["zone_hi"], reverse=True)


def build_support_areas(
    d1: dict,
    bars: list[dict] | None = None,
    tol_pct: float = SCENARIO_AREA_BAND_PCT,
) -> list[dict]:
    """Support areas below current price from merged W1+D1+H1 levels."""
    c = d1["close"]
    levels = [
        lv for lv in collect_structural_support_levels(d1)
        if lv["price"] < c * 0.999
        and (c - lv["price"]) / c <= SUPPORT_AREA_MAX_DIST_PCT
    ]
    areas = cluster_levels_to_areas(levels, tol_pct)
    flipped: list[dict] = []
    if bars:
        res_levels = [
            lv for lv in collect_structural_resistance_levels(d1)
            if lv["price"] < c * 0.999
            and (c - lv["price"]) / c <= SUPPORT_AREA_MAX_DIST_PCT
        ]
        for area in cluster_levels_to_areas(res_levels, tol_pct):
            if area_flipped_to_support(bars, area):
                area = dict(area)
                area["flipped"] = True
                flipped.append(area)

    valid: list[dict] = []
    for area in areas:
        if bars and not area_support_valid(bars, area):
            continue
        valid.append(area)
    valid.extend(flipped)
    pivot_areas = [
        a for a in pivot_bands_to_areas(d1.get("pivot_sr_bands") or [], c, "support")
        if not bars or area_support_valid(bars, a)
    ]
    valid = _dedupe_areas(pivot_areas + valid)

    for area in valid:
        area["role"] = "support"
        flip_tag = "·阻力→支持" if area.get("flipped") else ""
        src = " / ".join(area["sources"][:3])
        if len(area["sources"]) > 3:
            src += f" +{len(area['sources']) - 3}"
        area["area_label"] = (
            f"支持區 {format_area_range(area['zone_lo'], area['zone_hi'])}"
            f"（{area['edge_count']}源:{src}{flip_tag}）"
        )
    areas = sorted(valid, key=lambda a: a["zone_hi"], reverse=True)
    multi = [a for a in areas if a["edge_count"] >= 2]
    single_near = [
        a for a in areas if a["edge_count"] < 2
        and (c - a["zone_hi"]) / c <= 0.08
    ]
    pool = multi if multi else single_near
    out = _select_non_overlapping_areas(pool or areas, MAX_SUPPORT_AREAS, c)
    return out


def build_resistance_areas(
    d1: dict,
    bars: list[dict] | None = None,
    tol_pct: float = SCENARIO_AREA_BAND_PCT,
) -> list[dict]:
    """Resistance areas above current price from merged W1+D1+H1 levels."""
    c = d1["close"]
    levels = [
        lv for lv in collect_structural_resistance_levels(d1)
        if lv["price"] > c * 1.001
        and (lv["price"] - c) / c <= RESISTANCE_AREA_MAX_DIST_PCT
    ]
    # Always include canonical HTF wave top even if slightly farther
    wt = d1.get("wave_top") or 0
    wt_tf = d1.get("wave_top_tf") or "D1"
    if wt > c and not any(_price_matches(wt, lv["price"]) for lv in levels):
        levels.append({
            "price": round(wt, 2),
            "label": f"{wt_tf} 前浪頂",
            "tf": wt_tf,
            "priority": TF_PRIORITY.get(wt_tf, 9),
        })
    areas = cluster_levels_to_areas(levels, tol_pct)
    flipped: list[dict] = []
    if bars:
        sup_levels = [
            lv for lv in collect_structural_support_levels(d1)
            if lv["price"] > c * 1.001
            and (lv["price"] - c) / c <= RESISTANCE_AREA_MAX_DIST_PCT
        ]
        for area in cluster_levels_to_areas(sup_levels, tol_pct):
            if area_flipped_to_resistance(bars, area):
                area = dict(area)
                area["flipped"] = True
                flipped.append(area)

    valid: list[dict] = []
    for area in areas:
        if bars and not area_resistance_valid(bars, area):
            continue
        valid.append(area)
    valid.extend(flipped)
    pivot_areas = [
        a for a in pivot_bands_to_areas(d1.get("pivot_sr_bands") or [], c, "resistance")
        if not bars or area_resistance_valid(bars, a)
    ]
    valid = _dedupe_areas(pivot_areas + valid)

    for area in valid:
        area["role"] = "resistance"
        flip_tag = "·支持→阻力" if area.get("flipped") else ""
        src = " / ".join(area["sources"][:3])
        if len(area["sources"]) > 3:
            src += f" +{len(area['sources']) - 3}"
        area["area_label"] = (
            f"阻力區 {format_area_range(area['zone_lo'], area['zone_hi'])}"
            f"（{area['edge_count']}源:{src}{flip_tag}）"
        )
    areas = sorted(valid, key=lambda a: a["zone_lo"])
    return areas[:MAX_RESISTANCE_AREAS]


def find_area_for_price(price: float, areas: list[dict], tol: float = 0.03) -> dict | None:
    """Find S/R area containing a price (for stop/target labels)."""
    for area in areas:
        if area["zone_lo"] - tol <= price <= area["zone_hi"] + tol:
            return area
    return None


def _pick_nearest_structural_level(
    levels: list[dict],
    reference: float,
    *,
    below: bool,
    max_dist_pct: float = 0.10,
    prefer_tight: bool = True,
) -> dict | None:
    """Pick structural level; W1>D1>H1 tiebreak at similar distance."""
    if below:
        pool = [lv for lv in levels if lv["price"] < reference]
        if not pool:
            return None
        dist = lambda lv: reference - lv["price"]
    else:
        pool = [lv for lv in levels if lv["price"] > reference]
        if not pool:
            return None
        dist = lambda lv: lv["price"] - reference

    near = [lv for lv in pool if dist(lv) <= reference * max_dist_pct]
    candidates = near if near else pool
    if prefer_tight:
        return min(candidates, key=lambda lv: (dist(lv), lv["priority"]))
    return max(candidates, key=lambda lv: (lv["price"], -lv["priority"]))


def resolve_stop_keylevel(
    d1: dict,
    stop: float,
    setup_kind: str = "current",
    bars: list[dict] | None = None,
) -> str:
    """Human-readable stop key level from 關鍵 S/R."""
    z = find_key_sr_zone_for_price(d1, bars, stop)
    if z:
        return z["label"]
    for lv in key_sr_levels_below(d1, bars, stop + 0.01):
        if _price_matches(stop, lv["price"]):
            return lv["label"]
    return ""


def resolve_tp2_keylevel(d1: dict, reward_type: str, reward_label: str) -> str:
    if reward_type == "UTL":
        return "UTL 通道延伸"
    if reward_type == "DTL":
        return "DTL 突破量度"
    if reward_type == "fixed_2r":
        return "2R fallback"
    if reward_label:
        return reward_label.split(" $")[0]
    return "—"


def _supports_for_stop(
    d1: dict,
    entry: float,
    setup_kind: str,
    bars: list[dict] | None = None,
) -> list[dict]:
    """Key S/R supports below entry."""
    supports = key_sr_levels_below(d1, bars, entry)
    if setup_kind != "retest":
        return supports
    zone = find_key_sr_zone_for_price(d1, bars, entry)
    if not zone:
        return supports
    zone_band_lo = zone["zone_lo"] * (1 - SR_CONFLUENCE_BAND_PCT)
    return [lv for lv in supports if lv["price"] < zone_band_lo]


def pick_structure_stop(
    d1: dict,
    entry: float,
    setup_kind: str = "current",
    bars: list[dict] | None = None,
) -> tuple[float, str]:
    """Structure-based stop from 關鍵 S/R support below entry."""
    supports = _supports_for_stop(d1, entry, setup_kind, bars)
    if setup_kind in ("breakout", "current"):
        swing_pool = [lv for lv in supports if lv.get("tf") in SWING_STOP_TFS]
        pool = swing_pool if swing_pool else supports
        pick = _pick_nearest_structural_level(
            pool, entry, below=True, prefer_tight=False,
        )
    else:
        pick = _pick_nearest_structural_level(
            supports, entry, below=True, prefer_tight=True,
        )
    if not pick:
        return 0, ""
    z = find_key_sr_zone_for_price(d1, bars, pick["price"])
    if z:
        return pick["price"], f"{z['label']} {format_area_range(z['zone_lo'], z['zone_hi'])} ${pick['price']:.2f}"
    return pick["price"], f"{pick['label']} ${pick['price']:.2f}"


def pick_structure_stop_short(d1: dict, entry: float, setup_kind: str = "current") -> tuple[float, str]:
    """Short stop above resistance structure."""
    area = d1.get("trading_area") or {}
    zone_hi = area.get("zone_hi") or 0
    wave_top = d1.get("wave_top") or 0
    resist = d1["resistance"]

    candidates: list[tuple[float, str, float]] = []
    if zone_hi and zone_hi > entry:
        candidates.append((zone_hi, f"Trading area high ${zone_hi:.2f}", zone_hi - entry))
    if wave_top and wave_top > entry:
        candidates.append((wave_top, f"前浪頂 ${wave_top:.2f}", wave_top - entry))
    if resist > entry:
        candidates.append((resist, f"60日阻力 ${resist:.2f}", resist - entry))

    if not candidates:
        return 0, ""

    near = [c for c in candidates if c[2] <= entry * 0.10 and c[2] > 0]
    if near:
        pick = min(near, key=lambda x: x[2])
    else:
        pick = min(candidates, key=lambda x: x[0])
    return round(pick[0], 2), pick[1]


def collect_reward_targets_long(
    d1: dict, bars: list[dict], entry: float, tl: dict,
) -> list[tuple[float, str, str]]:
    """Reward candidates above entry from 關鍵 S/R resistance zones."""
    targets: list[tuple[float, str, str]] = []
    seen: set[float] = set()

    def add(price: float, ttype: str, label: str) -> None:
        p = round(price, 2)
        if p > entry * 1.003 and p not in seen:
            seen.add(p)
            targets.append((p, ttype, label))

    for lv in key_sr_levels_above(d1, bars, entry):
        ttype = "structure" if "band" in lv["label"].lower() else "wave_top"
        add(lv["price"], ttype, f"{lv['label']} ${lv['price']:.2f}")

    return targets


def collect_reward_targets_short(
    d1: dict, bars: list[dict], entry: float, tl: dict,
) -> list[tuple[float, str, str]]:
    """Reward candidates below entry: structural support only (no UTL/DTL TP)."""
    targets: list[tuple[float, str, str]] = []
    seen: set[float] = set()

    def add(price: float, ttype: str, label: str) -> None:
        p = round(price, 2)
        if p < entry * 0.997 and p not in seen:
            seen.add(p)
            targets.append((p, ttype, label))

    wave_bot = d1.get("wave_bottom") or 0
    if wave_bot:
        add(wave_bot, "wave_top", f"前浪底 ${wave_bot:.2f}")

    if bars:
        _, lows = find_swing_points_indexed(bars, 60)
        if len(lows) >= 2:
            add(lows[-2][1], "wave_top", f"前浪底(2) ${lows[-2][1]:.2f}")

    swing_lo = d1["swing_low"]
    add(swing_lo, "wave_top", f"20日低 ${swing_lo:.2f}")

    return targets


def _best_reward_target(
    targets: list[tuple[float, str, str]], entry: float, risk: float, direction: str,
) -> dict:
    """Pick highest-RR logical target; aim 5:1, accept >=2:1."""
    if risk <= 0:
        return {"target": entry, "type": "none", "label": "—", "raw_rr": 0.0}

    scored: list[tuple[float, float, str, str]] = []
    for price, ttype, label in targets:
        if direction == "long":
            rr = (price - entry) / risk
        else:
            rr = (entry - price) / risk
        if rr > 0:
            scored.append((rr, price, ttype, label))

    if scored:
        best = max(scored, key=lambda x: x[0])
        return {"target": best[1], "type": best[2], "label": best[3], "raw_rr": best[0]}

    fallback_rr = 2.0
    if direction == "long":
        tgt = entry + fallback_rr * risk
    else:
        tgt = entry - fallback_rr * risk
    return {"target": tgt, "type": "fixed_2r", "label": "2R fallback", "raw_rr": fallback_rr}


def _rr_discounted_note(raw_rr: float) -> str:
    if raw_rr >= 5:
        return "理想 5:1+"
    if raw_rr >= 2:
        return "達標 ≥2:1（實盤或打折）"
    return "RR 不足 2:1"


def _empty_rr_plan() -> dict:
    return {
        "preferred": "—", "entry": 0, "stop": 0, "tp1": 0, "tp2": 0, "rr": 0,
        "stop_reason": "", "reward_target": 0, "reward_target_type": "",
        "reward_target_label": "", "raw_rr": 0, "discounted_note": "RR 結構不足",
        "meta_aligned": False, "direction": "long",
    }


def build_rr_plan(
    d1: dict,
    bars: list[dict] | None = None,
    *,
    entry: float | None = None,
    setup_kind: str = "current",
    direction: str = "long",
) -> dict:
    """
    M.E.T.A. RR plan: structure stop + structural reward (wave top / resistance).
    UTL/DTL channel levels are chart reference only — not Setup TP targets.
    Pass edge when raw_rr >= 2; aim for 5:1 when structure allows.
    """
    c = entry if entry is not None else d1["close"]

    if direction == "short":
        stop, stop_reason = pick_structure_stop_short(d1, c, setup_kind)
        risk = stop - c
        if risk <= 0:
            plan = _empty_rr_plan()
            plan["direction"] = "short"
            return plan
        targets = collect_reward_targets_short(d1, bars or [], c, {})
        best = _best_reward_target(targets, c, risk, "short")
        raw_rr = best["raw_rr"]
        stop_kl = resolve_stop_keylevel(d1, stop, setup_kind, bars) or stop_reason.split(" $")[0]
        tp2_kl = resolve_tp2_keylevel(d1, best["type"], best["label"])
        return {
            "preferred": "breakdown" if not d1["sr_pass"] else "retest",
            "entry": round(c, 2),
            "stop": stop,
            "stop_reason": stop_reason,
            "stop_keylevel": stop_kl,
            "tp1": round(c - risk, 2),
            "tp1_keylevel": "1R 量度目標",
            "tp2": round(best["target"], 2),
            "tp2_keylevel": tp2_kl,
            "reward_target": round(best["target"], 2),
            "reward_target_type": best["type"],
            "reward_target_label": best["label"],
            "raw_rr": round(raw_rr, 2),
            "rr": round(raw_rr, 2),
            "discounted_note": _rr_discounted_note(raw_rr),
            "meta_aligned": raw_rr >= 2,
            "direction": "short",
        }

    stop, stop_reason = pick_structure_stop(d1, c, setup_kind, bars)
    risk = c - stop
    if risk <= 0:
        return _empty_rr_plan()

    targets = collect_reward_targets_long(d1, bars or [], c, {})
    best = _best_reward_target(targets, c, risk, "long")
    raw_rr = best["raw_rr"]
    preferred = "retest" if d1.get("retest_as_support") else ("breakout" if not d1["sr_pass"] else "retest")
    stop_kl = resolve_stop_keylevel(d1, stop, setup_kind, bars) or stop_reason.split(" $")[0]
    tp2_kl = resolve_tp2_keylevel(d1, best["type"], best["label"])

    return {
        "preferred": preferred,
        "entry": round(c, 2),
        "stop": stop,
        "stop_reason": stop_reason,
        "stop_keylevel": stop_kl,
        "tp1": round(c + risk, 2),
        "tp1_keylevel": "1R 量度目標",
        "tp2": round(best["target"], 2),
        "tp2_keylevel": tp2_kl,
        "reward_target": round(best["target"], 2),
        "reward_target_type": best["type"],
        "reward_target_label": best["label"],
        "raw_rr": round(raw_rr, 2),
        "rr": round(raw_rr, 2),
        "discounted_note": _rr_discounted_note(raw_rr),
        "meta_aligned": raw_rr >= 2,
        "direction": "long",
    }


def format_rr_note(plan: dict) -> str:
    if not plan.get("entry"):
        return plan.get("discounted_note", "RR 結構不足")
    tgt = plan.get("reward_target_label") or f"${plan.get('reward_target', plan['tp2'])}"
    return (
        f"Entry ${plan['entry']} | Stop ${plan['stop']} ({plan.get('stop_reason', '')}) | "
        f"Target {tgt} | RR {plan.get('raw_rr', plan['rr'])}:1 | {plan.get('discounted_note', '')}"
    )


EDGE_DISPLAY_NAMES = [
    "Momentum & Trend",
    "Support & Resistance",
    "Candle / PA / Volume",
    "Multi-Timeframe",
    "Relative Strength",
    "Reward / Risk / Structure",
    "Broad Market",
    "First Touch",
    "Major Indicators",
]

EDGE_OVERVIEW_KEYS = [
    "momentum_trend", "sr", "csp_pa_vol", "mtf", "ft", "mi",
]

EDGE_LABELS_ZH = {
    "momentum_trend": "Momentum & Trend",
    "sr": "Support & Resistance",
    "csp_pa_vol": "Candle / PA / Volume",
    "mtf": "Multi-Timeframe",
    "rs": "Relative Strength",
    "rrs": "Reward / Risk / Structure",
    "board_edge": "Broad Market",
    "ft": "First Touch",
    "mi": "Major Indicators（參考·不計分）" if not MI_EDGE_SCORING_ENABLED else "Major Indicators",
}


def compute_long_short_edges(
    bars: list[dict],
    price: float,
    rs_long: int,
    rs_short: int,
    board_long: int,
    board_short: int,
    mtf_long: int,
    mtf_short: int,
    rs_note: str = "",
    rs_short_note: str = "",
    board_long_note: str = "",
    board_short_note: str = "",
    mtf_note: str = "",
    mi_override: dict | None = None,
) -> tuple[dict[str, int], dict[str, int], dict[str, str], dict[str, str]]:
    sim = bars_at_price(bars, price)
    d1 = analyze_bars(sim)
    mom = analyze_momentum_trend(sim)
    sr_s = analyze_sr_short(sim)
    plan_long = build_rr_plan(d1, sim, entry=price, direction="long")
    plan_short = build_rr_plan(d1, sim, entry=price, direction="short")
    mi_d = mi_override or d1.get("mi_detail") or {
        "long_pass": bool(d1.get("mi_pass")),
        "short_pass": bool(d1.get("mi_short_pass")),
        "long_note": "MACD breakout 未確認",
        "short_note": "MACD breakout 未確認",
    }
    long_edges = {
        "momentum_trend": 1 if d1["momentum_pass"] else 0,
        "sr": d1["sr_pass"],
        "csp_pa_vol": 1 if d1["csr_pass"] else 0,
        "mtf": mtf_long,
        "rs": rs_long,
        "rrs": 1 if plan_long.get("meta_aligned") else 0,
        "board_edge": board_long,
        "ft": 1 if d1.get("ft_detail", {}).get("long_pass", d1["ft_pass"]) else 0,
        "mi": 1 if mi_d.get("long_pass") else 0,
    }
    short_edges = {
        "momentum_trend": 1 if mom["bear_pass"] else 0,
        "sr": sr_s["pass"],
        "csp_pa_vol": 1 if d1.get("csr_short_pass") else 0,
        "mtf": mtf_short,
        "rs": rs_short,
        "rrs": 1 if plan_short.get("meta_aligned") else 0,
        "board_edge": board_short,
        "ft": 1 if d1.get("ft_detail", {}).get("short_pass") else 0,
        "mi": 1 if mi_d.get("short_pass") else 0,
    }
    ft_d = d1.get("ft_detail") or {}
    long_notes = {
        "momentum_trend": d1.get("momentum_note", ""),
        "sr": d1.get("sr_note", ""),
        "csp_pa_vol": d1.get("csr_note", d1.get("csp_note", "")),
        "mtf": mtf_note if mtf_long else "MTF 未對齊",
        "rs": rs_note,
        "rrs": format_rr_note(plan_long) if long_edges["rrs"] else plan_long.get("discounted_note", "RR 結構不足"),
        "board_edge": board_long_note if board_long else board_long_note,
        "ft": ft_d.get("long_note", "近3K follow-through" if d1["ft_pass"] else "跟進弱"),
        "mi": mi_d.get("long_note", "MACD breakout 未確認"),
    }
    short_notes = {
        "momentum_trend": mom.get("bear_note", "跌勢未達標"),
        "sr": sr_s.get("note", ""),
        "csp_pa_vol": d1.get("csr_short_note", d1.get("csp_short_note", "未見 Short CSR")),
        "mtf": mtf_note if mtf_short else "MTF 未對齊",
        "rs": rs_short_note or rs_note,
        "rrs": format_rr_note(plan_short) if short_edges["rrs"] else plan_short.get("discounted_note", "RR 結構不足"),
        "board_edge": board_short_note if board_short else board_short_note,
        "ft": ft_d.get("short_note", "跟進弱/偏空"),
        "mi": mi_d.get("short_note", "MACD breakout 未確認"),
    }
    return long_edges, short_edges, long_notes, short_notes


def key_price_levels(
    d1: dict,
    w1: dict | None = None,
    h1: dict | None = None,
    bars: list[dict] | None = None,
) -> list[tuple[str, float, str, dict | None]]:
    """
    Swing key levels as S/R areas (W1+D1+H1 grouped).
    Returns (label, test_price, kind, area_meta).
    """
    c = d1["close"]
    levels: list[tuple[str, float, str, dict | None]] = [
        ("現價", c, "current", None),
    ]
    seen_mids: set[float] = {round(c, 2)}

    for area in build_support_areas(d1, bars=bars):
        price = area["mid"]
        if price in seen_mids:
            continue
        seen_mids.add(price)
        label = (
            f"回落至 支持區 {format_area_range(area['zone_lo'], area['zone_hi'])}"
            f"（{area['edge_count']}源）"
        )
        levels.append((label, price, "drop_support_area", area))

    for area in build_resistance_areas(d1, bars=bars):
        break_price = area["zone_hi"]
        if break_price not in seen_mids:
            seen_mids.add(break_price)
            label = (
                f"升破 阻力區 {format_area_range(area['zone_lo'], area['zone_hi'])}"
                f"（{area['edge_count']}源）"
            )
            levels.append((label, break_price, "break_resistance_area", area))
        if area["edge_count"] >= 2 and area["mid"] not in seen_mids:
            flip = bars and area_flipped_to_support(bars, area)
            if flip or not bars:
                seen_mids.add(area["mid"])
                tag = "阻力→支持·" if flip else ""
                label = (
                    f"突破後回踩 {format_area_range(area['zone_lo'], area['zone_hi'])}"
                    f"（{tag}{area['edge_count']}源）"
                )
                levels.append((label, area["mid"], "retest_resistance_area", area))

    return levels


def build_edge_scenarios(
    bars: list[dict],
    d1: dict,
    rs_long: int,
    rs_short: int,
    board_long: int,
    board_short: int,
    mtf_long: int,
    mtf_short: int,
    base_long: dict[str, int],
    base_short: dict[str, int],
    rs_note: str = "",
    rs_short_note: str = "",
    board_long_note: str = "",
    board_short_note: str = "",
    mtf_note: str = "",
    mi_override: dict | None = None,
    w1: dict | None = None,
    h1: dict | None = None,
) -> dict:
    scenarios = []
    for label, price, kind, area in key_price_levels(d1, w1=w1, h1=h1, bars=bars):
        long_e, short_e, _, _ = compute_long_short_edges(
            bars, price, rs_long, rs_short, board_long, board_short, mtf_long, mtf_short,
            rs_note, rs_short_note, board_long_note, board_short_note, mtf_note, mi_override,
        )
        long_count = sum_edge_scores(long_e)
        short_count = sum_edge_scores(short_e)
        long_new = [EDGE_LABELS_ZH[k] for k in EDGES if long_e[k] and not base_long.get(k)]
        short_new = [EDGE_LABELS_ZH[k] for k in EDGES if short_e[k] and not base_short.get(k)]
        long_lost = [EDGE_LABELS_ZH[k] for k in EDGES if base_long.get(k) and not long_e[k]]
        scenarios.append({
            "label": label,
            "price": round(price, 2),
            "kind": kind,
            "area_lo": area["zone_lo"] if area else None,
            "area_hi": area["zone_hi"] if area else None,
            "area_sources": area["sources"] if area else [],
            "area_flipped": bool(area.get("flipped")) if area else False,
            "edge_count": area["edge_count"] if area else 0,
            "long_count": long_count,
            "short_count": short_count,
            "long_new": long_new,
            "short_new": short_new,
            "long_lost": long_lost,
            "bias": "long" if long_count > short_count else ("short" if short_count > long_count else "neutral"),
            "sr_note": analyze_sr(bars_at_price(bars, price))["note"],
        })

    future = [s for s in scenarios if s["kind"] != "current"]
    cur_long = sum_edge_scores(base_long)
    best_long = max(
        future,
        key=lambda s: (len(s["long_new"]), s["long_count"], -s["short_count"]),
        default=None,
    )
    if best_long and not best_long["long_new"] and best_long["long_count"] <= cur_long:
        best_long = None
    best_short = max(
        future,
        key=lambda s: (len(s["short_new"]), s["short_count"], -s["long_count"]),
        default=None,
    )
    cur_short = sum_edge_scores(base_short)
    if best_short and not best_short["short_new"] and best_short["short_count"] <= cur_short:
        best_short = None
    if best_short and (
        best_short["short_count"] <= best_short["long_count"]
        or best_short["short_count"] < 4
    ):
        best_short = None

    return {
        "current_long": sum_edge_scores(base_long),
        "current_short": sum_edge_scores(base_short),
        "bias": "long" if sum_edge_scores(base_long) > sum_edge_scores(base_short)
        else ("short" if sum_edge_scores(base_short) > sum_edge_scores(base_long) else "neutral"),
        "scenarios": scenarios,
        "best_long": best_long,
        "best_short": best_short,
    }


def grade_from_edges(edges: dict) -> tuple[int, str, str]:
    total = sum_edge_scores(edges)
    core = [edges[k]["score"] for k in ("momentum_trend", "sr", "csp_pa_vol", "mtf", "rs")]
    if total >= 7 and all(core):
        return total, "A", "trade"
    if total == 6:
        return total, "B", "watch"
    if total <= 5:
        return total, "C", "skip"
    return total, "B", "watch"


def tf_direction(analysis: dict) -> str:
    """Classify timeframe bias: long, short, or neutral."""
    if analysis.get("momentum_pass") and analysis["close"] > analysis["ema20"]:
        return "long"
    if analysis.get("momentum_bear_pass") and analysis["close"] < analysis["ema20"]:
        return "short"
    if analysis["close"] > analysis["ema20"]:
        return "long"
    if analysis["close"] < analysis["ema20"]:
        return "short"
    return "neutral"


def analyze_mtf_cross(
    w1: dict | None,
    d1: dict,
    h1: dict | None,
) -> tuple[int, int, str]:
    """
    Edge #4 MTF across W1 (HTF) -> D1 (mid) -> H1 (LTF).
    Valid stack: W1~D1~5x, D1~H1~6.5x (>=4x apart).
    Long: HTF trend supports D1 setup; H1 pullback in uptrend counts as entry timing.
    Short: mirror.
    """
    w_dir = tf_direction(w1) if w1 else "neutral"
    d_dir = tf_direction(d1)
    h_dir = tf_direction(h1) if h1 else "neutral"

    stack_parts = []
    for label, present, direction in (
        ("W1", w1 is not None, w_dir),
        ("D1", True, d_dir),
        ("H1", h1 is not None, h_dir),
    ):
        stack_parts.append(f"{label}={direction if present else '缺'}")

    d1_long_setup = bool(
        d1.get("momentum_pass")
        or (d1["close"] > d1["ema20"] and w_dir in ("long", "neutral"))
    )
    d1_short_setup = bool(
        d1.get("momentum_bear_pass")
        or (d1["close"] < d1["ema20"] and w_dir in ("short", "neutral"))
    )

    def htf_allows_long() -> bool:
        return not (w_dir == "short" and d_dir == "long")

    def htf_allows_short() -> bool:
        return not (w_dir == "long" and d_dir == "short")

    def ltf_long_ok() -> bool:
        if h1 is None:
            return d_dir in ("long", "neutral")
        if h_dir == "long":
            return True
        if h_dir == "neutral":
            return d_dir != "short"
        # LTF bearish pullback while HTF/mid uptrend = valid entry technique
        return d_dir == "long" and w_dir in ("long", "neutral")

    def ltf_short_ok() -> bool:
        if h1 is None:
            return d_dir in ("short", "neutral")
        if h_dir == "short":
            return True
        if h_dir == "neutral":
            return d_dir != "long"
        return d_dir == "short" and w_dir in ("short", "neutral")

    mtf_long = 1 if (
        w1 is not None and h1 is not None
        and d1_long_setup and htf_allows_long() and ltf_long_ok()
    ) else 0
    mtf_short = 1 if (
        w1 is not None and h1 is not None
        and d1_short_setup and htf_allows_short() and ltf_short_ok()
    ) else 0

    missing = [t for t, present in (("W1", w1), ("H1", h1)) if not present]
    align = "Long 對齊" if mtf_long else ("Short 對齊" if mtf_short else "未對齊")
    stack_note = " | ".join(stack_parts)
    if missing:
        note = f"部分 stack（缺 {'/'.join(missing)}）：{stack_note}；{align}"
    else:
        note = f"W1->D1->H1 {align}：{stack_note}"
    return mtf_long, mtf_short, note


def apply_mi_override(analysis: dict, mi_detail: dict, source: str = "W1") -> dict:
    """Use one timeframe's M.I. result as canonical (e.g., W1-only MACD)."""
    if not analysis or not mi_detail:
        return analysis
    out = dict(analysis)
    out["mi_detail"] = dict(mi_detail)
    out["mi_pass"] = bool(mi_detail.get("long_pass"))
    out["mi_short_pass"] = bool(mi_detail.get("short_pass"))
    out["mi_source_tf"] = source
    out["mi_note"] = f"M.I. 以{source} MACD breakout為準"
    return out


def build_per_tf_block(
    tf_label: str,
    analysis: dict,
    bars: list[dict],
    *,
    mtf_long: int,
    mtf_short: int,
    mtf_note: str,
    rs_long: int,
    rs_short: int,
    rs_note: str,
    rs_short_note: str,
    board_long: int,
    board_short: int,
    board_long_note: str,
    board_short_note: str,
) -> dict:
    """Nine edges for one timeframe; #4 MTF is cross-stack; #5 RS / #7 大盤為 symbol/run 級。"""
    plan = build_rr_plan(analysis, bars)
    plan_short = build_rr_plan(analysis, bars, direction="short")
    sr_s = analyze_sr_short(bars)

    long_edges = {
        "momentum_trend": 1 if analysis["momentum_pass"] else 0,
        "sr": analysis["sr_pass"],
        "csp_pa_vol": 1 if analysis.get("csr_pass") else 0,
        "mtf": mtf_long,
        "rs": rs_long,
        "rrs": 1 if plan.get("meta_aligned") else 0,
        "board_edge": board_long,
        "ft": 1 if analysis.get("ft_detail", {}).get("long_pass", analysis["ft_pass"]) else 0,
        "mi": 1 if analysis["mi_pass"] else 0,
    }
    short_edges = {
        "momentum_trend": 1 if analysis.get("momentum_bear_pass") else 0,
        "sr": sr_s["pass"],
        "csp_pa_vol": 1 if analysis.get("csr_short_pass") else 0,
        "mtf": mtf_short,
        "rs": rs_short,
        "rrs": 1 if plan_short.get("meta_aligned") else 0,
        "board_edge": board_short,
        "ft": 1 if analysis.get("ft_detail", {}).get("short_pass") else 0,
        "mi": 1 if analysis.get("mi_short_pass") else 0,
    }
    ft_d = analysis.get("ft_detail") or {}
    long_notes = {
        "momentum_trend": analysis.get("momentum_note", ""),
        "sr": analysis.get("sr_note", ""),
        "csp_pa_vol": analysis.get("csr_note", analysis.get("csp_note", "")),
        "mtf": mtf_note if mtf_long else "MTF 未對齊",
        "rs": rs_note,
        "rrs": format_rr_note(plan) if long_edges["rrs"] else plan.get("discounted_note", "RR 結構不足"),
        "board_edge": board_long_note,
        "ft": ft_d.get("long_note", "跟進弱"),
        "mi": (analysis.get("mi_detail") or {}).get("long_note", analysis.get("mi_note", "MACD breakout 未確認")),
    }
    short_notes = {
        "momentum_trend": analysis.get("momentum_bear_note", "跌勢未達標"),
        "sr": sr_s.get("note", "—"),
        "csp_pa_vol": analysis.get("csr_short_note", analysis.get("csp_short_note", "未見 Short CSR")),
        "mtf": mtf_note if mtf_short else "MTF 未對齊",
        "rs": rs_short_note or rs_note,
        "rrs": format_rr_note(plan_short) if short_edges["rrs"] else plan_short.get("discounted_note", "RR 結構不足"),
        "board_edge": board_short_note if board_short else board_short_note,
        "ft": ft_d.get("short_note", "跟進弱/偏空"),
        "mi": (analysis.get("mi_detail") or {}).get("short_note", analysis.get("mi_note", "MACD breakout 未確認")),
    }
    return {
        "label": tf_label,
        "close": analysis["close"],
        "metrics": {
            "ema20": analysis["ema20"],
            "ema50": analysis["ema50"],
            "sma5": analysis.get("sma5"),
            "sma10": analysis.get("sma10"),
            "sma20": analysis.get("sma20"),
            "trend_dir": analysis.get("trend_dir", "—"),
            "swing_low": analysis["swing_low"],
            "resistance": analysis["resistance"],
            "wave_top": analysis.get("wave_top"),
            "wave_bottom": analysis.get("wave_bottom"),
            "trading_area": analysis.get("trading_area"),
        },
        "long_edges": long_edges,
        "short_edges": short_edges,
        "long_count": sum_edge_scores(long_edges),
        "short_count": sum_edge_scores(short_edges),
        "long_notes": long_notes,
        "short_notes": short_notes,
    }


def edges_dict_from_block(block: dict, direction: str = "long") -> dict:
    """Convert per-TF block to edges dict format for grade_from_edges (D1 primary)."""
    src = block["long_edges"] if direction == "long" else block["short_edges"]
    notes = block["long_notes"] if direction == "long" else block["short_notes"]
    out = {}
    for k in EDGES:
        out[k] = {"score": src[k], "note": notes[k]}
    if direction == "long":
        out["csp_pa_vol"]["short_score"] = block["short_edges"]["csp_pa_vol"]
        out["csp_pa_vol"]["short_note"] = block["short_notes"]["csp_pa_vol"]
    return out


def score_from_bars(
    symbol: str,
    d1_bars: list[dict],
    *,
    w1_bars: list[dict] | None = None,
    h1_bars: list[dict] | None = None,
    market_edge: dict | None = None,
    source: str = "CSV",
) -> dict:
    """Single scoring path for CSV and yfinance — same report structure."""
    sym = symbol.upper()
    bars = d1_bars
    d1 = analyze_bars(bars)

    w1 = analyze_bars(w1_bars) if w1_bars else None
    h1 = analyze_bars(h1_bars) if h1_bars else None
    d1 = merge_swing_sr(d1, bars, w1=w1, w1_bars=w1_bars, h1=h1, h1_bars=h1_bars)

    mi_source_tf = "W1" if w1 else "D1"
    mi_ref = w1 or d1
    mi_canonical = mi_ref.get("mi_detail") or {
        "long_pass": bool(mi_ref.get("mi_pass")),
        "short_pass": bool(mi_ref.get("mi_short_pass")),
        "long_note": "MACD breakout 未確認",
        "short_note": "MACD breakout 未確認",
    }
    d1 = apply_mi_override(d1, mi_canonical, mi_source_tf)
    if w1:
        w1 = apply_mi_override(w1, mi_canonical, mi_source_tf)
    if h1:
        h1 = apply_mi_override(h1, mi_canonical, mi_source_tf)

    mtf_pass, mtf_short, mtf_note = analyze_mtf_cross(w1, d1, h1)

    rs = assess_relative_strength(sym, bars)
    market = market_edge if market_edge is not None else get_broad_market_edge()
    board_long = market["long_pass"]
    board_short = market["short_pass"]
    board_long_note = market["long_note"]
    board_short_note = market["short_note"]
    sector_footnote = fetch_sector_footnote(sym)
    plan = build_rr_plan(d1, bars)
    channel_ref = compute_w1_channel_reference(w1_bars) if w1_bars else None

    cross = dict(
        mtf_long=mtf_pass,
        mtf_short=mtf_short,
        mtf_note=mtf_note,
        rs_long=rs["long_pass"],
        rs_short=rs["short_pass"],
        rs_note=rs["long_note"],
        rs_short_note=rs["short_note"],
        board_long=board_long,
        board_short=board_short,
        board_long_note=board_long_note,
        board_short_note=board_short_note,
    )

    tf_blocks: dict[str, dict] = {}
    for tf_label, analysis, tf_bars in (
        ("W1", w1, w1_bars),
        ("D1", d1, bars),
        ("H1", h1, h1_bars),
    ):
        if analysis is None or tf_bars is None:
            continue
        tf_blocks[tf_label] = build_per_tf_block(tf_label, analysis, tf_bars, **cross)

    d1_block = tf_blocks.get("D1") or build_per_tf_block("D1", d1, bars, **cross)
    edges = edges_dict_from_block(d1_block, "long")
    edges["csp_pa_vol"]["short_score"] = d1_block["short_edges"]["csp_pa_vol"]
    edges["csp_pa_vol"]["short_note"] = d1_block["short_notes"]["csp_pa_vol"]

    total, grade, decision = grade_from_edges(edges)
    base_long = d1_block["long_edges"]
    base_short = d1_block["short_edges"]
    long_edge_notes = d1_block["long_notes"]
    short_edge_notes = d1_block["short_notes"]

    for k in EDGES:
        if k != "csp_pa_vol":
            edges[k]["short_score"] = base_short[k]
            edges[k]["short_note"] = short_edge_notes[k]

    scenarios = build_edge_scenarios(
        bars, d1, rs["long_pass"], rs["short_pass"], board_long, board_short,
        mtf_pass, mtf_short, base_long, base_short,
        rs["long_note"], rs["short_note"], board_long_note, board_short_note, mtf_note,
        mi_canonical, w1=w1, h1=h1,
    )

    present = [t for t in TF_ORDER if t in tf_blocks]
    if present == list(TF_ORDER):
        tf_label = "W1+D1+H1"
    elif present:
        tf_label = "+".join(present)
    else:
        tf_label = "D1"

    setups = build_setups(d1, bars)
    setups = attach_setup_edge_overviews(
        setups,
        w1_bars=w1_bars,
        d1_bars=bars,
        h1_bars=h1_bars,
        mi_canonical=mi_canonical,
        mi_source_tf=mi_source_tf,
        cross=cross,
    )
    data = {
        "symbol": sym,
        "timeframe": tf_label,
        "price": d1["close"],
        "volume": str(d1["volume"]),
        "volume_avg": str(d1["avg_volume_20"]),
        "metrics": d1_block["metrics"],
        "timeframes": tf_blocks,
        "mtf_detail": {
            "long": mtf_pass,
            "short": mtf_short,
            "note": mtf_note,
        },
        "rs_detail": rs,
        "market_edge_detail": market,
        "sector_footnote": sector_footnote,
        "ft_detail": d1.get("ft_detail"),
        "mi_detail": mi_canonical,
        "mi_source_tf": mi_source_tf,
        "edges": edges,
        "short_edges": base_short,
        "short_edge_notes": short_edge_notes,
        "long_edge_notes": long_edge_notes,
        "total_score": total,
        "grade": grade,
        "decision": decision,
        "entry_plan": plan,
        "channel_ref": channel_ref,
        "setups": setups,
        "scenarios": scenarios,
        "d1_analysis": d1,
        "d1_bars": bars,
        "directional_bias": scenarios["bias"],
        "long_edge_count": scenarios["current_long"],
        "short_edge_count": scenarios["current_short"],
        "source": source,
    }
    data["summary_zh"] = build_summary_text(data)
    return data


def score_symbol(
    symbol: str,
    d1_path: Path,
    w1_path: Path | None = None,
    h1_path: Path | None = None,
    market_edge: dict | None = None,
) -> dict:
    sym = symbol.upper()
    w1_path = w1_path or CSV_DIR / f"{sym}_W1.csv"
    h1_path = h1_path or CSV_DIR / f"{sym}_H1.csv"

    bars = parse_bars(load_tv_csv(d1_path))
    w1_bars = (
        parse_bars(load_tv_csv(w1_path), min_bars=TF_MIN_BARS["W1"])
        if w1_path.exists()
        else None
    )
    h1_bars = (
        parse_bars(load_tv_csv(h1_path), min_bars=TF_MIN_BARS["H1"])
        if h1_path.exists()
        else None
    )
    return score_from_bars(
        sym, bars, w1_bars=w1_bars, h1_bars=h1_bars, market_edge=market_edge, source="CSV"
    )


def _resolve_breakout_entry(d1: dict, bars: list[dict] | None = None) -> tuple[float, str, str, str]:
    """Next breakout: nearest 關鍵 S/R resistance above close."""
    close = d1["close"]
    resistances = key_sr_levels_above(d1, bars, close)
    if not resistances:
        return 0, "", "", "無有效突破位（關鍵 S/R 阻力已破或無下一層）"

    pick = resistances[0]
    entry = pick["price"]
    entry_kl = pick["label"]
    trigger = f"收市站穩 >${entry:.2f}（{entry_kl}）+ 放量"
    wt_tf = d1.get("wave_top_tf") or "D1"
    wt = d1.get("wave_top") or 0
    note = ""
    if wt and wt < close and entry > wt * 1.003:
        note = f"⚠ {wt_tf} 前浪頂 ${wt:.2f} 已突破；改睇下一關鍵阻力"
    return round(entry, 2), entry_kl, trigger, note


def _resolve_retest_entry(d1: dict, bars: list[dict] | None = None) -> tuple[float, str, str, str, str]:
    """Retest: nearest 關鍵 S/R support below close."""
    close = d1["close"]
    supports = key_sr_levels_below(d1, bars, close)
    if not supports:
        supports = key_sr_levels_below(d1, bars, close * 1.05)
    if not supports:
        zone_lo = d1["swing_low"]
        zone_hi = d1.get("ema20") or zone_lo
        entry = round((zone_lo + zone_hi) / 2, 2)
        return entry, "D1 20日低區", f"回落至 ${zone_lo:.2f} 企穩", f"無明確關鍵 S/R 支持；參考 ${entry:.2f}", ""

    pick = supports[0]
    zlo = pick.get("zone_lo") or pick["price"]
    zhi = pick.get("zone_hi") or pick["price"]
    span_pct = (zhi - zlo) / zlo if zlo else 0
    if span_pct > 0.01:
        entry = round((zlo + zhi) / 2, 2)
        anchor = "中位"
    else:
        entry = round(zlo, 2)
        anchor = "下沿"
    entry_kl = pick["label"]
    trigger = f"回落 {format_area_range(zlo, zhi)}（{entry_kl}）企穩 @ ${entry:.2f}"
    retest_note = f"優先等 {format_area_range(zlo, zhi)} 企穩；入場參考 {anchor} ${entry:.2f}"
    return entry, entry_kl, trigger, retest_note, entry_kl


def _make_setup_dict(
    *,
    name: str,
    trigger: str,
    entry_kl: str,
    entry: float,
    plan: dict,
    valid: bool,
    retest_note: str = "",
    setup_note: str = "",
) -> dict:
    out = {
        "name": name,
        "trigger": trigger or "—",
        "entry_label": entry_kl or "Entry",
        "entry_keylevel": entry_kl,
        "entry": entry,
        "stop": plan.get("stop"),
        "tp1": plan.get("tp1"),
        "tp2": plan.get("tp2"),
        "rr": plan.get("raw_rr"),
        "stop_reason": plan.get("stop_reason", ""),
        "stop_keylevel": plan.get("stop_keylevel", ""),
        "tp1_keylevel": plan.get("tp1_keylevel", "1R 量度目標"),
        "tp2_keylevel": plan.get("tp2_keylevel", ""),
        "reward_target_label": plan.get("reward_target_label", ""),
        "valid": valid,
    }
    if retest_note:
        out["retest_note"] = retest_note
    if setup_note:
        out["setup_note"] = setup_note
    return out


def build_setups(d1: dict, bars: list[dict] | None = None) -> dict:
    """Watch setups: 現價 + breakout + retest（關鍵 S/R stop/TP）。"""
    close = round(d1["close"], 2)
    plan_c = build_rr_plan(d1, bars, entry=close, setup_kind="current", direction="long")
    setup_c = _make_setup_dict(
        name="現價 Current",
        trigger=f"現價收市 @ ${close:.2f}",
        entry_kl="現價收市",
        entry=close,
        plan=plan_c,
        valid=bool(plan_c.get("stop") and plan_c.get("raw_rr", 0) >= 2),
    )

    entry_a, entry_kl_a, trigger_a, note_a = _resolve_breakout_entry(d1, bars)
    plan_a = (
        build_rr_plan(d1, bars, entry=entry_a, setup_kind="breakout", direction="long")
        if entry_a else _empty_rr_plan()
    )
    setup_a = _make_setup_dict(
        name="突破 Breakout",
        trigger=trigger_a,
        entry_kl=entry_kl_a or "Breakout trigger",
        entry=entry_a,
        plan=plan_a,
        valid=bool(entry_a and plan_a.get("stop") and plan_a.get("raw_rr", 0) >= 2),
        setup_note=note_a or (
            f"下位已突破前浪頂（阻力→支持）；A = 升破 {entry_kl_a}"
            if d1.get("retest_as_support") and entry_a else ""
        ),
    )

    entry_b, entry_kl_b, trigger_b, retest_note, _ = _resolve_retest_entry(d1, bars)
    plan_b = build_rr_plan(d1, bars, entry=entry_b, setup_kind="retest", direction="long")
    setup_b = _make_setup_dict(
        name="回踩 Retest",
        trigger=trigger_b,
        entry_kl=entry_kl_b,
        entry=entry_b,
        plan=plan_b,
        valid=bool(plan_b.get("stop") and plan_b.get("raw_rr", 0) >= 2),
        retest_note=retest_note,
    )
    return {"current": setup_c, "breakout": setup_a, "retest": setup_b}


SCREENER_MIN_BEST_RR = 3.0


def evaluate_screener_setups(setups: dict | None) -> dict:
    """
    Screener shortlist: Breakout + Retest 兩個 setup 都要 valid，
    且至少一個 RR >= SCREENER_MIN_BEST_RR (default 3R).
    """
    bo = (setups or {}).get("breakout") or {}
    rt = (setups or {}).get("retest") or {}
    rr_bo = float(bo.get("rr") or 0)
    rr_rt = float(rt.get("rr") or 0)
    both_valid = bool(bo.get("valid")) and bool(rt.get("valid"))
    best_rr = max(rr_bo, rr_rt)
    passes = both_valid and best_rr >= SCREENER_MIN_BEST_RR
    return {
        "breakout_rr": round(rr_bo, 2),
        "retest_rr": round(rr_rt, 2),
        "best_rr": round(best_rr, 2),
        "both_valid": both_valid,
        "breakout_valid": bool(bo.get("valid")),
        "retest_valid": bool(rt.get("valid")),
        "passes": passes,
    }


def passes_screener_shortlist(data: dict) -> bool:
    """A/B grade + 雙 setup valid + 至少一個 ≥3R."""
    if (data.get("grade") or "") not in ("A", "B"):
        return False
    return evaluate_screener_setups(data.get("setups")).get("passes", False)


def attach_setup_edge_overviews(
    setups: dict,
    *,
    w1_bars: list[dict] | None,
    d1_bars: list[dict],
    h1_bars: list[dict] | None,
    mi_canonical: dict,
    mi_source_tf: str,
    cross: dict,
) -> dict:
    """Add edge_overview per Setup 現價/A/B price (for dashboard in format_md)."""

    def _tf_overview_at_price(price: float) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for tf_label, src_bars in (
            ("W1", w1_bars),
            ("D1", d1_bars),
            ("H1", h1_bars),
        ):
            if not src_bars:
                continue
            sim = bars_at_price(src_bars, price)
            sim_analysis = analyze_bars(sim)
            sim_analysis = apply_mi_override(sim_analysis, mi_canonical, mi_source_tf)
            out[tf_label] = build_per_tf_block(tf_label, sim_analysis, sim, **cross)
        return out

    for key in ("current", "breakout", "retest"):
        s = setups.get(key)
        if not s or not s.get("entry"):
            continue
        s["edge_overview"] = _tf_overview_at_price(float(s["entry"]))
    return setups


CORE_LABELS = {
    "momentum_trend": "Momentum & Trend",
    "sr": "Support & Resistance",
    "csp_pa_vol": "Candle / PA / Volume",
    "mtf": "Multi-Timeframe",
    "rs": "Relative Strength",
}


def build_summary_text(data: dict) -> str:
    sym = data["symbol"]
    total = data["total_score"]
    grade = data["grade"]
    decision = data["decision"]
    edges = data["edges"]

    decision_zh = {"trade": "可研究入場", "watch": "放 watchlist", "skip": "而家唔買"}
    headline = f"{sym} **{edge_score_fmt(total)} | Grade {grade} | {decision}** — {decision_zh.get(decision, decision)}。"

    failed_core = [CORE_LABELS[k] for k in CORE_LABELS if not edges[k]["score"]]
    parts = [headline]
    if failed_core and grade != "A":
        parts.append(f"核心未過：{', '.join(failed_core)}。")
    market = data.get("market_edge_detail") or {}
    if market.get("directive"):
        parts.append(f"**{market['directive']}**")
    elif not edges["board_edge"]["score"]:
        parts.append(f"大盤：{edges['board_edge']['note']}。")
    foot = data.get("sector_footnote")
    if foot:
        parts.append(foot + "。")
    if decision == "skip" and edges["sr"]["score"] == 0:
        parts.append("而家價位多數係中間位或追價，等 Setup A/B 信號。")
    elif decision == "trade":
        parts.append("核心 edge 達標，可配合 Entry Plan 執行。")

    ft = data.get("ft_detail") or {}
    if ft.get("quality_long") == "caution":
        parts.append(f"First Touch 第{ft.get('touch_number_long')}次——偏短線/小心。")
    elif ft.get("quality_long") == "ideal" and ft.get("long_pass"):
        parts.append("First Touch 第1次——META 理想入場區。")

    sc = data.get("scenarios") or {}
    if sc:
        bias = sc.get("bias", "neutral")
        bias_zh = {"long": "偏多", "short": "偏空", "neutral": "中性"}
        parts.append(
            f"Long edge {edge_score_fmt(sc.get('current_long', 0))} vs "
            f"Short {edge_score_fmt(sc.get('current_short', 0))}（{bias_zh.get(bias, bias)}）。"
        )
        tfs = data.get("timeframes") or {}
        if tfs:
            tf_bits = [f"{tf} L{tfs[tf]['long_count']}/S{tfs[tf]['short_count']}" for tf in TF_ORDER if tf in tfs]
            if tf_bits:
                parts.append(f"分週期：{' | '.join(tf_bits)}。")
        best = sc.get("best_long")
        if best and best.get("long_new"):
            gain = "、".join(best["long_new"])
            parts.append(f"若{best['label']} → Long {edge_score_fmt(best['long_count'])}（+{gain}）。")
        best_s = sc.get("best_short")
        if best_s and best_s.get("short_new"):
            gain_s = "、".join(best_s["short_new"])
            parts.append(f"若{best_s['label']} → Short {edge_score_fmt(best_s['short_count'])}（+{gain_s}）。")
    return " ".join(parts)


def discover_symbols() -> list[str]:
    syms = set()
    for f in CSV_DIR.glob("*_D1.csv"):
        syms.add(f.stem.replace("_D1", "").upper())
    for f in CSV_DIR.glob("*_d1.csv"):
        syms.add(f.stem.replace("_d1", "").upper())
    return sorted(syms)


def one_line_conclusion(data: dict) -> str:
    decision_zh = {"trade": "可研究入場", "watch": "放 watchlist", "skip": "而家唔買"}
    d = decision_zh.get(data["decision"], data["decision"])
    base = f"**{edge_score_fmt(data['total_score'])} | Grade {data['grade']} | {data['decision']}** — {d}。"
    extras: list[str] = []
    if data.get("grade") == "A" and not extras:
        return base
    for key in CORE_LABELS:
        if not data["edges"][key]["score"]:
            extras.append(f"**{CORE_LABELS[key]}** 未過：{data['edges'][key]['note']}")
    if not extras:
        return base
    return base + " " + " ".join(extras)


EDGE_TABLE_LABELS = EDGE_DISPLAY_NAMES


def _overview_col_headers() -> list[str]:
    return [EDGE_LABELS_ZH[k] for k in EDGE_OVERVIEW_KEYS]


def _edge_icon(v: int | bool) -> str:
    return "✅" if v else "❌"


_EDGE_OVERVIEW_WRAP_STYLE = "width:720px;max-width:100%;font-size:0.88em;margin-bottom:16px;"
_EDGE_OVERVIEW_TABLE_STYLE = "border-collapse:collapse;width:100%;table-layout:fixed;"
_EDGE_OVERVIEW_COLGROUP = (
    "<colgroup>"
    "<col style='width:5%'>"
    "<col style='width:6%'>"
    "<col style='width:6%'>"
    "<col style='width:9%'>"
    + "".join("<col style='width:12%'>" for _ in EDGE_OVERVIEW_KEYS)
    + "</colgroup>"
)


def _edge_overview_cell_style(col_idx: int) -> str:
    if col_idx == 0:
        return "padding:4px 6px;"
    if col_idx == 3:
        return "padding:4px 6px;text-align:right;"
    return "padding:4px 6px;text-align:center;"


def _edge_overview_table_rows(timeframes: dict[str, dict]) -> list[str]:
    cols = " | ".join(_overview_col_headers())
    rows: list[str] = [
        f"| TF | Long | Short | 收市 | {cols} |",
        f"|----|:----:|:-----:|-----:|{'|:---:|' * len(EDGE_OVERVIEW_KEYS)}",
    ]
    for tf in TF_ORDER:
        block = timeframes.get(tf)
        if not block:
            dash = " | ".join(["—"] * len(EDGE_OVERVIEW_KEYS))
            rows.append(f"| {tf} | — | — | — | {dash} |")
            continue
        le = block["long_edges"]
        icons = " | ".join(_edge_icon(le[k]) for k in EDGE_OVERVIEW_KEYS)
        rows.append(
            f"| **{tf}** | {edge_score_fmt(block['long_count'])} | {edge_score_fmt(block['short_count'])} | "
            f"${block['close']:.2f} | {icons} |"
        )
    return rows


def _edge_overview_html_table(title: str, timeframes: dict[str, dict]) -> str:
    headers = ["TF", "Long", "Short", "收市", *_overview_col_headers()]
    th = "".join(
        f"<th style='{_edge_overview_cell_style(i)}'>{h}</th>"
        for i, h in enumerate(headers)
    )
    body_rows: list[str] = []
    for tf in TF_ORDER:
        block = timeframes.get(tf)
        if not block:
            cells = ["—"] * (4 + len(EDGE_OVERVIEW_KEYS))
        else:
            le = block["long_edges"]
            cells = [
                f"<b>{tf}</b>",
                edge_score_fmt(block['long_count']),
                edge_score_fmt(block['short_count']),
                f"${block['close']:.2f}",
                *(_edge_icon(le[k]) for k in EDGE_OVERVIEW_KEYS),
            ]
        tds = "".join(
            f"<td style='{_edge_overview_cell_style(i)}'>{c}</td>"
            for i, c in enumerate(cells)
        )
        body_rows.append(f"<tr>{tds}</tr>")
    tbody = "".join(body_rows)
    return (
        f'<div style="{_EDGE_OVERVIEW_WRAP_STYLE}">'
        f"<p><b>{title}</b></p>"
        f'<table style="{_EDGE_OVERVIEW_TABLE_STYLE}">'
        f"{_EDGE_OVERVIEW_COLGROUP}"
        f"<thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table></div>"
    )


def format_combined_edge_dashboard(
    timeframes: dict[str, dict],
    setups: dict | None = None,
) -> list[str]:
    """Stacked content-width rows: 現價 → Setup A → Setup B."""
    blocks: list[str] = [
        _edge_overview_html_table("現價（D1 主評）", timeframes),
    ]
    setups = setups or {}
    for key, label in (
        ("current", "Setup 現價"),
        ("breakout", "Setup A 突破"),
        ("retest", "Setup B 回踩"),
    ):
        s = setups.get(key) or {}
        overview = s.get("edge_overview")
        entry = s.get("entry")
        if overview and entry:
            blocks.append(_edge_overview_html_table(f"{label} @ ${entry}", overview))
    html = "".join(blocks)
    return [
        "## 多週期 Edge 總覽（W1 / D1 / H1 分開計）",
        "",
        "> 整體 Grade 以 **D1** 為主；Multi-Timeframe 為 W1→D1→H1 對齊；Relative Strength / Broad Market 為 symbol / SPY 級。",
        "",
        html,
        "",
    ]


def format_tf_overview_table(timeframes: dict[str, dict]) -> list[str]:
    lines = [
        "## 多週期 Edge 總覽（W1 / D1 / H1 分開計）",
        "",
        "> 整體 Grade 以 **D1** 為主；#4 MTF 為 W1->D1->H1 跨週期對齊；#5 RS 為 symbol 級；#7 大盤為 SPY 市場級（同 run 共用）。",
        "",
        "| TF | Long | Short | 收市 | 趨勢 | S&R | CSR | MTF | F.T. | M.I. |",
        "|----|:----:|:-----:|-----:|:----:|:---:|:---:|:---:|:----:|:----:|",
    ]
    for tf in TF_ORDER:
        block = timeframes.get(tf)
        if not block:
            lines.append(f"| {tf} | — | — | — | — | — | — | — | — | — |")
            continue
        le = block["long_edges"]
        se = block["short_edges"]
        icon = lambda v: "✅" if v else "❌"
        lines.append(
            f"| **{tf}** | {edge_score_fmt(block['long_count'])} | {edge_score_fmt(block['short_count'])} | "
            f"${block['close']:.2f} | {icon(le['momentum_trend'])} | {icon(le['sr'])} | "
            f"{icon(le['csp_pa_vol'])} | {icon(le['mtf'])} | {icon(le['ft'])} | {icon(le['mi'])} |"
        )
    lines.append("")
    return lines


def format_tf_detail_section(tf: str, block: dict) -> list[str]:
    labels = EDGE_TABLE_LABELS
    lines = [
        f"## {tf} Edge 明細",
        "",
        f"收市 **${block['close']:.2f}** | Long **{edge_score_fmt(block['long_count'])}** | Short **{edge_score_fmt(block['short_count'])}**",
        "",
        "| # | Edge | Long | Short | Notes (Long) |",
        "|---|------|:----:|:-----:|--------------|",
    ]
    for i, key in enumerate(EDGES):
        le = block["long_edges"][key]
        se = block["short_edges"][key]
        note = block["long_notes"][key]
        if key in ("rs", "board_edge") and tf != "D1":
            note = f"{note}（市場/run 級，各 TF 共用）"
        if key == "mtf":
            note = block["long_notes"]["mtf"]
        if key == "mi" and not MI_EDGE_SCORING_ENABLED:
            note = f"{note}（不計分·請喺 TV 睇 MACD）"
        lines.append(
            f"| {i + 1} | {labels[i]} | {'✅' if le else '❌'} | {'✅' if se else '❌'} | {note} |"
        )
    lines.append("")
    return lines


def format_mtf_section(mtf_detail: dict) -> list[str]:
    note = mtf_detail.get("note", "")
    long_ok = mtf_detail.get("long")
    short_ok = mtf_detail.get("short")
    return [
        "## Edge #4 — Multi-Timeframe（W1 → D1 → H1）",
        "",
        f"- **Long Multi-Timeframe**：{'✅ 通過' if long_ok else '❌ 未過'}",
        f"- **Short Multi-Timeframe**：{'✅ 通過' if short_ok else '❌ 未過'}",
        f"- **邏輯**：HTF（W1）趨勢支持 D1 setup；H1 可為 entry timing（LTF 回調而 HTF 仍升勢 = 精準入場）",
        f"- **備註**：{note}",
        "",
    ]


def format_rs_section(rs_detail: dict) -> list[str]:
    if not rs_detail or not rs_detail.get("features"):
        note = rs_detail.get("long_note", "RS 數據不足") if rs_detail else "RS 數據不足"
        return [
            "## Edge #5 — Relative Strength（相對強度）",
            "",
            f"- **備註**：{note}",
            "",
        ]
    feats = rs_detail["features"]
    icon = lambda ok: "✅" if ok else "❌"
    long_ok = rs_detail.get("long_pass")
    short_ok = rs_detail.get("short_pass")
    lines = [
        "## Edge #5 — Relative Strength（相對強度 — 三特徵）",
        "",
        "> 唔使三樣齊晒：見到 **2/3** 特徵 + 前文後理判斷有 RS 即可；強勢領導股往往有 RS，但有 RS ≠ 一定係領導股。",
        "",
        f"- **Long Relative Strength**：{'✅ 通過' if long_ok else '❌ 未過'}（{rs_detail.get('long_count', 0)}/3）",
        f"- **Short 弱 Relative Strength**：{'✅ 通過' if short_ok else '❌ 未過'}（{rs_detail.get('short_count', 0)}/3）",
        f"- **3M 回報**：股票 {rs_detail.get('stock_ret_3m', '—')}% vs SPY {rs_detail.get('spy_ret_3m', '—')}%"
        + ("（跌市）" if rs_detail.get("spy_bearish") else ""),
        "",
        "| 特徵 | Long | Short | 說明 |",
        "|------|:----:|:-----:|------|",
    ]
    labels = {
        "counter_trend": "① 反向走勢（跌市最易見）",
        "leading_ma": "② 領先移動平均線",
        "rs_line": "③ RS線向上",
    }
    for key, label in labels.items():
        f = feats.get(key, {})
        lines.append(
            f"| {label} | {icon(f.get('long'))} | {icon(f.get('short'))} | {f.get('long_note', '—')} |"
        )
    lines.append("")
    return lines


def format_broad_market_section(market: dict) -> list[str]:
    if not market or not market.get("features"):
        note = market.get("long_note", "大盤數據不足") if market else "大盤數據不足"
        return [
            "## Edge #7 — Broad Market Edge（大盤 Long/Short Edge）",
            "",
            f"- **備註**：{note}",
            "",
        ]
    feats = market["features"]
    icon = lambda ok: "✅" if ok else "❌"
    long_ok = market.get("long_pass")
    short_ok = market.get("short_pass")
    bias_zh = {"long": "Long Edge（做多環境）", "short": "Short Edge（做空環境）", "neutral": "無明確 Edge"}
    lines = [
        "## Edge #7 — Broad Market Edge（大盤 Long/Short Edge）",
        "",
        "> **Trade What We See**：判斷大盤係 Long Edge 定 Short Edge；大盤 Long → 積極搵長倉（強勢股）；Short → 積極搵短倉。",
        "> 唔使三柱齊：**2/3** 通過即可（① S&R 匯聚區 ② 動能/趨勢 ③ MTF W1→D1）。",
        "",
        f"- **SPY 收市**：${market.get('close', '—')}（{market.get('source', '—')}）",
        f"- **大盤偏向**：{bias_zh.get(market.get('bias', 'neutral'), market.get('bias'))}",
        f"- **交易指引**：**{market.get('directive', '—')}**",
        f"- **Long Edge**：{'✅ 通過' if long_ok else '❌ 未過'}（{market.get('long_count', 0)}/3）",
        f"- **Short Edge**：{'✅ 通過' if short_ok else '❌ 未過'}（{market.get('short_count', 0)}/3）",
        "",
        "| 支柱 | Long | Short | 說明 |",
        "|------|:----:|:-----:|------|",
    ]
    labels = {
        "sr_zone": "① S&R Long/Short Edge zone",
        "momentum": "② 動能/趨勢",
        "mtf": "③ MTF W1→D1",
    }
    for key, label in labels.items():
        f = feats.get(key, {})
        lines.append(
            f"| {label} | {icon(f.get('long'))} | {icon(f.get('short'))} | {f.get('long_note', '—')} |"
        )
    qqq = market.get("qqq_note")
    if qqq:
        lines.append("")
        lines.append(f"- **次參考**：{qqq}")
    lines.append("")
    return lines


def format_channel_reference_section(channel_ref: dict | None) -> list[str]:
    """W1 UTL/DTL parallel channel — chart reference only, not Setup TP."""
    if not channel_ref:
        return []
    asc = channel_ref.get("ascending")
    desc = channel_ref.get("descending")
    primary = channel_ref.get("primary", "none")
    if primary == "ascending":
        desc = None
    elif primary == "descending":
        asc = None
    if not asc and not desc:
        return []

    lines = [
        "## W1 UTL / DTL 參考（圖表用，唔做 Setup TP）",
        "",
        "> 平行通道：下軌 UTL 連兩個遞升 swing low，上軌平行過浪頂；"
        "延伸 = 上軌向前投射（週數），唔係 Entry+2×量度。",
        "",
    ]
    if asc:
        p1, p2 = asc["utl_p1"], asc["utl_p2"]
        hi = asc["upper_anchor"]
        lines += [
            "### 上升通道（UTL）",
            "",
            f"- **下軌 anchor 1**：${p1[1]:.2f}（{asc.get('utl_p1_date', p1[0])}）",
            f"- **下軌 anchor 2**：${p2[1]:.2f}（{asc.get('utl_p2_date', p2[0])}）",
            f"- **上軌 anchor**：${hi[1]:.2f}（{asc.get('upper_anchor_date', hi[0])}）",
            f"- **通道闊度**：${asc['channel_width']:.2f}",
            f"- **今日下軌 / 上軌**：${asc['utl_now']:.2f} / ${asc['upper_now']:.2f}",
            f"- **上軌延伸 +{asc['project_bars']}W**（約 {asc.get('future_date', '—')}）："
            f" **${asc['upper_future']:.2f}**",
            "",
        ]
    if desc:
        p1, p2 = desc["dtl_p1"], desc["dtl_p2"]
        lo = desc["lower_anchor"]
        lines += [
            "### 下降通道（DTL）",
            "",
            f"- **上軌 anchor 1**：${p1[1]:.2f}（{desc.get('dtl_p1_date', p1[0])}）",
            f"- **上軌 anchor 2**：${p2[1]:.2f}（{desc.get('dtl_p2_date', p2[0])}）",
            f"- **下軌 anchor**：${lo[1]:.2f}（{desc.get('lower_anchor_date', lo[0])}）",
            f"- **通道闊度**：${desc['channel_width']:.2f}",
            f"- **今日上軌 / 下軌**：${desc['dtl_now']:.2f} / ${desc['lower_now']:.2f}",
            f"- **下軌延伸 +{desc['project_bars']}W**（約 {desc.get('future_date', '—')}）："
            f" **${desc['lower_future']:.2f}**",
            "",
        ]
    return lines


def format_rr_section(plan: dict) -> list[str]:
    """Edge #6 R&R&S — M.E.T.A. (entry + stop + reward must align)."""
    if not plan.get("entry"):
        return []
    icon = "✅" if plan.get("meta_aligned") else "❌"
    tgt_type = plan.get("reward_target_type", "—")
    type_zh = {"wave_top": "前浪頂", "UTL": "UTL", "DTL": "DTL", "fixed_2r": "2R fallback"}.get(tgt_type, tgt_type)
    lines = [
        "## Edge #6 — R&R&S（M.E.T.A.）",
        "",
        "> **M**oney **E**ntry **T**arget **A**lignment：入場、止損、目標、RR 四者必須一齊合理先入場。",
        "",
        f"| 項目 | 數值 |",
        f"|------|------|",
        f"| 方向 | {plan.get('direction', 'long')} |",
        f"| Entry | **${plan['entry']}** |",
        f"| Stop | **${plan['stop']}** — {plan.get('stop_keylevel') or plan.get('stop_reason', '—')} |",
        f"| TP1 (1R) | ${plan['tp1']} — {plan.get('tp1_keylevel', '1R 量度目標')} |",
        f"| TP2 (主目標) | ${plan['tp2']} — **{plan.get('tp2_keylevel') or plan.get('reward_target_label', '—')}** |",
        f"| 目標類型 | {type_zh} — {plan.get('tp2_keylevel') or plan.get('reward_target_label', '—')} |",
        f"| Raw RR | **{plan.get('raw_rr', plan['rr'])}:1** {icon} |",
        f"| 評語 | {plan.get('discounted_note', '—')} |",
        "",
        "目標優先序：前浪頂 / 60日阻力 / 結構位（UTL/DTL 通道僅圖表參考，唔做 Setup TP）；"
        "止損優先序：Trading area low → 前浪底 → 20日低。",
        "",
    ]
    return lines


def format_ft_section(ft: dict) -> list[str]:
    """Edge #8 F.T. — First Touch (META 進場)."""
    if not ft:
        return []
    qual_zh = {
        "ideal": "第1次（力量最強）",
        "ok": "第2次（仍理想）",
        "caution": "第3–4次（要小心/偏短線）",
        "stale": "第5次+（易穿越失效）",
        "none": "未見 touch",
    }
    long_icon = "✅" if ft.get("long_pass") else "❌"
    short_icon = "✅" if ft.get("short_pass") else "❌"
    lines = [
        "## Edge #8 — F.T.（First Touch / META 進場）",
        "",
        "> META 進場：**盡量第1或第2次** MA touch + 即時反彈；第3/4次要小心；多次穿越後力量減弱。",
        "",
        "| 方向 | 通過 | Touch 次數 | MA | 質量 | 說明 |",
        "|------|:----:|:----------:|:--:|------|------|",
        f"| Long | {long_icon} | {ft.get('touch_number_long', 0)} | {ft.get('touch_ma_long', '—')} "
        f"| {qual_zh.get(ft.get('quality_long'), '—')} | {ft.get('long_note', '—')} |",
        f"| Short | {short_icon} | {ft.get('touch_number_short', 0)} | {ft.get('touch_ma_short', '—')} "
        f"| {qual_zh.get(ft.get('quality_short'), '—')} | {ft.get('short_note', '—')} |",
        "",
    ]
    if ft.get("breakout_ft"):
        lines.append("- 另：突破後近3K follow-through 亦計 Long F.T.")
        lines.append("")
    return lines


def _fmt_setup_price(value: float | int | None) -> str:
    if not value:
        return "—"
    return f"${value:.2f}" if isinstance(value, float) else f"${value}"


def format_watch_setups(setups: dict) -> list[str]:
    if not setups:
        return []
    lines = [
        "## Watch Setup（現價 + A/B）",
        "",
        "> **現價 / Setup A / B** 各自獨立 Entry、Stop、TP、RR；全部用 **關鍵 S/R 水平**（W1 pivot band + 匯聚區）。",
        "> A = 突破下一關鍵阻力；B = 回踩下一關鍵支持；現價 = 而家入場參考。",
        "",
    ]
    for key, title in (
        ("current", "Setup 現價 — 而家入場"),
        ("breakout", "Setup A — 突破（優先）"),
        ("retest", "Setup B — 回踩"),
    ):
        s = setups.get(key, {})
        if not s:
            continue
        entry_kl = s.get("entry_keylevel") or s.get("entry_label", "Entry")
        stop = s.get("stop") or 0
        tp1 = s.get("tp1") or 0
        tp2 = s.get("tp2") or 0
        rr = s.get("rr") or 0
        stop_kl = s.get("stop_keylevel") or s.get("stop_reason") or "—"
        tp1_kl = s.get("tp1_keylevel") or "1R 量度目標"
        tp2_kl = s.get("tp2_keylevel") or s.get("reward_target_label") or "—"
        rr_text = f"{rr}:1" if rr else "—"
        lines += [
            f"### {title}",
            "",
            "| | 價位 | Key level |",
            "|---|------|-----------|",
            f"| 觸發 | {s['trigger']} | |",
            f"| Entry | **{_fmt_setup_price(s.get('entry'))}** | **{entry_kl}** |",
        ]
        if s.get("retest_note"):
            lines.append(f"| 備註 | {s['retest_note']} | |")
        if s.get("setup_note"):
            lines.append(f"| 備註 | {s['setup_note']} | |")
        if s.get("valid") is False:
            lines.append("| ⚠ | 結構/RR 不足 — 僅供參考 | |")
        lines += [
            f"| Stop | {_fmt_setup_price(stop)} | **{stop_kl}** |",
            f"| TP1 (1R) | {_fmt_setup_price(tp1)} | {tp1_kl} |",
            f"| TP2 (主目標) | {_fmt_setup_price(tp2)} | **{tp2_kl}** |",
            f"| RR | **{rr_text}**（Entry {_fmt_setup_price(s.get('entry'))} → TP2 {_fmt_setup_price(tp2)}，Stop {_fmt_setup_price(stop)}） | |",
            "",
        ]
    return lines


def _fmt_scenario_price(s: dict) -> str:
    lo, hi = s.get("area_lo"), s.get("area_hi")
    if lo and hi and hi > lo * 1.002:
        return format_area_range(lo, hi)
    return f"${s['price']:.2f}"


def _flatten_area_sources(sources: list[str]) -> list[str]:
    """Split compound source labels (joined with ' / ') into individual entries."""
    flat: list[str] = []
    seen: set[str] = set()
    for item in sources:
        for part in item.split(" / "):
            name = part.strip()
            if name and name not in seen:
                seen.add(name)
                flat.append(name)
    return flat


def _format_scenario_sources(s: dict, max_show: int = 3) -> str:
    """Abbreviated S/R cluster sources for scenario table verification."""
    raw = s.get("area_sources") or []
    sources = _flatten_area_sources(raw)
    if not sources:
        return "—"
    ec = s.get("edge_count") or len(sources)
    shown = sources[:max_show]
    src = " / ".join(shown)
    extra = len(sources) - len(shown)
    if extra > 0:
        src += f" +{extra}"
    flip_tag = ""
    if s.get("area_flipped"):
        if s.get("kind") == "retest_resistance_area":
            flip_tag = "·阻力→支持"
        elif s.get("kind") == "break_resistance_area":
            flip_tag = "·支持→阻力"
    return f"{ec}源: {src}{flip_tag}"


def format_price_scenarios(data: dict) -> list[str]:
    sc = data.get("scenarios") or {}
    if not sc:
        return []
    lines = [
        "## 價格情景 — 升/跌會中咩 Edge",
        "",
        "> 相近 key level 已 **group 成 S/R area**（支持區 / 阻力區）；價位欄顯示 area 範圍，情景用 area 中位或上沿測試；**匯聚來源** 列顯示 cluster 組成（首 3 源 +N）方便核對。",
        "",
        f'<div style="{_EDGE_OVERVIEW_WRAP_STYLE}">',
        "",
        "| 情景 | 價位（area） | 匯聚來源 | Long | Short | 新增 Long | 新增 Short | S&R 提示 |",
        "|------|-------------|----------|:----:|:-----:|-----------|------------|----------|",
    ]
    for s in sc.get("scenarios", []):
        ln = "、".join(s["long_new"]) if s["long_new"] else "—"
        sn = "、".join(s["short_new"]) if s["short_new"] else "—"
        sr_hint = (s.get("sr_note") or "")[:36]
        src_col = _format_scenario_sources(s)
        lines.append(
            f"| {s['label']} | {_fmt_scenario_price(s)} | {src_col} | {edge_score_fmt(s['long_count'])} | "
            f"{edge_score_fmt(s['short_count'])} | {ln} | {sn} | {sr_hint} |"
        )
    lines.append("")
    lines.append("</div>")
    lines.append("")
    best = sc.get("best_long")
    if best and best.get("long_new"):
        gain = "、".join(best["long_new"])
        lines += [
            f"**最有潛力 Long 情景**：{best['label']} @ {_fmt_scenario_price(best)} → Long **{edge_score_fmt(best['long_count'])}**（+{gain}）",
            "",
        ]
    best_s = sc.get("best_short")
    if best_s and best_s.get("short_new"):
        gain_s = "、".join(best_s["short_new"])
        lines += [
            f"**最有潛力 Short 情景**：{best_s['label']} @ {_fmt_scenario_price(best_s)} → Short **{edge_score_fmt(best_s['short_count'])}**（+{gain_s}）",
            "",
        ]
    return lines


def format_md(data: dict) -> str:
    labels = EDGE_DISPLAY_NAMES
    m = data.get("metrics") or {}
    setups = data.get("setups") or {}

    lines = [
        "## 一句結論",
        "",
        one_line_conclusion(data),
        "",
    ]
    src = data.get("source") or ""
    if src:
        src_zh = "TradingView CSV" if src == "CSV" else ("yfinance 自動拉數" if src == "yfinance" else src)
        lines += [f"> 數據來源：**{src_zh}** — Cloud 用 yfinance；本機 TV CSV 最準。", ""]

    tfs = data.get("timeframes") or {}
    if tfs:
        lines.extend(format_combined_edge_dashboard(tfs, setups))
        lines.extend(format_watch_setups(setups))
        if d1_analysis := data.get("d1_analysis"):
            lines.extend(format_sr_key_levels_section(d1_analysis, bars=data.get("d1_bars")))
        lines.extend(format_price_scenarios(data))
        if mtf := data.get("mtf_detail"):
            lines.extend(format_mtf_section(mtf))
        if rs := data.get("rs_detail"):
            lines.extend(format_rs_section(rs))
        if market := data.get("market_edge_detail"):
            lines.extend(format_broad_market_section(market))
        if p := data.get("entry_plan"):
            lines.extend(format_rr_section(p))
        if ft := data.get("ft_detail"):
            lines.extend(format_ft_section(ft))
        for tf in TF_ORDER:
            if tf in tfs:
                lines.extend(format_tf_detail_section(tf, tfs[tf]))

    lines += [
        "## D1 數據摘要（入場規劃用）",
        "",
        "| 項目 | 數值 |",
        "|------|------|",
        f"| 收市 | **${data['price']}** |",
        f"| 成交量 | {data['volume']}（均量 {data['volume_avg']}） |",
        f"| 5MA / 10MA / 20MA | {m.get('sma5', '—')} / {m.get('sma10', '—')} / {m.get('sma20', '—')} |",
        f"| EMA20 / EMA50 | {m.get('ema20', '—')} / {m.get('ema50', '—')} |",
        f"| 趨勢方向 | {m.get('trend_dir', '—')} |",
        f"| 前浪頂 / 前浪底 | ${m.get('wave_top', '—')} / ${m.get('wave_bottom', '—')} |",
        f"| 20 日低 / 60 日阻力 | ~**${m.get('swing_low', '—')}** / ~**${m.get('resistance', '—')}** |",
        "",
    ]

    area = m.get("trading_area") or {}
    if area:
        src = "、".join(area.get("sources", [])) or "—"
        lines += [
            "## Multiple Edge Trading Area",
            "",
            f"- **類型**：{area.get('type', '—')}",
            f"- **價區**：${area.get('zone_lo', '—')} – ${area.get('zone_hi', '—')}",
            f"- **匯聚來源（{area.get('edge_count', 0)}）**：{src}",
            "",
        ]

    sc = data.get("scenarios") or {}
    if sc:
        bias_zh = {"long": "偏多 → 可研究 Long", "short": "偏空 → 可研究 Short", "neutral": "中性"}
        lines += [
            "## Long vs Short Edge 對比（現價）",
            "",
            f"- **Long edges**：{edge_score_fmt(sc.get('current_long', 0))}",
            f"- **Short edges**：{edge_score_fmt(sc.get('current_short', 0))}",
            f"- **偏向**：{bias_zh.get(sc.get('bias', 'neutral'), sc.get('bias'))}",
            "",
        ]
    short_edges = data.get("short_edges") or {}
    short_notes = data.get("short_edge_notes") or {}
    edges = data["edges"]
    if short_edges:
        lines += [
            f"## Short Edge 明細（{EDGE_SCORE_MAX}/{EDGE_SCORE_MAX}）",
            "",
            "| # | Edge | Short | Notes |",
            "|---|------|:-----:|-------|",
        ]
        for i, key in enumerate(EDGES):
            icon = "✅" if short_edges.get(key) else "❌"
            note = short_notes.get(key, edges[key].get("short_note", "")) if key in (short_notes or {}) else edges[key].get("short_note", "")
            lines.append(f"| {i+1} | {labels[i]} | {icon} | {note} |")
        lines.append("")
    lines += [
        f"## {EDGE_SCORE_MAX}-Edge 評分（Long，D1 主評級）",
        "",
        "| # | Edge | Long | Notes |",
        "|---|------|:----:|-------|",
    ]
    for i, key in enumerate(EDGES):
        e = data["edges"][key]
        icon = "✅" if e["score"] else "❌"
        note = e["note"]
        if key == "mi" and not MI_EDGE_SCORING_ENABLED:
            note = f"{note}（不計分·請喺 TV 睇 MACD）"
        if key == "csp_pa_vol" and e.get("short_score"):
            note = f"Long: {note} | Short: {e.get('short_note', '')}"
        lines.append(f"| {i+1} | {labels[i]} | {icon} | {note} |")

    mi_note = ""
    if not MI_EDGE_SCORING_ENABLED:
        mi_note = " · *MI/MACD 不計分，請喺 TV 自行確認*"
    lines += [
        "",
        f"**Total: {edge_score_fmt(data['total_score'])} | Grade: {data['grade']} | Decision: {data['decision']}**{mi_note}",
        "",
    ]

    p = data.get("entry_plan") or {}
    if p.get("entry"):
        lines += [
            "## 若而家入場（結構 RR 參考）",
            "",
            f"- 類型：{p.get('preferred', '—')}",
            f"- Entry **${p['entry']}** | Stop **${p['stop']}**（{p.get('stop_reason', '—')}）",
            f"- TP1 ${p['tp1']} (1R) | TP2 ${p['tp2']} ({p.get('reward_target_label', '主目標')})",
            f"- Raw RR **{p.get('raw_rr', p['rr'])}:1** | {p.get('discounted_note', '')}",
            "",
        ]

    lines += ["## 總結", "", data.get("summary_zh", ""), ""]
    return "\n".join(lines)


def format_batch_summary(results: list[dict]) -> str:
    header = [f"# 9-Edge Batch Summary [CSV] — {date.today().isoformat()}", ""]
    if results:
        m = results[0].get("market_edge_detail") or {}
        if m.get("directive"):
            header.append(
                f"**大盤 SPY**：{m['directive']} "
                f"(Long Edge {m.get('long_count', 0)}/3 | Short Edge {m.get('short_count', 0)}/3)"
            )
            header.append("")
    lines = header + [
        "| Symbol | 現況 | Long | Short | 潛力價 | 潛力Long | 偏向 | Grade |",
        "|--------|:----:|:----:|:-----:|--------|:--------:|------|-------|",
    ]
    for r in sorted(
        results,
        key=lambda x: (
            -(x.get("scenarios") or {}).get("best_long", {}).get("long_count", x["total_score"]),
            -x["total_score"],
            x["symbol"],
        ),
    ):
        sc = r.get("scenarios") or {}
        best = sc.get("best_long") or {}
        pot_price = f"${best['price']}" if best.get("price") else "—"
        pot_long = edge_score_fmt(best['long_count']) if best.get("long_count") else "—"
        bias = sc.get("bias", "—")
        lines.append(
            f"| {r['symbol']} | {edge_score_fmt(r['total_score'])} | {edge_score_fmt(sc.get('current_long', 0))} | "
            f"{edge_score_fmt(sc.get('current_short', 0))} | {pot_price} | {pot_long} | {bias} | {r['grade']} |"
        )
    a_list = [r["symbol"] for r in results if r["grade"] == "A"]
    b_list = [r["symbol"] for r in results if r["grade"] == "B"]
    pot_list = sorted(
        [r for r in results if (r.get("scenarios") or {}).get("best_long")],
        key=lambda x: -(x["scenarios"]["best_long"]["long_count"]),
    )[:10]
    lines += ["", f"**A ({len(a_list)}):** {', '.join(a_list) or '—'}", ""]
    lines += [f"**B watch ({len(b_list)}):** {', '.join(b_list) or '—'}", ""]
    if pot_list:
        lines.append("**潛力榜（最佳 Long 情景）：**")
        for r in pot_list[:5]:
            b = r["scenarios"]["best_long"]
            lines.append(
                f"- **{r['symbol']}** @ ${b['price']} → Long {edge_score_fmt(b['long_count'])}"
                f"（現況 {edge_score_fmt(r['total_score'])}）"
            )
    return "\n".join(lines)


def run_one(
    symbol: str,
    d1: Path | None = None,
    w1: Path | None = None,
    h1: Path | None = None,
    market_edge: dict | None = None,
) -> dict:
    sym = symbol.upper()
    d1_path = d1 or CSV_DIR / f"{sym}_D1.csv"
    w1_path = w1 or CSV_DIR / f"{sym}_W1.csv"
    h1_path = h1 or CSV_DIR / f"{sym}_H1.csv"
    if not d1_path.exists():
        raise FileNotFoundError(d1_path)
    return score_symbol(
        sym, d1_path,
        w1_path if w1_path.exists() else None,
        h1_path if h1_path.exists() else None,
        market_edge=market_edge,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", "-s", help="Single symbol")
    parser.add_argument("--batch", "-b", action="store_true", help="Analyze all *_D1.csv in charts/csv/")
    parser.add_argument("--d1", type=Path)
    parser.add_argument("--w1", type=Path)
    parser.add_argument("--h1", type=Path)
    args = parser.parse_args()

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    if args.batch:
        symbols = discover_symbols()
        if not symbols:
            raise SystemExit(f"No *_D1.csv in {CSV_DIR}")
        market_edge = assess_broad_market_edge()
        global _MARKET_EDGE_CACHE
        _MARKET_EDGE_CACHE = market_edge
        results = []
        for sym in symbols:
            try:
                data = run_one(sym, None, None, None, market_edge=market_edge)
                out = REPORTS / f"{sym}_{date.today().isoformat()}_9edge_csv.md"
                out.write_text(format_md(data), encoding="utf-8")
                results.append(data)
                print(f"OK {sym} {edge_score_fmt(data['total_score'])} {data['grade']}")
            except Exception as e:
                print(f"SKIP {sym}: {e}")
        summary = REPORTS / f"BATCH_{date.today().isoformat()}_summary.md"
        summary.write_text(format_batch_summary(results), encoding="utf-8")
        print(f"\nSummary -> {summary}")
        return

    if not args.symbol:
        raise SystemExit("Use --symbol ETN or --batch")

    data = run_one(args.symbol, args.d1, args.w1, args.h1)
    out = REPORTS / f"{data['symbol']}_{date.today().isoformat()}_9edge_csv.md"
    md = format_md(data)
    out.write_text(md, encoding="utf-8")
    try:
        print(md)
    except UnicodeEncodeError:
        safe = md.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        )
        print(safe)
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main()
