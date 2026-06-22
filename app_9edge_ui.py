#!/usr/bin/env python3
"""9-Edge screening UI — unified launcher + TV MCP fetch + report viewer."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import streamlit as st

from edge_common import (
    csv_exists,
    get_csv_dir,
    is_cloud_environment,
    list_recent_reports,
    run_analyze_from_csv,
    run_analyze_from_yfinance,
    save_csv_uploads,
)

_TV: object | None = None


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
    return rid


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
        key=f"download_report_{entry.get('id') if entry else 'view'}",
    )
    st.markdown(report, unsafe_allow_html=True)


def render_report_zone(*, empty_hint: str = "") -> bool:
    """Main 報告區 — session library; analysis auto-adds here."""
    init_report_library()
    lib: list[dict] = st.session_state.get("report_library", [])
    has_legacy = bool(st.session_state.get("view_report"))

    if not lib and not has_legacy:
        return False

    st.subheader("📄 報告區")
    if not lib:
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

    labels = [e["title"] for e in lib]
    pick = st.selectbox(
        "報告列表",
        range(len(lib)),
        format_func=lambda i: labels[i],
        index=_report_entry_index(lib, st.session_state.get("active_report_id")),
        key="report_zone_pick",
        label_visibility="collapsed",
    )
    entry = lib[pick]
    st.session_state["active_report_id"] = entry["id"]
    st.session_state["view_report"] = entry["md"]
    st.session_state["view_title"] = entry["title"]
    st.session_state["view_report_path"] = entry.get("path", "")
    _render_report_body(entry["md"], entry["title"], entry)
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
            f"{result.symbol} — {result.total_score}/9 Grade {result.grade} "
            f"({result.decision})"
        )
        set_view_report(
            result.report_path, result.report_md, title,
            analyzed=True, symbol=result.symbol or sym,
        )
        st.session_state["last_result"] = result
        st.session_state.pop("last_error", None)
        st.toast(f"✅ {title}", icon="✅")
    else:
        st.session_state["last_error"] = result.error


def run_yfinance_analysis(sym: str) -> None:
    sym = (sym or "").strip().upper()
    if not sym:
        st.session_state["last_error"] = "請輸入股票代號"
        return
    with st.spinner(f"yfinance 拉 W1/D1/H1 + 分析 {sym}..."):
        result = run_analyze_from_yfinance(sym)
    st.session_state["last_logs"] = result.logs
    if result.ok:
        title = (
            f"{result.symbol} — {result.total_score}/9 Grade {result.grade} "
            f"({result.decision})"
        )
        set_view_report(
            result.report_path, result.report_md, title,
            analyzed=True, symbol=result.symbol or sym,
        )
        st.session_state["last_result"] = result
        st.session_state.pop("last_error", None)
        st.toast(f"✅ {title}", icon="✅")
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
    c1, c2 = st.columns([5, 1])
    with c1:
        sym = st.text_input(
            "股票代號",
            value=default_sym,
            placeholder="輸入代號，例如 WOLF、TSM、NVDA",
            key="cloud_symbol_input",
            label_visibility="collapsed",
        )
    with c2:
        if st.button("🔍 分析", type="primary", use_container_width=True):
            run_yfinance_analysis(sym or default_sym)


def render_cloud_sidebar() -> None:
    """Cloud sidebar — session reports sync with main 報告區."""
    with st.expander("📄 報告庫", expanded=False):
        init_report_library()
        lib = st.session_state.get("report_library", [])
        if lib:
            st.caption(f"今次 session：{len(lib)} 份 — 主區 **報告區** 會自動顯示")
            labels = [e["title"] for e in lib]
            pick = st.selectbox(
                "Session 報告",
                range(len(lib)),
                format_func=lambda i: labels[i],
                index=_report_entry_index(lib, st.session_state.get("active_report_id")),
                key="cloud_lib_pick",
            )
            st.session_state["active_report_id"] = lib[pick]["id"]
        else:
            st.caption("撳 **🔍 分析** 後會自動加入主區報告區")

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
                f"{result.symbol} — {result.total_score}/9 Grade {result.grade} "
                f"({result.decision})"
            )
            set_view_report(
                result.report_path, result.report_md, title,
                analyzed=True, symbol=result.symbol,
            )
            st.session_state["last_result"] = result
            st.session_state.pop("last_error", None)
            st.toast(f"✅ {title}", icon="✅")
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

    st.markdown("**🔍 跑 Screener 選股**（TradingView Screener CSV → 全 batch 評分）")
    default_csv = find_latest_screener_csv()
    if default_csv:
        st.caption(f"偵測到最新 CSV：`{default_csv.name}`（{default_csv.parent.name}/）")
    screener_upload = st.file_uploader(
        "Screener CSV（留空就用上面預設）",
        type=["csv"],
        key=f"{key_prefix}_screener_csv_upload",
    )
    if st.button(
        "🔍 開始 Screener 選股",
        use_container_width=True,
        key=f"{key_prefix}_screener_run",
    ):
        if screener_upload:
            tmp = ROOT / "screener" / screener_upload.name
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(screener_upload.getvalue())
            csv_path = tmp
        elif default_csv:
            csv_path = default_csv
        else:
            st.session_state["last_error"] = "請上載 Screener CSV，或放 new_*.csv 喺 ../ 或 screener/"
            csv_path = None
        if csv_path:
            with st.spinner(f"Screener 分析中（{csv_path.name}，可能 5–15 分鐘）..."):
                ok, msg, logs = run_screener_analysis(csv_path)
            append_logs(logs)
            if ok:
                st.toast(msg, icon="✅")
                p = latest_batch_summary()
                if p:
                    set_view_report(p, load_report(p), report_label(p))
                st.session_state.pop("last_error", None)
            else:
                st.session_state["last_error"] = msg

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
    with st.sidebar:
        render_cloud_sidebar()

    render_cloud_toolbar()

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
