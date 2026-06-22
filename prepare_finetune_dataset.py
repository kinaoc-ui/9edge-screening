#!/usr/bin/env python3
"""
Export training_examples/ to JSONL for vision fine-tuning (LLaMA-Factory / similar).

Usage:
  python prepare_finetune_dataset.py
  python prepare_finetune_dataset.py --out finetune_data/9edge_train.jsonl

Each line = one (image path, user prompt, assistant JSON response).
Collect 20-50+ pairs before real fine-tune.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "training_examples"
PROMPT = (
    "Analyze this TradingView D1 chart using the 9-edge US swing system. "
    "Return JSON only with symbol, price, edges 1-9 scores and Traditional Chinese notes, "
    "entry_plan, summary_zh."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "finetune_data" / "9edge_train.jsonl"))
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for jf in sorted(EXAMPLES.glob("*.json")):
        try:
            label = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        img = None
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            p = jf.with_suffix(ext)
            if p.exists():
                img = p
                break
        if not img:
            print(f"Skip {jf.name}: no matching image")
            continue
        rows.append(
            {
                "id": jf.stem,
                "image": str(img.resolve()),
                "conversations": [
                    {"from": "human", "value": f"<image>\n{PROMPT}"},
                    {"from": "gpt", "value": json.dumps(label, ensure_ascii=False)},
                ],
            }
        )

    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Exported {len(rows)} examples -> {out_path}")
    if len(rows) < 10:
        print("Need 10+ examples for few-shot quality, 30-50+ for real LoRA fine-tune.")


if __name__ == "__main__":
    main()
