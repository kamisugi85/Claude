# AI不使用、Pythonルールのみでカタログ/shortlistを一次選別する(Windows / PowerShell用)。
# ネットワーク・ブラウザ操作は行わない。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli screen
