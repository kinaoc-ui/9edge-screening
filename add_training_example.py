#!/usr/bin/env python3
"""Save a corrected 9-edge label as a training example for few-shot prompting."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "training_examples"

EDGES = [
    "momentum_trend", "sr", "csp_pa_vol", "mtf", "rs",
    "rrs", "board_edge", "ft", "mi",
]


def interactive_labels(symbol: str) -> dict:
    print(f"\n=== Corrected labels for {symbol} ===\n")
    edges = {}
    for key in EDGES:
        ans = input(f"{key} pass? [y/n]: ").strip().lower()
        note = input(f"  note: ").strip()
        edges[key] = {"score": 1 if ans == "y" else 0, "note": note}

    entry = float(input("\nEntry (0 skip): ") or 0)
    stop = float(input("Stop: ") or 0)
    plan = {}
    if entry and stop:
        r = entry - stop
        plan = {
            "preferred": input("Setup type [retest/breakout]: ").strip() or "retest",
            "entry": entry,
            "stop": stop,
            "tp1": entry + r,
            "tp2": entry + 2 * r,
            "rr": 2.0,
        }

    summary = input("Summary (中文): ").strip()
    return {
        "symbol": symbol.upper(),
        "timeframe": input("Timeframe [D1]: ").strip() or "D1",
        "edges": edges,
        "entry_plan": plan,
        "summary_zh": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", "-i", required=True, help="Chart screenshot")
    parser.add_argument("--labels", "-l", help="JSON file with corrected labels")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--symbol", "-s", default="")
    args = parser.parse_args()

    img = Path(args.image).resolve()
    if not img.exists():
        raise SystemExit(f"Image not found: {img}")

    EXAMPLES.mkdir(exist_ok=True)

    if args.interactive:
        sym = args.symbol or input("Symbol: ").strip().upper()
        data = interactive_labels(sym)
    elif args.labels:
        data = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    else:
        raise SystemExit("Use --interactive or --labels")

    sym = data.get("symbol", "UNKNOWN").upper()
    stem = f"{sym}_{date.today().isoformat()}"
    json_path = EXAMPLES / f"{stem}.json"
    img_path = EXAMPLES / f"{stem}{img.suffix}"

    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(img, img_path)

    total = sum(int(data["edges"][k]["score"]) for k in EDGES)
    print(f"\nSaved example:")
    print(f"  {json_path}")
    print(f"  {img_path}")
    print(f"  Total score: {total}/9")
    print(f"\nYou now have {len(list(EXAMPLES.glob('*.json')))} examples.")
    print("Rerun: python check_chart.py --backend ollama --model 9edge-chart ...")


if __name__ == "__main__":
    main()
