#!/usr/bin/env python3
"""Screen a TradingView screener export with 9-edge analysis (yfinance OHLCV)."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import analyze_tv_csv as eng  # noqa: E402

REPORTS = ROOT / "reports" / "batch"
TV_EXPORT = REPORTS / "tv_import"

# Official TV import: *_comma.txt only (see README_TV_IMPORT.txt)
TV_WATCHLIST_IMPORT_OPTIONS: list[tuple[str, str]] = [
    ("AB_grade_comma.txt", "AB 短名單（雙 Setup ≥3R）"),
    ("A_grade_comma.txt", "A 級全部"),
    ("B_grade_comma.txt", "B 級 Watch"),
    ("Potential_Top20_comma.txt", "潛力榜 Top 20"),
    ("Score7plus_comma.txt", f"現況 ≥7/{eng.EDGE_SCORE_MAX}"),
]

TV_EXCHANGE_MAP = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NAS": "NASDAQ",
    "NYQ": "NYSE",
    "NYS": "NYSE",
    "ASE": "AMEX",
    "PCX": "NYSEARCA",
    "BTS": "NYSE",
}


def read_screener_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_screener_symbols(path: Path) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in read_screener_rows(path):
        sym = (row.get("Symbol") or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def meta_from_results(results: list[dict]) -> dict[str, dict]:
    """Build sector/industry meta from cached JSON _meta fields."""
    meta: dict[str, dict] = {}
    for r in results:
        sym = (r.get("symbol") or "").strip().upper()
        m = r.get("_meta") or {}
        if sym and m:
            meta[sym] = {
                "description": m.get("description") or "",
                "sector": m.get("sector") or "",
                "industry": m.get("industry") or "",
                "price": m.get("price") or "",
            }
    return meta


def read_screener_meta(path: Path) -> dict[str, dict]:
    """Symbol -> {description, sector, industry, price}."""
    meta: dict[str, dict] = {}
    for row in read_screener_rows(path):
        sym = (row.get("Symbol") or "").strip().upper()
        if not sym:
            continue
        meta[sym] = {
            "description": (row.get("Description") or "").strip(),
            "sector": (row.get("Sector") or "").strip(),
            "industry": (row.get("Industry") or "").strip(),
            "price": (row.get("Price") or "").strip(),
        }
    return meta


def _safe_filename(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE)
    return re.sub(r"\s+", "_", s.strip())[:60] or "unknown"


def resolve_tv_ticker(symbol: str, cache: dict[str, str]) -> str:
    if symbol in cache:
        return cache[symbol]
    import yfinance as yf

    prefix = "NASDAQ"
    try:
        info = yf.Ticker(symbol).info or {}
        ex = (info.get("exchange") or "").upper()
        prefix = TV_EXCHANGE_MAP.get(ex, prefix)
    except Exception:
        pass
    tv = f"{prefix}:{symbol}"
    cache[symbol] = tv
    return tv


def list_tv_export_dirs() -> list[Path]:
    """Newest-first tv_import batch folders."""
    if not TV_EXPORT.is_dir():
        return []
    return sorted(
        [p for p in TV_EXPORT.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def parse_comma_watchlist(path: Path) -> list[str]:
    """Parse EXCHANGE:SYMBOL comma list from official TV import .txt."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [t.strip() for t in text.split(",") if t.strip()]


def watchlist_import_path(export_dir: Path, filename: str) -> Path | None:
    p = export_dir / filename
    return p if p.is_file() else None


def write_tv_txt(tickers: list[str], path: Path, *, comma: bool = True) -> None:
    """TradingView official import: .txt, EXCHANGE:SYMBOL, comma-separated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ",".join(tickers) if comma else "\n".join(tickers)
    path.write_text(body + "\n", encoding="utf-8")


def write_tv_sector_industry_txt(
    rows: list[dict],
    path: Path,
    *,
    group_label: str = "A_Grade",
    use_tv_ticker: bool = False,
) -> None:
    """Human-readable watchlist: ### Group — Sector — Industry, then one symbol per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[dict]] = {}
    for x in rows:
        sec = x.get("sector") or "Unknown"
        ind = x.get("industry") or "Unknown"
        groups.setdefault((sec, ind), []).append(x)

    lines: list[str] = []
    for (sec, ind), grp in sorted(groups.items()):
        lines.append(f"### {group_label} — {sec} — {ind}")
        lines.append("")
        for x in sorted(grp, key=lambda r: (-int(r.get("score") or 0), r["symbol"])):
            sym = x["tv_ticker"] if use_tv_ticker else x["symbol"]
            lines.append(sym)
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def enrich_result_row(r: dict, meta: dict[str, dict], tv_cache: dict[str, str]) -> dict:
    sym = r["symbol"]
    m = meta.get(sym) or {}
    sc = r.get("scenarios") or {}
    best = sc.get("best_long") or {}
    setup_ev = r.get("setup_eval") or eng.evaluate_screener_setups(r.get("setups"))
    setups = r.get("setups") or {}
    bo = setups.get("breakout") or {}
    rt = setups.get("retest") or {}
    grade = r.get("grade") or ""
    if "shortlist" in r:
        shortlist = bool(r["shortlist"])
    else:
        shortlist = grade in ("A", "B") and setup_ev.get("passes", False)
    return {
        "symbol": sym,
        "tv_ticker": resolve_tv_ticker(sym, tv_cache),
        "sector": m.get("sector") or "",
        "industry": m.get("industry") or "",
        "description": m.get("description") or "",
        "grade": grade,
        "decision": r.get("decision") or "",
        "score": r.get("total_score") or 0,
        "long": sc.get("current_long", "—"),
        "short": sc.get("current_short", "—"),
        "pot_long": best.get("long_count") or "",
        "pot_price": best.get("price") or "",
        "bias": sc.get("bias") or "",
        "shortlist": shortlist,
        "breakout_rr": setup_ev.get("breakout_rr", 0),
        "retest_rr": setup_ev.get("retest_rr", 0),
        "best_rr": setup_ev.get("best_rr", 0),
        "breakout_entry": bo.get("entry") or "",
        "retest_entry": rt.get("entry") or "",
        "both_setups_valid": setup_ev.get("both_valid", False),
    }


def export_tv_watchlists(
    results: list[dict],
    meta: dict[str, dict],
    out_dir: Path,
    *,
    pot_top_n: int = 20,
) -> Path:
    """Export TV .txt watchlists + classified CSV with sector/industry."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tv_cache: dict[str, str] = {}
    rows = [enrich_result_row(r, meta, tv_cache) for r in results]

    a_rows = [x for x in rows if x["grade"] == "A"]
    b_rows = [x for x in rows if x["grade"] == "B"]
    ab_rows = [x for x in rows if x.get("shortlist")]
    pot_rows = sorted(
        [x for x in rows if x["pot_long"]],
        key=lambda x: (-int(x["pot_long"]), -int(x["score"])),
    )[:pot_top_n]
    g7_rows = [x for x in rows if int(x["score"]) >= 7]

    readme = out_dir / "README_TV_IMPORT.txt"
    readme.write_text(
        "TradingView Watchlist Import\n"
        "============================\n\n"
        "Format: .TXT (唔係 CSV / MLB)\n"
        "Official: EXCHANGE:SYMBOL, comma-separated\n\n"
        "Import steps:\n"
        "1. Open TradingView → Watchlist panel (right side)\n"
        "2. Click watchlist name → menu (⋯) → Upload list / Import list\n"
        "3. Select the .txt file (Pro plan required)\n\n"
        "Files:\n"
        "  A_grade_comma.txt       — A 級全部（comma，官方 import 格式）\n"
        "  A_grade_lines.txt       — A 級（每行一隻 EXCHANGE:SYMBOL）\n"
        "  A_grade_by_sector_industry.txt — A 級按 Sector + Industry 分組（參考 TV_Watchlist 格式）\n"
        "  B_grade_comma.txt       — B 級 Watch\n"
        "  B_grade_by_sector_industry.txt — B 級按 Sector + Industry 分組\n"
        "  AB_grade_comma.txt      — A/B 級 + 雙 Setup valid + 至少一個 ≥3R\n"
        "  AB_grade_by_sector_industry.txt — 同上，按 Sector + Industry 分組\n"
        "  Potential_Top20_comma.txt — 潛力榜 Top 20\n"
        "  Score7plus_comma.txt    — 現況 ≥7\n"
        "  A_by_sector/*.txt        — A 級只按 Sector 分組（comma）\n"
        "  classified_full.csv      — 完整表（含 Sector / Industry）\n\n"
        "Note: TV 官方 import 用 *_comma.txt（唔支援 sector header）。\n"
        "      要 sector + industry 分組睇 A_grade_by_sector_industry.txt。\n",
        encoding="utf-8",
    )

    def _export_group(group_rows: list[dict], stem: str) -> None:
        tickers = [x["tv_ticker"] for x in group_rows]
        if not tickers:
            return
        write_tv_txt(tickers, out_dir / f"{stem}_comma.txt", comma=True)
        write_tv_txt(tickers, out_dir / f"{stem}_lines.txt", comma=False)

    _export_group(a_rows, "A_grade")
    _export_group(b_rows, "B_grade")
    _export_group(ab_rows, "AB_grade")
    _export_group(pot_rows, "Potential_Top20")
    _export_group(g7_rows, "Score7plus")

    if a_rows:
        write_tv_sector_industry_txt(
            a_rows, out_dir / "A_grade_by_sector_industry.txt", group_label="A_Grade"
        )
    if b_rows:
        write_tv_sector_industry_txt(
            b_rows, out_dir / "B_grade_by_sector_industry.txt", group_label="B_Grade"
        )
    if ab_rows:
        write_tv_sector_industry_txt(
            ab_rows, out_dir / "AB_grade_by_sector_industry.txt", group_label="AB_Grade"
        )

    sector_dir = out_dir / "A_by_sector"
    sector_dir.mkdir(exist_ok=True)
    by_sector: dict[str, list[dict]] = {}
    for x in a_rows:
        sec = x["sector"] or "Unknown"
        by_sector.setdefault(sec, []).append(x)
    for sec, sec_rows in sorted(by_sector.items()):
        tickers = [x["tv_ticker"] for x in sec_rows]
        fname = _safe_filename(sec)
        write_tv_txt(tickers, sector_dir / f"{fname}_comma.txt", comma=True)
        write_tv_txt(tickers, sector_dir / f"{fname}_lines.txt", comma=False)

    csv_path = out_dir / "classified_full.csv"
    fields = [
        "symbol", "tv_ticker", "sector", "industry", "description",
        "grade", "decision", "score", "long", "short", "pot_long", "pot_price", "bias",
        "shortlist", "breakout_entry", "retest_entry", "breakout_rr", "retest_rr", "best_rr",
        "both_setups_valid",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for x in sorted(rows, key=lambda r: (-int(r["score"]), r["symbol"])):
            w.writerow({k: x.get(k, "") for k in fields})

    for sec, sec_rows in sorted(by_sector.items()):
        sec_csv = out_dir / "classified" / f"A_{_safe_filename(sec)}.csv"
        sec_csv.parent.mkdir(exist_ok=True)
        with sec_csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for x in sorted(sec_rows, key=lambda r: -int(r["score"])):
                w.writerow({k: x.get(k, "") for k in fields})

    return out_dir


def yf_to_bars(symbol: str, interval: str, period: str, min_bars: int) -> list[dict] | None:
    import yfinance as yf

    try:
        h = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    except Exception:
        return None
    if h is None or h.empty or len(h) < min_bars:
        return None
    bars: list[dict] = []
    for _, row in h.iterrows():
        o = float(row["Open"])
        hi = float(row["High"])
        lo = float(row["Low"])
        c = float(row["Close"])
        v = float(row["Volume"] or 0)
        if c <= 0:
            continue
        bars.append({"open": o, "high": hi, "low": lo, "close": c, "volume": v})
    return bars if len(bars) >= min_bars else None


def score_from_yf(symbol: str, market_edge: dict) -> dict | None:
    sym = symbol.upper()
    d1_bars = yf_to_bars(sym, "1d", "2y", eng.TF_MIN_BARS["D1"])
    if not d1_bars:
        return None
    w1_bars = yf_to_bars(sym, "1wk", "5y", eng.TF_MIN_BARS["W1"])
    h1_bars = yf_to_bars(sym, "60m", "60d", eng.TF_MIN_BARS["H1"])
    return eng.score_from_bars(
        sym,
        d1_bars,
        w1_bars=w1_bars,
        h1_bars=h1_bars,
        market_edge=market_edge,
        source="yfinance",
    )


def _format_ab_shortlist_section(ab_list: list[dict]) -> list[str]:
    if not ab_list:
        return [
            "## A/B 級 — 雙 Setup 短名單",
            "",
            f"*（0 隻符合：Grade A/B + Breakout & Retest 都 valid + 至少一個 ≥{eng.SCREENER_MIN_BEST_RR:.0f}R）*",
            "",
        ]

    lines = [
        "## A/B 級 — 雙 Setup 短名單",
        "",
        f"**條件**：Grade A 或 B · Breakout + Retest 兩個 setup 都 valid · 至少一個 ≥{eng.SCREENER_MIN_BEST_RR:.0f}R",
        "",
        f"**共 {len(ab_list)} 隻**",
        "",
    ]

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in ab_list:
        m = r.get("_meta") or {}
        sec = m.get("sector") or "Unknown"
        ind = m.get("industry") or "Unknown"
        groups.setdefault((sec, ind), []).append(r)

    for (sec, ind), grp in sorted(groups.items()):
        lines.append(f"### {sec} — {ind}")
        lines.append("")
        lines.append(
            "| Symbol | Grade | 現況 | Breakout RR | Retest RR | Best | Setup |"
        )
        lines.append(
            "|--------|:-----:|:----:|:-----------:|:---------:|:----:|-------|"
        )
        for r in sorted(grp, key=lambda x: (-float(x.get("_setup_eval", {}).get("best_rr", 0)), -x["total_score"])):
            setups = r.get("setups") or {}
            bo = setups.get("breakout") or {}
            rt = setups.get("retest") or {}
            ev = r.get("_setup_eval") or eng.evaluate_screener_setups(setups)
            setup_txt = (
                f"B ${bo.get('entry', '—')} ({ev.get('breakout_rr', '—')}R) / "
                f"R ${rt.get('entry', '—')} ({ev.get('retest_rr', '—')}R)"
            )
            lines.append(
                f"| **{r['symbol']}** | {r['grade']} | {eng.edge_score_fmt(r['total_score'])} | "
                f"{ev.get('breakout_rr', '—')} | {ev.get('retest_rr', '—')} | "
                f"**{ev.get('best_rr', '—')}R** | {setup_txt} |"
            )
        lines.append("")

    lines.append(
        f"TV import：`reports/batch/tv_import/.../AB_grade_by_sector_industry.txt` 或 `AB_grade_comma.txt`",
    )
    lines.append("")
    return lines


def format_screener_summary(results: list[dict], source_name: str) -> str:
    today = date.today().isoformat()
    lines = [
        f"# Screener 9-Edge Filter — {today}",
        "",
        f"**來源**：{source_name}（{len(results)} 隻成功分析）",
        "",
    ]
    m = results[0].get("market_edge_detail") or {} if results else {}
    if m.get("directive"):
        lines += [
            f"**大盤 SPY**：{m['directive']} "
            f"(Long Edge {m.get('long_count', 0)}/3 | Short Edge {m.get('short_count', 0)}/3)",
            "",
        ]

    a_list = [r for r in results if r["grade"] == "A" and r["decision"] == "trade"]
    b_list = [r for r in results if r["grade"] == "B"]
    ab_list = [r for r in results if eng.passes_screener_shortlist(r)]
    for r in ab_list:
        r["_setup_eval"] = eng.evaluate_screener_setups(r.get("setups"))
    watch7 = [r for r in results if r["total_score"] >= 7 and r["grade"] != "A"]

    def _best_long_count(r: dict) -> int:
        best = (r.get("scenarios") or {}).get("best_long") or {}
        return int(best.get("long_count") or 0)

    pot = sorted(
        [r for r in results if _best_long_count(r) > 0],
        key=lambda x: (-_best_long_count(x), -x["total_score"]),
    )

    lines += [
        "## 摘要",
        "",
        f"| 類別 | 數量 |",
        f"|------|------|",
        f"| **A 級（可交易）** | {len(a_list)} |",
        f"| **B 級（Watch）** | {len(b_list)} |",
        f"| **A/B 雙 Setup ≥{eng.SCREENER_MIN_BEST_RR:.0f}R** | {len(ab_list)} |",
        f"| **現況 ≥7/{eng.EDGE_SCORE_MAX}** | {len([r for r in results if r['total_score'] >= 7])} |",
        f"| **Long 偏向** | {len([r for r in results if r.get('directional_bias') == 'long'])} |",
        "",
    ]

    lines += _format_ab_shortlist_section(ab_list)

    if a_list:
        lines += ["## A 級 — 可交易", ""]
        lines += [
            "| Symbol | Sector | Industry | 現況 | Long | Short | Setup | 備註 |",
            "|--------|--------|----------|:----:|:----:|:-----:|-------|------|",
        ]
        for r in sorted(a_list, key=lambda x: -x["total_score"]):
            sym = r["symbol"]
            m = (r.get("_meta") or {})
            setup = r.get("setups") or {}
            sa = setup.get("breakout") or setup.get("current") or {}
            setup_txt = f"A @ ${sa.get('entry', '—')}" if sa.get("entry") else "—"
            note = eng.one_line_conclusion(r).split("—", 1)[-1].strip()[:60]
            sc = r.get("scenarios") or {}
            lines.append(
                f"| **{sym}** | {m.get('sector', '—')} | {m.get('industry', '—')} | "
                f"{eng.edge_score_fmt(r['total_score'])} | "
                f"{eng.edge_score_fmt(sc.get('current_long', 0))} | {eng.edge_score_fmt(sc.get('current_short', 0))} | "
                f"{setup_txt} | {note} |"
            )
        lines.append("")

    if b_list:
        lines += ["## B 級 — Watch", ""]
        lines += ["| Symbol | 現況 | Long | Short | 潛力價 | 潛力Long |", "|--------|:----:|:----:|:-----:|--------|:--------:|"]
        for r in sorted(b_list, key=lambda x: -x["total_score"])[:30]:
            sc = r.get("scenarios") or {}
            best = sc.get("best_long") or {}
            pot_p = f"${best['price']}" if best.get("price") else "—"
            pot_l = eng.edge_score_fmt(best['long_count']) if best.get("long_count") else "—"
            lines.append(
                f"| {r['symbol']} | {eng.edge_score_fmt(r['total_score'])} | "
                f"{eng.edge_score_fmt(sc.get('current_long', 0))} | {eng.edge_score_fmt(sc.get('current_short', 0))} | "
                f"{pot_p} | {pot_l} |"
            )
        if len(b_list) > 30:
            lines.append(f"\n*（其餘 {len(b_list) - 30} 隻 B 級見完整表）*")
        lines.append("")

    lines += ["## 潛力榜 Top 20（最佳 Long 情景）", ""]
    lines += ["| # | Symbol | 現況 | 現Long | 潛力價 | 潛力Long | Grade |", "|:---:|--------|:----:|:------:|--------|:--------:|:-----:|"]
    for i, r in enumerate(pot[:20], 1):
        b = r["scenarios"]["best_long"]
        sc = r.get("scenarios") or {}
        lines.append(
            f"| {i} | **{r['symbol']}** | {eng.edge_score_fmt(r['total_score'])} | "
            f"{eng.edge_score_fmt(sc.get('current_long', 0))} | ${b['price']} | {eng.edge_score_fmt(b['long_count'])} | {r['grade']} |"
        )
    lines.append("")

    lines += [
        "## 完整排名",
        "",
        "| Symbol | 現況 | Long | Short | 潛力Long | Grade | 偏向 |",
        "|--------|:----:|:----:|:-----:|:--------:|:-----:|------|",
    ]
    for r in sorted(
        results,
        key=lambda x: (
            -_best_long_count(x) if _best_long_count(x) else -x["total_score"],
            -x["total_score"],
            x["symbol"],
        ),
    ):
        sc = r.get("scenarios") or {}
        best = sc.get("best_long") or {}
        pot_l = eng.edge_score_fmt(best['long_count']) if best.get("long_count") else "—"
        lines.append(
            f"| {r['symbol']} | {eng.edge_score_fmt(r['total_score'])} | "
            f"{eng.edge_score_fmt(sc.get('current_long', 0))} | {eng.edge_score_fmt(sc.get('current_short', 0))} | "
            f"{pot_l} | {r['grade']} | {sc.get('bias', '—')} |"
        )
    return "\n".join(lines)


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class ScreenerRunResult:
    ok: bool
    error: str = ""
    logs: list[str] = field(default_factory=list)
    source_csv: Path | None = None
    analyzed: int = 0
    total_symbols: int = 0
    skipped: int = 0
    a_count: int = 0
    b_count: int = 0
    ab_count: int = 0
    a_symbols: list[str] = field(default_factory=list)
    b_symbols: list[str] = field(default_factory=list)
    ab_symbols: list[str] = field(default_factory=list)
    summary_path: Path | None = None
    json_path: Path | None = None
    export_dir: Path | None = None
    summary_md: str = ""


def _slim_results(results: list[dict]) -> list[dict]:
    slim: list[dict] = []
    for r in results:
        sc = r.get("scenarios") or {}
        setups = r.get("setups") or {}
        bo = setups.get("breakout") or {}
        rt = setups.get("retest") or {}
        setup_ev = eng.evaluate_screener_setups(setups)
        slim.append({
            "symbol": r["symbol"],
            "total_score": r["total_score"],
            "grade": r["grade"],
            "decision": r["decision"],
            "directional_bias": r.get("directional_bias"),
            "scenarios": {
                "current_long": sc.get("current_long"),
                "current_short": sc.get("current_short"),
                "bias": sc.get("bias"),
                "best_long": sc.get("best_long"),
            },
            "setups": {
                "breakout": {
                    "entry": bo.get("entry"),
                    "rr": bo.get("rr"),
                    "valid": bo.get("valid"),
                },
                "retest": {
                    "entry": rt.get("entry"),
                    "rr": rt.get("rr"),
                    "valid": rt.get("valid"),
                },
            },
            "setup_eval": setup_ev,
            "shortlist": eng.passes_screener_shortlist(r),
            "_meta": r.get("_meta") or {},
        })
    return slim


def _grade_counts(results: list[dict]) -> tuple[list[str], list[str], list[str]]:
    a = [r["symbol"] for r in results if r["grade"] == "A"]
    b = [r["symbol"] for r in results if r["grade"] == "B"]
    ab = [r["symbol"] for r in results if eng.passes_screener_shortlist(r)]
    return a, b, ab


def export_screener_from_cache(csv_path: Path) -> ScreenerRunResult:
    """Re-export TV watchlists from saved JSON (CLI --export-only)."""
    logs: list[str] = []
    if not csv_path.exists():
        return ScreenerRunResult(ok=False, error=f"檔案不存在：{csv_path}", logs=logs)

    stem = csv_path.stem
    today = date.today().isoformat()
    json_path = REPORTS / f"SCREENER_{stem}_{today}_results.json"
    if not json_path.exists():
        return ScreenerRunResult(
            ok=False,
            error=f"搵唔到快取 JSON：{json_path.name}",
            logs=logs,
            source_csv=csv_path,
        )

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    results = [dict(r) for r in raw]
    meta = meta_from_results(results)
    meta = {**meta, **read_screener_meta(csv_path)}
    export_dir = TV_EXPORT / f"{stem}_{today}"
    export_tv_watchlists(results, meta, export_dir)
    a, b, ab = _grade_counts(results)
    logs.append(f"Re-export TV watchlists -> {export_dir}")
    return ScreenerRunResult(
        ok=True,
        logs=logs,
        source_csv=csv_path,
        analyzed=len(results),
        total_symbols=len(results),
        a_count=len(a),
        b_count=len(b),
        ab_count=len(ab),
        a_symbols=a,
        b_symbols=b,
        ab_symbols=ab,
        json_path=json_path,
        export_dir=export_dir,
    )


def run_screener(
    csv_path: Path,
    *,
    limit: int = 0,
    delay: float = 0.15,
    progress_callback: ProgressCallback | None = None,
) -> ScreenerRunResult:
    """Screen a TradingView screener CSV with yfinance OHLCV (UI / library entry)."""
    logs: list[str] = [f"Screener CSV: {csv_path}"]
    if not csv_path.exists():
        msg = f"檔案不存在：{csv_path}"
        logs.append(msg)
        return ScreenerRunResult(ok=False, error=msg, logs=logs, source_csv=csv_path)

    stem = csv_path.stem
    today = date.today().isoformat()
    json_path = REPORTS / f"SCREENER_{stem}_{today}_results.json"
    meta = read_screener_meta(csv_path)
    symbols = read_screener_symbols(csv_path)
    if limit:
        symbols = symbols[:limit]

    total = len(symbols)
    if not total:
        msg = "CSV 內冇有效 Symbol"
        logs.append(msg)
        return ScreenerRunResult(ok=False, error=msg, logs=logs, source_csv=csv_path)

    REPORTS.mkdir(parents=True, exist_ok=True)
    market_edge = eng.assess_broad_market_edge()
    eng._MARKET_EDGE_CACHE = market_edge

    results: list[dict] = []
    skipped: list[str] = []
    for i, sym in enumerate(symbols, 1):
        line = f"[{i}/{total}] {sym}"
        if progress_callback:
            progress_callback(i, total, sym)
        try:
            data = score_from_yf(sym, market_edge)
            if data is None:
                skipped.append(sym)
                logs.append(f"{line} SKIP (no data)")
                continue
            data["_meta"] = meta.get(sym) or {}
            results.append(data)
            logs.append(
                f"{line} OK {eng.edge_score_fmt(data['total_score'])} Grade {data['grade']}"
            )
        except Exception as e:
            skipped.append(sym)
            logs.append(f"{line} ERR: {e}")
        if delay and i < total:
            time.sleep(delay)

    json_path.write_text(
        json.dumps(_slim_results(results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_path = REPORTS / f"SCREENER_{stem}_{today}_summary.md"
    summary_md = format_screener_summary(results, csv_path.name)
    summary_path.write_text(summary_md, encoding="utf-8")

    export_dir = TV_EXPORT / f"{stem}_{today}"
    export_tv_watchlists(results, meta, export_dir)

    a, b, ab = _grade_counts(results)
    logs.append(f"Analyzed {len(results)} / {total} | Skipped {len(skipped)}")
    logs.append(f"Summary -> {summary_path}")
    logs.append(f"TV import -> {export_dir}")
    logs.append(f"JSON -> {json_path}")

    return ScreenerRunResult(
        ok=True,
        logs=logs,
        source_csv=csv_path,
        analyzed=len(results),
        total_symbols=total,
        skipped=len(skipped),
        a_count=len(a),
        b_count=len(b),
        ab_count=len(ab),
        a_symbols=a,
        b_symbols=b,
        ab_symbols=ab,
        summary_path=summary_path,
        json_path=json_path,
        export_dir=export_dir,
        summary_md=summary_md,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path, help="TradingView screener export CSV")
    parser.add_argument("--limit", type=int, default=0, help="Max symbols (0=all)")
    parser.add_argument("--delay", type=float, default=0.15, help="Seconds between symbols")
    parser.add_argument("--export-only", action="store_true", help="Re-export TV files from saved JSON")
    args = parser.parse_args()

    if args.export_only:
        result = export_screener_from_cache(args.csv)
        if not result.ok:
            raise SystemExit(result.error)
        for line in result.logs:
            print(line)
        return

    result = run_screener(
        args.csv,
        limit=args.limit,
        delay=args.delay,
        progress_callback=lambda i, n, sym: print(f"[{i}/{n}] {sym}"),
    )
    if not result.ok:
        raise SystemExit(result.error)
    for line in result.logs:
        print(line)
    a, b, ab = result.a_symbols, result.b_symbols, result.ab_symbols
    print(f"A ({len(a)}): {', '.join(a[:12])}{'...' if len(a) > 12 else ''}")
    print(f"B ({len(b)}): {', '.join(b[:12])}{'...' if len(b) > 12 else ''}")
    print(f"AB shortlist ({len(ab)}): {', '.join(ab[:12])}{'...' if len(ab) > 12 else ''}")


if __name__ == "__main__":
    main()
