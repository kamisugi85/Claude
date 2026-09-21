#!/usr/bin/env bash
# 詳細ページ追加取得の候補母集団を選定する(実際の取得は行わない)。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli plan-detail-fetch
