# 297件から高性能AI評価に回す60件をPythonのみで抽出する
# (EPCあり45件+EPCなし15件、AI/LLM不使用、Program Masterからは削除しない、Windows / PowerShell用)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli select-ai-review
