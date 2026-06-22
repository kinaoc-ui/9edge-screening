#!/usr/bin/env python3
"""Cloud-safe shared helpers — no TradingView MCP / subprocess TV deps."""

from __future__ import annotations

import csv
import importlib
import io
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_DIR = ROOT / "charts" / "csv"
REPORTS = ROOT / "reports" / "batch"
CSV_LABELS = ("W1", "D1", "H1")
_CLOUD_CSV_DIR: Path | None = None


def is_cloud_environment() -> bool:
    """True on Streamlit Community Cloud (no local TradingView CDP)."""
    if os.environ.get("NINE_EDGE_CLOUD") == "1":
        return True
    if os.environ.get("STREAMLIT_SHARING") == "1":
        return True
    if os.environ.get("STREAMLIT_CLOUD") == "1":
        return True
    if os.environ.get("USER") == "appuser":
        return True
    home = os.environ.get("HOME", "")
    if home.startswith("/home/appuser"):
        return True
    if Path("/mount/src").is_dir():
        return True
    return False


def get_csv_dir(*, cloud: bool | None = None) -> Path:
    global _CLOUD_CSV_DIR
    use_cloud = is_cloud_environment() if cloud is None else cloud
    if use_cloud:
        if _CLOUD_CSV_DIR is None:
            _CLOUD_CSV_DIR = Path(tempfile.gettempdir()) / "9edge" / "charts" / "csv"
        _CLOUD_CSV_DIR.mkdir(parents=True, exist_ok=True)
        return _CLOUD_CSV_DIR
    return CSV_DIR


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


def list_recent_reports(limit: int = 20) -> list[Path]:
    if not REPORTS.exists():
        return []
    files = [
        *REPORTS.glob("*_9edge_csv.md"),
        *REPORTS.glob("*_9edge_yf.md"),
        *REPORTS.glob("*_summary.md"),
    ]
    files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def _reload_analyze_module():
    sys.path.insert(0, str(ROOT))
    import analyze_tv_csv

    return importlib.reload(analyze_tv_csv)


def run_analyze_from_csv(
    symbol: str,
    *,
    csv_dir: Path | None = None,
) -> PipelineResult:
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
        logs.append(f"CSV 重新分析：{sym}")
        mod = _reload_analyze_module()
        data = mod.run_one(
            sym,
            d1 if d1.exists() else None,
            w1 if w1.exists() else None,
            h1 if h1.exists() else None,
        )
        report_md = mod.format_md(data)
        report_path = REPORTS / f"{sym}_{date.today().isoformat()}_9edge_csv.md"
        if not is_cloud_environment():
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_md, encoding="utf-8")

        result.ok = True
        result.report_path = report_path if report_path.exists() else None
        result.report_md = report_md
        result.grade = data.get("grade", "")
        result.total_score = data.get("total_score", 0)
        result.decision = data.get("decision", "")
        logs.append(f"Done: {result.total_score}/9 Grade {result.grade} ({result.decision})")
        return result
    except Exception as e:
        result.error = str(e)
        logs.append(f"ERROR: {e}")
        return result


def run_analyze_from_yfinance(symbol: str) -> PipelineResult:
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
        return result
    except Exception as e:
        result.error = str(e)
        logs.append(f"ERROR: {e}")
        return result
