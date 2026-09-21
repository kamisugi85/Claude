#!/usr/bin/env python3
"""Team Claude: 台本テキストに対する機械的コンプライアンスチェック(AI/LLM不使用)。

仕様書(a8_tiktok_script_competition_spec_20260922.json)のprohibitedリストと
本プロジェクトの禁止表現方針を反映した、単純な文字列一致による一次チェック。
これは最終確認を代替しない(compliance_self_check.unconfirmed_itemsに記載の
事項は引き続き広告主承認後の人間確認が必要)。あくまで明らかな禁止表現の
混入を機械的に検出するためのセーフティネット。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(os.path.dirname(_HERE))
_SCRIPTS_PATH = os.path.join(_BASE_DIR, "data", "state", "a8_claude_scripts_9_provisional_20260922.json")

# 断定的な保証表現・誇大表現・根拠のない最上級表現(仕様書のprohibitedリストに対応)
_BANNED_PATTERNS = [
    (re.compile(r"必ず(転職|成功|採用|内定)"), "断定的な転職成功の保証表現"),
    (re.compile(r"年収.{0,5}(アップ|増加|保証)"), "収入増加の保証表現"),
    (re.compile(r"(業界|日本|国内).{0,5}No\.?1"), "根拠のないNo.1表現"),
    (re.compile(r"最(高|大|強|上級)の(サービス|求人|職場)"), "根拠のない最上級表現"),
    (re.compile(r"絶対に"), "断定・煽り表現"),
    (re.compile(r"100%(成功|保証|採用)"), "断定的な保証表現"),
]

_REQUIRED_PR_MARKERS = ["#PR", "PR"]


def check_item(item: dict) -> dict:
    text_fields = {
        "hook": item.get("hook", ""),
        "post_caption": item.get("post_caption", ""),
        "on_screen_captions": " ".join(item.get("on_screen_captions", [])),
        "narration": item.get("narration", ""),
        "CTA": item.get("CTA", ""),
    }
    combined = " ".join(text_fields.values())

    violations = []
    for pattern, label in _BANNED_PATTERNS:
        if pattern.search(combined):
            violations.append(label)

    has_pr_in_caption = any(marker in text_fields["post_caption"] for marker in _REQUIRED_PR_MARKERS)
    has_pr_badge_note = "PR" in (item.get("required_PR_disclosure") or "")

    return {
        "creative_id": item["creative_id"],
        "passed": len(violations) == 0 and has_pr_in_caption,
        "banned_phrase_violations": violations,
        "post_caption_has_pr_tag": has_pr_in_caption,
        "required_pr_disclosure_documented": has_pr_badge_note,
    }


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else None
    with open(_SCRIPTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data["items"] if target is None else [i for i in data["items"] if i["creative_id"] == target]
    results = [check_item(item) for item in items]

    for r in results:
        status = "OK" if r["passed"] else "NG"
        print(f"[{status}] {r['creative_id']}  violations={r['banned_phrase_violations']}  pr_tag={r['post_caption_has_pr_tag']}")

    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
