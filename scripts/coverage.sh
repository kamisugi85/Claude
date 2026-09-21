#!/usr/bin/env bash
# ランキングに使える項目の取得件数/欠損率を確認する。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli coverage
