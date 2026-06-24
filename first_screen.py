#!/usr/bin/env python3
"""
First Screen — 獨立初篩（唔改 9-edge 評分）

W1 + D1：MA 轉上 + 量價配合（股升日量>VolMA、股跌日量<VolMA）+ optional 大陽燭。

  python first_screen.py --symbol STX
  python first_screen.py --csv path/to/screener.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import analyze_tv_csv as tv  # noqa: E402
import screen_screener_csv as screener  # noqa: E402

REPORTS = ROOT / "reports" / "first_screen"
_CLOUD_FS_REPORTS: Path | None = None


def get_fs_reports_dir() -> Path:
    """Writable First Screen output dir (temp on Streamlit Cloud)."""
    global _CLOUD_FS_REPORTS
    try:
        from edge_common import is_cloud_environment
    except ImportError:
        return REPORTS
    if not is_cloud_environment():
        return REPORTS
    if _CLOUD_FS_REPORTS is None:
        import tempfile
        _CLOUD_FS_REPORTS = Path(tempfile.gettempdir()) / "9edge" / "first_screen"
    _CLOUD_FS_REPORTS.mkdir(parents=True, exist_ok=True)
    return _CLOUD_FS_REPORTS

FS_TV_IMPORT_OPTIONS: list[tuple[str, str]] = [
    ("FirstScreen_{date}_comma.txt", "入選全部（建議 · 含 Sector / Industry）"),
    ("hits_comma.txt", "入選全部（含 Sector / Industry）"),
    ("hits_by_sector_industry.txt", "入選 · Sector+Industry 分組（同 dated 檔）"),
    ("W1_only_comma.txt", "只 W1 入選（含 Sector / Industry）"),
    ("D1_only_comma.txt", "只 D1 入選（含 Sector / Industry）"),
    ("W1_D1_both_comma.txt", "W+D 齊過（含 Sector / Industry）"),
]

MIN_BARS_D = 60
MIN_BARS_W = 55
VOLUME_WINDOW_D = 20  # fallback cap when MA 轉平搵唔到
VOLUME_WINDOW_W = 12
VOLUME_MA_D = 50  # 同 TV Volume MA（D1）
VOLUME_MA_W = 50  # 同 TV Volume MA（W1）
VOLUME_MA_FLAT_MAX_SPAN_D = 45  # 量價 window 最長 cap
VOLUME_MA_FLAT_LOCAL_SPAN_D = 18  # 搵 trough / 轉平只睇近 ~18 根（避免攞到更早浪谷）
VOLUME_MA_FLAT_MAX_SPAN_W = 20
VOLUME_MA_FLAT_LOCAL_SPAN_W = 10
VOLUME_DOWN_VOL_SLACK = 0.05  # 跌日：vol ≤ VolMA × (1 + slack)


@dataclass(frozen=True)
class VolumePassConfig:
    """量價 finetune：逐根 K 計數 + 可選均量確認。

    - pass_ratio：升/跌日入面幾多比例根 K 要符合（對應 TV 逐根睇藍線）
    - min_hits：最少幾根（避免 W 線樣本少時 2/3 巧合過關）
    - require_avg：升/跌日**平均量** vs 該組**平均 VolMA** 都要啱（平滑單日異常）
    - down_vol_slack：跌日容差（0.05 = 可 ≤ VolMA×1.05）；升日維持嚴格 >
    """

    pass_ratio: float
    min_hits: int
    require_avg: bool = True
    down_vol_slack: float = VOLUME_DOWN_VOL_SLACK


# D1 樣本多（~20 日 window）→ 稍嚴；W1 樣本少（~12 週）→ 稍鬆 + 必須均量確認
VOLUME_CFG_D = VolumePassConfig(pass_ratio=0.55, min_hits=4, require_avg=True)
VOLUME_CFG_W = VolumePassConfig(pass_ratio=0.42, min_hits=2, require_avg=True)


@dataclass(frozen=True)
class BigBullPassConfig:
    """大陽燭：近 window 根入面至少 min_hits 根符合 9-edge body 定義。"""

    window: int
    min_hits: int
    body_avg_period: int = 20


# D 20 日內 ≥2 根大陽（動能股常見）；W 12 週內 ≥1 根週陽已夠明顯
BIG_BULL_CFG_D = BigBullPassConfig(window=20, min_hits=2, body_avg_period=20)
BIG_BULL_CFG_W = BigBullPassConfig(window=12, min_hits=1, body_avg_period=12)


def _volume_side_pass(
    hit: int,
    total: int,
    avg_vol: float,
    avg_vma: float,
    *,
    cfg: VolumePassConfig,
    up_side: bool,
) -> tuple[bool, bool, bool, int]:
    """Return (pass, per_bar_ok, avg_ok, need_hits)."""
    if total <= 0 or avg_vma <= 0:
        return False, False, False, 0
    # 短 window 跌日少：need 唔可以大過 total（例：3 跌日唔使硬要 4 根）
    need = max(min(cfg.min_hits, total), math.ceil(total * cfg.pass_ratio))
    per_bar_ok = hit >= need
    if up_side:
        avg_ok = avg_vol > avg_vma
    else:
        slack = 1.0 + cfg.down_vol_slack
        avg_ok = avg_vol < avg_vma * slack
    if cfg.require_avg:
        ok = per_bar_ok and avg_ok
    else:
        ok = per_bar_ok
    return ok, per_bar_ok, avg_ok, need


def find_ma10_flat_start(
    s10: list[float],
    *,
    slope_bars: int = 5,
    flat_slope: float = 0.008,
    max_span: int = 45,
    local_span: int = 18,
    min_pullback: float = 0.03,
    fallback_window: int = 20,
) -> int:
    """量價 window 起點：10MA 真正轉平日（唔計仍向下斜率段）。

    由近段 trough 向前掃：slope 唔再急跌（≥-1%/5bar）或 3-bar 10MA 橫行 → 開始計跌量。
    MU 8/12：8/1 slope -2.7% 仍跌 ❌ → 8/8 +0.1% ✅；12/1 微平 ✅。
    """
    n = len(s10)
    if n < slope_bars + 15:
        return max(0, n - fallback_window)

    def slope_at(i: int) -> float:
        if i < slope_bars:
            return 0.0
        base = s10[i - slope_bars]
        return (s10[i] - base) / base if base > 0 else 0.0

    end = n - 1
    search_start = max(slope_bars + 5, end - local_span)

    # 近段 recovery 起點嘅 10MA 谷（由尾往前，同 detect_ma_inflection_up；避免 W 線攞到更早 Jan 低點）
    min_i = end
    while min_i > search_start and s10[min_i] >= s10[min_i - 1] * 0.997:
        min_i -= 1
    min_v = s10[min_i]

    peak_start = max(0, min_i - 25)
    peak_end = max(peak_start, min_i - 3)
    peak_i = max(range(peak_start, peak_end + 1), key=lambda k: s10[k])
    if s10[peak_i] < min_v * (1 + min_pullback):
        return max(0, n - fallback_window)

    flat_start = min_i
    for i in range(min_i, end + 1):
        if slope_at(i) >= -flat_slope:
            flat_start = i
            break

    if end - flat_start + 1 > max_span:
        flat_start = end - max_span + 1
    # 轉平剛開始 1–2 根都 accept，唔 fallback 去固定 20/12 日
    return max(0, min(flat_start, end))


def volume_window_start(
    bars: list[dict],
    *,
    tf_label: str,
    fixed_window: int,
) -> int:
    """Dynamic seg_start index for volume scan (inclusive)."""
    closes = [b["close"] for b in bars]
    s10 = tv.sma(closes, 10)
    if tf_label == "W1":
        return find_ma10_flat_start(
            s10,
            max_span=VOLUME_MA_FLAT_MAX_SPAN_W,
            local_span=VOLUME_MA_FLAT_LOCAL_SPAN_W,
            fallback_window=fixed_window,
        )
    return find_ma10_flat_start(
        s10,
        max_span=VOLUME_MA_FLAT_MAX_SPAN_D,
        local_span=VOLUME_MA_FLAT_LOCAL_SPAN_D,
        fallback_window=fixed_window,
    )


@dataclass
class TfFilter:
    """Per-timeframe optional sub-checks (ticked = required)."""

    enabled: bool = False
    ma_turn: bool = False
    down_vol: bool = False
    up_vol: bool = False
    big_bull: bool = False
    pullback_turn: bool = False

    def is_active(self) -> bool:
        return (
            self.enabled
            or self.ma_turn
            or self.down_vol
            or self.up_vol
            or self.big_bull
            or self.pullback_turn
        )

    def required_keys(self) -> list[str]:
        if not self.is_active():
            return []
        keys: list[str] = []
        if self.ma_turn:
            keys.append("ma_turn")
        if self.pullback_turn:
            keys.append("pullback_turn")
        if self.down_vol:
            keys.append("down_vol")
        if self.up_vol:
            keys.append("up_vol")
        if self.big_bull:
            keys.append("big_bull")
        if self.enabled and not keys:
            return ["ma_turn", "down_vol", "up_vol"]
        return keys

    def summary(self, label: str) -> str:
        keys = self.required_keys()
        if not keys:
            return ""
        names = {
            "ma_turn": "MA轉上",
            "pullback_turn": "拉回後轉上",
            "down_vol": "股跌日量低",
            "up_vol": "股升日量高",
            "big_bull": "大陽燭",
        }
        return f"{label}(" + "+".join(names[k] for k in keys) + ")"


@dataclass
class FirstScreenFilters:
    """Optional gates — ticked items must all pass for on-list."""

    w1: TfFilter = field(default_factory=TfFilter)
    d1: TfFilter = field(default_factory=TfFilter)
    require_counter_trend: bool = False
    require_leading_ma: bool = False

    def has_tf_filters(self) -> bool:
        return self.w1.is_active() or self.d1.is_active()

    def summary(self) -> str:
        parts: list[str] = []
        if self.w1.is_active():
            parts.append(self.w1.summary("W1"))
        if self.d1.is_active():
            parts.append(self.d1.summary("D1"))
        if not self.has_tf_filters():
            parts.append("W 或 D 3/3")
        if self.require_counter_trend:
            parts.append("反向走勢")
        if self.require_leading_ma:
            parts.append("領先MA vs SPY")
        return " + ".join(parts)

    def needs_rs(self) -> bool:
        return self.require_counter_trend or self.require_leading_ma


def attach_row_meta(row: dict, meta: dict[str, dict]) -> None:
    sym = row["symbol"]
    m = meta.get(sym) or {}
    row["_meta"] = m
    row["sector"] = m.get("sector") or ""
    row["industry"] = m.get("industry") or ""


def row_sector_industry(r: dict) -> tuple[str, str]:
    m = r.get("_meta") or {}
    sec = r.get("sector") or m.get("sector") or "—"
    ind = r.get("industry") or m.get("industry") or "—"
    return sec or "—", ind or "—"


def build_hit_rows(results: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for r in results:
        if not r.get("pass"):
            continue
        sec, ind = row_sector_industry(r)
        rows.append(
            {
                "symbol": r["symbol"],
                "sector": sec,
                "industry": ind,
                "price": r.get("price"),
                "pass_tf": r.get("pass_tf") or "—",
                "grade": r.get("grade") or "",
                "w1": f"{r.get('w1_score', 0)}/3",
                "d1": f"{r.get('d1_score', 0)}/3",
                "note": (r.get("note") or "")[:80],
            }
        )
    return rows


def build_check_rows(items: list[tuple[str, str, bool, str]]) -> list[dict]:
    return [{"key": k, "label": lbl, "pass": ok, "note": note} for k, lbl, ok, note in items]


def assess_volume_up_down(
    bars: list[dict],
    *,
    window: int,
    vol_ma_len: int = VOLUME_MA_D,
    vol_cfg: VolumePassConfig | None = None,
    tf_label: str = "D1",
) -> dict:
    """量價配合：股升日量>VolMA、股跌日量<VolMA×(1+slack)（逐根 K + 可選均量確認）。"""
    cfg = vol_cfg or VOLUME_CFG_D
    fail = {
        "pass": False,
        "up_avg": 0.0,
        "down_avg": 0.0,
        "vol_ma": 0.0,
        "vol_ma_len": vol_ma_len,
        "up_high": False,
        "down_low": False,
        "up_hit": 0,
        "up_total": 0,
        "down_hit": 0,
        "down_total": 0,
        "window_bars": 0,
        "window_from": None,
        "note": "量價數據不足",
    }
    min_need = max(window + 5, vol_ma_len + 2)
    if len(bars) < min_need:
        return fail

    vols = [float(b["volume"]) for b in bars]
    vol_ma = tv.sma(vols, vol_ma_len)
    seg_start = volume_window_start(bars, tf_label=tf_label, fixed_window=window)
    down_cap = 1.0 + cfg.down_vol_slack
    slack_pct = int(cfg.down_vol_slack * 100)

    up_hit = up_total = down_hit = down_total = 0
    up_vols: list[float] = []
    down_vols: list[float] = []
    up_vmas: list[float] = []
    down_vmas: list[float] = []
    for i in range(seg_start, len(bars)):
        if i < 1:
            continue
        b = bars[i]
        vma = vol_ma[i]
        if vma <= 0:
            continue
        prev = bars[i - 1]["close"]
        vol = float(b["volume"])
        if b["close"] < prev:
            down_total += 1
            down_vols.append(vol)
            down_vmas.append(vma)
            if vol < vma * down_cap:
                down_hit += 1
        elif b["close"] > prev:
            up_total += 1
            up_vols.append(vol)
            up_vmas.append(vma)
            if vol > vma:
                up_hit += 1

    if up_total == 0 and down_total == 0:
        return {
            **fail,
            "window_bars": len(bars) - seg_start,
            "window_from": bars[seg_start].get("date") if seg_start < len(bars) else None,
            "note": "近段股升/股跌日太少（跟前收市比）",
        }

    # 轉平後只得升日（V 底剛起 1–2 根）：無跌日可量 → down_vol 視為 pass
    if down_total == 0 and up_total > 0:
        up_avg = sum(up_vols) / len(up_vols)
        vma_up_avg = sum(up_vmas) / len(up_vmas)
        vol_ma_now = vol_ma[-1]
        win_from = bars[seg_start].get("date")
        win_n = len(bars) - seg_start
        up_high, _, up_avg_ok, up_need = _volume_side_pass(
            up_hit, up_total, up_avg, vma_up_avg, cfg=cfg, up_side=True
        )
        note = (
            f"窗{win_n}根(自{win_from})；"
            f"轉平後無跌日✓；"
            f"股升 {up_hit}/{up_total}≥{up_need}根"
            f"{'✓' if up_high else '✗'}"
        )
        return {
            "pass": up_high,
            "up_avg": round(up_avg),
            "down_avg": 0,
            "vol_ma": round(vol_ma_now),
            "vol_ma_len": vol_ma_len,
            "up_high": up_high,
            "down_low": True,
            "up_higher": up_high,
            "up_hit": up_hit,
            "up_total": up_total,
            "down_hit": 0,
            "down_total": 0,
            "up_need": up_need,
            "down_need": 0,
            "up_per_bar": up_hit >= up_need if up_need else False,
            "down_per_bar": True,
            "up_avg_ok": up_avg_ok,
            "down_avg_ok": True,
            "vma_up_avg": round(vma_up_avg),
            "vma_down_avg": 0,
            "window_bars": win_n,
            "window_from": win_from,
            "note": note,
        }

    if up_total == 0 or down_total == 0:
        return {
            **fail,
            "window_bars": len(bars) - seg_start,
            "window_from": bars[seg_start].get("date") if seg_start < len(bars) else None,
            "note": "近段股升/股跌日太少（跟前收市比）",
        }

    up_avg = sum(up_vols) / len(up_vols)
    down_avg = sum(down_vols) / len(down_vols)
    vma_up_avg = sum(up_vmas) / len(up_vmas)
    vma_down_avg = sum(down_vmas) / len(down_vmas)
    vol_ma_now = vol_ma[-1]
    win_from = bars[seg_start].get("date")
    win_n = len(bars) - seg_start

    up_high, up_bar, up_avg_ok, up_need = _volume_side_pass(
        up_hit, up_total, up_avg, vma_up_avg, cfg=cfg, up_side=True
    )
    down_low, dn_bar, dn_avg_ok, dn_need = _volume_side_pass(
        down_hit, down_total, down_avg, vma_down_avg, cfg=cfg, up_side=False
    )

    pct = int(cfg.pass_ratio * 100)
    note = (
        f"窗{win_n}根(自{win_from})；"
        f"股升 {up_hit}/{up_total}≥{up_need}根({pct}%+均量{'✓' if up_avg_ok else '✗'})"
        f"{'✓' if up_high else '✗'}"
        f"；股跌 {down_hit}/{down_total}≥{dn_need}根"
        f"(≤VolMA+{slack_pct}%·{'✓' if dn_avg_ok else '✗'}均量)"
        f"{'✓' if down_low else '✗'}"
    )
    return {
        "pass": up_high and down_low,
        "up_avg": round(up_avg),
        "down_avg": round(down_avg),
        "vol_ma": round(vol_ma_now),
        "vol_ma_len": vol_ma_len,
        "up_high": up_high,
        "down_low": down_low,
        "up_higher": up_high,
        "up_hit": up_hit,
        "up_total": up_total,
        "down_hit": down_hit,
        "down_total": down_total,
        "up_need": up_need,
        "down_need": dn_need,
        "up_per_bar": up_bar,
        "down_per_bar": dn_bar,
        "up_avg_ok": up_avg_ok,
        "down_avg_ok": dn_avg_ok,
        "vma_up_avg": round(vma_up_avg),
        "vma_down_avg": round(vma_down_avg),
        "window_bars": win_n,
        "window_from": win_from,
        "note": note,
    }


def assess_ma_turn_up(bars: list[dict], *, weekly: bool = False) -> dict:
    closes = [b["close"] for b in bars]
    s10 = tv.sma(closes, 10)
    s20 = tv.sma(closes, 20)
    s50 = tv.sma(closes, 50)
    e20 = tv.ema(closes, 20)
    ok, detail = tv.detect_ma_inflection_up(
        s10, s20, s50, e20, closes[-1], relax_now_rising=weekly,
    )
    return {
        "pass": ok,
        "detail": detail,
        "sma10": round(s10[-1], 2),
        "sma20": round(s20[-1], 2),
        "sma50": round(s50[-1], 2),
        "ema20": round(e20[-1], 2),
    }


def assess_pullback_ma_turn(bars: list[dict], *, peak_window: int = 50) -> dict:
    closes = [b["close"] for b in bars]
    s10 = tv.sma(closes, 10)
    s20 = tv.sma(closes, 20)
    ok, detail = tv.detect_pullback_ma_turn_up(
        bars, s10, s20, closes[-1], peak_window=peak_window,
    )
    return {"pass": ok, "detail": detail}


def assess_big_bullish_candles(bars: list[dict], *, cfg: BigBullPassConfig) -> dict:
    """大陽燭：body≥1.8×近均 body 且 body 佔 range≥55%（同 9-edge Edge #3）。"""
    fail = {
        "pass": False,
        "hits": 0,
        "window": cfg.window,
        "min_hits": cfg.min_hits,
        "note": "大陽燭數據不足",
    }
    min_need = cfg.window + cfg.body_avg_period + 2
    if len(bars) < min_need:
        return fail

    seg_start = len(bars) - cfg.window
    hits = 0
    hit_dates: list[str] = []
    for i in range(seg_start, len(bars)):
        avg_b = tv.avg_body_size(bars, i, period=cfg.body_avg_period)
        if tv.is_big_bullish_body(bars[i], avg_b):
            hits += 1
            hit_dates.append(str(bars[i].get("date", "")))

    ok = hits >= cfg.min_hits
    recent = "、".join(hit_dates[-2:]) if hit_dates else "—"
    note = (
        f"近{cfg.window}根 {hits} 根大陽（需≥{cfg.min_hits}）"
        f"{'✓' if ok else '✗'}"
        f"{(' · ' + recent) if hit_dates else ''}"
    )
    return {
        "pass": ok,
        "hits": hits,
        "window": cfg.window,
        "min_hits": cfg.min_hits,
        "hit_dates": hit_dates,
        "note": note,
    }


def assess_tf_screen(
    bars: list[dict],
    *,
    tf_label: str,
    volume_window: int,
    volume_ma_len: int,
    vol_cfg: VolumePassConfig,
    bull_cfg: BigBullPassConfig,
    min_bars: int,
) -> dict:
    """單一 TF 三項初篩。"""
    empty = {
        "tf": tf_label,
        "pass": False,
        "score": 0,
        "max_score": 3,
        "grade": "C",
        "check_rows": [],
        "ma": {},
        "volume": {},
        "big_bull": {},
        "close": None,
        "note": "K 線不足",
    }
    if len(bars) < min_bars:
        return empty

    ma = assess_ma_turn_up(bars, weekly=(tf_label == "W1"))
    pb_peak = 12 if tf_label == "W1" else 50
    pb = assess_pullback_ma_turn(bars, peak_window=pb_peak)
    vol = assess_volume_up_down(
        bars,
        window=volume_window,
        vol_ma_len=volume_ma_len,
        vol_cfg=vol_cfg,
        tf_label=tf_label,
    )
    bull = assess_big_bullish_candles(bars, cfg=bull_cfg)
    last = bars[-1]

    down_low = bool(vol.get("down_low"))
    up_high = bool(vol.get("up_high"))
    vol_note = vol.get("note", "")
    checks = [
        ("ma_turn", "① MA 由平/下轉上", ma["pass"], ma["detail"]),
        ("pullback_turn", "①b 拉回後轉上", pb["pass"], pb["detail"]),
        ("down_vol", "② 股跌日量低", down_low, vol_note),
        ("up_vol", "③ 股升日量高", up_high, vol_note),
        ("big_bull", "④ 大陽燭", bool(bull.get("pass")), bull.get("note", "")),
    ]
    rows = build_check_rows(checks)
    score = sum(1 for c in checks[:3] if c[2])
    passed = score >= 3
    grade = "A" if score >= 3 else ("B" if score == 2 else "C")

    notes = []
    if ma["pass"]:
        notes.append(f"MA轉上")
    if pb["pass"]:
        notes.append("拉回後轉上")
    if up_high and down_low:
        notes.append("量價配合")
    elif up_high:
        notes.append("股升日量高")
    elif down_low:
        notes.append("股跌日量低")
    if bull.get("pass"):
        notes.append(f"大陽{bull.get('hits', 0)}根")

    return {
        "tf": tf_label,
        "pass": passed,
        "score": score,
        "max_score": 3,
        "grade": grade,
        "check_rows": rows,
        "ma": ma,
        "pullback": pb,
        "volume": vol,
        "big_bull": bull,
        "close": round(last["close"], 2),
        "note": "；".join(notes) if notes else "未達",
    }


def eval_tf_filter(tf: dict, flt: TfFilter) -> bool | None:
    """Return True/False if filter active; None if this TF not in filter set."""
    keys = flt.required_keys()
    if not keys:
        return None
    if not tf or tf.get("note") == "K 線不足":
        return False
    rows = {c["key"]: c for c in (tf.get("check_rows") or [])}
    return all(rows.get(k, {}).get("pass") for k in keys)


def apply_filters(
    combo: dict,
    w1: dict,
    d1: dict,
    *,
    filters: FirstScreenFilters | None,
    rs: dict | None = None,
) -> dict:
    """Apply optional W/D sub-checks + RS tick-box gates."""
    out = dict(combo)
    w_base = bool(w1.get("pass"))
    d_base = bool(d1.get("pass"))

    w_flt = eval_tf_filter(w1, filters.w1) if filters else None
    d_flt = eval_tf_filter(d1, filters.d1) if filters else None

    if filters and filters.has_tf_filters():
        results = [r for r in (w_flt, d_flt) if r is not None]
        if not results:
            tf_ok = w_base or d_base
            tf_note = combo.get("pass_tf") or ""
        elif w_flt is not None and d_flt is not None:
            tf_ok = bool(w_flt or d_flt)
            if w_flt and d_flt:
                tf_note = "W1+D1"
            elif w_flt:
                tf_note = "W1"
            elif d_flt:
                tf_note = "D1"
            else:
                tf_note = "W/D 未過"
        else:
            tf_ok = bool(results[0])
            tf_note = "W1" if w_flt is not None else "D1"
            if not tf_ok:
                tf_note += " 未過"
    else:
        tf_ok = w_base or d_base
        tf_note = combo.get("pass_tf") or ("W 或 D" if tf_ok else "")

    counter_ok = leading_ok = True
    rs_bits: list[str] = []
    if filters and filters.needs_rs() and rs:
        feats = rs.get("features") or {}
        if filters.require_counter_trend:
            counter_ok = bool(feats.get("counter_trend", {}).get("long"))
            rs_bits.append(feats.get("counter_trend", {}).get("long_note", "反向—"))
        if filters.require_leading_ma:
            leading_ok = bool(feats.get("leading_ma", {}).get("long"))
            rs_bits.append(feats.get("leading_ma", {}).get("long_note", "領先MA—"))
    elif filters and filters.needs_rs():
        counter_ok = leading_ok = False
        rs_bits.append("RS 數據不足")

    on_list = tf_ok and counter_ok and leading_ok
    out["pass"] = on_list
    out["w1_pass"] = w_base
    out["d1_pass"] = d_base
    out["w1_filter_pass"] = w_flt
    out["d1_filter_pass"] = d_flt
    out["tf_ok"] = tf_ok
    out["counter_ok"] = counter_ok
    out["leading_ma_ok"] = leading_ok

    if on_list:
        if w_flt and d_flt:
            out["grade"] = "A+"
            out["pass_tf"] = tf_note if tf_note in ("W1+D1", "W1", "D1") else "W1+D1"
        elif w_flt or (tf_ok and w_base and not d_base):
            out["grade"] = "A"
            out["pass_tf"] = "W1" if (w_flt or w_base) and not (d_flt or d_base) else tf_note
        elif d_flt or (tf_ok and d_base):
            out["grade"] = "A"
            out["pass_tf"] = "D1" if d_flt or d_base else tf_note
        else:
            out["grade"] = "A"
            out["pass_tf"] = tf_note
    elif w_base or d_base or (int(w1.get("score") or 0) >= 2 or int(d1.get("score") or 0) >= 2):
        out["grade"] = "B"
        out["pass_tf"] = ""
    else:
        out["grade"] = "C"
        out["pass_tf"] = ""

    notes = [n for n in [combo.get("note"), tf_note if not tf_ok else "", *rs_bits] if n]
    out["note"] = " | ".join(notes) if notes else "未達"
    if rs:
        out["rs"] = rs
    return out


def combine_wd(w1: dict, d1: dict) -> dict:
    """W + D 合併：入選 = W1 3/3 或 D1 3/3（任一中晒三樣）。"""
    w_sc = int(w1.get("score") or 0)
    d_sc = int(d1.get("score") or 0)
    w_pass = bool(w1.get("pass"))
    d_pass = bool(d1.get("pass"))
    on_list = w_pass or d_pass
    total = w_sc + d_sc

    if w_pass and d_pass:
        grade = "A+"
        pass_tf = "W1+D1"
    elif w_pass:
        grade = "A"
        pass_tf = "W1"
    elif d_pass:
        grade = "A"
        pass_tf = "D1"
    elif w_sc >= 2 or d_sc >= 2:
        grade = "B"
        pass_tf = ""
    else:
        grade = "C"
        pass_tf = ""

    parts = []
    if w1.get("note") and w1["note"] != "K 線不足":
        parts.append(f"W1 {w_sc}/3：{w1['note']}")
    if d1.get("note") and d1["note"] != "K 線不足":
        parts.append(f"D1 {d_sc}/3：{d1['note']}")

    return {
        "pass": on_list,
        "pass_tf": pass_tf,
        "score": total,
        "max_score": 6,
        "grade": grade,
        "w1_score": w_sc,
        "d1_score": d_sc,
        "w1_pass": w_pass,
        "d1_pass": d_pass,
        "note": " | ".join(parts) if parts else "W/D 未達 3/3",
    }


def score_symbol(
    symbol: str,
    *,
    as_of: date | None = None,
    filters: FirstScreenFilters | None = None,
) -> dict | None:
    sym = symbol.upper().strip()
    d1_bars = tv.yf_fetch_bars(sym, "1d", as_of=as_of, min_bars=MIN_BARS_D)
    w1_bars = tv.yf_fetch_bars(sym, "1wk", as_of=as_of, min_bars=MIN_BARS_W)
    if not d1_bars and not w1_bars:
        return None

    w1 = assess_tf_screen(
        w1_bars or [],
        tf_label="W1",
        volume_window=VOLUME_WINDOW_W,
        volume_ma_len=VOLUME_MA_W,
        vol_cfg=VOLUME_CFG_W,
        bull_cfg=BIG_BULL_CFG_W,
        min_bars=MIN_BARS_W,
    )
    d1 = assess_tf_screen(
        d1_bars or [],
        tf_label="D1",
        volume_window=VOLUME_WINDOW_D,
        volume_ma_len=VOLUME_MA_D,
        vol_cfg=VOLUME_CFG_D,
        bull_cfg=BIG_BULL_CFG_D,
        min_bars=MIN_BARS_D,
    )
    combo = combine_wd(w1, d1)
    rs = None
    if filters and filters.needs_rs():
        rs = tv.assess_relative_strength(sym, d1_bars)
    combo = apply_filters(combo, w1, d1, filters=filters, rs=rs)
    as_of_str = as_of.isoformat() if as_of else (
        d1_bars[-1]["date"] if d1_bars else w1_bars[-1]["date"]
    )

    return {
        "symbol": sym,
        "as_of_date": as_of_str,
        "price": d1.get("close") or w1.get("close"),
        "w1": w1,
        "d1": d1,
        **combo,
    }


def format_sub_items_table(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    icon = lambda ok: "✅" if ok else "❌"
    lines = [
        "| 子項 | 狀態 | 說明 |",
        "|------|:----:|------|",
    ]
    for row in rows:
        lines.append(f"| {row['label']} | {icon(row.get('pass'))} | {row.get('note', '—')} |")
    lines.append("")
    return lines


def _tf_section(tf: dict) -> list[str]:
    if not tf or tf.get("note") == "K 線不足":
        return [f"### {tf.get('tf', '?')} — 數據不足", ""]
    ma = tf.get("ma") or {}
    return [
        f"### {tf['tf']} — {tf['score']}/3 {'✅' if tf.get('pass') else '❌'}",
        "",
        f"- **收市**：${tf.get('close', '—')}",
        f"- **10/20/50/EMA20**：{ma.get('sma10', '—')} / {ma.get('sma20', '—')} / "
        f"{ma.get('sma50', '—')} / {ma.get('ema20', '—')}",
        "",
        *format_sub_items_table(tf.get("check_rows")),
    ]


def format_symbol_md(data: dict) -> str:
    sym = data["symbol"]
    lines = [
        f"# First Screen — {sym}（W1 + D1）",
        "",
    ]
    if data.get("as_of_date") and data.get("as_of_date") != date.today().isoformat():
        lines += [
            f"> **回測 as-of**：`{data['as_of_date']}` — 只用該日及之前 K 線；"
            "目標係 **入場日前一日** 已出信號。",
            "",
        ]
    lines += [
        f"**W {data.get('w1_score', 0)}/3 | D {data.get('d1_score', 0)}/3 | 合計 {data['score']}/{data['max_score']} | Grade {data['grade']}**",
        f" {'✅ 入選' if data['pass'] else '❌ 未入選'}"
        f"{('（' + data['pass_tf'] + '）') if data.get('pass_tf') else ''} — {data.get('note', '')}",
        "",
        f"- **D1 收市**：${data.get('price', '—')}",
        f"- **As-of**：{data.get('as_of_date', '—')}",
    ]
    fwd = data.get("forward") or {}
    if fwd:
        def _fpct(k: str) -> str:
            v = fwd.get(k)
            return f"{v:+.1f}%" if v is not None else "—"

        lines += [
            "",
            "### 事後升幅（驗證爆升）",
            "",
            f"- **+20 交易日**：{_fpct('fwd_20d')}",
            f"- **+40 交易日**：{_fpct('fwd_40d')}",
            f"- **+60 交易日**：{_fpct('fwd_60d')}",
            f"- **60 日內最高**：{_fpct('peak_60d')}",
        ]
    lines += [
        "",
        "> **初篩**：股價升嗰日成交量 > VolMA；股價跌嗰日成交量 < VolMA（逐根 K 對藍線）。",
        "> **入選** = W1 **或** D1 任一中晒 3/3。獨立於 9-edge。",
        "",
        *_tf_section(data.get("w1") or {}),
        *_tf_section(data.get("d1") or {}),
    ]
    return "\n".join(lines)


def _tf_icons(tf: dict) -> tuple[str, str, str]:
    rows = {c["key"]: c for c in (tf or {}).get("check_rows", [])}
    icon = lambda k: "✅" if rows.get(k, {}).get("pass") else "❌"
    if not tf or tf.get("note") == "K 線不足":
        return "—", "—", "—"
    return icon("ma_turn"), icon("down_vol"), icon("up_vol")


def format_batch_summary(
    results: list[dict],
    source_name: str,
    *,
    filters: FirstScreenFilters | None = None,
) -> str:
    today = date.today().isoformat()
    hits = [r for r in results if r.get("pass")]
    b_list = [r for r in results if r.get("grade") == "B"]

    lines = [
        f"# First Screen 初篩（W1 + D1）— {today}",
        "",
        f"**來源**：{source_name}（{len(results)} 隻成功分析）",
        "",
        f"> **入選條件**：{(filters.summary() if filters else 'W 或 D 任一中晒 3/3')}",
        "",
        "> W1（近 12 週）+ D1（近 20 日）量價初篩；勾選嘅 optional 條件必須一齊過。",
        "",
        "## 摘要",
        "",
        "| 類別 | 數量 |",
        "|------|------|",
        f"| **入選（W 或 D 3/3）** | {len(hits)} |",
        f"| **W1+D1 齊過（A+）** | {len([r for r in hits if r.get('pass_tf') == 'W1+D1'])} |",
        f"| **Grade B（接近，未入選）** | {len(b_list)} |",
        "",
    ]

    if hits:
        lines += [
            "## ✅ 入選名單",
            "",
            "| Symbol | Sector | Industry | 價 | 入選TF | W | D | 備註 |",
            "|--------|--------|----------|---:|:------:|:--:|:--:|------|",
        ]
        for r in sorted(hits, key=lambda x: (-(x.get("w1_pass") and x.get("d1_pass")), -x["score"], x["symbol"])):
            sec, ind = row_sector_industry(r)
            lines.append(
                f"| **{r['symbol']}** | {sec} | {ind} | "
                f"${r.get('price', '—')} | {r.get('pass_tf', '—')} | {r.get('w1_score')}/3 | "
                f"{r.get('d1_score')}/3 | {(r.get('note') or '')[:40]} |"
            )
        lines.append("")

        by_sector: dict[str, int] = {}
        for r in hits:
            sec, _ = row_sector_industry(r)
            by_sector[sec] = by_sector.get(sec, 0) + 1
        if by_sector:
            lines += [
                "### 入選 · Sector 分佈",
                "",
                "| Sector | 數量 |",
                "|--------|------|",
            ]
            for sec, n in sorted(by_sector.items(), key=lambda x: (-x[1], x[0])):
                lines.append(f"| {sec} | {n} |")
            lines.append("")

    lines += [
        "## 完整排名",
        "",
        "| Symbol | Sector | Industry | 入選 | TF | W | D | 合計 | Gr | 價 | W① | W② | W③ | D① | D② | D③ | 備註 |",
        "|--------|--------|----------|:----:|:--:|:--:|:--:|:----:|:--:|---:|:--:|:--:|:--:|:--:|:--:|:--:|------|",
    ]
    for r in sorted(results, key=lambda x: (-int(x.get("pass") or 0), -x["score"], x["symbol"])):
        w1 = r.get("w1") or {}
        d1 = r.get("d1") or {}
        wm, wd, wu = _tf_icons(w1)
        dm, dd, du = _tf_icons(d1)
        sec, ind = row_sector_industry(r)
        lines.append(
            f"| {r['symbol']} | {sec} | {ind} | {'✅' if r.get('pass') else '❌'} | {r.get('pass_tf') or '—'} | "
            f"{r.get('w1_score', 0)}/3 | {r.get('d1_score', 0)}/3 | "
            f"{r.get('score', 0)}/6 | {r.get('grade', '—')} | ${r.get('price', '—')} | "
            f"{wm} | {wd} | {wu} | {dm} | {dd} | {du} | {(r.get('note') or '—')[:28]} |"
        )
    lines.append("")
    return "\n".join(lines)


def enrich_fs_row(r: dict, meta: dict[str, dict], tv_cache: dict[str, str]) -> dict:
    sym = r["symbol"]
    m = meta.get(sym) or {}
    return {
        "symbol": sym,
        "tv_ticker": screener.resolve_tv_ticker(sym, tv_cache, m),
        "sector": m.get("sector") or "",
        "industry": m.get("industry") or "",
        "description": m.get("description") or "",
        "grade": r.get("grade") or "",
        "pass_tf": r.get("pass_tf") or "",
        "w1_score": r.get("w1_score", 0),
        "d1_score": r.get("d1_score", 0),
        "score": r.get("score", 0),
        "price": r.get("price") or "",
        "note": r.get("note") or "",
        "on_list": bool(r.get("pass")),
    }


def _repair_legacy_sector_industry_txt() -> None:
    """Strip old '### First_Screen — ' headers from prior exports."""
    if not REPORTS.is_dir():
        return
    for path in REPORTS.rglob("hits_by_sector_industry.txt"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        cleaned = text
        while "### First_Screen — " in cleaned:
            cleaned = cleaned.replace("### First_Screen — ", "### ")
        if cleaned != text:
            path.write_text(cleaned, encoding="utf-8")


def _clear_tv_subdir_exports(out_dir: Path, *folder_names: str) -> None:
    """Remove stale per-sector .txt from prior runs (same-day folder reuse)."""
    for name in folder_names:
        sub = out_dir / name
        if not sub.is_dir():
            continue
        for p in sub.glob("*.txt"):
            p.unlink(missing_ok=True)


def export_tv_watchlists(
    results: list[dict],
    meta: dict[str, dict],
    out_dir: Path,
) -> Path:
    """Export TV import files with sector / industry grouping."""
    today = (
        out_dir.name.removeprefix("FirstScreen_")
        if out_dir.name.startswith("FirstScreen_")
        else date.today().isoformat()
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    tv_cache: dict[str, str] = {}
    symbols = [r["symbol"] for r in results]
    screener.prefetch_tv_exchanges(symbols, meta, tv_cache)
    rows = [enrich_fs_row(r, meta, tv_cache) for r in results]
    hit_rows = [x for x in rows if x["on_list"]]
    w_only = [x for x in hit_rows if x["pass_tf"] == "W1"]
    d_only = [x for x in hit_rows if x["pass_tf"] == "D1"]
    both = [x for x in hit_rows if x["pass_tf"] == "W1+D1"]

    readme = out_dir / "README_TV_IMPORT.txt"
    readme.write_text(
        "First Screen — TradingView Watchlist Import\n"
        "==========================================\n\n"
        "格式：### Sector — Industry 標題 + 每行 EXCHANGE:SYMBOL\n"
        "所有 *_comma.txt / *_lines.txt 都含 Sector / Industry 分組。\n\n"
        "Files:\n"
        "  FirstScreen_YYYY-MM-DD_comma.txt — 入選全部（建議 import 呢個）\n"
        "  hits_comma.txt                   — 同上\n"
        "  hits_by_sector_industry.txt      — 同上（備份檔名）\n"
        "  hits_by_sector/*.txt             — 按 Sector 分檔\n"
        "  hits_by_sector_industry/*.txt    — 按 Sector+Industry 分檔\n"
        "  W1_only_comma.txt / D1_only_comma.txt — 子集\n"
        "  classified_full.csv              — 完整表（含 Sector / Industry）\n\n"
        "Import: TV Watchlist → ⋯ → Import list → 揀 FirstScreen_YYYY-MM-DD_comma.txt\n",
        encoding="utf-8",
    )

    screener._export_tv_group(hit_rows, out_dir, "hits")
    screener._export_tv_group(w_only, out_dir, "W1_only")
    screener._export_tv_group(d_only, out_dir, "D1_only")
    screener._export_tv_group(both, out_dir, "W1_D1_both")

    _clear_tv_subdir_exports(out_dir, "hits_by_sector", "hits_by_sector_industry")
    screener._export_rows_by_sector(hit_rows, out_dir, folder_name="hits_by_sector")
    dated_comma = out_dir / f"FirstScreen_{today}_comma.txt"
    shortcut = REPORTS / f"FirstScreen_{today}_hits_comma.txt"
    if hit_rows:
        screener.write_tv_import_txt(hit_rows, dated_comma, use_tv_ticker=True)
        screener.write_tv_import_txt(hit_rows, shortcut, use_tv_ticker=True)
        screener.write_tv_import_txt(
            hit_rows,
            out_dir / "hits_by_sector_industry.txt",
            use_tv_ticker=True,
        )
    screener._export_rows_by_sector(hit_rows, out_dir, folder_name="hits_by_sector")
    screener._export_rows_by_sector_industry(hit_rows, out_dir, folder_name="hits_by_sector_industry")

    fields = [
        "symbol", "tv_ticker", "sector", "industry", "description",
        "grade", "pass_tf", "on_list", "w1_score", "d1_score", "score", "price", "note",
    ]
    csv_path = out_dir / "classified_full.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for x in sorted(rows, key=lambda r: (-int(r["on_list"]), -int(r["score"]), r["symbol"])):
            w.writerow({k: x.get(k, "") for k in fields})

    by_sector: dict[str, list[dict]] = {}
    for x in hit_rows:
        sec = x["sector"] or "Unknown"
        by_sector.setdefault(sec, []).append(x)
    for sec, sec_rows in sorted(by_sector.items()):
        sec_csv = out_dir / "classified" / f"hits_{screener._safe_filename(sec)}.csv"
        sec_csv.parent.mkdir(exist_ok=True)
        with sec_csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for x in sorted(sec_rows, key=lambda r: -int(r["score"])):
                w.writerow({k: x.get(k, "") for k in fields})

    _repair_legacy_sector_industry_txt()
    return out_dir


def list_export_dirs() -> list[Path]:
    """Newest-first First Screen export folders."""
    _repair_legacy_sector_industry_txt()
    if not REPORTS.is_dir():
        return []
    return sorted(
        [
            p for p in REPORTS.iterdir()
            if p.is_dir() and (p.name.startswith("FirstScreen_") or p.name.startswith("tv_import_"))
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def tv_import_options_for_date(day: str | None = None) -> list[tuple[str, str]]:
    day = day or date.today().isoformat()
    return [
        (fname.replace("{date}", day), label.replace("{date}", day))
        for fname, label in FS_TV_IMPORT_OPTIONS
    ]


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class FirstScreenRunResult:
    ok: bool
    error: str = ""
    logs: list[str] = field(default_factory=list)
    source_csv: Path | None = None
    analyzed: int = 0
    total_symbols: int = 0
    skipped: int = 0
    hit_count: int = 0
    hit_symbols: list[str] = field(default_factory=list)
    hit_rows: list[dict] = field(default_factory=list)
    summary_path: Path | None = None
    json_path: Path | None = None
    export_dir: Path | None = None
    dated_hits_path: Path | None = None
    watchlist_name: str = ""
    summary_md: str = ""
    filters: FirstScreenFilters | None = None


def run_screen(
    csv_path: Path,
    *,
    limit: int = 0,
    delay: float = 0.12,
    progress_callback: ProgressCallback | None = None,
    filters: FirstScreenFilters | None = None,
) -> FirstScreenRunResult:
    logs: list[str] = [f"First Screen CSV: {csv_path}"]
    if not csv_path.is_file():
        return FirstScreenRunResult(ok=False, error=f"檔案不存在：{csv_path}", logs=logs)

    symbols = screener.read_screener_symbols(csv_path)
    meta = screener.read_screener_meta(csv_path)
    if limit > 0:
        symbols = symbols[:limit]
    total = len(symbols)
    logs.append(f"Symbols: {total} (W1+D1 each)")
    if filters:
        logs.append(f"Filters: {filters.summary()}")

    results: list[dict] = []
    skipped = 0
    for i, sym in enumerate(symbols, 1):
        if progress_callback:
            progress_callback(i, total, sym)
        try:
            row = score_symbol(sym, filters=filters)
            if row:
                attach_row_meta(row, meta)
                results.append(row)
            else:
                skipped += 1
                logs.append(f"  skip {sym}: no bars")
        except Exception as e:
            skipped += 1
            logs.append(f"  skip {sym}: {e}")
        if delay > 0:
            time.sleep(delay)

    out_dir = get_fs_reports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = csv_path.stem
    today = date.today().isoformat()
    summary_path = out_dir / f"FIRST_SCREEN_{stem}_{today}_summary.md"
    json_path = out_dir / f"FIRST_SCREEN_{stem}_{today}_results.json"

    summary_md = format_batch_summary(results, csv_path.name, filters=filters)
    summary_path.write_text(summary_md, encoding="utf-8")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    export_dir = out_dir / f"FirstScreen_{today}"
    export_tv_watchlists(results, meta, export_dir)
    dated_hits_path = out_dir / f"FirstScreen_{today}_hits_comma.txt"
    watchlist_name = f"FirstScreen_{today}"

    hits = [r["symbol"] for r in results if r.get("pass")]
    hit_rows = build_hit_rows(results)
    logs.append(f"TV export: {export_dir}")
    if dated_hits_path.is_file():
        logs.append(f"Dated list: {dated_hits_path.name}")
    logs.append(f"Done: {len(results)} analyzed, {len(hits)} on list, {skipped} skipped")
    logs.append(f"Summary: {summary_path}")

    return FirstScreenRunResult(
        ok=True,
        logs=logs,
        source_csv=csv_path,
        analyzed=len(results),
        total_symbols=total,
        skipped=skipped,
        hit_count=len(hits),
        hit_symbols=hits,
        hit_rows=hit_rows,
        summary_path=summary_path,
        json_path=json_path,
        export_dir=export_dir,
        dated_hits_path=dated_hits_path if dated_hits_path.is_file() else None,
        watchlist_name=watchlist_name,
        summary_md=summary_md,
        filters=filters,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="First Screen — W1+D1 MA 轉上 + 量價初篩")
    parser.add_argument("--symbol", "-s", help="單一 symbol")
    parser.add_argument("--csv", "-c", type=Path, help="TradingView screener CSV")
    parser.add_argument("--limit", "-n", type=int, default=0, help="試跑上限（0=全部）")
    parser.add_argument("--out", "-o", type=Path, help="單股報告輸出路徑")
    args = parser.parse_args()

    if args.symbol:
        data = score_symbol(args.symbol)
        if not data:
            raise SystemExit(f"無法取得 {args.symbol.upper()} W1/D1 數據")
        md = format_symbol_md(data)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(md, encoding="utf-8")
            print(f"Wrote {args.out}")
        else:
            sys.stdout.buffer.write((md + "\n").encode("utf-8"))
        return

    if args.csv:
        result = run_screen(args.csv, limit=args.limit)
        if not result.ok:
            raise SystemExit(result.error)
        sys.stdout.buffer.write((result.summary_md + "\n").encode("utf-8"))
        return

    raise SystemExit("Use --symbol STX or --csv screener.csv")


if __name__ == "__main__":
    main()
