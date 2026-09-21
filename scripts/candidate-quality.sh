#!/usr/bin/env bash
# 候補300件だけを対象に取得件数/欠損率と見出し出現頻度を集計する。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli candidate-quality
