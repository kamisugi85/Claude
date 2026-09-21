#!/usr/bin/env bash
# select-ai-reviewが確定した60件を、ChatGPT/Claude共有用の1ファイルに
# 書き出す(ローカル保存のみ、Google Driveへのコピーは別途手動で行う)。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli export-ai-review
