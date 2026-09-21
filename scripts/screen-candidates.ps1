# 候補300件をexcluded_clear/eligible_clear/needs_language_reviewの3区分に
# 一次判定する(AI不使用、Windows / PowerShell用)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli screen-candidates
