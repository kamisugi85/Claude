#!/usr/bin/env bash
# plan-detail-fetchの候補から指定件数だけ実際に詳細ページを取得する。
# 使い方: ./scripts/fetch-details.sh 50
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
LIMIT="${1:-20}"
python -m a8_automation.cli fetch-details --limit "$LIMIT"
