# select-ai-reviewが確定した60件を、ChatGPT/Claude共有用の1ファイルに
# 書き出す(ローカル保存のみ、Google Driveへのコピーは別途手動で行う、Windows / PowerShell用)。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli export-ai-review
