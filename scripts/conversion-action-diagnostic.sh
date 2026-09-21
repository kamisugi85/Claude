#!/usr/bin/env bash
# 成果条件テキストの候補キーワード出現頻度・マッチ数分布を診断する
# (分類は確定しない、AI/LLM不使用、Program Masterは変更しない)。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli conversion-action-diagnostic
