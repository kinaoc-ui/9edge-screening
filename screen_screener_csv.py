#!/usr/bin/env python3
"""Screen a TradingView screener export with 9-edge analysis (yfinance OHLCV)."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import analyze_tv_csv as eng  # noqa: E402

REPORTS = ROOT / "reports" / "batch"
TV_EXPORT = REPORTS / "tv_import"

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
    return {
        "symbol": sym,
        "tv_ticker": resolve_tv_ticker(sym, tv_cache),
        "sector": m.get("sector") or "",
        "industry": m.get("industry") or "",
        "description": m.get("description") or "",
        "grade": r.get("grade") or "",
        "decision": r.get("decision") or "",
        "score": r.get("total_score") or 0,
        "long": sc.get("current_long", "—"),
        "short": sc.get("current_short", "—"),
        "pot_long": best.get("long_count") or "",
        "pot_price": best.get("price") or "",
        "bias": sc.get("bias") or "",
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
        "  Potential_Top20_comma.txt — 潛力榜 Top 20\n"
        "  Score7plus_comma.txt    — 現況 ≥7/9\n"
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

    d1 = eng.analyze_bars(d1_bars)
    w1 = eng.analyze_bars(w1_bars) if w1_bars else None
    h1 = eng.analyze_bars(h1_bars) if h1_bars else None
    d1 = eng.merge_swing_sr(d1, d1_bars, w1=w1, w1_bars=w1_bars, h1=h1, h1_bars=h1_bars)

    mi_source_tf = "W1" if w1 else "D1"
    mi_ref = w1 or d1
    mi_canonical = mi_ref.get("mi_detail") or {
        "long_pass": bool(mi_ref.get("mi_pass")),
        "short_pass": bool(mi_ref.get("mi_short_pass")),
        "long_note": "MACD breakout 未確認",
        "short_note": "MACD breakout 未確認",
    }
    d1 = eng.apply_mi_override(d1, mi_canonical, mi_source_tf)
    if w1:
        w1 = eng.apply_mi_override(w1, mi_canonical, mi_source_tf)
    if h1:
        h1 = eng.apply_mi_override(h1, mi_canonical, mi_source_tf)

    mtf_pass, mtf_short, mtf_note = eng.analyze_mtf_cross(w1, d1, h1)
    rs = eng.assess_relative_strength(sym, d1_bars)
    board_long = market_edge["long_pass"]
    board_short = market_edge["short_pass"]
    board_long_note = market_edge["long_note"]
    board_short_note = market_edge["short_note"]
    sector_footnote = eng.fetch_sector_footnote(sym)
    plan = eng.build_rr_plan(d1, d1_bars)

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
        ("D1", d1, d1_bars),
        ("H1", h1, h1_bars),
    ):
        if analysis is None or tf_bars is None:
            continue
        tf_blocks[tf_label] = eng.build_per_tf_block(tf_label, analysis, tf_bars, **cross)

    d1_block = tf_blocks.get("D1") or eng.build_per_tf_block("D1", d1, d1_bars, **cross)
    edges = eng.edges_dict_from_block(d1_block, "long")
    edges["csp_pa_vol"]["short_score"] = d1_block["short_edges"]["csp_pa_vol"]
    edges["csp_pa_vol"]["short_note"] = d1_block["short_notes"]["csp_pa_vol"]

    total, grade, decision = eng.grade_from_edges(edges)
    base_long = d1_block["long_edges"]
    base_short = d1_block["short_edges"]
    long_edge_notes = d1_block["long_notes"]
    short_edge_notes = d1_block["short_notes"]

    for k in eng.EDGES:
        if k != "csp_pa_vol":
            edges[k]["short_score"] = base_short[k]
            edges[k]["short_note"] = short_edge_notes[k]

    scenarios = eng.build_edge_scenarios(
        d1_bars, d1, rs["long_pass"], rs["short_pass"], board_long, board_short,
        mtf_pass, mtf_short, base_long, base_short,
        rs["long_note"], rs["short_note"], board_long_note, board_short_note, mtf_note,
        mi_canonical, w1=w1, h1=h1,
    )

    present = [t for t in eng.TF_ORDER if t in tf_blocks]
    tf_label = "+".join(present) if present else "D1"

    data = {
        "symbol": sym,
        "timeframe": tf_label,
        "price": d1["close"],
        "volume": str(d1["volume"]),
        "volume_avg": str(d1["avg_volume_20"]),
        "metrics": d1_block["metrics"],
        "timeframes": tf_blocks,
        "rs_detail": rs,
        "market_edge_detail": market_edge,
        "sector_footnote": sector_footnote,
        "edges": edges,
        "short_edges": base_short,
        "short_edge_notes": short_edge_notes,
        "long_edge_notes": long_edge_notes,
        "total_score": total,
        "grade": grade,
        "decision": decision,
        "entry_plan": plan,
        "scenarios": scenarios,
        "directional_bias": scenarios["bias"],
        "long_edge_count": scenarios["current_long"],
        "short_edge_count": scenarios["current_short"],
        "source": "yfinance",
    }
    data["summary_zh"] = eng.build_summary_text(data)
    return data


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
        f"| **現況 ≥7/9** | {len([r for r in results if r['total_score'] >= 7])} |",
        f"| **Long 偏向** | {len([r for r in results if r.get('directional_bias') == 'long'])} |",
        "",
    ]

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
                f"{r['total_score']}/9 | "
                f"{sc.get('current_long', '—')}/9 | {sc.get('current_short', '—')}/9 | "
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
            pot_l = f"{best['long_count']}/9" if best.get("long_count") else "—"
            lines.append(
                f"| {r['symbol']} | {r['total_score']}/9 | "
                f"{sc.get('current_long', '—')}/9 | {sc.get('current_short', '—')}/9 | "
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
            f"| {i} | **{r['symbol']}** | {r['total_score']}/9 | "
            f"{sc.get('current_long', '—')}/9 | ${b['price']} | {b['long_count']}/9 | {r['grade']} |"
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
        pot_l = f"{best['long_count']}/9" if best.get("long_count") else "—"
        lines.append(
            f"| {r['symbol']} | {r['total_score']}/9 | "
            f"{sc.get('current_long', '—')}/9 | {sc.get('current_short', '—')}/9 | "
            f"{pot_l} | {r['grade']} | {sc.get('bias', '—')} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path, help="TradingView screener export CSV")
    parser.add_argument("--limit", type=int, default=0, help="Max symbols (0=all)")
    parser.add_argument("--delay", type=float, default=0.15, help="Seconds between symbols")
    parser.add_argument("--export-only", action="store_true", help="Re-export TV files from saved JSON")
    args = parser.parse_args()

    stem = args.csv.stem
    today = date.today().isoformat()
    json_path = REPORTS / f"SCREENER_{stem}_{today}_results.json"

    if args.export_only:
        if not json_path.exists():
            raise SystemExit(f"No cached results: {json_path}")
        results = json.loads(json_path.read_text(encoding="utf-8"))
        meta = meta_from_results(results)
        if args.csv.exists():
            meta = {**meta, **read_screener_meta(args.csv)}
        export_dir = TV_EXPORT / f"{stem}_{today}"
        export_tv_watchlists(results, meta, export_dir)
        print(f"Exported TV watchlists -> {export_dir}")
        return

    meta = read_screener_meta(args.csv)
    symbols = read_screener_symbols(args.csv)
    if args.limit:
        symbols = symbols[: args.limit]

    REPORTS.mkdir(parents=True, exist_ok=True)
    market_edge = eng.assess_broad_market_edge()
    eng._MARKET_EDGE_CACHE = market_edge

    results: list[dict] = []
    skipped: list[str] = []
    for i, sym in enumerate(symbols, 1):
        try:
            data = score_from_yf(sym, market_edge)
            if data is None:
                skipped.append(sym)
                print(f"[{i}/{len(symbols)}] SKIP {sym} (no data)")
                continue
            data["_meta"] = meta.get(sym) or {}
            results.append(data)
            print(f"[{i}/{len(symbols)}] OK {sym} {data['total_score']}/9 Grade {data['grade']}")
        except Exception as e:
            skipped.append(sym)
            print(f"[{i}/{len(symbols)}] ERR {sym}: {e}")
        if args.delay and i < len(symbols):
            time.sleep(args.delay)

    slim = []
    for r in results:
        sc = r.get("scenarios") or {}
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
            "_meta": r.get("_meta") or {},
        })
    json_path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

    out = REPORTS / f"SCREENER_{stem}_{today}_summary.md"
    out.write_text(format_screener_summary(results, args.csv.name), encoding="utf-8")

    export_dir = TV_EXPORT / f"{stem}_{today}"
    export_tv_watchlists(results, meta, export_dir)

    print(f"\nAnalyzed {len(results)} / {len(symbols)} | Skipped {len(skipped)}")
    print(f"Summary -> {out}")
    print(f"TV import files -> {export_dir}")
    print(f"Results JSON -> {json_path}")
    a = [r["symbol"] for r in results if r["grade"] == "A"]
    b = [r["symbol"] for r in results if r["grade"] == "B"]
    print(f"A ({len(a)}): {', '.join(a[:12])}{'...' if len(a) > 12 else ''}")
    print(f"B ({len(b)}): {', '.join(b[:12])}{'...' if len(b) > 12 else ''}")


if __name__ == "__main__":
    main()
