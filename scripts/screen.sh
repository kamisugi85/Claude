#!/usr/bin/env bash
# AI不使用、Pythonルールのみでカタログ/shortlistを一次選別する。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli screen
