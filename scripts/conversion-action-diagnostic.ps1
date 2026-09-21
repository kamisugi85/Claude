# 成果条件テキストの候補キーワード出現頻度・マッチ数分布を診断する
# (分類は確定しない、AI/LLM不使用、Program Masterは変更しない、Windows / PowerShell用)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli conversion-action-diagnostic
