# 収集済みデータのサマリとサンプルを表示する(Windows / PowerShell用)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli inspect
