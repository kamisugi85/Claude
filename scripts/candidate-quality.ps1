# 候補300件だけを対象に取得件数/欠損率と見出し出現頻度を集計する(Windows / PowerShell用)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli candidate-quality
