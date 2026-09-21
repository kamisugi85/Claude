# 無人実行用エントリポイント(Windows / PowerShell用)。タスクスケジューラや手動実行から呼び出す。
# 異常を検知した場合はリトライせず即座に停止し、終了コード1を返す。
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
}
python -m a8_automation.cli run
exit $LASTEXITCODE
