#!/usr/bin/env python3
"""9-Edge backtest: score a symbol as-of a historical date (fine-tune calibration)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import analyze_tv_csv as eng  # noqa: E402

REPORTS = ROOT / "reports" / "backtest"

ProgressCallback = Callable[[int, int, date], None]


@dataclass
class BacktestScanRow:
    as_of: str
    total_score: int
    max_score: int
    grade: str
    decision: str
    price: float


def parse_as_of(text: str) -> date:
    """YYYY-MM-DD or MM-DD (current year)."""
    text = text.strip()
    if len(text) == 5 and text[2] == "-":
        text = f"{date.today().year}-{text}"
    return date.fromisoformat(text)


def grade_filter_set(mode: str) -> set[str] | None:
    m = (mode or "A").upper()
    if m in ("A", "GRADE_A"):
        return {"A"}
    if m in ("AB", "A+B", "A_B"):
        return {"A", "B"}
    return None


def scan_backtest_range(
    symbol: str,
    start: date,
    end: date,
    *,
    grades: set[str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[BacktestScanRow]:
    """Walk calendar days end→start; return rows matching grade filter."""
    if start > end:
        start, end = end, start
    sym = symbol.upper()
    rows: list[BacktestScanRow] = []
    total = (end - start).days + 1
    done = 0
    cur = end
    max_score = eng.EDGE_SCORE_MAX
    while cur >= start:
        done += 1
        if progress_callback:
            progress_callback(done, total, cur)
        try:
            data = eng.run_backtest(sym, cur)
        except Exception:
            data = None
        if data:
            g = data.get("grade", "")
            if grades is None or g in grades:
                rows.append(
                    BacktestScanRow(
                        as_of=cur.isoformat(),
                        total_score=int(data.get("total_score") or 0),
                        max_score=max_score,
                        grade=g,
                        decision=data.get("decision", ""),
                        price=float(data.get("price") or 0),
                    )
                )
        cur -= timedelta(days=1)
    return rows


def run_single_backtest(symbol: str, as_of: date, *, out: Path | None = None) -> tuple[dict, Path]:
    """Run one day; return (data, report_path)."""
    sym = symbol.upper()
    data = eng.run_backtest(sym, as_of)
    if not data:
        raise ValueError(f"數據不足：{sym} @ {as_of}")
    md = eng.format_md(data)
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = out or (REPORTS / f"{sym}_{as_of.isoformat()}_9edge_backtest.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return data, path


def main() -> None:
    p = argparse.ArgumentParser(
        description="9-Edge backtest — 用歷史日期評分（fine-tune 用）",
        epilog="例：python backtest_9edge.py MU 2026-04-21  # 入場日4/22 → 應4/21已出信號",
    )
    p.add_argument("symbol", help="美股代號，例如 MU")
    p.add_argument("as_of", nargs="?", help="回測日期 YYYY-MM-DD")
    p.add_argument("-o", "--out", type=Path, help="輸出 .md 路徑（預設 reports/backtest/）")
    p.add_argument("-q", "--quiet", action="store_true", help="只印一行摘要")
    p.add_argument(
        "--scan",
        nargs=2,
        metavar=("START", "END"),
        help="掃描日期區間（例 2026-01-01 2026-04-21）",
    )
    p.add_argument(
        "--grades",
        default="A",
        choices=["A", "AB", "all"],
        help="掃描時篩選 Grade（預設 A）",
    )
    args = p.parse_args()

    sym = args.symbol.upper()

    if args.scan:
        start = parse_as_of(args.scan[0])
        end = parse_as_of(args.scan[1])
        gf = grade_filter_set(args.grades)
        rows = scan_backtest_range(sym, start, end, grades=gf)
        print(f"Scan {sym} {start} → {end} | grades={args.grades} | hits={len(rows)}")
        for r in rows[:50]:
            print(
                f"  {r.as_of}  {r.total_score}/{r.max_score}  Grade {r.grade}  "
                f"({r.decision})  ${r.price:.2f}"
            )
        if len(rows) > 50:
            print(f"  ... +{len(rows) - 50} more")
        return

    if not args.as_of:
        p.error("請提供 as_of 日期，或用 --scan START END")
    as_of = parse_as_of(args.as_of)
    data, out = run_single_backtest(sym, as_of, out=args.out)

    score = eng.edge_score_fmt(data["total_score"])
    grade = data["grade"]
    decision = data["decision"]
    price = data.get("price", "—")
    setups = data.get("setups") or {}
    b_rr = (setups.get("breakout") or {}).get("rr") or 0
    r_rr = (setups.get("retest") or {}).get("rr") or 0

    summary = (
        f"{sym} @ {as_of} | 收市 ${price} | {score} Grade {grade} ({decision}) "
        f"| Setup A RR {b_rr or '—'} | Setup B RR {r_rr or '—'}"
    )
    if args.quiet:
        print(summary)
    else:
        print(summary)
        print()
        edges = data.get("edges") or {}
        labels = eng.EDGE_TABLE_LABELS
        for i, key in enumerate(eng.EDGES):
            e = edges.get(key) or {}
            mark = "Y" if e.get("score") else "-"
            lbl = labels[i] if i < len(labels) else key
            print(f"  {mark} {lbl}: {(e.get('note') or '')[:80]}")

    if not args.quiet:
        print()
        print(f"Report -> {out}")


if __name__ == "__main__":
    main()
