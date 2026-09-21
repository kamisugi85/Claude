# plan-detail-fetchの候補から指定件数だけ実際に詳細ページを取得する(Windows / PowerShell用)。
# 使い方: .\scripts\fetch-details.ps1 -Limit 50
param([int]$Limit = 20)
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli fetch-details --limit $Limit
