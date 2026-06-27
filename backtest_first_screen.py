#!/usr/bin/env python3
"""First Screen backtest — score as-of historical dates + forward return check."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import analyze_tv_csv as tv  # noqa: E402
import first_screen as fs  # noqa: E402
import screen_screener_csv as screener  # noqa: E402
from backtest_9edge import parse_as_of  # noqa: E402

REPORTS = ROOT / "reports" / "first_screen" / "backtest"


def _md_table_cell(text: str) -> str:
    """Escape chars that break markdown pipe tables."""
    return (text or "—").replace("|", "／").replace("\n", " ").strip()


ProgressCallback = Callable[[int, int, date], None]
CsvProgressCallback = Callable[[int, int, str], None]


def _ensure_fresh_fs():
    """Streamlit keeps old modules in memory — reload before each backtest run."""
    import importlib
    global fs, tv
    tv = importlib.reload(tv)
    fs = importlib.reload(fs)
    return fs

DEFAULT_HORIZONS = (20, 40, 60)


@dataclass
class FsBacktestScanRow:
    as_of: str
    pass_list: bool
    grade: str
    pass_tf: str
    w1_score: int
    d1_score: int
    price: float
    fwd_20d: float | None = None
    fwd_40d: float | None = None
    fwd_60d: float | None = None
    peak_60d: float | None = None
    note: str = ""


@dataclass
class FsCsvBacktestRow:
    symbol: str
    pass_list: bool
    grade: str
    pass_tf: str
    w1_score: int
    d1_score: int
    price: float
    fwd_20d: float | None = None
    fwd_40d: float | None = None
    fwd_60d: float | None = None
    peak_60d: float | None = None
    note: str = ""


def pass_filter_set(mode: str) -> set[str] | None:
    m = (mode or "pass").lower()
    if m in ("pass", "on_list", "入選"):
        return {"pass"}
    if m in ("ab", "a+b", "a_b", "grade_ab"):
        return {"pass", "near"}
    return None


def _index_on_or_before(bars: list[dict], as_of: date) -> int | None:
    idx = None
    for i, b in enumerate(bars):
        bd = date.fromisoformat(str(b.get("date", ""))[:10])
        if bd <= as_of:
            idx = i
        else:
            break
    return idx


def measure_forward_returns(
    symbol: str,
    as_of: date,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    peak_days: int = 60,
) -> dict[str, float | None]:
    """Trading-day forward % from as_of close (事後驗證爆升用)."""
    bars = tv.yf_fetch_bars(symbol.upper(), "1d", min_bars=max(peak_days + 30, 80))
    out: dict[str, float | None] = {f"fwd_{h}d": None for h in horizons}
    out["peak_60d"] = None
    if not bars:
        return out

    idx = _index_on_or_before(bars, as_of)
    if idx is None:
        return out

    base = float(bars[idx]["close"])
    if base <= 0:
        return out

    for h in horizons:
        j = idx + h
        if j < len(bars):
            out[f"fwd_{h}d"] = round((float(bars[j]["close"]) - base) / base * 100, 2)

    end = min(idx + peak_days, len(bars) - 1)
    if end > idx:
        peak = max(float(b["high"]) for b in bars[idx + 1 : end + 1])
        out["peak_60d"] = round((peak - base) / base * 100, 2)
    return out


def attach_forward(data: dict, symbol: str, as_of: date) -> dict:
    data = dict(data)
    data["forward"] = measure_forward_returns(symbol, as_of)
    return data


def scan_backtest_range(
    symbol: str,
    start: date,
    end: date,
    *,
    filters: fs.FirstScreenFilters | None = None,
    pass_modes: set[str] | None = None,
    measure_fwd: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> list[FsBacktestScanRow]:
    """Walk calendar days end→start; return rows matching pass filter."""
    _ensure_fresh_fs()
    if start > end:
        start, end = end, start
    sym = symbol.upper()
    rows: list[FsBacktestScanRow] = []
    total = (end - start).days + 1
    done = 0
    cur = end
    while cur >= start:
        done += 1
        if progress_callback:
            progress_callback(done, total, cur)
        try:
            data = fs.score_symbol(sym, as_of=cur, filters=filters)
        except Exception:
            data = None
        if not data:
            cur -= timedelta(days=1)
            continue

        passed = bool(data.get("pass"))
        grade = str(data.get("grade") or "")
        mode = "pass" if passed else ("near" if grade == "B" else "fail")
        if pass_modes is not None and mode not in pass_modes:
            cur -= timedelta(days=1)
            continue

        fwd = measure_forward_returns(sym, cur) if measure_fwd else {}
        rows.append(
            FsBacktestScanRow(
                as_of=cur.isoformat(),
                pass_list=passed,
                grade=grade,
                pass_tf=str(data.get("pass_tf") or ""),
                w1_score=int(data.get("w1_score") or 0),
                d1_score=int(data.get("d1_score") or 0),
                price=float(data.get("price") or 0),
                fwd_20d=fwd.get("fwd_20d"),
                fwd_40d=fwd.get("fwd_40d"),
                fwd_60d=fwd.get("fwd_60d"),
                peak_60d=fwd.get("peak_60d"),
                note=(data.get("note") or "")[:320],
            )
        )
        cur -= timedelta(days=1)
    return rows


def backtest_csv_at_date(
    csv_path: Path,
    as_of: date,
    *,
    filters: fs.FirstScreenFilters | None = None,
    limit: int = 0,
    only_pass: bool = True,
    measure_fwd: bool = True,
    progress_callback: CsvProgressCallback | None = None,
) -> list[FsCsvBacktestRow]:
    """Run First Screen on whole screener CSV as-of one date."""
    _ensure_fresh_fs()
    symbols = screener.read_screener_symbols(csv_path)
    if limit > 0:
        symbols = symbols[:limit]
    rows: list[FsCsvBacktestRow] = []
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        if progress_callback:
            progress_callback(i, total, sym)
        try:
            data = fs.score_symbol(sym, as_of=as_of, filters=filters)
        except Exception:
            data = None
        if not data:
            continue
        if only_pass and not data.get("pass"):
            continue
        fwd = measure_forward_returns(sym, as_of) if measure_fwd else {}
        rows.append(
            FsCsvBacktestRow(
                symbol=sym,
                pass_list=bool(data.get("pass")),
                grade=str(data.get("grade") or ""),
                pass_tf=str(data.get("pass_tf") or ""),
                w1_score=int(data.get("w1_score") or 0),
                d1_score=int(data.get("d1_score") or 0),
                price=float(data.get("price") or 0),
                fwd_20d=fwd.get("fwd_20d"),
                fwd_40d=fwd.get("fwd_40d"),
                fwd_60d=fwd.get("fwd_60d"),
                peak_60d=fwd.get("peak_60d"),
                note=(data.get("note") or "")[:320],
            )
        )
    rows.sort(
        key=lambda r: (
            -(r.peak_60d or -999),
            -(r.fwd_20d or -999),
            r.symbol,
        ),
    )
    return rows


def run_single_backtest(
    symbol: str,
    as_of: date,
    *,
    filters: fs.FirstScreenFilters | None = None,
    out: Path | None = None,
) -> tuple[dict, Path]:
    _ensure_fresh_fs()
    sym = symbol.upper()
    data = fs.score_symbol(sym, as_of=as_of, filters=filters)
    if not data:
        raise ValueError(f"數據不足：{sym} @ {as_of}")
    data = attach_forward(data, sym, as_of)
    md = fs.format_symbol_md(data)
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = out or (REPORTS / f"{sym}_{as_of.isoformat()}_first_screen_backtest.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return data, path


def format_scan_md(
    rows: list[FsBacktestScanRow],
    symbol: str,
    *,
    start: date,
    end: date,
    filters: fs.FirstScreenFilters | None = None,
) -> str:
    today = date.today().isoformat()
    lines = [
        f"# First Screen 回測掃描 — {symbol.upper()}",
        "",
        f"**區間**：{start.isoformat()} → {end.isoformat()} · **產生**：{today}",
        f"**篩選**：{(filters.summary() if filters else 'W 或 D 3/3')}",
        "",
        "> 只用 as-of 日及之前 K 線；**+20/40/60 日**同 **60 日內最高** 為事後驗證（唔係當日已知）。",
        "",
    ]
    n_pass = sum(1 for r in rows if r.pass_list)
    lines.append(f"**掃描**：{len(rows)} 日 · **入選**：{n_pass} 日")
    lines += [
        "",
    ]
    if not rows:
        lines.append("_（無符合條件日期）_")
        return "\n".join(lines)

    lines += [
        "| 日期 | 入選 | TF | W | D | 收市 | +20d | +40d | +60d | 60d高 | 備註 |",
        "|------|:----:|:--:|:--:|:--:|---:|-----:|-----:|-----:|------:|------|",
    ]
    for r in rows:
        def pct(v: float | None) -> str:
            return f"{v:+.1f}%" if v is not None else "—"

        lines.append(
            f"| {r.as_of} | {'✅' if r.pass_list else '❌'} | {r.pass_tf or '—'} | "
            f"{r.w1_score}/3 | {r.d1_score}/3 | ${r.price:.2f} | "
            f"{pct(r.fwd_20d)} | {pct(r.fwd_40d)} | {pct(r.fwd_60d)} | "
            f"{pct(r.peak_60d)} | {_md_table_cell(r.note)} |"
        )
    lines.append("")
    return "\n".join(lines)


def format_csv_backtest_md(
    rows: list[FsCsvBacktestRow],
    csv_path: Path,
    as_of: date,
    *,
    filters: fs.FirstScreenFilters | None = None,
) -> str:
    today = date.today().isoformat()
    lines = [
        f"# First Screen CSV 回測 — {as_of.isoformat()}",
        "",
        f"**來源**：{csv_path.name} · **產生**：{today}",
        f"**篩選**：{(filters.summary() if filters else 'W 或 D 3/3')}",
        "",
        "> 模擬「當日跑 First Screen」；+20/40/60 日 / 60 日內最高 = 事後睇有冇爆升。",
        "",
        f"**入選**：{len(rows)} 隻（按 60 日內最高升幅排序）",
        "",
    ]
    if not rows:
        lines.append("_（無入選）_")
        return "\n".join(lines)

    lines += [
        "| Symbol | TF | W | D | 收市 | +20d | +40d | +60d | 60d高 | 備註 |",
        "|--------|:--:|:--:|:--:|---:|-----:|-----:|-----:|------:|------|",
    ]
    for r in rows:
        def pct(v: float | None) -> str:
            return f"{v:+.1f}%" if v is not None else "—"

        lines.append(
            f"| **{r.symbol}** | {r.pass_tf or '—'} | {r.w1_score}/3 | {r.d1_score}/3 | "
            f"${r.price:.2f} | {pct(r.fwd_20d)} | {pct(r.fwd_40d)} | {pct(r.fwd_60d)} | "
            f"{pct(r.peak_60d)} | {_md_table_cell(r.note)} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        description="First Screen 回測 — 歷史 as-of + 事後升幅驗證",
        epilog="例：python backtest_first_screen.py MU 2026-01-06",
    )
    p.add_argument("symbol", nargs="?", help="美股代號（--csv 時可省略）")
    p.add_argument("as_of", nargs="?", help="回測日期 YYYY-MM-DD")
    p.add_argument("-o", "--out", type=Path, help="輸出 .md")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--scan", nargs=2, metavar=("START", "END"), help="單股日期區間掃描")
    p.add_argument(
        "--pass-mode",
        default="pass",
        choices=["pass", "ab", "all"],
        help="掃描篩選：入選 / 入選+B / 全部",
    )
    p.add_argument("--csv", type=Path, help="Screener CSV — 單日 batch 回測")
    p.add_argument("--limit", type=int, default=0, help="CSV 試跑上限")
    p.add_argument("--all", action="store_true", help="CSV 回測顯示全部（唔只入選）")
    args = p.parse_args()

    if args.csv:
        if not args.as_of:
            p.error("CSV 回測請提供 as_of 日期")
        as_of = parse_as_of(args.as_of)
        rows = backtest_csv_at_date(
            args.csv,
            as_of,
            limit=args.limit,
            only_pass=not args.all,
        )
        md = format_csv_backtest_md(rows, args.csv, as_of)
        REPORTS.mkdir(parents=True, exist_ok=True)
        out = args.out or (REPORTS / f"CSV_{as_of.isoformat()}_{args.csv.stem}_backtest.md")
        out.write_text(md, encoding="utf-8")
        print(f"CSV @ {as_of}: {len(rows)} on list -> {out}")
        for r in rows[:20]:
            pk = f"{r.peak_60d:+.1f}%" if r.peak_60d is not None else "—"
            print(f"  {r.symbol}  {r.pass_tf}  60d peak {pk}")
        return

    if not args.symbol:
        p.error("請提供 symbol，或用 --csv")
    sym = args.symbol.upper()

    if args.scan:
        start = parse_as_of(args.scan[0])
        end = parse_as_of(args.scan[1])
        pf = pass_filter_set(args.pass_mode)
        rows = scan_backtest_range(sym, start, end, pass_modes=pf)
        md = format_scan_md(rows, sym, start=start, end=end)
        REPORTS.mkdir(parents=True, exist_ok=True)
        out = args.out or (REPORTS / f"{sym}_scan_{start}_{end}_backtest.md")
        out.write_text(md, encoding="utf-8")
        print(f"Scan {sym} {start}→{end} | hits={len(rows)} -> {out}")
        for r in rows[:30]:
            pk = f" peak60 {r.peak_60d:+.1f}%" if r.peak_60d is not None else ""
            print(
                f"  {r.as_of}  {'PASS' if r.pass_list else r.grade}  "
                f"W{r.w1_score}/3 D{r.d1_score}/3  ${r.price:.2f}{pk}"
            )
        return

    if not args.as_of:
        p.error("請提供 as_of，或用 --scan / --csv")
    as_of = parse_as_of(args.as_of)
    data, out = run_single_backtest(sym, as_of, out=args.out)
    fwd = data.get("forward") or {}
    summary = (
        f"{sym} @ {as_of} | {'✅入選' if data.get('pass') else '未入選'} "
        f"Grade {data.get('grade')} ({data.get('pass_tf') or '—'}) | "
        f"W{data.get('w1_score')}/3 D{data.get('d1_score')}/3 | "
        f"${data.get('price')} | +20d {fwd.get('fwd_20d')}% | 60d peak {fwd.get('peak_60d')}%"
    )
    print(summary)
    if not args.quiet:
        print(f"Report -> {out}")


if __name__ == "__main__":
    main()
