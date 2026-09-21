#!/usr/bin/env bash
# 無人実行用エントリポイント。cronや手動実行から呼び出す。
# 異常を検知した場合はリトライせず即座に停止し、終了コード1を返す。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli run
