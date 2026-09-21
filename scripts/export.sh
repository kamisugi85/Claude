#!/usr/bin/env bash
# AI候補データのみを共通フォーマットで書き出す。カタログ全体は出力しない。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli export
