#!/usr/bin/env python3
"""Draw W1 UTL/DTL channel + S/R levels on TradingView (local, via MCP CLI or manual JSON)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from analyze_tv_csv import (  # noqa: E402
    CHANNEL_PROJECT_W1,
    TF_MIN_BARS,
    channel_ref_to_tv_shapes,
    compute_pivot_sr_bands,
    compute_w1_channel_reference,
    load_tv_csv,
    parse_bars,
    pivot_sr_bands_to_tv_shapes,
)

_YF_INTERVAL = {"W1": ("1wk", "3y"), "D1": ("1d", "1y"), "H1": ("1h", "60d")}


def _parse_times_from_rows(rows: list[dict]) -> list[int]:
    times: list[int] = []
    for row in rows:
        tkey = next((k for k in row if "time" in k.lower() or "date" in k.lower()), None)
        if not tkey:
            break
        raw = row[tkey]
        try:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
                try:
                    times.append(int(datetime.strptime(raw[:19], fmt).timestamp()))
                    break
                except ValueError:
                    continue
            else:
                times.append(int(float(raw)))
        except (ValueError, TypeError):
            pass
    return times


def load_bars_from_yfinance(symbol: str, tf: str) -> tuple[list[dict], list[int]]:
    import yfinance as yf

    interval, period = _YF_INTERVAL[tf]
    df = yf.Ticker(symbol).history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"No {tf} data for {symbol}")
    bars: list[dict] = []
    times: list[int] = []
    for ts, row in df.iterrows():
        bars.append({
            "open": float(row.Open),
            "high": float(row.High),
            "low": float(row.Low),
            "close": float(row.Close),
            "volume": float(row.Volume),
        })
        times.append(int(ts.timestamp()))
    return bars, times


def load_bars_from_csv(symbol: str, tf: str, csv_dir: Path) -> tuple[list[dict], list[int]]:
    path = csv_dir / f"{symbol.upper()}_{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    rows = load_tv_csv(path)
    bars = parse_bars(rows, min_bars=TF_MIN_BARS.get(tf, 20))
    times = _parse_times_from_rows(rows)
    if len(times) != len(bars):
        times = []
    return bars, times


def load_tf_bars(symbol: str, tf: str, csv_dir: Path) -> tuple[list[dict], list[int], str]:
    try:
        return *load_bars_from_csv(symbol, tf, csv_dir), "CSV"
    except (FileNotFoundError, ValueError):
        return *load_bars_from_yfinance(symbol, tf), "yfinance"


def print_channel_summary(symbol: str, channel_ref: dict) -> None:
    utl = channel_ref.get("utl_line")
    dtl = channel_ref.get("dtl_line")
    print(f"\n=== {symbol} W1 UTL/DTL Trendlines (primary: {channel_ref.get('primary', 'none')}) ===")
    if utl:
        p1, p2 = utl["p1"], utl["p2"]
        n = utl.get("chain_len", 2)
        print(
            f"UTL (support, {n} pivot lows): ${p1[1]:.2f} ({utl.get('p1_date')}) -> "
            f"${p2[1]:.2f} ({utl.get('p2_date')})"
        )
        print(
            f"  Today: ${utl['line_now']:.2f} | "
            f"+{utl['project_bars']}W ({utl.get('future_date')}): ${utl['line_future']:.2f}"
        )
    else:
        print("UTL: (none)")
    if dtl:
        p1, p2 = dtl["p1"], dtl["p2"]
        n = dtl.get("chain_len", 2)
        print(
            f"DTL (resistance, {n} pivot highs): ${p1[1]:.2f} ({dtl.get('p1_date')}) -> "
            f"${p2[1]:.2f} ({dtl.get('p2_date')})"
        )
        print(
            f"  Today: ${dtl['line_now']:.2f} | "
            f"+{dtl['project_bars']}W ({dtl.get('future_date')}): ${dtl['line_future']:.2f}"
        )
    else:
        print("DTL: (none)")
    if not utl and not dtl:
        print("No valid W1 trendlines detected.")


def print_sr_levels_table(levels: list[dict]) -> None:
    if not levels:
        print("\n=== S/R Levels ===\n(none)")
        return
    print("\n=== S/R Levels (drawn) ===")
    print(f"{'Kind':<12} {'TF':<4} {'Price':>10}  Label")
    print("-" * 72)
    for lv in levels:
        kind = lv["kind"]
        tf = lv.get("tf", "")
        if kind == "zone":
            price = f"{lv['zone_lo']:.2f}-{lv['zone_hi']:.2f}"
        else:
            price = f"{lv['price']:.2f}"
        print(f"{kind:<12} {tf:<4} {price:>10}  {lv['label']}")


def tv_cli(*cli_args: str) -> dict:
    """Invoke tradingview-mcp CLI (requires TV Desktop + CDP)."""
    node = Path.home() / "tradingview-mcp" / "src" / "cli" / "index.js"
    if not node.exists():
        raise FileNotFoundError(f"TV MCP CLI not found: {node}")
    proc = subprocess.run(
        ["node", str(node), *cli_args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "TV CLI failed")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout}


def shape_spec_to_cli(spec: dict) -> list[str]:
    """Map draw_shape spec → tv draw shape CLI args."""
    args = ["draw", "shape", "--type", spec["shape"]]
    pt = spec["point"]
    args.extend(["--time", str(int(pt["time"])), "--price", str(pt["price"])])
    if pt2 := spec.get("point2"):
        args.extend(["--time2", str(int(pt2["time"])), "--price2", str(pt2["price"])])
    if text := spec.get("text"):
        args.extend(["--text", text])
    if overrides := spec.get("overrides"):
        args.extend(["--overrides", overrides])
    return args


def mcp_call(tool: str, arguments: dict) -> dict:
    """Backward-compatible wrapper for legacy tool names."""
    if tool == "chart_set_symbol":
        return tv_cli("symbol", str(arguments["symbol"]))
    if tool == "chart_set_timeframe":
        return tv_cli("timeframe", str(arguments["timeframe"]))
    if tool == "draw_clear":
        return tv_cli("draw", "clear")
    if tool == "draw_shape":
        return tv_cli(*shape_spec_to_cli(arguments))
    if tool == "capture_screenshot":
        args = ["screenshot", "--region", arguments.get("region", "chart")]
        if fn := arguments.get("filename"):
            args.extend(["--output", fn])
        return tv_cli(*args)
    raise ValueError(f"Unknown TV tool: {tool}")


def build_sr_shapes(
    symbol: str,
    *,
    csv_dir: Path | None = None,
) -> tuple[list[dict], list[dict], str]:
    """Compute W1 pivot S/R band shapes from CSV (or yfinance fallback)."""
    base = csv_dir or ROOT / "charts" / "csv"
    w1_bars, w1_times, src = load_tf_bars(symbol.upper(), "W1", base)
    close = w1_bars[-1]["close"]
    sr_bands = compute_pivot_sr_bands(w1_bars)
    shapes, levels = pivot_sr_bands_to_tv_shapes(sr_bands, w1_times or [], close=close)
    return shapes, levels, src


def draw_sr_bands_for_symbol(
    symbol: str,
    *,
    tv_symbol: str | None = None,
    csv_dir: Path | None = None,
    clear: bool = False,
) -> tuple[int, str]:
    """Draw pivot S/R bands on TradingView W1. Returns (band_count, log line)."""
    sym = symbol.upper()
    shapes, levels, src = build_sr_shapes(sym, csv_dir=csv_dir)
    if not shapes:
        return 0, f"S/R: no bands for {sym} ({src})"
    draw_on_tv(tv_symbol or sym, shapes, clear=clear, screenshot=f"{sym}_sr_bands")
    return len(levels), f"S/R: drew {len(levels)} bands on {sym} W1 ({src})"


def draw_on_tv(
    symbol: str,
    shapes: list[dict],
    *,
    clear: bool = False,
    screenshot: str | None = None,
) -> str | None:
    mcp_call("chart_set_symbol", {"symbol": symbol.upper()})
    mcp_call("chart_set_timeframe", {"timeframe": "W"})
    if clear:
        mcp_call("draw_clear", {})
    for spec in shapes:
        args = {k: v for k, v in spec.items() if k != "label"}
        mcp_call("draw_shape", args)
    shot_name = screenshot or f"{symbol}_levels"
    result = mcp_call("capture_screenshot", {"region": "chart", "filename": shot_name})
    return (result or {}).get("path") or (result or {}).get("filename") or shot_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Draw W1 S/R bands on TradingView (trendlines: draw yourself or --trendlines)",
    )
    parser.add_argument("symbol", nargs="?", default="CVCO")
    parser.add_argument("--csv-dir", type=Path, default=ROOT / "charts" / "csv")
    parser.add_argument("--project-bars", type=int, default=CHANNEL_PROJECT_W1)
    parser.add_argument("--draw", action="store_true", help="Send shapes to TradingView via MCP")
    parser.add_argument(
        "--trendlines",
        action="store_true",
        help="Also draw auto UTL/DTL (default: off — draw trendlines yourself on TV)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear all chart drawings before draw (default: keep your manual lines)",
    )
    parser.add_argument(
        "--sr",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Draw S/R levels (default: on with --draw)",
    )
    parser.add_argument("--json", action="store_true", help="Print draw_shape JSON only")
    parser.add_argument("--max-sr", type=int, default=15, help="Max S/R levels to draw")
    args = parser.parse_args()
    if args.sr is None:
        args.sr = args.draw
    sym = args.symbol.upper()

    w1_bars, w1_times, w1_src = load_tf_bars(sym, "W1", args.csv_dir)
    channel_ref = None
    if args.trendlines:
        channel_ref = compute_w1_channel_reference(
            w1_bars, times=w1_times or None, project_bars=args.project_bars,
        )
        print_channel_summary(sym, channel_ref)
    else:
        print(f"\n=== {sym} W1 ===\nTrendlines: skipped (draw manually on TV)")
    print(f"W1 data source: {w1_src}")

    shapes: list[dict] = []
    sr_levels: list[dict] = []

    if args.trendlines and w1_times and channel_ref:
        shapes.extend(channel_ref_to_tv_shapes(channel_ref, w1_times))

    if args.sr:
        close = w1_bars[-1]["close"]
        sr_bands = compute_pivot_sr_bands(w1_bars)
        sr_shapes, sr_levels = pivot_sr_bands_to_tv_shapes(
            sr_bands, w1_times or [], close=close,
        )
        shapes.extend(sr_shapes)
        print_sr_levels_table(sr_levels)
        print(
            f"S/R: Pivot L/R=10, ATRx0.2, min 3 touches — "
            f"W1 close=${close:.2f}, {len(sr_bands)} bands"
        )

    if args.json:
        print(json.dumps(shapes, indent=2))
        return

    if args.draw:
        if not shapes:
            print("No shapes to draw.", file=sys.stderr)
            sys.exit(1)
        try:
            shot = draw_on_tv(
                sym, shapes, clear=args.clear, screenshot=f"{sym}_sr_bands",
            )
            ch = sum(1 for s in shapes if s.get("shape") == "trend_line")
            sr_n = len(sr_levels) if args.sr else 0
            print(
                f"Drawn {len(shapes)} shapes on {sym} W1 "
                f"({ch} channel lines, {sr_n} S/R levels) — check TradingView."
            )
            if shot:
                print(f"Screenshot: {shot}")
        except FileNotFoundError as exc:
            print(f"TV MCP unavailable: {exc}", file=sys.stderr)
            print("\nFallback — draw_shape JSON:", file=sys.stderr)
            print(json.dumps(shapes, indent=2))
            if sr_levels:
                print_sr_levels_table(sr_levels)
            sys.exit(2)
        except RuntimeError as exc:
            print(f"TV draw failed: {exc}", file=sys.stderr)
            print(json.dumps(shapes, indent=2))
            sys.exit(2)
    else:
        ch = sum(1 for s in shapes if s.get("shape") == "trend_line")
        sr_n = len(sr_levels) if args.sr else 0
        print(
            f"\n{len(shapes)} shapes ready ({ch} trendlines, {sr_n} S/R). "
            "Use --draw [--clear] to send S/R to TV; add --trendlines for auto UTL/DTL."
        )


if __name__ == "__main__":
    main()
