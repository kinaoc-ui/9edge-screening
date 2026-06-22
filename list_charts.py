#!/usr/bin/env python3
"""List chart images ready in charts/ folder."""

from pathlib import Path
import re

CHARTS = Path(__file__).resolve().parent / "charts"
EXT = {".png", ".jpg", ".jpeg", ".webp"}


def parse_symbol(name: str) -> str:
    base = Path(name).stem.upper()
    m = re.match(r"^([A-Z0-9.^-]+)", base)
    return m.group(1) if m else base


def main() -> None:
    if not CHARTS.exists():
        print("charts/ folder not found")
        return

    files = sorted(f for f in CHARTS.iterdir() if f.suffix.lower() in EXT)
    if not files:
        print(f"No images in {CHARTS}")
        print("Drop files like ETN_D1.png, WOLF_D1.png")
        return

    by_sym: dict[str, list[str]] = {}
    for f in files:
        sym = parse_symbol(f.name)
        by_sym.setdefault(sym, []).append(f.name)

    print(f"Found {len(files)} image(s), {len(by_sym)} symbol(s):\n")
    for sym in sorted(by_sym):
        print(f"  {sym}: {', '.join(by_sym[sym])}")
    print(f"\nFolder: {CHARTS}")


if __name__ == "__main__":
    main()
