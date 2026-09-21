# 初回のみ手動で実行する(Windows / PowerShell用)。
# ブラウザウィンドウが開くのでA8にログインし、完了したらターミナルに戻って Enter を押す。
# ID/パスワードはどこにも保存されない。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli login
