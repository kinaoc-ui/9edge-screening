#!/usr/bin/env python3
"""9-Edge screening UI — unified launcher + TV MCP fetch + report viewer."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from edge_common import (
    csv_exists,
    get_csv_dir,
    is_cloud_environment,
    list_recent_reports,
    run_analyze_from_csv,
    run_analyze_from_yfinance,
    run_backtest_from_yfinance,
    save_csv_uploads,
)

_SCREENER: object | None = None
_TV: object | None = None


def _screener():
    """Lazy-load screener (heavy analyze_tv_csv import) — faster Cloud cold start."""
    global _SCREENER
    if _SCREENER is None:
        import screen_screener_csv as _SCREENER
    return _SCREENER


def _widget_key(prefix: str, raw_id: str | None) -> str:
    digest = hashlib.md5((raw_id or "view").encode(), usedforsecurity=False).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _tv():
    """Lazy-load TradingView MCP backend (local only — skip on Streamlit Cloud)."""
    global _TV
    if _TV is None:
        import fetch_tv_mcp as _TV
    return _TV


def cdp_available() -> bool:
    if is_cloud_environment():
        return False
    return _tv().cdp_available()


def get_chart_state() -> dict:
    return _tv().get_chart_state()


def find_latest_screener_csv():
    return _tv().find_latest_screener_csv()


def launch_tradingview_debug():
    return _tv().launch_tradingview_debug()


def launch_rrr():
    return _tv().launch_rrr()


def run_batch_csv_analysis():
    return _tv().run_batch_csv_analysis()


def run_pipeline(*args, **kwargs):
    return _tv().run_pipeline(*args, **kwargs)


def run_screener_analysis(*args, **kwargs):
    return _tv().run_screener_analysis(*args, **kwargs)


def import_watchlist_from_txt(*args, **kwargs):
    return _tv().import_watchlist_from_txt(*args, **kwargs)


def open_path_in_explorer(path: Path) -> tuple[bool, str]:
    """Open a file or folder in the OS file manager (local only)."""
    if not path.exists():
        return False, f"路徑不存在：{path}"
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True, f"已開啟 {path}"
    except Exception as e:
        return False, str(e)


def resolve_screener_csv(
    *,
    uploaded,
    path_text: str,
    default_csv: Path | None,
) -> Path | None:
    if uploaded:
        tmp = ROOT / "screener" / uploaded.name
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(uploaded.getvalue())
        return tmp
    custom = (path_text or "").strip().strip('"')
    if custom:
        p = Path(custom).expanduser()
        if p.is_file():
            return p
    return default_csv


def render_tv_watchlist_import(
    *,
    key_prefix: str,
    default_export_dir: Path | None = None,
) -> None:
    """Pick a screener export .txt and download or push to TradingView watchlist."""
    st.markdown("**📥 TV Watchlist 匯入**")
    st.caption(
        "揀 `tv_import` 入面嘅 comma 清單 → 下載手動 import，或經 MCP 逐隻加入而家嘅 TV watchlist"
        "（要 TradingView Desktop + CDP 9222）。"
        " **要分 Sector**：用 `AB_by_sector/` 或 `AB_by_sector_industry/` 入面逐個檔 import；"
        "flat `*_comma.txt` 唔會自動分組。"
    )

    export_dirs = _screener().list_tv_export_dirs()
    if default_export_dir and default_export_dir.is_dir():
        if default_export_dir not in export_dirs:
            export_dirs = [default_export_dir, *export_dirs]

    if not export_dirs:
        st.info("未有 `reports/batch/tv_import/` 輸出。先跑 Screener 選股，或用 `--export-only` 匯出。")
        return

    dir_labels = [p.name for p in export_dirs]
    default_idx = 0
    if default_export_dir and default_export_dir in export_dirs:
        default_idx = export_dirs.index(default_export_dir)

    picked_dir = export_dirs[
        st.selectbox(
            "批次資料夾",
            range(len(export_dirs)),
            format_func=lambda i: dir_labels[i],
            index=default_idx,
            key=f"{key_prefix}_tv_export_dir",
        )
    ]

    option_files = [f for f, _ in _screener().TV_WATCHLIST_IMPORT_OPTIONS]
    option_labels = {f: label for f, label in _screener().TV_WATCHLIST_IMPORT_OPTIONS}
    available = [f for f in option_files if (picked_dir / f).is_file()]
    if not available:
        st.warning(f"`{picked_dir.name}` 入面冇 comma watchlist 檔。")
        if st.button("📂 開資料夾", key=f"{key_prefix}_tv_open_empty"):
            ok, msg = open_path_in_explorer(picked_dir)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg
        return

    default_list = "AB_grade_comma.txt" if "AB_grade_comma.txt" in available else available[0]
    list_file = st.selectbox(
        "清單類型",
        available,
        index=available.index(default_list),
        format_func=lambda f: option_labels.get(f, f),
        key=f"{key_prefix}_tv_list_file",
    )
    list_path = picked_dir / list_file
    symbols = _screener().parse_comma_watchlist(list_path)
    st.caption(f"`{list_path.name}` · **{len(symbols)}** 隻 · `{list_path}`")

    if symbols:
        preview = ", ".join(symbols[:8])
        st.caption(f"預覽：{preview}{'…' if len(symbols) > 8 else ''}")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.download_button(
            "⬇️ 下載 .txt",
            data=list_path.read_text(encoding="utf-8"),
            file_name=list_file,
            mime="text/plain",
            use_container_width=True,
            key=f"{key_prefix}_tv_dl",
        )
    with c2:
        if st.button("📂 開資料夾", use_container_width=True, key=f"{key_prefix}_tv_open_dir"):
            ok, msg = open_path_in_explorer(picked_dir)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg
    with c3:
        if st.button("📄 開選中檔案", use_container_width=True, key=f"{key_prefix}_tv_open_file"):
            ok, msg = open_path_in_explorer(list_path)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg
    with c4:
        import_disabled = not symbols or not cdp_available()
        if st.button(
            "📥 加入 TV Watchlist",
            use_container_width=True,
            disabled=import_disabled,
            key=f"{key_prefix}_tv_import",
            help="經 MCP 逐隻加入而家 active 嘅 watchlist（Pro 手動 import 仍可用下載檔）",
        ):
            progress = st.progress(0.0, text="匯入 TV watchlist…")
            status = st.empty()

            def on_progress(i: int, total: int, sym: str) -> None:
                progress.progress(i / total, text=f"[{i}/{total}] {sym}")
                status.caption(f"加入 **{sym}**…")

            with st.spinner(f"匯入 {len(symbols)} 隻去 TV…"):
                added, total, errors, logs = import_watchlist_from_txt(
                    list_path,
                    progress_callback=on_progress,
                )
            progress.empty()
            status.empty()
            append_logs(logs)
            if added == total:
                st.toast(f"✅ 已加入 {added} 隻", icon="✅")
                st.session_state.pop("last_error", None)
            elif added:
                st.toast(f"部分完成：{added}/{total}", icon="⚠️")
                st.session_state["last_error"] = "\n".join(errors[:5])
            else:
                st.session_state["last_error"] = errors[0] if errors else "匯入失敗"

    if not cdp_available():
        st.caption("⚠️ CDP 未連線 — 可用「下載 .txt」去 TV Watchlist → ⋯ → Import list")


def render_screener_results(result) -> None:
    """Show A/B/AB counts, output paths, downloads (after screener run)."""
    st.success(
        f"✅ 完成 — 分析 {result.analyzed}/{result.total_symbols} 隻"
        + (f"（跳過 {result.skipped}）" if result.skipped else "")
    )
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("A 級", result.a_count)
    with c2:
        st.metric("B 級", result.b_count)
    with c3:
        st.metric("AB 短名單", result.ab_count)
    with c4:
        st.metric("跳過", result.skipped)

    if result.a_symbols:
        st.caption(f"A：{', '.join(result.a_symbols[:20])}{'…' if len(result.a_symbols) > 20 else ''}")
    if result.ab_symbols:
        st.caption(f"AB：{', '.join(result.ab_symbols[:20])}{'…' if len(result.ab_symbols) > 20 else ''}")

    st.caption("輸出檔案")
    if result.summary_path and result.summary_path.exists():
        st.text(f"摘要 · {result.summary_path}")
    if result.json_path and result.json_path.exists():
        st.text(f"JSON · {result.json_path}")
    if result.export_dir and result.export_dir.exists():
        st.text(f"TV import · {result.export_dir}")

    if result.summary_md:
        st.download_button(
            "⬇️ 下載摘要 (.md)",
            data=result.summary_md,
            file_name=result.summary_path.name if result.summary_path else "screener_summary.md",
            mime="text/markdown",
            key="screener_dl_summary",
        )

    st.divider()
    render_tv_watchlist_import(
        key_prefix="screener_result",
        default_export_dir=result.export_dir,
    )


def run_local_screener(csv_path: Path, *, limit: int = 0) -> None:
    progress = st.progress(0.0, text="準備 Screener…")
    status = st.empty()

    def on_progress(i: int, total: int, sym: str) -> None:
        progress.progress(i / total, text=f"[{i}/{total}] {sym}")
        status.caption(f"yfinance 分析緊 **{sym}**…")

    with st.spinner(f"Screener 跑緊（{csv_path.name}，可能 5–15 分鐘）…"):
        result = _screener().run_screener(csv_path, limit=limit, progress_callback=on_progress)

    progress.empty()
    status.empty()
    append_logs(result.logs)
    st.session_state["last_screener_result"] = result

    if result.ok:
        st.session_state.pop("last_error", None)
        title = report_label(result.summary_path) if result.summary_path else "Screener 摘要"
        set_view_report(
            result.summary_path,
            result.summary_md,
            title,
            analyzed=True,
            symbol="SCREENER",
        )
        st.toast(
            f"✅ A {result.a_count} · B {result.b_count} · AB {result.ab_count}",
            icon="✅",
        )
        st.rerun()
    else:
        st.session_state["last_error"] = result.error

st.set_page_config(
    page_title="9-Edge Screening",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed" if is_cloud_environment() else "expanded",
)

ROOT = Path(__file__).resolve().parent
FRIEND_GUIDE = ROOT / "朋友使用指南.md"
DEPLOY_GUIDE = ROOT / "GITHUB部署.md"


def detect_cloud_mode() -> bool:
    """Streamlit Cloud or explicit sharing env (no local TradingView CDP)."""
    if is_cloud_environment():
        return True
    try:
        host = (st.context.headers.get("Host") or "").lower()
        if any(
            token in host
            for token in (
                "streamlit.app",
                "share.streamlit.io",
                "streamlit-community.cloud",
            )
        ):
            return True
    except Exception:
        pass
    return False


def tv_status_badge(cloud_mode: bool) -> tuple[bool, str, str]:
    if cloud_mode:
        return False, "", ""
    try:
        if cdp_available():
            state = get_chart_state()
            sym = state.get("symbol") or "?"
            tf = state.get("resolution") or "?"
            return True, sym, tf
        return False, "", ""
    except Exception as e:
        return False, "", str(e)


def load_report(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_guide(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"（搵唔到 {path.name}）"


def init_report_library() -> None:
    if "report_library" not in st.session_state:
        st.session_state["report_library"] = []


def register_report(
    *,
    title: str,
    md: str,
    path: Path | None = None,
    symbol: str = "",
) -> str:
    """Add report to session 報告區 (newest first). Returns entry id."""
    init_report_library()
    sym = (symbol or "").strip().upper()
    rid = f"{sym or 'RPT'}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    entry = {
        "id": rid,
        "title": title,
        "md": md,
        "path": str(path) if path else "",
        "symbol": sym,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    lib: list[dict] = st.session_state["report_library"]
    if sym:
        lib = [e for e in lib if e.get("symbol") != sym]
    lib.insert(0, entry)
    st.session_state["report_library"] = lib[:40]
    st.session_state["active_report_id"] = rid
    # 新分析要覆蓋 selectbox 舊選擇，否則報告區仍顯示上一份
    catalog = cloud_report_catalog()
    pick_idx = _report_entry_index(catalog, rid)
    st.session_state["report_zone_pick"] = pick_idx
    st.session_state["cloud_lib_pick"] = pick_idx
    return rid


def cloud_report_catalog() -> list[dict]:
    """Session 報告 + Cloud 暫存 md 檔（分析後應出現喺報告庫）。"""
    init_report_library()
    catalog: list[dict] = list(st.session_state.get("report_library", []))
    paths_in: set[str] = set()
    for e in catalog:
        if e.get("path"):
            try:
                paths_in.add(str(Path(e["path"]).resolve()))
            except OSError:
                paths_in.add(str(e["path"]))
    for p in list_recent_reports():
        if "_9edge_" not in p.name:
            continue
        ps = str(p.resolve())
        if ps in paths_in:
            continue
        sym = p.stem.split("_")[0].upper()
        catalog.append({
            "id": f"disk:{ps}",
            "title": f"{sym} — {report_label(p)}",
            "md": "",
            "path": ps,
            "symbol": sym,
            "ts": report_mtime_label(p),
        })
    return catalog


def _resolve_catalog_entry(entry: dict) -> dict:
    if entry.get("md"):
        return entry
    if path := entry.get("path"):
        p = Path(path)
        if p.exists():
            resolved = dict(entry)
            resolved["md"] = load_report(p)
            return resolved
    return entry


def _apply_catalog_entry(entry: dict) -> None:
    entry = _resolve_catalog_entry(entry)
    st.session_state["active_report_id"] = entry["id"]
    st.session_state["view_report"] = entry.get("md", "")
    st.session_state["view_title"] = entry.get("title", "Report")
    st.session_state["view_report_path"] = entry.get("path", "")


def set_view_report(
    path: Path | None,
    md: str,
    title: str,
    *,
    analyzed: bool = False,
    symbol: str = "",
    add_to_library: bool = True,
) -> None:
    st.session_state["view_report"] = md
    st.session_state["view_title"] = title
    st.session_state["view_report_path"] = str(path) if path else ""
    if analyzed:
        st.session_state["just_analyzed"] = True
    if add_to_library:
        register_report(title=title, md=md, path=path, symbol=symbol)


def _report_entry_index(lib: list[dict], entry_id: str | None) -> int:
    if not entry_id:
        return 0
    for i, e in enumerate(lib):
        if e.get("id") == entry_id:
            return i
    return 0


def _render_report_body(report: str, title: str, entry: dict | None = None) -> None:
    st.markdown(f'<div id="nine-edge-report"></div>', unsafe_allow_html=True)
    dl_name = "9edge_report.md"
    if entry:
        sym = entry.get("symbol") or ""
        if sym:
            dl_name = f"{sym}_9edge.md"
        if entry.get("path"):
            p = Path(entry["path"])
            if p.exists():
                st.caption(f"{p.name} · {entry.get('ts', '')[:19]}")
                dl_name = p.name
    elif path_str := st.session_state.get("view_report_path", ""):
        p = Path(path_str)
        if p.exists():
            st.caption(f"{p.name} · 更新 {report_mtime_label(p)}")
            dl_name = p.name
    st.download_button(
        "⬇️ 下載報告 (.md)",
        data=report,
        file_name=dl_name,
        mime="text/markdown",
        key=_widget_key("download_report", entry.get("id") if entry else None),
    )
    st.markdown(report, unsafe_allow_html=True)


def render_report_zone(*, empty_hint: str = "") -> bool:
    """Main 報告區 — session library; analysis auto-adds here."""
    catalog = cloud_report_catalog()
    has_legacy = bool(st.session_state.get("view_report"))

    if not catalog and not has_legacy:
        return False

    st.subheader("📄 報告區")

    if not catalog:
        if empty_hint:
            st.caption(empty_hint)
        if report := st.session_state.get("view_report"):
            if st.session_state.pop("just_analyzed", False):
                st.success("✅ 分析完成")
            title = st.session_state.get("view_title", "Report")
            _render_report_body(report, title)
            return True
        return False

    if st.session_state.pop("just_analyzed", False):
        st.success("✅ 分析完成 — 已自動加入報告區")

    active_idx = _report_entry_index(catalog, st.session_state.get("active_report_id"))
    if "report_zone_pick" not in st.session_state:
        st.session_state["report_zone_pick"] = active_idx
    else:
        pick_cur = int(st.session_state["report_zone_pick"])
        if pick_cur >= len(catalog):
            st.session_state["report_zone_pick"] = active_idx
        else:
            lib_id = st.session_state.get("active_report_id")
            if lib_id and catalog[pick_cur].get("id") != lib_id:
                st.session_state["report_zone_pick"] = active_idx

    labels = [e["title"] for e in catalog]
    pick = st.selectbox(
        "報告列表",
        range(len(catalog)),
        format_func=lambda i: labels[i],
        key="report_zone_pick",
        label_visibility="collapsed",
    )
    entry = _resolve_catalog_entry(catalog[pick])
    _apply_catalog_entry(entry)
    _render_report_body(entry.get("md", ""), entry.get("title", "Report"), entry)
    return True


def render_report_panel() -> bool:
    """Legacy single-report panel (local fallback)."""
    if not (report := st.session_state.get("view_report")):
        return False

    if st.session_state.pop("just_analyzed", False):
        st.success("✅ 分析完成")

    title = st.session_state.get("view_title", "Report")
    st.markdown(f'<div id="nine-edge-report"></div>', unsafe_allow_html=True)
    st.subheader(f"📄 {title}")
    _render_report_body(report, title)
    return True


def append_logs(lines: list[str]) -> None:
    prev = st.session_state.get("last_logs") or []
    st.session_state["last_logs"] = prev + lines


def report_mtime_label(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    ts = datetime.fromtimestamp(path.stat().st_mtime)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def report_label(path: Path) -> str:
    stem = path.stem
    if stem.endswith("_9edge_csv"):
        return stem.replace("_9edge_csv", "")
    if stem.endswith("_summary"):
        return f"{stem} (summary)"
    return stem


def latest_batch_summary() -> Path | None:
    summaries = sorted(
        ROOT.glob("reports/batch/SCREENER_*_summary.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return summaries[0] if summaries else None


def find_git_executable() -> str | None:
    """Resolve git.exe — PATH first, then standard Git for Windows locations."""
    found = shutil.which("git")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\Git\cmd\git.exe"),
        Path(r"C:\Program Files\Git\bin\git.exe"),
        Path(r"C:\Program Files (x86)\Git\cmd\git.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def run_git_push(commit_msg: str) -> tuple[bool, str, list[str]]:
    """git add / commit / push — updates Streamlit Cloud after deploy."""
    logs: list[str] = []
    git_exe = find_git_executable()
    if not git_exe:
        return (
            False,
            "搵唔到 git — 請安裝 Git for Windows 後 **關閉再開 9edge.bat**；"
            "或確認已裝喺 C:\\Program Files\\Git\\",
            logs,
        )
    logs.append(f"git: {git_exe}")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        logs.append(f"$ git {' '.join(args)}")
        return subprocess.run(
            [git_exe, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    if not (ROOT / ".git").exists():
        run("init")
        run("branch", "-M", "main")
        chk = run("remote", "-v")
        logs.append((chk.stdout or chk.stderr or "").strip())
        if "origin" not in (chk.stdout or ""):
            return (
                False,
                "首次 push：請跟 Sidebar「使用指南」設定 GitHub remote，再撳一次",
                logs,
            )

    status = run("status", "-sb")
    if status.stdout:
        logs.append(status.stdout.strip())

    add = run("add", ".")
    if add.stderr:
        logs.append(add.stderr.strip())

    commit = run("commit", "-m", commit_msg)
    commit_out = (commit.stdout or commit.stderr or "").strip()
    if commit_out:
        logs.append(commit_out)
    if commit.returncode != 0:
        if "Author identity unknown" in commit_out or "user.email" in commit_out:
            return (
                False,
                "Git 未設定姓名／電郵 — 喺 PowerShell 跑一次（換成你 GitHub 資料）：\n"
                'git config --global user.name "kinaoc-ui"\n'
                'git config --global user.email "你@github.com 用的 email"\n'
                "設定完再撳 Push",
                logs,
            )
        if "nothing to commit" not in commit_out:
            return False, "commit 失敗（見 log）", logs
        logs.append("（工作區無新變更，繼續 push…）")

    push = run("push", "-u", "origin", "main")
    push_out = (push.stderr or push.stdout or "").strip()
    if push.returncode != 0:
        if push_out:
            logs.append(push_out)
        if "Repository not found" in push_out:
            return (
                False,
                "GitHub 搵唔到 repo — 請先去 github.com/new 建立 **9edge-screening**（Owner: kinaoc-ui），"
                "或確認 remote URL 同登入帳號啱，再撳 Push",
                logs,
            )
        return False, "push 失敗 — 檢查 GitHub 登入同 remote URL", logs
    if push.stdout:
        logs.append(push.stdout.strip())
    if push.stderr:
        logs.append(push.stderr.strip())

    return True, "Push 完成 — Streamlit Cloud 約 1–2 分鐘 rebuild", logs


def run_csv_analysis(
    sym: str,
    *,
    cloud_mode: bool,
    uploaded_csvs: list | None = None,
    uploaded_zip,
) -> None:
    sym = sym.strip().upper()
    csv_dir = get_csv_dir(cloud=cloud_mode)
    logs: list[str] = []

    if uploaded_csvs or uploaded_zip:
        csv_files = None
        if uploaded_csvs:
            csv_files = [(f.name, f.getvalue()) for f in uploaded_csvs]
        zip_bytes = uploaded_zip.getvalue() if uploaded_zip else None
        save_logs, save_errors = save_csv_uploads(
            sym,
            csv_files=csv_files,
            zip_bytes=zip_bytes,
            csv_dir=csv_dir,
        )
        logs.extend(save_logs)
        if save_errors:
            st.session_state["last_logs"] = logs
            st.session_state["last_error"] = "; ".join(save_errors)
            return

    with st.spinner(f"Reload + 分析 {sym}..."):
        result = run_analyze_from_csv(sym, csv_dir=csv_dir)
    logs.extend(result.logs)
    st.session_state["last_logs"] = logs
    if result.ok:
        title = (
            f"{result.symbol} — {_screener().eng.edge_score_fmt(result.total_score)} Grade {result.grade} "
            f"({result.decision})"
        )
        set_view_report(
            result.report_path, result.report_md, title,
            analyzed=True, symbol=result.symbol or sym,
        )
        st.session_state["last_result"] = result
        st.session_state.pop("last_error", None)
        st.toast(f"✅ {title}", icon="✅")
        st.rerun()
    else:
        st.session_state["last_error"] = result.error


def run_backtest_scan(
    sym: str,
    scan_start: date,
    scan_end: date,
    *,
    grade_mode: str,
    key_prefix: str,
) -> None:
    sym = (sym or "").strip().upper()
    if not sym:
        st.session_state["last_error"] = "請輸入股票代號"
        return
    if scan_start > scan_end:
        scan_start, scan_end = scan_end, scan_start

    import backtest_9edge as bt

    progress = st.progress(0.0, text="準備掃描…")
    status = st.empty()
    logs: list[str] = [f"掃描 {sym}：{scan_start} → {scan_end}（{grade_mode}）"]

    def on_progress(done: int, total: int, cur: date) -> None:
        progress.progress(done / max(total, 1), text=f"掃描中 {cur.isoformat()} ({done}/{total})")
        status.caption(f"正在處理 **{cur.isoformat()}** …")

    grades = bt.grade_filter_set(grade_mode)
    rows = bt.scan_backtest_range(
        sym,
        scan_start,
        scan_end,
        grades=grades,
        progress_callback=on_progress,
    )
    progress.empty()
    status.empty()
    st.session_state[f"{key_prefix}_backtest_scan_rows"] = [r.__dict__ for r in rows]
    st.session_state[f"{key_prefix}_backtest_scan_sym"] = sym
    logs.append(f"完成：{len(rows)} 日符合篩選")
    st.session_state["last_logs"] = logs
    st.session_state.pop("last_error", None)
    st.toast(f"掃描完成：{len(rows)} 日", icon="📊")


def render_backtest_panel(default_sym: str = "", *, key_prefix: str = "bt") -> str:
    """回測單日 + 日期區間掃描。Returns current symbol text."""
    st.markdown("**📅 回測（as-of）**")
    st.caption(
        "只用該日及之前 K 線；**入場日前一日** 出信號（例：4/22 入場 → 回測 4/21）。"
        " 即時分析唔勾回測。"
    )
    sym = st.text_input(
        "股票代號",
        value=default_sym,
        placeholder="MU、NVDA、WOLF…",
        key=f"{key_prefix}_symbol",
    ).strip().upper()

    use_bt = st.checkbox("回測模式（指定 as-of 日期）", key=f"{key_prefix}_use_backtest")
    as_of: date | None = None
    if use_bt:
        as_of = st.date_input(
            "回測日期",
            value=date(2026, 4, 21),
            max_value=date.today(),
            key=f"{key_prefix}_backtest_date",
        )

    if st.button("🔍 分析", type="primary", use_container_width=True, key=f"{key_prefix}_analyze"):
        run_yfinance_analysis(sym or default_sym, as_of=as_of if use_bt else None)

    with st.expander("📊 日期區間掃描（搵 Grade A/B）", expanded=False):
        st.caption("由結束日向前掃到開始日；慢（每日約 1–2 秒），請耐心等。")
        c1, c2 = st.columns(2)
        with c1:
            scan_end = st.date_input(
                "結束日",
                value=date(2026, 4, 21),
                max_value=date.today(),
                key=f"{key_prefix}_scan_end",
            )
        with c2:
            scan_start = st.date_input(
                "開始日",
                value=date(2026, 1, 1),
                max_value=date.today(),
                key=f"{key_prefix}_scan_start",
            )
        grade_mode = st.selectbox(
            "只顯示",
            ["A", "AB", "all"],
            format_func=lambda x: {"A": "Grade A", "AB": "Grade A + B", "all": "全部"}[x],
            key=f"{key_prefix}_scan_grades",
        )
        if st.button("開始掃描", use_container_width=True, key=f"{key_prefix}_scan_run"):
            run_backtest_scan(sym or default_sym, scan_start, scan_end, grade_mode=grade_mode, key_prefix=key_prefix)

        rows = st.session_state.get(f"{key_prefix}_backtest_scan_rows") or []
        scan_sym = st.session_state.get(f"{key_prefix}_backtest_scan_sym") or sym
        if rows:
            st.caption(f"**{scan_sym}** · {len(rows)} 日符合 · 新→舊")
            table = [
                {
                    "日期": r["as_of"],
                    "分數": f"{r['total_score']}/{r['max_score']}",
                    "Grade": r["grade"],
                    "Decision": r["decision"],
                    "收市": f"${r['price']:.2f}",
                }
                for r in rows
            ]
            st.dataframe(table, use_container_width=True, hide_index=True)

            pick_dates = [r["as_of"] for r in rows]
            picked = st.selectbox(
                "開啟該日完整報告",
                pick_dates,
                key=f"{key_prefix}_scan_pick",
            )
            if st.button("📄 載入選中日期報告", key=f"{key_prefix}_scan_open"):
                picked_date = date.fromisoformat(picked)
                run_yfinance_analysis(scan_sym, as_of=picked_date)

    return sym


def run_yfinance_analysis(sym: str, *, as_of: date | None = None) -> None:
    sym = (sym or "").strip().upper()
    if not sym:
        st.session_state["last_error"] = "請輸入股票代號"
        return
    label = f"回測 {sym} @ {as_of}" if as_of else f"yfinance 拉 W1/D1/H1 + 分析 {sym}"
    with st.spinner(label + "..."):
        result = (
            run_backtest_from_yfinance(sym, as_of)
            if as_of
            else run_analyze_from_yfinance(sym)
        )
    st.session_state["last_logs"] = result.logs
    if result.ok:
        title = (
            f"{result.symbol} — {_screener().eng.edge_score_fmt(result.total_score)} Grade {result.grade} "
            f"({result.decision})"
        )
        set_view_report(
            result.report_path, result.report_md, title,
            analyzed=True, symbol=result.symbol or sym,
        )
        st.session_state["last_result"] = result
        st.session_state.pop("last_error", None)
        st.toast(f"✅ {title}", icon="✅")
        st.rerun()
    else:
        st.session_state["last_error"] = result.error


def render_status_row(connected: bool, chart_sym: str, chart_tf: str) -> None:
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if connected:
            st.success("✅ TV 已連線 (CDP 9222)")
        else:
            st.warning("⚠️ TV 未連線 — 撳「開 TradingView」")
    with col_b:
        if connected:
            st.metric("Chart", chart_sym)
    with col_c:
        if connected:
            st.metric("Timeframe", chart_tf)


def render_cloud_toolbar(default_sym: str = "") -> None:
    render_backtest_panel(default_sym, key_prefix="cloud_bt")


def render_cloud_sidebar() -> None:
    """Cloud sidebar — session + disk reports in 報告庫."""
    catalog = cloud_report_catalog()
    with st.expander("📄 報告庫", expanded=bool(catalog)):
        if catalog:
            st.caption(f"共 {len(catalog)} 份（分析後自動加入）")
            labels = [e["title"] for e in catalog]
            active_idx = _report_entry_index(catalog, st.session_state.get("active_report_id"))
            if "cloud_lib_pick" not in st.session_state:
                st.session_state["cloud_lib_pick"] = active_idx
            else:
                pick_cur = int(st.session_state["cloud_lib_pick"])
                if pick_cur >= len(catalog):
                    st.session_state["cloud_lib_pick"] = active_idx
                elif catalog[pick_cur].get("id") != st.session_state.get("active_report_id"):
                    st.session_state["cloud_lib_pick"] = active_idx
            pick = st.selectbox(
                "報告",
                range(len(catalog)),
                format_func=lambda i: labels[i],
                key="cloud_lib_pick",
            )
            _apply_catalog_entry(catalog[pick])
        else:
            st.caption("撳 **🔍 分析** 後會自動出現喺呢度")

        batch = [p for p in list_recent_reports() if "_summary" in p.name]
        if batch:
            st.divider()
            st.caption("Batch 摘要（GitHub）")
            blabels = [report_label(p) for p in batch]
            bpick = st.selectbox(
                "Batch",
                range(len(batch)),
                format_func=lambda i: blabels[i],
                key="cloud_batch_pick",
            )
            if st.button("載入摘要", use_container_width=True, key="cloud_batch_load"):
                p = batch[bpick]
                set_view_report(p, load_report(p), blabels[bpick])

    with st.expander("📤 進階", expanded=False):
        st.caption("一般唔使開；要上傳 TradingView CSV 或 .md 報告先用。")
        csv_sym = st.text_input("CSV 代號", value="WOLF", key="cloud_csv_sym").strip().upper()
        uploaded_csvs = st.file_uploader(
            "W1 / D1 / H1 CSV",
            type=["csv"],
            accept_multiple_files=True,
            key="cloud_csv_upload_multi",
        )
        uploaded_zip = st.file_uploader("或 ZIP", type=["zip"], key="cloud_csv_upload_zip")
        if st.button("用 CSV 分析", use_container_width=True, key="cloud_csv_analyze"):
            run_csv_analysis(
                csv_sym,
                cloud_mode=True,
                uploaded_csvs=uploaded_csvs,
                uploaded_zip=uploaded_zip,
            )
        md_upload = st.file_uploader("Markdown 報告 (.md)", type=["md"], key="cloud_md_upload")
        if md_upload and st.button("載入上載報告", use_container_width=True, key="cloud_md_load"):
            md_text = md_upload.read().decode("utf-8", errors="replace")
            set_view_report(None, md_text, md_upload.name)

    with st.expander("📖 使用指南", expanded=False):
        st.markdown(load_guide(FRIEND_GUIDE))


def render_sidebar_controls(cloud_mode: bool, symbol_override: str | None) -> tuple[str, list | None, object | None]:
    if cloud_mode:
        render_cloud_sidebar()
        return None, "", None, None

    st.header("⚙️ 設定")
    symbol_override = st.text_input(
        "股票代號（可留空）",
        value=symbol_override or "",
        placeholder="留空 = 用 TV 而家 chart",
        help="例如 WOLF、ETN；留空就分析 TradingView 而家開緊嗰隻。",
    ).strip() or None

    restore_tf = st.checkbox(
        "分析完還原 timeframe",
        value=True,
    )
    st.session_state["restore_tf"] = restore_tf

    st.divider()
    st.subheader("📄 報告庫")
    csv_sym = st.text_input(
        "CSV 代號",
        value=(symbol_override or "WOLF").upper(),
        placeholder="WOLF",
    ).strip().upper()

    recent = list_recent_reports()
    if not recent:
        st.caption("未有報告")
    else:
        labels = [report_label(p) for p in recent]
        pick = st.selectbox(
            "揀 report",
            options=range(len(recent)),
            format_func=lambda i: labels[i],
        )
        col_load, col_refresh = st.columns(2)
        with col_load:
            if st.button("載入", use_container_width=True, key="sidebar_load"):
                p = recent[pick]
                set_view_report(p, load_report(p), labels[pick])
        with col_refresh:
            if st.button("最新", use_container_width=True, key="sidebar_latest"):
                p = recent[0]
                set_view_report(p, load_report(p), labels[0])

    st.divider()
    st.subheader("📤 上載")
    show_upload = not csv_exists(csv_sym, ROOT / "charts" / "csv")
    uploaded_csvs = None
    uploaded_zip = None
    if show_upload:
        st.caption("上載 TradingView 匯出嘅 W1/D1/H1 CSV（或 ZIP）— 本機未有 CSV 時需要。")
        uploaded_csvs = st.file_uploader(
            "W1 / D1 / H1 CSV",
            type=["csv"],
            accept_multiple_files=True,
            key="csv_upload_multi",
        )
        uploaded_zip = st.file_uploader(
            "或 ZIP",
            type=["zip"],
            key="csv_upload_zip",
        )

    md_upload = st.file_uploader("Markdown 報告 (.md)", type=["md"], key="md_upload")
    if md_upload and st.button("載入上載報告", use_container_width=True):
        md_text = md_upload.read().decode("utf-8", errors="replace")
        set_view_report(None, md_text, md_upload.name)

    st.divider()
    with st.expander("📖 使用指南", expanded=False):
        st.markdown(load_guide(DEPLOY_GUIDE))
        st.caption("朋友用 Streamlit Cloud link，唔使 ngrok / 唔使連你部機。")

    return symbol_override, csv_sym, uploaded_csvs, uploaded_zip


def render_local_toolbar(
    connected: bool,
    symbol_override: str | None,
    csv_sym: str,
    uploaded_csvs,
    uploaded_zip,
    *,
    key_prefix: str,
) -> None:
    st.subheader("📅 回測 · yfinance")
    render_backtest_panel(
        (symbol_override or csv_sym or "").upper(),
        key_prefix=f"{key_prefix}_backtest",
    )
    st.divider()
    st.subheader("📺 第一步 · 開 TradingView")
    st.caption("關閉 TradingView 後撳掣，等 CDP ready（約 5–10 秒）再分析。")
    if st.button(
        "📺 開 TradingView (CDP 9222)",
        use_container_width=True,
        key=f"{key_prefix}_launch_tv",
    ):
        ok, msg = launch_tradingview_debug()
        append_logs([msg])
        if ok:
            st.toast(msg, icon="📺")
        else:
            st.session_state["last_error"] = msg

    st.divider()
    st.subheader("📈 第二步 · 分析")
    tv_disabled = not connected
    c1, c2 = st.columns(2)
    with c1:
        analyze_btn = st.button(
            "📈 分析 (TV)",
            type="primary",
            use_container_width=True,
            disabled=tv_disabled,
            help="用 TV chart 拉 W1/D1/H1 → 9-edge 評分",
            key=f"{key_prefix}_analyze_tv",
        )
    with c2:
        csv_btn = st.button(
            "🔄 單股 CSV 分析",
            use_container_width=True,
            help="用 charts/csv 現有 CSV 重新計分（唔使 TV）",
            key=f"{key_prefix}_csv_analyze",
        )

    if analyze_btn:
        with st.spinner("拎 W1/D1/H1 + 計分中..."):
            result = run_pipeline(
                symbol_override,
                restore_tf=st.session_state.get("restore_tf", True),
                analyze=True,
            )
        st.session_state["last_logs"] = result.logs
        if result.ok:
            title = (
                f"{result.symbol} — {_screener().eng.edge_score_fmt(result.total_score)} Grade {result.grade} "
                f"({result.decision})"
            )
            set_view_report(
                result.report_path, result.report_md, title,
                analyzed=True, symbol=result.symbol,
            )
            st.session_state["last_result"] = result
            st.session_state.pop("last_error", None)
            st.toast(f"✅ {title}", icon="✅")
            st.rerun()
        else:
            st.session_state["last_error"] = result.error

    if csv_btn:
        sym = (symbol_override or csv_sym or "WOLF").upper()
        run_csv_analysis(
            sym,
            cloud_mode=False,
            uploaded_csvs=uploaded_csvs,
            uploaded_zip=uploaded_zip,
        )

    st.divider()
    st.subheader("📦 第三步 · 批量")
    b1, b2 = st.columns(2)
    with b1:
        if st.button(
            "📦 Batch CSV 分析",
            use_container_width=True,
            key=f"{key_prefix}_batch_csv",
        ):
            with st.spinner("分析 charts/csv 全部股票..."):
                ok, msg, logs = run_batch_csv_analysis()
            append_logs(logs)
            if ok:
                st.toast(msg, icon="✅")
                st.session_state.pop("last_error", None)
            else:
                st.session_state["last_error"] = msg
    with b2:
        if st.button(
            "📋 載入最新 Batch 摘要",
            use_container_width=True,
            key=f"{key_prefix}_load_batch",
        ):
            p = latest_batch_summary()
            if p:
                set_view_report(p, load_report(p), report_label(p))
            else:
                st.session_state["last_error"] = "搵唔到 SCREENER_*_summary.md"

    st.markdown("**🔍 Screener 選股**（TradingView Screener CSV → yfinance 全 batch 評分）")
    default_csv = find_latest_screener_csv()
    if default_csv:
        st.caption(f"偵測到最新 CSV：`{default_csv.name}`（{default_csv.parent.name}/）")
    screener_upload = st.file_uploader(
        "上載 Screener CSV（留空就用預設或路徑）",
        type=["csv"],
        key=f"{key_prefix}_screener_csv_upload",
    )
    screener_path_text = st.text_input(
        "或輸入本機 CSV 路徑",
        value="",
        placeholder=str(default_csv) if default_csv else r"C:\...\new_2026-06-18.csv",
        key=f"{key_prefix}_screener_path",
    )
    screener_limit = st.number_input(
        "試跑上限（0 = 全部）",
        min_value=0,
        max_value=5000,
        value=0,
        step=10,
        help="測試用，例如 10 隻；正式跑留 0",
        key=f"{key_prefix}_screener_limit",
    )
    if st.button(
        "🔍 開始 Screener 選股",
        use_container_width=True,
        key=f"{key_prefix}_screener_run",
    ):
        csv_path = resolve_screener_csv(
            uploaded=screener_upload,
            path_text=screener_path_text,
            default_csv=default_csv,
        )
        if not csv_path:
            st.session_state["last_error"] = (
                "請上載 Screener CSV、輸入本機路徑，或放 new_*.csv 喺 ../ 或 screener/"
            )
        elif not csv_path.exists():
            st.session_state["last_error"] = f"搵唔到 CSV：{csv_path}"
        else:
            run_local_screener(csv_path, limit=int(screener_limit))

    if prev := st.session_state.get("last_screener_result"):
        if isinstance(prev, _screener().ScreenerRunResult) and prev.ok:
            with st.expander("📊 上次 Screener 結果", expanded=True):
                render_screener_results(prev)
    elif _screener().list_tv_export_dirs():
        with st.expander("📥 TV Watchlist 匯入（已有匯出）", expanded=False):
            render_tv_watchlist_import(key_prefix="tv_import_tool")

    st.divider()
    st.subheader("💰 RRR · 風險回報計算")
    rrr_path = ROOT.parent / "RRR.py"
    st.caption(
        f"開本機 **RRR.py**（倉位、止損/目標、Pool、TV webhook）\n"
        f"`{rrr_path}`"
    )
    if st.button(
        "💰 開 RRR 計算器",
        use_container_width=True,
        key=f"{key_prefix}_launch_rrr",
    ):
        ok, msg = launch_rrr()
        append_logs([msg])
        if ok:
            st.toast(msg, icon="💰")
        else:
            st.session_state["last_error"] = msg

    st.divider()
    st.subheader("🌐 第四步 · 分享俾朋友（GitHub → Streamlit Cloud）")
    st.caption("跑完 Screener 後 push，朋友開 Cloud link 就得 — 唔使連你部機。")
    commit_msg = st.text_input(
        "Commit 訊息",
        value="Update batch reports and app",
        key=f"{key_prefix}_git_commit_msg",
    )
    if st.button(
        "🚀 Push 上 GitHub",
        use_container_width=True,
        key=f"{key_prefix}_git_push",
    ):
        with st.spinner("git add / commit / push..."):
            ok, msg, logs = run_git_push(commit_msg.strip() or "Update batch reports and app")
        append_logs(logs)
        if ok:
            st.toast(msg, icon="✅")
            st.session_state.pop("last_error", None)
        else:
            st.session_state["last_error"] = msg


def render_tools_expander(
    connected: bool,
    symbol_override: str | None,
    csv_sym: str,
    uploaded_csvs,
    uploaded_zip,
    *,
    expanded: bool,
    key: str,
) -> None:
    with st.expander("🛠️ 工具", expanded=expanded, key=key):
        render_local_toolbar(
            connected,
            symbol_override,
            csv_sym,
            uploaded_csvs,
            uploaded_zip,
            key_prefix=key,
        )


def main_cloud() -> None:
    render_cloud_toolbar()

    with st.sidebar:
        render_cloud_sidebar()

    if err := st.session_state.get("last_error"):
        st.error(err)

    has_report = render_report_zone(
        empty_hint="輸入代號撳 **🔍 分析**，報告會自動顯示喺報告區。",
    )
    if not has_report:
        st.info("輸入代號撳 **🔍 分析**，報告會自動加入 **📄 報告區**。")


def main_local(
    connected: bool,
    chart_sym: str,
    chart_tf_or_err: str,
    symbol_override: str | None,
    csv_sym: str,
    uploaded_csvs,
    uploaded_zip,
) -> None:
    st.title("9-Edge 主控台")
    st.caption("雙擊 **9edge.bat** 開呢個頁面 — 所有功能撳掣就得，唔使揀 .bat")
    render_status_row(connected, chart_sym, chart_tf_or_err)

    if err := st.session_state.get("last_error"):
        st.error(err)

    has_report = st.session_state.get("view_report") or st.session_state.get("report_library")
    render_tools_expander(
        connected,
        symbol_override,
        csv_sym,
        uploaded_csvs,
        uploaded_zip,
        expanded=not has_report,
        key="tools_top",
    )

    has_report = render_report_zone()

    render_tools_expander(
        connected,
        symbol_override,
        csv_sym,
        uploaded_csvs,
        uploaded_zip,
        expanded=False,
        key="tools_bottom",
    )

    if logs := st.session_state.get("last_logs"):
        with st.expander("📋 執行 log", expanded=bool(st.session_state.get("last_error"))):
            st.code("\n".join(logs))

    if not has_report:
        st.info("撳 **工具** 開始分析，或 Sidebar **報告庫** 載入 batch 摘要。")


def main() -> None:
    try:
        _main_body()
    except Exception as e:
        st.error("應用程式錯誤")
        st.exception(e)


def _main_body() -> None:
    cloud_mode = detect_cloud_mode()
    connected, chart_sym, chart_tf_or_err = tv_status_badge(cloud_mode)

    if cloud_mode:
        main_cloud()
        return

    with st.sidebar:
        symbol_override, csv_sym, uploaded_csvs, uploaded_zip = render_sidebar_controls(
            cloud_mode, None
        )

    main_local(
        connected,
        chart_sym,
        chart_tf_or_err,
        symbol_override,
        csv_sym,
        uploaded_csvs,
        uploaded_zip,
    )


if __name__ == "__main__":
    main()
