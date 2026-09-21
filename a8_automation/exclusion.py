from __future__ import annotations

from typing import Optional

from .classification import classify_program
from .utils import iso_now

# Python/ルールによる明確な不適合案件の除外(AIは使わない)。ジャンルや報酬単価による
# 事前絞り込みは意図的に行わない(スコアやしきい値では絶対に除外しない)。ここは
# 「明確に不適合」と言えるものだけを機械的に弾く場所で、まだ実例が少ないため保守的な
# キーワードのみ。実際の除外パターンが分かり次第、ルールを追加していく想定。
STATUS_EXCLUSION_KEYWORDS = [
    "募集停止",
    "掲載終了",
    "提携停止",
    "受付終了",
]


def evaluate_exclusion(record: dict) -> Optional[dict]:
    """明確な不適合を検知したら除外メタデータを返す。判定できなければNone
    (=除外しない)を返す。レコード自体は呼び出し側が削除せず保持する前提。

    2種類の確定的ルールのみを使う(どちらも明示的な文言に基づく判定で、推測は
    行わない):
    1. 募集停止等のステータスキーワード
    2. classification.classify_program() が確信を持って "likely_excluded"
       と判定したもの(TikTok/SNSの明示的な掲載NG、またはTikTokポリシー上
       高リスクなカテゴリ)
    """
    text = " ".join(v for v in record.values() if isinstance(v, str))
    for keyword in STATUS_EXCLUSION_KEYWORDS:
        if keyword in text:
            return {
                "excluded": True,
                "exclusion_reason": f"status_keyword:{keyword}",
                "judged_at": iso_now(),
            }

    verdict = classify_program(record)
    if verdict["tiktok_overall"] == "likely_excluded":
        basis = verdict["sns_basis"] if verdict["tiktok_verdict"] == "prohibited" else verdict["category_basis"]
        return {
            "excluded": True,
            "exclusion_reason": f"tiktok_policy:{basis}",
            "judged_at": iso_now(),
        }

    return None


def apply_exclusion(record: dict) -> dict:
    """レコードにルール判定結果を反映する。除外に該当しない場合、既存の除外状態
    (前回除外されていたなら、そのまま)は変更しない -- 除外解除はreview_queue側の
    「報酬変更による復活」ロジックが明示的に扱う。
    """
    verdict = evaluate_exclusion(record)
    if verdict is not None:
        return {**record, **verdict}
    return record
