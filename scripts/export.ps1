# AI候補データのみを共通フォーマットで書き出す(Windows / PowerShell用)。
# カタログ全体(4,475件)は出力しない。ネットワーク・ブラウザ操作は行わない。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli export
