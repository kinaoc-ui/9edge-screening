#!/usr/bin/env python3
"""
Fetch W1 + D1 + H1 OHLCV from TradingView Desktop via MCP CLI, save CSV, run 9-edge analysis.

Requires:
  - TradingView Desktop running with --remote-debugging-port=9222 (launch_tv_debug.bat)
  - tradingview-mcp installed at ~/tradingview-mcp

  python fetch_tv_mcp.py              # use symbol on active TV chart
  python fetch_tv_mcp.py --symbol ETN # set chart to NASDAQ:ETN first
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_DIR = ROOT / "charts" / "csv"
REPORTS = ROOT / "reports" / "batch"
NODE = Path(os.environ.get("NODE_EXE", r"C:\Program Files\nodejs\node.exe"))
TV_CLI = Path(os.environ.get("TV_MCP_CLI", Path.home() / "tradingview-mcp" / "src" / "cli" / "index.js"))

TIMEFRAMES = (
    ("W", "W1"),
    ("D", "D1"),
    ("60", "H1"),
)

CSV_LABELS = ("W1", "D1", "H1")
_CLOUD_CSV_DIR: Path | None = None


from edge_common import is_cloud_environment as _edge_is_cloud


def is_cloud_environment() -> bool:
    """True on Streamlit Community Cloud (no local TradingView CDP)."""
    return _edge_is_cloud()


def get_csv_dir(*, cloud: bool | None = None) -> Path:
    """Writable CSV directory; cloud uses ephemeral temp storage."""
    global _CLOUD_CSV_DIR
    use_cloud = is_cloud_environment() if cloud is None else cloud
    if use_cloud:
        if _CLOUD_CSV_DIR is None:
            _CLOUD_CSV_DIR = Path(tempfile.gettempdir()) / "9edge" / "charts" / "csv"
        _CLOUD_CSV_DIR.mkdir(parents=True, exist_ok=True)
        return _CLOUD_CSV_DIR
    return CSV_DIR


def cdp_available() -> bool:
    if is_cloud_environment():
        return False
    try:
        return bool(check_cdp().get("cdp_connected"))
    except Exception:
        return False


@dataclass
class PipelineResult:
    ok: bool
    symbol: str = ""
    full_symbol: str = ""
    report_path: Path | None = None
    report_md: str = ""
    grade: str = ""
    total_score: int = 0
    decision: str = ""
    logs: list[str] = field(default_factory=list)
    error: str = ""


def tv_cmd(*args: str) -> dict:
    if not NODE.exists():
        raise RuntimeError(f"Node not found: {NODE}")
    if not TV_CLI.exists():
        raise RuntimeError(f"tradingview-mcp CLI not found: {TV_CLI}")
    proc = subprocess.run(
        [str(NODE), str(TV_CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "TV_CDP_PORT": os.environ.get("TV_CDP_PORT", "9222")},
    )
    if proc.returncode == 2:
        raise RuntimeError(
            "Cannot connect to TradingView CDP on port 9222. "
            "Close TV, run launch_tv_debug.bat, wait for CDP ready."
        )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"tv {' '.join(args)} failed: {err}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from tv {' '.join(args)}: {e}") from e


def check_cdp() -> dict:
    return tv_cmd("status")


def get_chart_state() -> dict:
    return tv_cmd("state")


def short_symbol(full: str) -> str:
    s = (full or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    return re.sub(r"[^A-Z0-9._-]", "", s)


def csv_exists(symbol: str, csv_dir: Path | None = None) -> bool:
    sym = short_symbol(symbol or "")
    if not sym:
        return False
    base = csv_dir or get_csv_dir()
    return (base / f"{sym}_D1.csv").exists()


def _label_from_csv_name(name: str, symbol: str) -> str | None:
    stem = Path(name).stem.upper()
    sym = short_symbol(symbol)
    for label in CSV_LABELS:
        if stem == f"{sym}_{label}" or stem.endswith(f"_{label}"):
            return label
    return None


def save_csv_uploads(
    symbol: str,
    *,
    csv_files: list[tuple[str, bytes]] | None = None,
    zip_bytes: bytes | None = None,
    csv_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Save W1/D1/H1 CSV uploads. Returns (log lines, errors)."""
    sym = short_symbol(symbol or "")
    if not sym:
        return [], ["請輸入股票代號"]

    target = csv_dir or get_csv_dir()
    target.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []
    errors: list[str] = []
    pending: dict[str, bytes] = {}

    if zip_bytes:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".csv"):
                        continue
                    label = _label_from_csv_name(Path(info.filename).name, sym)
                    if label:
                        pending[label] = zf.read(info)
        except zipfile.BadZipFile:
            errors.append("ZIP 檔案損壞或格式不正確")
            return logs, errors

    if csv_files:
        for name, data in csv_files:
            label = _label_from_csv_name(name, sym)
            if label:
                pending[label] = data
            else:
                errors.append(f"無法辨識 timeframe：{name}（需要 {sym}_W1/D1/H1.csv）")

    if not pending:
        errors.append(f"請上載 {sym}_W1.csv、{sym}_D1.csv、{sym}_H1.csv 或包含上述檔案的 ZIP")
        return logs, errors

    for label, data in pending.items():
        out = target / f"{sym}_{label}.csv"
        out.write_bytes(data)
        logs.append(f"已儲存 {out.name} ({len(data)} bytes)")

    missing = [lb for lb in CSV_LABELS if not (target / f"{sym}_{lb}.csv").exists()]
    if missing:
        errors.append(f"仍缺少：{', '.join(f'{sym}_{lb}.csv' for lb in missing)}")

    return logs, errors


def chart_symbol_to_tv_query(sym: str) -> str:
    s = sym.strip().upper()
    if ":" in s:
        return s
    if s.endswith(".HK") or s.isdigit():
        return f"HKEX:{s.replace('.HK', '')}"
    return f"NASDAQ:{s}"


def format_bar_time(ts: int | float, label: str) -> str:
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    if label in ("D1", "W1"):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def write_tv_csv(path: Path, bars: list[dict], label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "Volume"])
        for b in bars:
            w.writerow([
                format_bar_time(b["time"], label),
                b["open"],
                b["high"],
                b["low"],
                b["close"],
                int(b.get("volume") or 0),
            ])


def fetch_timeframe(tf: str, label: str, count: int) -> list[dict]:
    tv_cmd("timeframe", tf)
    data = tv_cmd("ohlcv", "-n", str(count))
    bars = data.get("bars") or []
    if len(bars) < 30:
        raise RuntimeError(f"{label}: need >=30 bars, got {len(bars)} (chart still loading?)")
    return bars


def find_latest_screener_csv() -> Path | None:
    """Latest TradingView screener export (../new_*.csv or screener/new_*.csv)."""
    candidates: list[Path] = []
    parent = ROOT.parent
    if parent.exists():
        candidates.extend(parent.glob("new_*.csv"))
    screener_dir = ROOT / "screener"
    if screener_dir.exists():
        candidates.extend(screener_dir.glob("new_*.csv"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _run_python_cli(script: str, *args: str, timeout: float | None = None) -> tuple[int, str]:
    """Run a project Python script; return (exit_code, combined_output)."""
    cmd = [sys.executable, str(ROOT / script), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = "\n".join(x for x in (proc.stdout, proc.stderr) if x and x.strip())
    return proc.returncode, out


def launch_tradingview_debug() -> tuple[bool, str]:
    """Start TradingView Desktop with CDP port 9222 (local only)."""
    if is_cloud_environment():
        return False, "Cloud 模式唔支援本機 TradingView"
    bat = ROOT / "launch_tv_debug.bat"
    if not bat.exists():
        return False, f"搵唔到 {bat.name}"
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        cwd=str(ROOT),
        creationflags=creationflags,
    )
    return True, "已啟動 TradingView（CDP 9222）— 等 CDP ready 後再分析"


def run_screener_analysis(csv_path: Path) -> tuple[bool, str, list[str]]:
    """Run screen_screener_csv.py on a screener export CSV."""
    logs = [f"Screener CSV: {csv_path}"]
    if not csv_path.exists():
        msg = f"檔案不存在：{csv_path}"
        logs.append(msg)
        return False, msg, logs
    try:
        rc, out = _run_python_cli("screen_screener_csv.py", str(csv_path), timeout=3600)
        if out:
            logs.extend(out.splitlines()[-40:])
        if rc != 0:
            return False, f"Screener 失敗 (exit {rc})", logs
        summary = sorted(REPORTS.glob("SCREENER_*_summary.md"), key=lambda p: p.stat().st_mtime)
        hint = f"摘要：{summary[-1].name}" if summary else "完成"
        logs.append(hint)
        return True, hint, logs
    except subprocess.TimeoutExpired:
        msg = "Screener 逾時（>60 分鐘）"
        logs.append(msg)
        return False, msg, logs
    except Exception as e:
        msg = str(e)
        logs.append(f"ERROR: {msg}")
        return False, msg, logs


def run_batch_csv_analysis() -> tuple[bool, str, list[str]]:
    """Analyze all symbols with W1/D1/H1 CSV in charts/csv/."""
    logs = ["Batch CSV 分析：charts/csv/ 全部有 D1 嘅股票"]
    try:
        rc, out = _run_python_cli("analyze_tv_csv.py", "--batch", timeout=1800)
        if out:
            logs.extend(out.splitlines()[-40:])
        if rc != 0:
            return False, f"Batch 失敗 (exit {rc})", logs
        return True, "Batch CSV 分析完成", logs
    except subprocess.TimeoutExpired:
        msg = "Batch 逾時（>30 分鐘）"
        logs.append(msg)
        return False, msg, logs
    except Exception as e:
        msg = str(e)
        logs.append(f"ERROR: {msg}")
        return False, msg, logs


def list_recent_reports(limit: int = 20) -> list[Path]:
    if not REPORTS.exists():
        return []
    files = [
        *REPORTS.glob("*_9edge_csv.md"),
        *REPORTS.glob("*_summary.md"),
    ]
    files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def _reload_analyze_module():
    """Force reload so Streamlit picks up analyze_tv_csv.py edits without restart."""
    import importlib

    sys.path.insert(0, str(ROOT))
    import analyze_tv_csv

    return importlib.reload(analyze_tv_csv)


def run_analyze_from_csv(
    symbol: str,
    *,
    csv_dir: Path | None = None,
) -> PipelineResult:
    """Re-score from existing charts/csv/{SYMBOL}_*.csv (no TradingView fetch)."""
    logs: list[str] = []
    sym = short_symbol(symbol or "")
    if not sym:
        return PipelineResult(ok=False, error="請輸入股票代號", logs=logs)

    result = PipelineResult(ok=False, symbol=sym, logs=logs)
    base = csv_dir or get_csv_dir()
    d1 = base / f"{sym}_D1.csv"
    w1 = base / f"{sym}_W1.csv"
    h1 = base / f"{sym}_H1.csv"
    if not d1.exists():
        result.error = f"搵唔到 {d1.name} — 上載 CSV 或先跑 TV 分析"
        logs.append(result.error)
        return result

    try:
        logs.append(f"CSV 重新分析：{sym}（reload analyze_tv_csv）")
        mod = _reload_analyze_module()
        data = mod.run_one(
            sym,
            d1 if d1.exists() else None,
            w1 if w1.exists() else None,
            h1 if h1.exists() else None,
        )
        report_md = mod.format_md(data)
        report_path = REPORTS / f"{sym}_{date.today().isoformat()}_9edge_csv.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")

        result.ok = True
        result.report_path = report_path
        result.report_md = report_md
        result.grade = data.get("grade", "")
        result.total_score = data.get("total_score", 0)
        result.decision = data.get("decision", "")
        logs.append(f"Done: {result.total_score}/9 Grade {result.grade} ({result.decision})")
        logs.append(f"Report: {report_path.name}")
        return result
    except Exception as e:
        result.error = str(e)
        logs.append(f"ERROR: {e}")
        return result


def run_analyze_from_yfinance(symbol: str) -> PipelineResult:
    """Score a US symbol via yfinance W1/D1/H1 — no CSV upload or TradingView."""
    logs: list[str] = []
    sym = short_symbol(symbol or "")
    if not sym:
        return PipelineResult(ok=False, error="請輸入股票代號", logs=logs)

    result = PipelineResult(ok=False, symbol=sym, logs=logs)
    try:
        import screen_screener_csv as screener

        mod = _reload_analyze_module()
        logs.append(f"yfinance 拉數分析：{sym}")
        market_edge = mod.assess_broad_market_edge()
        mod._MARKET_EDGE_CACHE = market_edge

        data = screener.score_from_yf(sym, market_edge)
        if data is None:
            result.error = f"搵唔到 {sym} 嘅 yfinance 數據（確認美股代號）"
            logs.append(result.error)
            return result

        report_md = mod.format_md(data)
        report_path: Path | None = None
        if not is_cloud_environment():
            report_path = REPORTS / f"{sym}_{date.today().isoformat()}_9edge_yf.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_md, encoding="utf-8")

        result.ok = True
        result.report_path = report_path
        result.report_md = report_md
        result.grade = data.get("grade", "")
        result.total_score = data.get("total_score", 0)
        result.decision = data.get("decision", "")
        logs.append(
            f"Done: {result.total_score}/9 Grade {result.grade} ({result.decision}) · yfinance"
        )
        if report_path:
            logs.append(f"Report: {report_path.name}")
        return result
    except Exception as e:
        result.error = str(e)
        logs.append(f"ERROR: {e}")
        return result


def run_pipeline(
    symbol: str | None = None,
    *,
    count: int = 500,
    restore_tf: bool = True,
    analyze: bool = True,
) -> PipelineResult:
    logs: list[str] = []
    result = PipelineResult(ok=False, logs=logs)

    try:
        status = check_cdp()
        if not status.get("cdp_connected"):
            raise RuntimeError("TradingView CDP not connected. Run launch_tv_debug.bat first.")

        state = get_chart_state()
        orig_tf = str(state.get("resolution") or state.get("chart_resolution") or "")
        full_sym = state.get("symbol") or state.get("chart_symbol") or ""

        if symbol and symbol.strip():
            query = chart_symbol_to_tv_query(symbol.strip())
            logs.append(f"Switch chart -> {query}")
            tv_cmd("symbol", query)
            time.sleep(1.5)
            state = get_chart_state()
            full_sym = state.get("symbol") or query

        sym = short_symbol(full_sym)
        if not sym:
            raise RuntimeError("Could not read symbol. Open a stock chart in TradingView first.")

        result.symbol = sym
        result.full_symbol = full_sym
        logs.append(f"Symbol: {full_sym} -> {sym}")
        logs.append("Fetching W1 + D1 + H1 via MCP...")

        for tf, label in TIMEFRAMES:
            bars = fetch_timeframe(tf, label, min(count, 500))
            out = CSV_DIR / f"{sym}_{label}.csv"
            write_tv_csv(out, bars, label)
            logs.append(f"  {label}: {len(bars)} bars -> {out.name}")

        if restore_tf and orig_tf:
            tv_cmd("timeframe", orig_tf)
            logs.append(f"Restored timeframe: {orig_tf}")

        if not analyze:
            result.ok = True
            logs.append("CSV saved (no analysis).")
            return result

        logs.append("Running 9-edge scoring...")
        mod = _reload_analyze_module()
        data = mod.run_one(sym, None, None)
        report_md = mod.format_md(data)
        report_path = REPORTS / f"{sym}_{date.today().isoformat()}_9edge_csv.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")

        result.ok = True
        result.report_path = report_path
        result.report_md = report_md
        result.grade = data.get("grade", "")
        result.total_score = data.get("total_score", 0)
        result.decision = data.get("decision", "")
        logs.append(f"Done: {result.total_score}/9 Grade {result.grade} ({result.decision})")
        logs.append(f"Report: {report_path.name}")
        return result

    except Exception as e:
        result.error = str(e)
        logs.append(f"ERROR: {e}")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch TV chart data via MCP and run 9-edge analysis")
    parser.add_argument("--symbol", "-s", help="Ticker e.g. ETN (optional; default = active chart)")
    parser.add_argument("--count", "-n", type=int, default=500, help="Bars per timeframe (max 500)")
    parser.add_argument("--no-analyze", action="store_true", help="Only fetch CSV, skip analyze_tv_csv.py")
    parser.add_argument("--restore-tf", action="store_true", help="Restore original timeframe after fetch")
    args = parser.parse_args()

    result = run_pipeline(
        args.symbol,
        count=args.count,
        restore_tf=args.restore_tf,
        analyze=not args.no_analyze,
    )
    for line in result.logs:
        print(line)
    if not result.ok:
        raise SystemExit(result.error or "Pipeline failed")
    if result.report_path:
        print(f"\nReport: {result.report_path}")


if __name__ == "__main__":
    main()
