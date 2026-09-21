# ランキングに使える項目の取得件数/欠損率を確認する(Windows / PowerShell用)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli coverage
