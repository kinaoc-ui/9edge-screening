#!/usr/bin/env python3
"""9-Edge screening UI — unified launcher + TV MCP fetch + report viewer."""

from __future__ import annotations

import hashlib
import json
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
    short_symbol,
)

_SCREENER: object | None = None
_FIRST_SCREEN: object | None = None
_TV: object | None = None


def _screener():
    """Lazy-load screener (heavy analyze_tv_csv import) — faster Cloud cold start."""
    global _SCREENER
    if _SCREENER is None:
        import screen_screener_csv as _SCREENER
    return _SCREENER


def _first_screen():
    global _FIRST_SCREEN
    import importlib
    if _FIRST_SCREEN is None:
        import first_screen as _FIRST_SCREEN
    elif not hasattr(_FIRST_SCREEN, "TfFilter"):
        _FIRST_SCREEN = importlib.reload(_FIRST_SCREEN)
    return _FIRST_SCREEN


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
    export_dirs: list[Path] | None = None,
    file_options: list[tuple[str, str]] | None = None,
    default_list_file: str | None = None,
    title: str = "**📥 TV Watchlist 匯入**",
    watchlist_hint: str | None = None,
) -> None:
    """Pick export .txt → download, open folder, or MCP import to active TV watchlist."""
    st.markdown(title)
    if watchlist_hint:
        st.info(watchlist_hint)
    st.caption(
        "下載 `.txt` → TV Watchlist → ⋯ → **Import list**；"
        "或經 MCP 加入而家 active 嘅 watchlist（要 TV Desktop + CDP 9222）。"
        " **所有清單檔都有 `### Sector — Industry` 分組**。"
        " 分細 sector → `hits_by_sector/` 或 `hits_by_sector_industry/`。"
    )

    if export_dirs is None:
        export_dirs = _screener().list_tv_export_dirs()
    if default_export_dir and default_export_dir.is_dir():
        if default_export_dir not in export_dirs:
            export_dirs = [default_export_dir, *export_dirs]

    if not export_dirs:
        st.info("未有匯出資料夾。先跑 Screener 或 First Screen。")
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

    opts = file_options or _screener().TV_WATCHLIST_IMPORT_OPTIONS
    option_files = [f for f, _ in opts]
    option_labels = {f: label for f, label in opts}
    available = [f for f in option_files if (picked_dir / f).is_file()]
    if not available:
        st.warning(f"`{picked_dir.name}` 入面搵唔到 watchlist 檔。")
        if st.button("📂 開匯出資料夾", key=f"{key_prefix}_tv_open_empty", use_container_width=True):
            ok, msg = open_path_in_explorer(picked_dir)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg
        return

    default_list = default_list_file if default_list_file in available else available[0]
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
        st.caption("⚠️ CDP 未連線 — 用「下載 .txt」→ TV Watchlist → ⋯ → Import list")


def _day_from_export_dir(export_dir: Path) -> str:
    name = export_dir.name
    if name.startswith("FirstScreen_"):
        return name.replace("FirstScreen_", "", 1)
    if "_202" in name:
        return name.rsplit("_", 1)[-1]
    return date.today().isoformat()


def render_fs_tv_import_block(*, key_prefix: str, default_dir: Path | None = None) -> None:
    """Shared TV import UI for First Screen exports (same controls as 9-edge screener)."""
    fs = _first_screen()
    dirs = fs.list_export_dirs()
    if not dirs:
        st.caption("跑完 First Screen 後，呢度會出現 TV 匯入（下載 / 開資料夾 / 加入 Watchlist）。")
        return
    picked = default_dir if default_dir and default_dir in dirs else dirs[0]
    day = _day_from_export_dir(picked)
    render_tv_watchlist_import(
        key_prefix=key_prefix,
        default_export_dir=picked,
        export_dirs=dirs,
        file_options=fs.tv_import_options_for_date(day),
        default_list_file=(
            f"FirstScreen_{day}_comma.txt"
            if (picked / f"FirstScreen_{day}_comma.txt").is_file()
            else "hits_comma.txt"
        ),
        title="**📥 First Screen → TV Watchlist**",
        watchlist_hint=(
            f"建議喺 TV **新建 watchlist：`FirstScreen_{day}`**，"
            f"再 Import **`FirstScreen_{day}_comma.txt`**（內有 ### Sector — Industry 分組）。"
        ),
    )


def render_first_screen_results(result, *, key_prefix: str = "fs_result") -> None:
    """First Screen 完成後：入選數、開資料夾、TV import。"""
    st.success(
        f"✅ First Screen 完成 — 入選 **{result.hit_count}** / {result.analyzed} 隻"
        + (f"（跳過 {result.skipped}）" if result.skipped else "")
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("入選", result.hit_count)
    with c2:
        st.metric("分析", result.analyzed)
    with c3:
        st.metric("跳過", result.skipped)

    if result.watchlist_name:
        st.caption(
            f"建議 TV watchlist 名：**{result.watchlist_name}**"
            "（新建 list → Import `FirstScreen_YYYY-MM-DD_comma.txt`）"
        )
    if getattr(result, "filters", None):
        st.caption(f"入選條件：**{result.filters.summary()}**")

    hit_rows = getattr(result, "hit_rows", None) or []
    if not hit_rows and result.export_dir:
        classified = result.export_dir / "classified_full.csv"
        if classified.is_file():
            import csv as _csv
            with classified.open(encoding="utf-8-sig", newline="") as f:
                hit_rows = [
                    {
                        "symbol": row.get("symbol", ""),
                        "sector": row.get("sector", ""),
                        "industry": row.get("industry", ""),
                        "price": row.get("price", ""),
                        "pass_tf": row.get("pass_tf", ""),
                        "grade": row.get("grade", ""),
                        "w1": f"{row.get('w1_score', 0)}/3",
                        "d1": f"{row.get('d1_score', 0)}/3",
                        "note": (row.get("note") or "")[:80],
                    }
                    for row in _csv.DictReader(f)
                    if str(row.get("on_list", "")).lower() in ("true", "1", "yes")
                ]
    if hit_rows:
        st.markdown("**✅ 入選名單（Sector / Industry）**")
        st.dataframe(
            hit_rows,
            use_container_width=True,
            hide_index=True,
            column_config={
                "symbol": st.column_config.TextColumn("Symbol", width="small"),
                "sector": st.column_config.TextColumn("Sector", width="medium"),
                "industry": st.column_config.TextColumn("Industry", width="large"),
                "price": st.column_config.TextColumn("價", width="small"),
                "pass_tf": st.column_config.TextColumn("TF", width="small"),
                "grade": st.column_config.TextColumn("Gr", width="small"),
                "w1": st.column_config.TextColumn("W", width="small"),
                "d1": st.column_config.TextColumn("D", width="small"),
                "note": st.column_config.TextColumn("備註", width="large"),
            },
        )
        classified = (result.export_dir / "classified_full.csv") if result.export_dir else None
        if classified and classified.is_file():
            st.download_button(
                "⬇️ 下載 classified_full.csv（含 Sector / Industry）",
                data=classified.read_bytes(),
                file_name=classified.name,
                mime="text/csv",
                key=f"{key_prefix}_dl_classified",
            )

    open_target = (
        result.export_dir
        if result.export_dir and result.export_dir.is_dir()
        else ROOT / "reports" / "first_screen"
    )
    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        if st.button("📂 開 TV 匯出資料夾", use_container_width=True, key=f"{key_prefix}_open_dir"):
            ok, msg = open_path_in_explorer(open_target)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg
    with bc2:
        dated = result.dated_hits_path
        if dated and dated.is_file() and st.button(
            "📄 開日期清單檔",
            use_container_width=True,
            key=f"{key_prefix}_open_dated",
            disabled=not dated,
        ):
            ok, msg = open_path_in_explorer(dated)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg
    with bc3:
        if result.summary_path and result.summary_path.is_file() and st.button(
            "📄 開摘要",
            use_container_width=True,
            key=f"{key_prefix}_open_summary",
        ):
            ok, msg = open_path_in_explorer(result.summary_path)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg

    if result.export_dir:
        st.caption(f"匯出：`{result.export_dir}`")

    st.divider()
    render_fs_tv_import_block(
        key_prefix=f"{key_prefix}_tv",
        default_dir=result.export_dir,
    )


def render_screener_results(result, *, key_prefix: str = "screener_result") -> None:
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
            key=f"{key_prefix}_dl_summary",
        )

    st.divider()
    render_tv_watchlist_import(
        key_prefix=f"{key_prefix}_tv",
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


def build_fs_filters(
    fs_mod,
    *,
    w_en: bool,
    w_ma: bool,
    w_pb: bool,
    w_down: bool,
    w_up: bool,
    w_bull: bool,
    d_en: bool,
    d_ma: bool,
    d_pb: bool,
    d_down: bool,
    d_up: bool,
    d_bull: bool,
    req_counter: bool,
    req_leading: bool,
):
    return fs_mod.FirstScreenFilters(
        w1=fs_mod.TfFilter(
            enabled=w_en,
            ma_turn=w_ma,
            pullback_turn=w_pb,
            down_vol=w_down,
            up_vol=w_up,
            big_bull=w_bull,
        ),
        d1=fs_mod.TfFilter(
            enabled=d_en,
            ma_turn=d_ma,
            pullback_turn=d_pb,
            down_vol=d_down,
            up_vol=d_up,
            big_bull=d_bull,
        ),
        require_counter_trend=req_counter,
        require_leading_ma=req_leading,
    )


def _fs_bt_publish_report(
    *,
    md: str,
    title: str,
    symbol: str,
    path: Path | None,
    key_prefix: str,
    flash: str,
) -> None:
    """Load backtest markdown into 報告區 and surface a visible banner."""
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")
    set_view_report(path, md, title, analyzed=True, symbol=symbol)
    st.session_state["fs_bt_flash"] = flash
    st.session_state["fs_bt_report_path"] = str(path) if path else ""
    st.session_state["fs_bt_open"] = True
    append_logs([flash])
    st.session_state.pop("last_error", None)
    st.toast(title, icon="📅")
    st.rerun()


def run_fs_single_backtest(
    sym: str,
    as_of: date,
    *,
    filters,
    key_prefix: str,
) -> None:
    sym = (sym or "").strip().upper()
    if not sym:
        st.session_state["last_error"] = "請輸入股票代號"
        return
    import backtest_first_screen as fsbt

    with st.spinner(f"First Screen 回測 {sym} @ {as_of}…"):
        try:
            data, path = fsbt.run_single_backtest(sym, as_of, filters=filters)
        except ValueError as e:
            st.session_state["last_error"] = str(e)
            return
    md = path.read_text(encoding="utf-8")
    title = (
        f"FS {sym} @ {as_of} — "
        f"{'入選' if data.get('pass') else '未入選'} "
        f"W{data.get('w1_score')}/3 D{data.get('d1_score')}/3"
    )
    fwd = data.get("forward") or {}
    pk = fwd.get("peak_60d")
    pk_s = f" · 60d高 {pk:+.1f}%" if pk is not None else ""
    flash = f"{title}{pk_s} — 報告已載入下方 **📄 報告區**"
    _fs_bt_publish_report(
        md=md,
        title=title,
        symbol=sym,
        path=path,
        key_prefix=key_prefix,
        flash=flash,
    )


def run_fs_backtest_scan(
    sym: str,
    scan_start: date,
    scan_end: date,
    *,
    pass_mode: str,
    filters,
    key_prefix: str,
) -> None:
    sym = (sym or "").strip().upper()
    if not sym:
        st.session_state["last_error"] = "請輸入股票代號"
        return
    if scan_start > scan_end:
        scan_start, scan_end = scan_end, scan_start

    import backtest_first_screen as fsbt

    progress = st.progress(0.0, text="準備 First Screen 掃描…")
    status = st.empty()

    def on_progress(done: int, total: int, cur: date) -> None:
        progress.progress(done / max(total, 1), text=f"掃描 {cur.isoformat()} ({done}/{total})")
        status.caption(f"**{sym}** · {cur.isoformat()}")

    pf = fsbt.pass_filter_set(pass_mode)
    rows = fsbt.scan_backtest_range(
        sym,
        scan_start,
        scan_end,
        filters=filters,
        pass_modes=pf,
        progress_callback=on_progress,
    )
    progress.empty()
    status.empty()
    md = fsbt.format_scan_md(rows, sym, start=scan_start, end=scan_end, filters=filters)
    st.session_state["fs_bt_rows"] = [r.__dict__ for r in rows]
    st.session_state["fs_bt_scan_sym"] = sym
    st.session_state["fs_bt_scan_md"] = md

    out_path = fsbt.REPORTS / f"{sym}_scan_{scan_start}_{scan_end}_backtest.md"
    title = f"FS 掃描 {sym} · {len(rows)} 日"
    if not rows:
        st.session_state["last_error"] = (
            f"{sym} {scan_start}→{scan_end}：0 日符合「{pass_mode}」篩選；"
            "試放鬆條件或改「全部有分數」。"
        )
        out_path.write_text(md, encoding="utf-8")
        append_logs([f"FS 掃描 {sym}：0 日"])
        return

    best = max(rows, key=lambda r: r.peak_60d or -999)
    best_pk = (
        f"{best.peak_60d:+.1f}%"
        if best.peak_60d is not None
        else "—"
    )
    flash = (
        f"FS 區間掃描 **{sym}**：{len(rows)} 日符合 · "
        f"最佳 60d高 {best_pk}（{best.as_of}）— 報告已載入下方 **📄 報告區**"
    )
    _fs_bt_publish_report(
        md=md,
        title=title,
        symbol=f"FSBT_{sym}",
        path=out_path,
        key_prefix=key_prefix,
        flash=flash,
    )


def run_fs_csv_backtest(
    csv_path: Path,
    as_of: date,
    *,
    limit: int,
    only_pass: bool,
    filters,
    key_prefix: str,
) -> None:
    import backtest_first_screen as fsbt

    progress = st.progress(0.0, text="準備 CSV 回測…")
    status = st.empty()

    def on_progress(i: int, total: int, sym: str) -> None:
        progress.progress(i / max(total, 1), text=f"[{i}/{total}] {sym}")
        status.caption(f"回測 **{sym}** @ {as_of.isoformat()}")

    rows = fsbt.backtest_csv_at_date(
        csv_path,
        as_of,
        filters=filters,
        limit=limit,
        only_pass=only_pass,
        progress_callback=on_progress,
    )
    progress.empty()
    status.empty()
    md = fsbt.format_csv_backtest_md(rows, csv_path, as_of, filters=filters)
    out_dir = fsbt.REPORTS
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"CSV_{as_of.isoformat()}_{csv_path.stem}_backtest.md"
    out_path.write_text(md, encoding="utf-8")
    st.session_state["fs_csv_bt_rows"] = [r.__dict__ for r in rows]
    st.session_state["fs_csv_bt_md"] = md
    st.session_state["fs_csv_bt_path"] = str(out_path)
    title = f"FS CSV @ {as_of} — {len(rows)} 入選"
    top_pk = rows[0].peak_60d if rows else None
    pk_s = f" · Top 60d高 {top_pk:+.1f}% ({rows[0].symbol})" if rows and top_pk is not None else ""
    flash = f"{title}{pk_s} — 報告已載入下方 **📄 報告區**"
    _fs_bt_publish_report(
        md=md,
        title=title,
        symbol="FSBT_CSV",
        path=out_path,
        key_prefix=key_prefix,
        flash=flash,
    )


def render_fs_backtest_panel(
    *,
    fs_filters,
    csv_path: Path | None,
) -> None:
    """First Screen 回測：單股掃描 + CSV 單日 batch（含事後升幅）。"""
    if flash := st.session_state.get("fs_bt_flash"):
        st.success(flash)
        st.caption("⬇️ 向下捲到 **📄 報告區** 睇完整 Markdown；表格亦喺下面。")
        bt_path = st.session_state.get("fs_bt_report_path")
        if bt_path:
            bp = Path(bt_path)
            if bp.is_file() and st.button("📂 開回測報告檔", key="fs_bt_open_file"):
                ok, msg = open_path_in_explorer(bp)
                if ok:
                    st.toast(msg, icon="📂")
                else:
                    st.session_state["last_error"] = msg

    scan_rows = st.session_state.get("fs_bt_rows") or []
    csv_rows = st.session_state.get("fs_csv_bt_rows") or []
    bt_expanded = bool(
        st.session_state.get("fs_bt_open")
        or scan_rows
        or csv_rows
        or st.session_state.get("fs_bt_flash")
    )

    with st.expander("📅 First Screen 回測（驗證爆升）", expanded=bt_expanded):
        st.caption(
            "只用 as-of 日及之前 K 線；**+20/40/60 日**同 **60 日內最高** 係事後驗證。"
            " 用上面 W/D/RS 勾選做篩選條件。跑完會自動載入 **📄 報告區**。"
        )
        fs_sym = st.text_input(
            "單股代號（區間掃描）",
            value="",
            placeholder="MU、STX、WOLF…",
            key="fs_bt_sym",
        ).strip().upper()

        bt_c1, bt_c2 = st.columns(2)
        with bt_c1:
            fs_bt_date = st.date_input(
                "單日回測",
                value=date(2026, 1, 6),
                max_value=date.today(),
                key="fs_bt_date",
            )
        with bt_c2:
            fs_bt_pass = st.selectbox(
                "區間掃描顯示",
                ["pass", "ab", "all"],
                format_func=lambda x: {
                    "pass": "只入選",
                    "ab": "入選 + Grade B",
                    "all": "全部有分數",
                }[x],
                key="fs_bt_pass_mode",
            )

        if st.button("🔍 單股單日回測", use_container_width=True, key="fs_bt_single"):
            if not fs_sym:
                st.session_state["last_error"] = "請輸入單股代號"
            else:
                run_fs_single_backtest(fs_sym, fs_bt_date, filters=fs_filters, key_prefix="fs")

        st.markdown("**日期區間掃描**（搵幾時入選 + 之後升幾多）")
        sc1, sc2 = st.columns(2)
        with sc1:
            fs_scan_end = st.date_input(
                "結束日",
                value=date(2026, 4, 21),
                max_value=date.today(),
                key="fs_scan_end",
            )
        with sc2:
            fs_scan_start = st.date_input(
                "開始日",
                value=date(2026, 1, 1),
                max_value=date.today(),
                key="fs_scan_start",
            )
        if st.button("開始單股區間掃描", use_container_width=True, key="fs_bt_scan"):
            if not fs_sym:
                st.session_state["last_error"] = "請輸入單股代號"
            else:
                run_fs_backtest_scan(
                    fs_sym,
                    fs_scan_start,
                    fs_scan_end,
                    pass_mode=fs_bt_pass,
                    filters=fs_filters,
                    key_prefix="fs",
                )

        scan_sym = st.session_state.get("fs_bt_scan_sym") or fs_sym
        if scan_rows:
            st.markdown(f"**區間掃描結果 · {scan_sym} · {len(scan_rows)} 日**")
            table = [
                {
                    "日期": r["as_of"],
                    "入選": "✅" if r["pass_list"] else "❌",
                    "TF": r.get("pass_tf") or "—",
                    "W": f"{r['w1_score']}/3",
                    "D": f"{r['d1_score']}/3",
                    "收市": f"${r['price']:.2f}",
                    "+20d": f"{r['fwd_20d']:+.1f}%" if r.get("fwd_20d") is not None else "—",
                    "60d高": f"{r['peak_60d']:+.1f}%" if r.get("peak_60d") is not None else "—",
                }
                for r in scan_rows
            ]
            st.dataframe(table, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("**CSV 單日 batch**（模擬當日跑 screener 名單，睇邊啲後來爆升）")
        ensure_ui_pref("fs_csv_bt_limit")
        fs_csv_limit = st.number_input(
            "CSV 回測上限（0=全部）",
            min_value=0,
            max_value=5000,
            step=10,
            key="fs_csv_bt_limit",
        )
        fs_csv_only_pass = st.checkbox("只顯示入選", value=True, key="fs_csv_bt_only_pass")
        if st.button(
            "📊 CSV 單日回測",
            use_container_width=True,
            key="fs_csv_bt_run",
            disabled=not (csv_path and csv_path.is_file()),
        ):
            if not csv_path or not csv_path.is_file():
                st.session_state["last_error"] = "請先揀 Screener CSV"
            else:
                run_fs_csv_backtest(
                    csv_path,
                    fs_bt_date,
                    limit=int(fs_csv_limit),
                    only_pass=fs_csv_only_pass,
                    filters=fs_filters,
                    key_prefix="fs",
                )
        elif not (csv_path and csv_path.is_file()):
            st.caption("（需先揀 Screener CSV）")

        csv_rows = st.session_state.get("fs_csv_bt_rows") or []
        if csv_rows:
            st.markdown(f"**CSV 回測結果 · {len(csv_rows)} 隻**（按 60 日內最高升幅排序）")
            csv_table = [
                {
                    "Symbol": r["symbol"],
                    "TF": r.get("pass_tf") or "—",
                    "W": f"{r['w1_score']}/3",
                    "D": f"{r['d1_score']}/3",
                    "收市": f"${r['price']:.2f}",
                    "+20d": f"{r['fwd_20d']:+.1f}%" if r.get("fwd_20d") is not None else "—",
                    "+60d": f"{r['fwd_60d']:+.1f}%" if r.get("fwd_60d") is not None else "—",
                    "60d高": f"{r['peak_60d']:+.1f}%" if r.get("peak_60d") is not None else "—",
                }
                for r in csv_rows[:80]
            ]
            st.dataframe(csv_table, use_container_width=True, hide_index=True)


def render_first_screen_section(
    *,
    screener_upload=None,
) -> None:
    """First Screen — 置頂；設定寫入 .local/ui_prefs.json（F5 保留）。"""
    ensure_all_ui_prefs()
    fs_mod = _first_screen()

    st.subheader("🌱 First Screen 初篩")
    st.caption(
        "W/D 子項可單獨 tick；都冇勾 = **W 或 D 3/3**。"
        " **設定自動儲存**（`.local/ui_prefs.json`），F5 唔使重 tick。"
    )

    st.markdown("**W1 圖**")
    fw0, fw1, fw2, fw3, fw4 = st.columns(5)
    with fw0:
        st.checkbox("W1", key="fs_w_en")
    with fw1:
        st.checkbox("MA 轉上", key="fs_w_ma")
    with fw2:
        st.checkbox("股跌日量低", key="fs_w_down")
    with fw3:
        st.checkbox("股升日量高", key="fs_w_up")
    with fw4:
        st.checkbox("大陽燭", key="fs_w_bull")
    st.checkbox(
        "W · 拉回後轉上（大跌→走平→10MA 再向上）",
        key="fs_w_pb",
        help="MU 11/18 跌後、12 月橫行再突破 — 入場喺轉上日，唔係跌日",
    )

    st.markdown("**D1 圖**")
    fd0, fd1, fd2, fd3, fd4 = st.columns(5)
    with fd0:
        st.checkbox("D1", key="fs_d_en")
    with fd1:
        st.checkbox("MA 轉上", key="fs_d_ma")
    with fd2:
        st.checkbox("股跌日量低", key="fs_d_down")
    with fd3:
        st.checkbox("股升日量高", key="fs_d_up")
    with fd4:
        st.checkbox("大陽燭", key="fs_d_bull")
    st.checkbox(
        "D · 拉回後轉上（大跌→走平→10MA 再向上）",
        key="fs_d_pb",
        help="建議 D1 用於 MU 類：12/10 前後轉上",
    )

    fc_r1, fc_r2 = st.columns(2)
    with fc_r1:
        st.checkbox("① 反向走勢", key="fs_req_counter")
    with fc_r2:
        st.checkbox("② 領先 MA vs SPY", key="fs_req_leading")

    default_csv = find_latest_screener_csv()
    if default_csv:
        st.caption(f"偵測到 CSV：`{default_csv.name}`")
    c_up, c_path = st.columns([1, 2])
    with c_up:
        fs_upload = st.file_uploader(
            "Screener CSV",
            type=["csv"],
            key="fs_screener_upload",
            label_visibility="collapsed",
        )
    with c_path:
        st.text_input(
            "或 CSV 路徑",
            placeholder=str(default_csv) if default_csv else r"C:\...\new_*.csv",
            key="screener_path",
        )
    upload = fs_upload or screener_upload
    fs_csv_path = resolve_screener_csv(
        uploaded=upload,
        path_text=st.session_state.get("screener_path", ""),
        default_csv=default_csv,
    )

    st.number_input(
        "試跑上限（0=全部）",
        min_value=0,
        max_value=5000,
        step=10,
        key="fs_limit",
    )

    fs_filters = build_fs_filters(
        fs_mod,
        w_en=st.session_state["fs_w_en"],
        w_ma=st.session_state["fs_w_ma"],
        w_pb=st.session_state["fs_w_pb"],
        w_down=st.session_state["fs_w_down"],
        w_up=st.session_state["fs_w_up"],
        w_bull=st.session_state["fs_w_bull"],
        d_en=st.session_state["fs_d_en"],
        d_ma=st.session_state["fs_d_ma"],
        d_pb=st.session_state["fs_d_pb"],
        d_down=st.session_state["fs_d_down"],
        d_up=st.session_state["fs_d_up"],
        d_bull=st.session_state["fs_d_bull"],
        req_counter=st.session_state["fs_req_counter"],
        req_leading=st.session_state["fs_req_leading"],
    )
    if fs_filters.has_tf_filters() or fs_filters.needs_rs():
        st.caption(f"**目前條件**：{fs_filters.summary()}")

    if st.button("🌱 開始 First Screen", type="primary", use_container_width=True, key="fs_run"):
        if not fs_csv_path:
            st.session_state["last_error"] = "請上載或輸入 Screener CSV 路徑"
        elif not fs_csv_path.exists():
            st.session_state["last_error"] = f"搵唔到 CSV：{fs_csv_path}"
        else:
            run_local_first_screen(
                fs_csv_path,
                limit=int(st.session_state.get("fs_limit", 0)),
                filters=fs_filters,
            )

    render_fs_backtest_panel(
        fs_filters=fs_filters,
        csv_path=fs_csv_path if fs_csv_path and fs_csv_path.exists() else None,
    )

    if fs_prev := st.session_state.get("last_first_screen_result"):
        if isinstance(fs_prev, fs_mod.FirstScreenRunResult) and fs_prev.ok:
            with st.expander("🌱 上次 First Screen 結果", expanded=False):
                render_first_screen_results(fs_prev, key_prefix="fs_prev")
    elif fs_mod.list_export_dirs():
        with st.expander("📥 TV Watchlist 匯入", expanded=False):
            render_fs_tv_import_block(key_prefix="fs_tv")

    st.divider()


def run_local_first_screen(
    csv_path: Path,
    *,
    limit: int = 0,
    filters: "FirstScreenFilters | None" = None,
) -> None:
    progress = st.progress(0.0, text="準備 First Screen…")
    status = st.empty()

    def on_progress(i: int, total: int, sym: str) -> None:
        progress.progress(i / total, text=f"[{i}/{total}] {sym}")
        status.caption(f"初篩分析緊 **{sym}**…")

    with st.spinner(f"First Screen 跑緊（{csv_path.name}）…"):
        result = _first_screen().run_screen(
            csv_path,
            limit=limit,
            progress_callback=on_progress,
            filters=filters,
        )

    progress.empty()
    status.empty()
    append_logs(result.logs)
    st.session_state["last_first_screen_result"] = result

    if result.ok:
        st.session_state.pop("last_error", None)
        title = report_label(result.summary_path) if result.summary_path else "First Screen 摘要"
        set_view_report(
            result.summary_path,
            result.summary_md,
            title,
            analyzed=True,
            symbol="FIRST_SCREEN",
        )
        st.toast(f"✅ 初篩通過 {result.hit_count} 隻", icon="✅")
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
UI_PREFS_PATH = ROOT / ".local" / "ui_prefs.json"

UI_PREF_KEYS: tuple[str, ...] = (
    "fs_w_en", "fs_w_ma", "fs_w_pb", "fs_w_down", "fs_w_up", "fs_w_bull",
    "fs_d_en", "fs_d_ma", "fs_d_pb", "fs_d_down", "fs_d_up", "fs_d_bull",
    "fs_req_counter", "fs_req_leading",
    "fs_limit", "fs_csv_bt_limit", "screener_path",
    "tools_other_visible",
)

UI_PREF_DEFAULTS: dict[str, bool | int | str] = {
    "fs_w_en": False,
    "fs_w_ma": False,
    "fs_w_pb": False,
    "fs_w_down": False,
    "fs_w_up": False,
    "fs_w_bull": False,
    "fs_d_en": False,
    "fs_d_ma": False,
    "fs_d_pb": False,
    "fs_d_down": False,
    "fs_d_up": False,
    "fs_d_bull": False,
    "fs_req_counter": False,
    "fs_req_leading": False,
    "fs_limit": 0,
    "fs_csv_bt_limit": 50,
    "screener_path": "",
    "tools_other_visible": False,
}


def load_ui_prefs() -> dict:
    try:
        if UI_PREFS_PATH.is_file():
            return json.loads(UI_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_ui_prefs(prefs: dict) -> None:
    UI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    UI_PREFS_PATH.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_ui_pref(key: str) -> None:
    """Load disk prefs into session_state before widgets (survives F5)."""
    if key not in st.session_state:
        disk = load_ui_prefs()
        st.session_state[key] = disk.get(key, UI_PREF_DEFAULTS.get(key, False))


def ensure_all_ui_prefs() -> None:
    for key in UI_PREF_KEYS:
        ensure_ui_pref(key)


def persist_ui_prefs() -> None:
    prefs = load_ui_prefs()
    for key in UI_PREF_KEYS:
        if key in st.session_state:
            prefs[key] = st.session_state[key]
    save_ui_prefs(prefs)


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


def _report_file_path(entry: dict | None) -> Path | None:
    if entry and entry.get("path"):
        p = Path(entry["path"])
        if p.is_file():
            return p
    path_str = st.session_state.get("view_report_path", "")
    if path_str:
        p = Path(path_str)
        if p.is_file():
            return p
    return None


def _render_report_body(report: str, title: str, entry: dict | None = None) -> None:
    st.markdown(f'<div id="nine-edge-report"></div>', unsafe_allow_html=True)
    dl_name = "9edge_report.md"
    report_path: Path | None = None
    if entry:
        sym = entry.get("symbol") or ""
        if sym:
            dl_name = f"{sym}_9edge.md"
        report_path = _report_file_path(entry)
        if report_path:
            st.caption(f"{report_path.name} · {entry.get('ts', '')[:19]}")
            dl_name = report_path.name
    elif path_str := st.session_state.get("view_report_path", ""):
        p = Path(path_str)
        if p.exists():
            report_path = p
            st.caption(f"{p.name} · 更新 {report_mtime_label(p)}")
            dl_name = p.name

    btn_key = _widget_key("report_action", entry.get("id") if entry else None)
    if not detect_cloud_mode() and report_path and report_path.parent.is_dir():
        st.caption(f"`{report_path}`")
        if st.button("📂 開報告資料夾", key=btn_key, use_container_width=False):
            ok, msg = open_path_in_explorer(report_path.parent)
            if ok:
                st.toast(msg, icon="📂")
            else:
                st.session_state["last_error"] = msg
    else:
        st.download_button(
            "⬇️ 下載報告 (.md)",
            data=report,
            file_name=dl_name,
            mime="text/markdown",
            key=btn_key,
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


def resolve_backtest_symbol(
    typed: str,
    *,
    tv_sym: str = "",
    fallback: str = "",
) -> str:
    """留空 → TV chart；再 fallback sidebar/CSV 代號。"""
    s = short_symbol(typed)
    if s:
        return s
    tv = short_symbol(tv_sym)
    if tv:
        return tv
    return short_symbol(fallback)


def render_backtest_panel(
    *,
    fallback_sym: str = "",
    tv_sym: str = "",
    key_prefix: str = "bt",
) -> str:
    """回測單日 + 日期區間掃描。Returns typed symbol (may be empty)."""
    tv_label = short_symbol(tv_sym)
    st.markdown("**📅 回測（as-of）**")
    st.caption(
        "只用該日及之前 K 線；**入場日前一日** 出信號（例：4/22 入場 → 回測 4/21）。"
        " 代號**留空** = 用 TV chart 而家睇緊嗰隻。"
    )
    if tv_label:
        st.caption(f"📺 TV chart：**{tv_label}**")
    sym = st.text_input(
        "股票代號",
        value="",
        placeholder=(
            f"留空 → TV（{tv_label}）"
            if tv_label
            else (f"留空 → {fallback_sym}" if fallback_sym else "MU、NVDA、WOLF…")
        ),
        key=f"{key_prefix}_symbol",
    ).strip().upper()

    def _eff() -> str:
        return resolve_backtest_symbol(sym, tv_sym=tv_sym, fallback=fallback_sym)

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
        eff = _eff()
        if not eff:
            st.session_state["last_error"] = "請輸入代號，或連接 TV（CDP 9222）"
            return
        run_yfinance_analysis(eff, as_of=as_of if use_bt else None)

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
            eff = _eff()
            if not eff:
                st.session_state["last_error"] = "請輸入代號，或連接 TV（CDP 9222）"
            else:
                run_backtest_scan(eff, scan_start, scan_end, grade_mode=grade_mode, key_prefix=key_prefix)

        rows = st.session_state.get(f"{key_prefix}_backtest_scan_rows") or []
        scan_sym = st.session_state.get(f"{key_prefix}_backtest_scan_sym") or _eff()
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
    render_backtest_panel(fallback_sym=default_sym, key_prefix="cloud_bt")


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


def render_other_local_tools(
    connected: bool,
    chart_sym: str,
    symbol_override: str | None,
    csv_sym: str,
    uploaded_csvs,
    uploaded_zip,
    *,
    key_prefix: str,
) -> None:
    st.subheader("📅 回測 · yfinance")
    render_backtest_panel(
        fallback_sym=(symbol_override or csv_sym or "").upper(),
        tv_sym=chart_sym if connected else "",
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
    if st.session_state.get("screener_path"):
        st.caption(f"CSV 路徑（First Screen 區設定）：`{st.session_state['screener_path']}`")
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
            path_text=st.session_state.get("screener_path", ""),
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
                render_screener_results(prev, key_prefix=f"{key_prefix}_screener")
    elif _screener().list_tv_export_dirs():
        with st.expander("📥 TV Watchlist 匯入（已有匯出）", expanded=False):
            render_tv_watchlist_import(key_prefix=f"{key_prefix}_tv_import")

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
    chart_sym: str,
    symbol_override: str | None,
    csv_sym: str,
    uploaded_csvs,
    uploaded_zip,
    *,
    key: str,
) -> None:
    with st.expander("🛠️ 其他工具（TV / 9-edge / Screener）", expanded=False, key=key):
        render_other_local_tools(
            connected,
            chart_sym,
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

    render_first_screen_section(screener_upload=uploaded_csvs)

    has_report = render_report_zone()

    ensure_ui_pref("tools_other_visible")
    if st.toggle(
        "🛠️ 顯示其他工具（TV / 9-edge / Screener / 回測）",
        key="tools_other_visible",
    ):
        render_other_local_tools(
            connected,
            chart_sym,
            symbol_override,
            csv_sym,
            uploaded_csvs,
            uploaded_zip,
            key_prefix="tools",
        )

    persist_ui_prefs()

    if logs := st.session_state.get("last_logs"):
        with st.expander("📋 執行 log", expanded=bool(st.session_state.get("last_error"))):
            st.code("\n".join(logs))

    if not has_report:
        st.info("撳 **🌱 開始 First Screen** 或 Sidebar **報告庫** 載入摘要。")


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
