from __future__ import annotations

from collections import Counter
from typing import Dict, List, Set, Tuple

from .classification import classify_program
from .diff_store import load_snapshot
from .exclusion import evaluate_exclusion
from .utils import iso_now, read_json, write_json


def screen_catalog(catalog: Dict[str, dict]) -> Tuple[Set[str], Counter, Counter]:
    """カタログ全体にPython/ルールのみの一次選別を適用する(AI不使用)。

    スコアやしきい値による足切りは行わない -- evaluate_exclusion() が確定的な
    ルール(ステータスキーワード、TikTok/SNSの明示的NG、高リスクカテゴリ)で
    除外すると判定したものだけを除外する。
    """
    excluded_ids: Set[str] = set()
    reason_counts: Counter = Counter()
    overall_counts: Counter = Counter()

    for pid, record in catalog.items():
        overall_counts[classify_program(record)["tiktok_overall"]] += 1

        verdict = evaluate_exclusion(record)
        if verdict is not None:
            excluded_ids.add(pid)
            reason_counts[verdict["exclusion_reason"]] += 1

    return excluded_ids, reason_counts, overall_counts


def screen_items(items: List[dict], excluded_ids: Set[str]) -> List[dict]:
    return [item for item in items if item.get("program_id") not in excluded_ids]


def run_screen(settings) -> dict:
    catalog = load_snapshot(settings.latest_snapshot_path)
    shortlist_data = read_json(settings.shortlist_path, default={"items": []})
    shortlist_items = shortlist_data.get("items", [])

    excluded_ids, reason_counts, overall_counts = screen_catalog(catalog)
    ai_candidates = screen_items(shortlist_items, excluded_ids)

    write_json(
        settings.ai_candidates_path,
        {"generated_at": iso_now(), "count": len(ai_candidates), "items": ai_candidates},
    )
    write_json(
        settings.excluded_by_rules_path,
        {
            "generated_at": iso_now(),
            "count": len(excluded_ids),
            "reason_counts": dict(reason_counts),
            "program_ids": sorted(excluded_ids),
        },
    )

    return {
        "catalog_total": len(catalog),
        "catalog_excluded": len(excluded_ids),
        "catalog_remaining": len(catalog) - len(excluded_ids),
        "shortlist_total": len(shortlist_items),
        "shortlist_remaining": len(ai_candidates),
        "reason_counts": dict(reason_counts),
        "overall_counts": dict(overall_counts),
    }
