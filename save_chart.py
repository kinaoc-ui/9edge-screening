#!/usr/bin/env python3
"""
Save TradingView screenshots from clipboard to charts/ folder.

Per stock (enter symbol ONCE):
  1st paste -> SYMBOL_D1.png
  2nd paste -> SYMBOL_H4.png

Usage:
  python save_chart.py              # continuous pair mode (30 stocks)
  python save_chart.py --symbol ETN # one stock: D1 then H4
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    from PIL import ImageGrab
except ImportError:
    print("Run: pip install pillow")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
CHARTS = ROOT / "charts"
EXT = ".png"
PAIR = [("D1", "D1 日線"), ("H4", "H4 四小時")]


def grab_clipboard():
    img = ImageGrab.grabclipboard()
    if img is None:
        return None
    return img


def save_path(symbol: str, tf: str) -> Path:
    CHARTS.mkdir(exist_ok=True)
    sym = symbol.strip().upper()
    tf = tf.strip().upper()
    path = CHARTS / f"{sym}_{tf}{EXT}"
    if not path.exists():
        return path
    stamp = datetime.now().strftime("%H%M%S")
    return CHARTS / f"{sym}_{tf}_{stamp}{EXT}"


def save_one(symbol: str, tf: str) -> Path | None:
    img = grab_clipboard()
    if img is None:
        print("  剪貼簿冇圖，跳過。請 Win+Shift+S 截圖後再 Enter。")
        return None
    path = save_path(symbol, tf)
    img.save(path, "PNG")
    print(f"  已儲存 -> {path.name}")
    return path


def save_pair(symbol: str) -> int:
    """D1 then H4 for one symbol. Returns number saved."""
    sym = symbol.strip().upper()
    if not sym:
        return 0
    print(f"\n--- {sym} ---")
    saved = 0
    for tf, label in PAIR:
        input(f"  [{label}] Win+Shift+S 截圖後按 Enter...")
        if save_one(sym, tf):
            saved += 1
    return saved


def run_pairs() -> None:
    print("\n=== 連續貼圖（每隻股：D1 → H4）===\n")
    print("每隻股只輸入一次代號：")
    print("  第 1 次 Enter = D1 圖")
    print("  第 2 次 Enter = H4 圖\n")
    total = 0
    stocks = 0
    while True:
        sym = input("股票代號 (Enter 空/q=退出): ").strip()
        if sym.lower() in ("q", "quit", "exit") or sym == "":
            break
        n = save_pair(sym)
        total += n
        stocks += 1
    print(f"\n完成：{stocks} 隻股，共 {total} 張圖")
    print(f"Folder: {CHARTS}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Save D1+H4 chart pair to charts/")
    parser.add_argument("--symbol", "-s", help="One stock: save D1 then H4")
    parser.add_argument("--loop", "-l", action="store_true", help="Continuous pair mode")
    args = parser.parse_args()

    CHARTS.mkdir(exist_ok=True)

    if args.loop or (not args.symbol and len(sys.argv) == 1):
        run_pairs()
        return

    sym = args.symbol or input("股票代號: ").strip()
    if not sym:
        raise SystemExit("需要代號")
    save_pair(sym)


if __name__ == "__main__":
    main()
