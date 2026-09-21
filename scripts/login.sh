#!/usr/bin/env bash
# 初回のみ手動で実行する。ブラウザウィンドウが開くのでA8にログインし、
# 完了したらターミナルに戻って Enter を押す。ID/パスワードはどこにも保存されない。
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  source .venv/bin/activate
fi
python -m a8_automation.cli login
