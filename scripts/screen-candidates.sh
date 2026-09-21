#!/usr/bin/env bash
# 候補300件をexcluded_clear/eligible_clear/needs_language_reviewの3区分に
# 一次判定する(AI不使用)。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli screen-candidates
