# Ollama setup for 9-edge chart checker
# Run in PowerShell from screening folder:
#   powershell -ExecutionPolicy Bypass -File ollama\setup_ollama.ps1

Write-Host "=== 9-Edge Ollama Setup ===" -ForegroundColor Cyan

# 1. Check Ollama
try {
    $ver = ollama --version 2>&1
    Write-Host "Ollama found: $ver" -ForegroundColor Green
} catch {
    Write-Host "Ollama not installed. Download: https://ollama.com/download" -ForegroundColor Red
    Write-Host "After install, rerun this script."
    exit 1
}

# 2. Pull base vision model (choose one)
Write-Host "`nPulling vision model (llava 7B - faster, ~4GB)..." -ForegroundColor Yellow
Write-Host "For better accuracy use: ollama pull llava:13b (slower, ~8GB)" -ForegroundColor Gray
ollama pull llava

# 3. Create custom 9edge-chart model from Modelfile
$modelfile = Join-Path $PSScriptRoot "Modelfile"
if (-not (Test-Path $modelfile)) {
    Write-Host "Modelfile not found at $modelfile" -ForegroundColor Red
    exit 1
}

Write-Host "`nCreating custom model '9edge-chart' from Modelfile..." -ForegroundColor Yellow
ollama create 9edge-chart -f $modelfile

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host @"

Test:
  cd $(Split-Path $PSScriptRoot -Parent)
  python check_chart.py --backend ollama --model 9edge-chart --paste --symbol ETN

Better accuracy (if RAM allows):
  ollama pull llava:13b
  Edit ollama\Modelfile: change FROM llava:13b
  ollama create 9edge-chart -f ollama\Modelfile

Training (practical path):
  1. Run checker, if wrong -> python add_training_example.py -i chart.png --interactive -s ETN
  2. Collect 10-20 examples in training_examples/
  3. Examples auto-inject into prompt (few-shot) - no GPU training needed

Real fine-tune (advanced, optional):
  Needs 50+ labeled charts + GPU 8GB+. Not recommended until few-shot maxed out.

"@
