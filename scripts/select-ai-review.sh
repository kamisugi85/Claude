#!/usr/bin/env bash
# 297件から高性能AI評価に回す60件をPythonのみで抽出する
# (EPCあり45件+EPCなし15件、AI/LLM不使用、Program Masterからは削除しない)。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli select-ai-review
