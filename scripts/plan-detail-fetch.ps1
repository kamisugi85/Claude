# 詳細ページ追加取得の候補母集団を選定する(実際の取得は行わない)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli plan-detail-fetch
