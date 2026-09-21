from __future__ import annotations

from collections import Counter
from typing import Dict, List

from .diff_store import promote_snapshot
from .screening_status import ELIGIBLE_CLEAR, EXCLUDED_CLEAR, NEEDS_LANGUAGE_REVIEW, classify_screening_status
from .utils import iso_now, write_json


def run_candidate_screening(
    catalog: Dict[str, dict], population_ids: List[str], latest_snapshot_path: str, report_path: str
) -> dict:
    """300件の候補母集団に、単純な文字列・構造ルールだけの3区分判定
    (excluded_clear / eligible_clear / needs_language_review)を適用する。
    Program Masterからは何も削除しない -- screening_status/screening_reason/
    screened_at を各レコードに記録して保存するだけ。AI/LLMは使用しない。
    """
    status_counts: Counter = Counter()
    exclusion_reason_counts: Counter = Counter()
    review_reason_counts: Counter = Counter()
    screened_at = iso_now()

    for pid in population_ids:
        record = catalog.get(pid)
        if record is None:
            continue

        verdict = classify_screening_status(record)
        catalog[pid] = {**record, **verdict, "screened_at": screened_at}

        status_counts[verdict["screening_status"]] += 1
        if verdict["screening_status"] == EXCLUDED_CLEAR:
            exclusion_reason_counts[verdict["screening_reason"]] += 1
        elif verdict["screening_status"] == NEEDS_LANGUAGE_REVIEW:
            review_reason_counts[verdict["screening_reason"]] += 1

    promote_snapshot(catalog, latest_snapshot_path)

    report = {
        "generated_at": screened_at,
        "population_total": len(population_ids),
        "excluded_clear_count": status_counts[EXCLUDED_CLEAR],
        "eligible_clear_count": status_counts[ELIGIBLE_CLEAR],
        "needs_language_review_count": status_counts[NEEDS_LANGUAGE_REVIEW],
        "exclusion_reason_counts": dict(exclusion_reason_counts),
        "review_reason_counts": dict(review_reason_counts),
    }
    write_json(report_path, report)
    return report
