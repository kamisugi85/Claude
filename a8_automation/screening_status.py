from __future__ import annotations

from .classification import classify_program
from .exclusion import evaluate_exclusion

# 3区分(排他的)。スコアやしきい値は一切使わない。
EXCLUDED_CLEAR = "excluded_clear"
ELIGIBLE_CLEAR = "eligible_clear"
NEEDS_LANGUAGE_REVIEW = "needs_language_review"


def classify_screening_status(record: dict) -> dict:
    """単純な文字列・構造ルールだけで判定できるものだけをexcluded_clear /
    eligible_clearに分類し、それ以外(条件付き文言・言及なしではない曖昧な
    表現など)は needs_language_review に分離する。1レコードは必ずどれか
    1区分にのみ属する。

    - excluded_clear: 案件停止等のステータス、TikTok/SNSの明示的な掲載NG、
      SNSが他媒体に限定されTikTokが対象外、TikTokポリシー上高リスクな
      カテゴリ、のいずれか(evaluate_exclusion()と同一のルール)。
    - eligible_clear: 上記に該当せず、かつSNS/TikTok掲載が明示OK、または
      除外シグナルが一切見つからない(=沈黙は許可とみなす、本PJ方針)。
    - needs_language_review: 条件付き文言(事前相談要 等)で、機械的に
      OK/NGと言い切れないもの。
    """
    exclusion_verdict = evaluate_exclusion(record)
    if exclusion_verdict is not None:
        return {"screening_status": EXCLUDED_CLEAR, "screening_reason": exclusion_verdict["exclusion_reason"]}

    verdict = classify_program(record)
    if verdict["tiktok_overall"] == "likely_ok":
        return {"screening_status": ELIGIBLE_CLEAR, "screening_reason": verdict["sns_basis"]}

    return {"screening_status": NEEDS_LANGUAGE_REVIEW, "screening_reason": verdict["sns_basis"]}
