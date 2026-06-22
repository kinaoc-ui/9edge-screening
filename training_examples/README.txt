# Add a corrected training example (few-shot learning data)

When AI scores a chart wrong, save YOUR corrected labels here.
These examples are injected into the prompt — no GPU training needed.

## Usage

```powershell
# After you manually fix a bad AI result:
python add_training_example.py --image etn.png --labels corrected.json

# Or interactive: paste image path, then answer y/n for each edge
python add_training_example.py --image etn.png --interactive --symbol ETN
```

## Goal

Collect 10-20 labeled TradingView screenshots from YOUR course style.
Accuracy improves as examples grow (few-shot), without fine-tuning.

## Files

- `training_examples/*.json` — your corrected labels
- `training_examples/*.png` — matching chart images (optional copy)

## When to upgrade to real fine-tuning

Only if you have 50+ labeled charts AND a GPU (8GB+ VRAM).
See `ollama/setup_ollama.ps1` for Modelfile approach (recommended first).
