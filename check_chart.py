#!/usr/bin/env python3
"""
9-Edge TradingView Chart Checker (local)

Analyze a TradingView screenshot and produce a 9-edge checklist + trade plan.

Usage:
  python check_chart.py --image chart.png --symbol ETN
  python check_chart.py --paste --symbol ETN          # paste image from clipboard (Windows)
  python check_chart.py --image d1.png --image h4.png --symbol ETN
  python check_chart.py --manual --symbol ETN         # no vision, interactive checklist

Vision backends (auto-detect):
  1. OPENAI_API_KEY  -> OpenAI gpt-4o-mini
  2. Ollama running  -> 9edge-chart (custom) or llava

Setup Ollama:
  powershell -ExecutionPolicy Bypass -File ollama\\setup_ollama.ps1
  ollama pull llava
  ollama create 9edge-chart -f ollama\\Modelfile

Training (few-shot, no GPU):
  python add_training_example.py -i chart.png --interactive -s ETN
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

try:
    from PIL import Image, ImageGrab
except ImportError:
    print("Install dependencies: pip install -r requirements.txt")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
SCORECARD = ROOT / "9edge_scorecard_template.csv"
EXAMPLES_DIR = ROOT / "training_examples"
DEFAULT_OLLAMA_MODEL = "9edge-chart"

EDGES = [
    ("momentum_trend", "1. Momentum & Trend", "D1 above EMA20/50, EMA20 > EMA50"),
    ("sr", "2. S&R", "Entry near support, or breakout + successful retest"),
    ("csp_pa_vol", "3. CSP + PA + VOL", "Consolidation, clear trigger, volume expansion"),
    ("mtf", "4. MTF", "D1 and H4 direction aligned"),
    ("rs", "5. RS", "Outperform SPY/QQQ (1-3 months)"),
    ("rrs", "6. R&R&S", "RR >= 2 with structure-based stop"),
    ("board_edge", "7. Board Market Edge", "Leading sector/industry group"),
    ("ft", "8. F.T.", "Follow-through after trigger"),
    ("mi", "9. M.I.", "MACD breakout-only (W1 first)"),
]

SYSTEM_PROMPT = """You are a US swing-trading analyst using the 9-edge confluence model.
Analyze TradingView chart screenshot(s). Extract visible data: symbol, timeframe, OHLC, volume vs average, MA values, trend, support/resistance, candle patterns.

Score each edge: pass (1), fail (0), or uncertain (0 with note).
Rules:
- A setup: total >= 7 AND edges 1-5 ALL pass -> tradable
- B setup: total = 6 OR missing edge 1-5 -> watch only
- C setup: total <= 5 -> skip
- Hard entry: must pass 1-5, plus any 2 of 6-9, RR >= 2
- If only D1 provided, mark MTF (edge 4) as uncertain
- If price is chasing (far from support after bounce), fail edge 2 S&R
- Risk: 1% per trade

CHART READING (do this FIRST before scoring):
1. Read top-left header: symbol, O/H/L/C, change%
2. Read volume line: current vs average (e.g. 3.72M vs 2.48M)
3. Read MA legend numbers on left (e.g. 400.75, 402.49, 405.10, 368.81)
4. Compare CLOSE price to each MA number — if close > all short MAs, edge1 PASS
5. Do NOT guess — use visible numbers only

CRITICAL OUTPUT RULES:
- NEVER use "..." or ellipsis in any note field
- Each note: 1-2 sentences in Traditional Chinese, cite specific chart data (price, MA, volume)
- summary_zh: one real sentence about trade decision, NOT placeholder text
- Do NOT use double-quote character inside note strings (use single quotes or no quotes)

Respond ONLY with valid JSON (no markdown fences). Use numbers read FROM THE CHART only:
{
  "symbol": "TICKER_FROM_CHART",
  "timeframe": "D1",
  "price": 0,
  "change_pct": 0,
  "volume": "",
  "volume_avg": "",
  "edges": {
    "momentum_trend": {"score": 0, "note": "繁中說明+圖上數字"},
    "sr": {"score": 0, "note": ""},
    "csp_pa_vol": {"score": 0, "note": ""},
    "mtf": {"score": 0, "note": ""},
    "rs": {"score": 0, "note": ""},
    "rrs": {"score": 0, "note": ""},
    "board_edge": {"score": 0, "note": ""},
    "ft": {"score": 0, "note": ""},
    "mi": {"score": 0, "note": ""}
  },
  "total_score": 0,
  "grade": "",
  "decision": "",
  "skip_reason": null,
  "entry_plan": {
    "preferred": "retest or breakout",
    "entry": 0,
    "stop": 0,
    "tp1": 0,
    "tp2": 0,
    "rr": 0
  },
  "summary_zh": ""
}"""


def load_symbol_example(symbol: str) -> tuple[Path | None, dict | None]:
    """Find gold-standard image+json for a symbol in training_examples/."""
    if not symbol or not EXAMPLES_DIR.exists():
        return None, None
    sym = symbol.upper()
    for jf in sorted(EXAMPLES_DIR.glob(f"{sym}_*.json"), reverse=True):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            img = jf.with_suffix(ext)
            if img.exists():
                return img, data
        return None, data
    return None, None


def load_few_shot_examples(symbol: str = "", max_examples: int = 2) -> str:
    """Load few-shot labels — ONLY for the same symbol (never leak ETN into WOLF)."""
    if not EXAMPLES_DIR.exists():
        return ""
    sym = symbol.upper()
    if sym:
        files = sorted(EXAMPLES_DIR.glob(f"{sym}_*.json"), reverse=True)[:max_examples]
    else:
        files = []
    if not files:
        return (
            "\n\nNo training example for this symbol. "
            "Read prices ONLY from the chart image. Do NOT invent or copy other tickers."
        )

    blocks = [f"\n\nFEW-SHOT for {sym} only (style reference — still read live prices from image):\n"]
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            blocks.append(f"--- {f.stem} ---\n{json.dumps(data, ensure_ascii=False)}\n")
        except (json.JSONDecodeError, OSError):
            continue
    return "".join(blocks)


def fetch_market_facts(symbol: str) -> dict:
    """Fetch live price, volume, cap, RS from yfinance (hybrid mode)."""
    try:
        import yfinance as yf
    except ImportError:
        return {}

    sym = symbol.upper()
    try:
        t = yf.Ticker(sym)
        info = t.info or {}
        hist = t.history(period="3mo")
        spy = yf.Ticker("SPY").history(period="3mo")
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price is None and not hist.empty:
            price = float(hist["Close"].iloc[-1])
        avg_vol = info.get("averageVolume") or info.get("averageVolume10days")
        mcap = info.get("marketCap")
        rs_note = ""
        if not hist.empty and not spy.empty and len(hist) > 5 and len(spy) > 5:
            stock_ret = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
            spy_ret = (spy["Close"].iloc[-1] / spy["Close"].iloc[0] - 1) * 100
            rs_note = f"3M return {stock_ret:.1f}% vs SPY {spy_ret:.1f}%"
        return {
            "symbol": sym,
            "price": round(float(price), 2) if price else None,
            "avg_volume": int(avg_vol) if avg_vol else None,
            "market_cap": int(mcap) if mcap else None,
            "rs_note": rs_note,
        }
    except Exception as exc:
        return {"error": str(exc)}


def apply_hybrid_facts(data: dict, facts: dict) -> dict:
    """Merge yfinance facts; vision model scores patterns only."""
    if not facts or facts.get("error"):
        return data
    if facts.get("price"):
        data["price"] = facts["price"]
    if facts.get("avg_volume"):
        data["volume_avg"] = f"{facts['avg_volume'] / 1e6:.2f}M"
    if facts.get("market_cap"):
        data["market_cap"] = facts["market_cap"]
    if facts.get("rs_note"):
        e = data.setdefault("edges", {}).setdefault("rs", {"score": 0, "note": ""})
        if "vs SPY" in facts["rs_note"]:
            try:
                parts = facts["rs_note"].split("vs SPY")
                stock_ret = float(parts[0].split()[-1].replace("%", ""))
                spy_ret = float(parts[1].strip().replace("%", ""))
                e["score"] = 1 if stock_ret > spy_ret else 0
                e["note"] = facts["rs_note"]
            except (ValueError, IndexError):
                e["note"] = facts["rs_note"]
    data["_hybrid"] = True
    return data


def build_prompt(symbol: str = "", market_facts: dict | None = None) -> str:
    base = SYSTEM_PROMPT + load_few_shot_examples(symbol)
    if market_facts and market_facts.get("price"):
        base += (
            f"\n\nLIVE MARKET DATA (use these numbers for price/volume/RS, do NOT use 0):\n"
            f"{json.dumps(market_facts, ensure_ascii=False)}"
        )
    return base


def encode_image(path: Path) -> str:
    data = path.read_bytes()
    ext = path.suffix.lower().lstrip(".")
    mime = "image/png" if ext == "png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def save_clipboard_image(dest: Path) -> Path:
    img = ImageGrab.grabclipboard()
    if img is None:
        raise SystemExit("Clipboard has no image. Copy a screenshot first (Win+Shift+S).")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    print(f"Saved clipboard image -> {dest}")
    return dest


def call_openai(image_paths: list[Path], model: str) -> dict:
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Analyze {len(image_paths)} chart image(s) for 9-edge scoring. "
                "If multiple timeframes (e.g. D1 + H4), use both for MTF edge."
            ),
        }
    ]
    for p in image_paths:
        b64 = base64.b64encode(p.read_bytes()).decode()
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": build_prompt(symbol)},
            {"role": "user", "content": content},
        ],
        max_tokens=2000,
        temperature=0.2,
    )
    return parse_json_response(resp.choices[0].message.content or "")


def call_ollama(
    image_paths: list[Path], model: str, host: str, symbol: str = "",
    market_facts: dict | None = None,
) -> dict:
    import requests

    # llava supports only ONE image per request — gold example goes in text, not as image
    if len(image_paths) > 1:
        print("Note: sending first chart image only (llava = 1 image limit). For MTF, run D1 and H4 separately.")

    prompt_parts = [build_prompt(symbol, market_facts)]
    _, ex_data = load_symbol_example(symbol)
    if ex_data and ex_data.get("symbol", "").upper() == symbol.upper():
        prompt_parts.append(
            "\n\nGOLD STANDARD LABELS for "
            + ex_data.get("symbol", symbol)
            + " (match this scoring style and note detail):\n"
            + json.dumps(ex_data, ensure_ascii=False)
        )

    prompt_parts.append(
        "\n\nScore the attached TradingView chart. "
        "Read O/H/L/C and MA numbers from top-left and legend. "
        "Return JSON only. Every note: Traditional Chinese with specific numbers."
    )
    prompt = "".join(prompt_parts)

    img_path = image_paths[0]
    images_b64 = [base64.b64encode(img_path.read_bytes()).decode()]

    resp = requests.post(
        f"{host.rstrip('/')}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": images_b64}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 2048},
        },
        timeout=300,
    )
    if not resp.ok:
        detail = resp.text[:500]
        raise RuntimeError(f"Ollama {resp.status_code}: {detail}")
    body = resp.json()
    raw = body["message"]["content"]
    raw_file = REPORTS / "last_ollama_raw.txt"
    REPORTS.mkdir(exist_ok=True)
    data = parse_json_response(raw, raw_path=raw_file)
    if symbol:
        data["symbol"] = symbol.upper()
    return merge_reference_on_parse_fail(data, symbol)


def is_placeholder(text: str) -> bool:
    t = (text or "").strip()
    return t in {"", "...", "…", "...", "n/a", "N/A", "繁體中文一句總結", "一句總結"}


def sanitize_ai_output(data: dict, symbol: str = "") -> dict:
    """Replace lazy AI placeholders with readable fallback text."""
    bad_notes = 0
    _, ref = load_symbol_example(symbol or data.get("symbol", ""))
    ref_ok = ref and ref.get("symbol", "").upper() == (symbol or data.get("symbol", "")).upper()

    for key, label, criteria in EDGES:
        e = data.setdefault("edges", {}).setdefault(key, {"score": 0, "note": ""})
        if is_placeholder(e.get("note", "")):
            bad_notes += 1
            if ref_ok and key in ref.get("edges", {}):
                e["note"] = ref["edges"][key].get("note", criteria)
            else:
                sc = int(e.get("score", 0))
                verdict = "通過" if sc == 1 else "未通過"
                e["note"] = f"（AI 未詳述）{label.split('.', 1)[-1].strip()}：{verdict}。標準：{criteria}"

    plan = data.get("entry_plan") or {}
    if ref_ok and ref.get("entry_plan"):
        ref_plan = ref["entry_plan"]
        if not plan.get("entry") and ref_plan.get("entry"):
            data["entry_plan"] = ref_plan.copy()
            data["_entry_from_reference"] = True

    if is_placeholder(data.get("summary_zh", "")):
        bad_notes += 1
        if ref_ok and ref.get("summary_zh"):
            data["summary_zh"] = ref["summary_zh"]
        else:
            grade = data.get("grade", "?")
            decision = data.get("decision", "?")
            sym = data.get("symbol", "?")
            data["summary_zh"] = f"{sym} 評級 {grade}，決策 {decision}。請參考各 edge 分數同 Entry Plan。"

    if bad_notes:
        data["_ai_warning"] = (
            f"有 {bad_notes} 項 AI 輸出唔完整，已用標準例子/備用說明補充。"
            " 改善：雙擊 加入ETN標準例子.bat 或 升級準確度.bat"
        )
    return data


def repair_json_text(raw: str) -> str:
    """Fix common LLM JSON mistakes."""
    s = raw.strip()
    s = s.replace(""", '"').replace(""", '"').replace("'", "'").replace("'", "'")
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    # Remove control chars that break JSON
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s


def parse_json_response(text: str, raw_path: Path | None = None) -> dict:
    text = (text or "").strip()
    if raw_path:
        raw_path.write_text(text, encoding="utf-8")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON in response:\n{text[:800]}")

    chunk = text[start : end + 1]
    for attempt in (chunk, repair_json_text(chunk)):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue

    # Last resort: extract edge scores with regex
    data: dict = {"edges": {}}
    for key, _, _ in EDGES:
        m = re.search(rf'"{key}"\s*:\s*\{{[^}}]*"score"\s*:\s*(\d)', chunk)
        if m:
            data["edges"][key] = {"score": int(m.group(1)), "note": ""}
    if data["edges"]:
        data["_parse_warning"] = "JSON 部分損壞，已用 regex 提取分數"
        return data

    raise ValueError(f"Invalid JSON:\n{chunk[:800]}")


def detect_contamination(data: dict, symbol: str) -> dict:
    """Flag when output looks copied from another ticker (e.g. ETN prices on WOLF)."""
    sym = (symbol or data.get("symbol", "")).upper()
    price = data.get("price")
    warnings: list[str] = []

    # Known cross-ticker leak patterns from ETN example
    etn_markers = ("414", "421", "407", "398", "405", "ETN", "XLI", "工業板塊")
    # Check only model output fields (not our own warning messages)
    check = {
        "symbol": data.get("symbol"),
        "price": data.get("price"),
        "edges": data.get("edges"),
        "entry_plan": data.get("entry_plan"),
        "summary_zh": data.get("summary_zh"),
    }
    blob = json.dumps(check, ensure_ascii=False)
    if sym == "WOLF" and any(m in blob for m in etn_markers):
        warnings.append("輸出疑似混入 ETN 數據，請重新截 WOLF 圖再分析")
    if sym == "WOLF" and isinstance(price, (int, float)) and (price > 150 or price == 0):
        warnings.append(f"價格 {price} 讀取失敗或唔合理（WOLF 通常 $50-80）")

    if warnings:
        data["_contamination_warning"] = "；".join(warnings)
        data["decision"] = "skip"
        data["grade"] = "C"
        data["skip_reason"] = data["_contamination_warning"]
        data["entry_plan"] = {}
        data["summary_zh"] = f"{sym} 分析失敗：AI 讀錯價格，請重新截圖再 run。參考 reports/{sym}_checklist_正確版.md"
    return data


def merge_reference_on_parse_fail(data: dict, symbol: str) -> dict:
    """When Ollama JSON breaks, use reference ONLY if same symbol."""
    if not data.get("_parse_warning"):
        return data
    _, ref = load_symbol_example(symbol or data.get("symbol", ""))
    if not ref or not ref.get("edges"):
        return data
    if ref.get("symbol", "").upper() != (symbol or data.get("symbol", "")).upper():
        return data
    ref_sym = ref.get("symbol", symbol)
    data["edges"] = json.loads(json.dumps(ref["edges"]))
    data["symbol"] = ref_sym
    data["price"] = ref.get("price")
    data["change_pct"] = ref.get("change_pct")
    data["volume"] = ref.get("volume")
    data["volume_avg"] = ref.get("volume_avg")
    data["summary_zh"] = ref.get("summary_zh", data.get("summary_zh"))
    if ref.get("entry_plan"):
        data["entry_plan"] = ref["entry_plan"].copy()
    data["_parse_warning"] = f"Ollama JSON 損壞，已改用 {ref_sym} 標準例子（圖表相同時可信）"
    return data


def compute_grade(edges: dict) -> tuple[int, str, str]:
    scores = {k: int(edges[k].get("score", 0)) for k, _, _ in EDGES}
    total = sum(scores.values())
    core = [scores["momentum_trend"], scores["sr"], scores["csp_pa_vol"], scores["mtf"], scores["rs"]]
    core_pass = all(s == 1 for s in core)

    if total >= 7 and core_pass:
        return total, "A", "trade"
    if total == 6:
        return total, "B", "watch"
    if total <= 5:
        return total, "C", "skip"
    return total, "B", "watch"  # e.g. 7+ but core edge missing


def format_checklist(data: dict, image_paths: list[Path]) -> str:
    edges = data.get("edges", {})
    total = data.get("total_score")
    grade = data.get("grade", "?")
    decision = data.get("decision", "?")
    symbol = data.get("symbol", "?")
    tf = data.get("timeframe", "?")

    lines = [
        f"# 9-Edge Checklist — {symbol} ({tf})",
        f"Date: {date.today().isoformat()}",
        f"Images: {', '.join(p.name for p in image_paths)}",
        "",
        f"Price: {data.get('price', '—')} | Change: {data.get('change_pct', '—')}%",
        f"Volume: {data.get('volume', '—')} vs avg {data.get('volume_avg', '—')}",
        "",
        "| # | Edge | Score | Notes |",
        "|---|------|-------|-------|",
    ]

    for key, label, _ in EDGES:
        e = edges.get(key, {})
        sc = int(e.get("score", 0))
        icon = "✅" if sc == 1 else "❌" if sc == 0 and "uncertain" not in e.get("note", "").lower() else "⚠️"
        note = e.get("note", "")
        lines.append(f"| {label.split('.')[0].strip()} | {label.split('.', 1)[1].strip()} | {icon} | {note} |")

    lines.extend(
        [
            "",
            f"**Total: {total}/9 | Grade: {grade} | Decision: {decision}**",
            "",
        ]
    )

    if data.get("_parse_warning"):
        lines.append(f"\n> ⚠️ {data['_parse_warning']}")

    if data.get("_contamination_warning"):
        lines.append(f"\n> 🚫 {data['_contamination_warning']}")

    if data.get("_ai_warning"):
        lines.append(f"\n> ⚠️ {data['_ai_warning']}")

    if data.get("skip_reason"):
        lines.append(f"Skip reason: {data['skip_reason']}")

    plan = data.get("entry_plan") or {}
    if plan:
        lines.extend(
            [
                "## Entry Plan",
                f"- Type: {plan.get('preferred', '—')}",
                f"- Entry: {plan.get('entry', '—')}",
                f"- Stop: {plan.get('stop', '—')}",
                f"- TP1 (1R): {plan.get('tp1', '—')}",
                f"- TP2 (2R): {plan.get('tp2', '—')}",
                f"- RR: {plan.get('rr', '—')}",
                "",
            ]
        )

    if data.get("summary_zh"):
        lines.append(f"## 總結\n{data['summary_zh']}")

    lines.append("\n---\n*Generated by check_chart.py*")
    return "\n".join(lines)


def append_scorecard(data: dict) -> None:
    edges = data.get("edges", {})
    plan = data.get("entry_plan") or {}
    row = [
        date.today().isoformat(),
        data.get("symbol", ""),
        plan.get("preferred", ""),
        plan.get("entry", ""),
        plan.get("stop", ""),
        plan.get("tp1", ""),
        plan.get("tp2", ""),
        plan.get("rr", ""),
    ]
    for key, _, _ in EDGES:
        row.append(edges.get(key, {}).get("score", 0))
    row.extend(
        [
            data.get("total_score", ""),
            data.get("decision", ""),
            (data.get("summary_zh") or "")[:80],
        ]
    )
    header_needed = not SCORECARD.exists()
    with SCORECARD.open("a", encoding="utf-8") as f:
        if header_needed:
            f.write(
                "date,symbol,setup_type,entry,stop,tp1,tp2,rr,"
                + ",".join(k for k, _, _ in EDGES)
                + ",total_score,decision,notes\n"
            )
        f.write(",".join(str(x) for x in row) + "\n")
    print(f"Appended row -> {SCORECARD}")


def run_manual(symbol: str, account: float) -> None:
    print(f"\n=== 9-Edge Manual Checklist ({symbol}) ===\n")
    edges: dict[str, dict] = {}
    for key, label, criteria in EDGES:
        print(f"{label}")
        print(f"  Criteria: {criteria}")
        ans = input("  Pass? [y/n/u] ").strip().lower()
        score = 1 if ans == "y" else 0
        note = input("  Note (optional): ").strip()
        edges[key] = {"score": score, "note": note}

    total, grade, decision = compute_grade(edges)
    print(f"\nTotal: {total}/9 | Grade: {grade} | Decision: {decision}")

    entry = float(input("\nEntry price (0 to skip): ") or 0)
    stop = float(input("Stop price: ") or 0)
    plan = {}
    if entry and stop and entry > stop:
        risk = entry - stop
        tp1 = entry + risk
        tp2 = entry + 2 * risk
        rr = 2.0
        shares = int((account * 0.01) / risk) if account else 0
        plan = {"preferred": "manual", "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "rr": rr}
        print(f"TP1: {tp1:.2f} | TP2: {tp2:.2f} | Shares (1% risk): {shares}")

    data = {
        "symbol": symbol,
        "timeframe": "manual",
        "edges": edges,
        "total_score": total,
        "grade": grade,
        "decision": decision,
        "entry_plan": plan,
        "summary_zh": "手動輸入評分",
    }
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"{symbol}_{date.today().isoformat()}_manual.md"
    out.write_text(format_checklist(data, []), encoding="utf-8")
    print(f"Saved -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="9-Edge TradingView chart checker")
    parser.add_argument("--image", "-i", action="append", help="Chart image path (repeat for D1+H4)")
    parser.add_argument("--paste", action="store_true", help="Use image from clipboard")
    parser.add_argument("--symbol", "-s", default="", help="Stock symbol e.g. ETN")
    parser.add_argument("--manual", action="store_true", help="Interactive checklist without vision")
    parser.add_argument("--account", type=float, default=100_000, help="Account size for position calc")
    parser.add_argument("--backend", choices=["auto", "openai", "ollama"], default="auto")
    parser.add_argument("--model", default="", help="OpenAI or Ollama model name")
    parser.add_argument("--ollama-host", default="http://localhost:11434")
    parser.add_argument("--hybrid", action="store_true", help="Use yfinance for price/vol/cap/RS")
    parser.add_argument("--no-save", action="store_true", help="Do not write report files")
    args = parser.parse_args()

    market_facts: dict = {}
    if args.hybrid and args.symbol:
        print(f"Fetching market data for {args.symbol.upper()}...")
        market_facts = fetch_market_facts(args.symbol)
        if market_facts.get("price"):
            print(f"  Price: {market_facts['price']} | RS: {market_facts.get('rs_note', 'n/a')}")
        elif market_facts.get("error"):
            print(f"  yfinance warning: {market_facts['error']}")

    if args.manual:
        sym = args.symbol or input("Symbol: ").strip().upper()
        run_manual(sym, args.account)
        return

    image_paths: list[Path] = []
    sym = (args.symbol or "").strip().upper()

    if args.paste:
        REPORTS.mkdir(exist_ok=True)
        clip_name = f"{sym}_{date.today().isoformat()}.png" if sym else f"clipboard_{date.today().isoformat()}.png"
        clip_path = REPORTS / clip_name
        image_paths.append(save_clipboard_image(clip_path))
    if args.image:
        image_paths.extend(Path(p).resolve() for p in args.image)

    if not image_paths:
        parser.error("Provide --image, --paste, or --manual")

    for p in image_paths:
        if not p.exists():
            raise SystemExit(f"Image not found: {p}")

    backend = args.backend
    if backend == "auto":
        if os.environ.get("OPENAI_API_KEY"):
            backend = "openai"
        else:
            backend = "ollama"

    model = args.model or ("gpt-4o-mini" if backend == "openai" else DEFAULT_OLLAMA_MODEL)

    n_examples = len(list(EXAMPLES_DIR.glob("*.json"))) if EXAMPLES_DIR.exists() else 0
    ex_img, ex_data = load_symbol_example(args.symbol)
    print(f"Analyzing {len(image_paths)} image(s) via {backend} ({model})...")
    if ex_data:
        print(f"Gold example JSON loaded: {ex_data.get('symbol', args.symbol)}")
    elif n_examples:
        print(f"Few-shot JSON examples: {min(n_examples, 3)}")
    try:
        if backend == "openai":
            data = call_openai(image_paths, model)
        else:
            data = call_ollama(
                image_paths, model, args.ollama_host,
                symbol=args.symbol, market_facts=market_facts or None,
            )
    except Exception as exc:
        print(f"\nVision backend failed: {exc}")
        print("\nFallback options:")
        print("  1. Set OPENAI_API_KEY and rerun")
        print("  2. Run: powershell -ExecutionPolicy Bypass -File ollama\\setup_ollama.ps1")
        print("  3. Run: python check_chart.py --manual --symbol ETN")
        sys.exit(1)

    if args.symbol:
        data["symbol"] = args.symbol.upper()

    if args.hybrid and market_facts:
        data = apply_hybrid_facts(data, market_facts)

    data = sanitize_ai_output(data, symbol=args.symbol or data.get("symbol", ""))
    edges = data.get("edges", {})
    total, grade, decision = compute_grade(edges)
    data["total_score"] = total
    data["grade"] = grade
    data["decision"] = decision
    data = detect_contamination(data, args.symbol or data.get("symbol", ""))

    report = format_checklist(data, image_paths)
    print("\n" + report)

    if not args.no_save:
        REPORTS.mkdir(exist_ok=True)
        sym = data.get("symbol", "UNKNOWN")
        out = REPORTS / f"{sym}_{date.today().isoformat()}_checklist.md"
        out.write_text(report, encoding="utf-8")
        json_out = REPORTS / f"{sym}_{date.today().isoformat()}_checklist.json"
        json_out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved -> {out}")
        print(f"Saved -> {json_out}")
        append_scorecard(data)


if __name__ == "__main__":
    main()
