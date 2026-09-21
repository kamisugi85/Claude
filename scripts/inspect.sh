#!/usr/bin/env bash
# 収集済みデータのサマリとサンプルを表示する。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli inspect
