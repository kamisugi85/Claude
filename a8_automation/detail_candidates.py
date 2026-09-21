from __future__ import annotations

from typing import Dict, List

from .scoring import parse_number
from .scraper import has_detail_fields
from .utils import iso_now, write_json


def _sorted_by(pids: List[str], catalog: Dict[str, dict], field: str) -> List[str]:
    with_value = [(pid, parse_number(catalog[pid].get(field))) for pid in pids]
    with_value = [(pid, v) for pid, v in with_value if v is not None]
    with_value.sort(key=lambda pv: pv[1], reverse=True)
    return [pid for pid, _ in with_value]


def select_epc_tier(catalog: Dict[str, dict], limit: int) -> List[str]:
    pids_with_epc = [pid for pid, r in catalog.items() if parse_number(r.get("epc")) is not None]
    return _sorted_by(pids_with_epc, catalog, "epc")[:limit]


def select_non_epc_tier(catalog: Dict[str, dict], exclude_ids: set, limit: int) -> List[str]:
    """EPC欠損案件だけを対象に、報酬額→確定率の順で別枠として優先順位付けする。
    架空のEPC(報酬額×確定率等)は一切計算しない。どちらも無い案件は元の順序の
    まま末尾に回すだけで、除外はしない。
    """
    pool = [pid for pid, r in catalog.items() if pid not in exclude_ids and parse_number(r.get("epc")) is None]

    reward_ranked = _sorted_by([pid for pid in pool if parse_number(catalog[pid].get("reward")) is not None], catalog, "reward")
    remaining = [pid for pid in pool if pid not in set(reward_ranked)]

    rate_ranked = _sorted_by([pid for pid in remaining if parse_number(catalog[pid].get("conversion_rate")) is not None], catalog, "conversion_rate")
    no_signal = [pid for pid in remaining if pid not in set(rate_ranked)]

    return (reward_ranked + rate_ranked + no_signal)[:limit]


def build_detail_fetch_population(catalog: Dict[str, dict], epc_limit: int = 250, non_epc_limit: int = 50) -> dict:
    epc_tier = select_epc_tier(catalog, epc_limit)
    non_epc_tier = select_non_epc_tier(catalog, exclude_ids=set(epc_tier), limit=non_epc_limit)

    population = epc_tier + non_epc_tier
    already_detailed = [pid for pid in population if has_detail_fields(catalog[pid])]
    needs_fetch = [pid for pid in population if pid not in set(already_detailed)]

    return {
        "epc_tier_count": len(epc_tier),
        "non_epc_tier_count": len(non_epc_tier),
        "population_count": len(population),
        "already_detailed_count": len(already_detailed),
        "needs_fetch_count": len(needs_fetch),
        "population": population,
        "needs_fetch": needs_fetch,
    }


def save_detail_fetch_population(
    catalog: Dict[str, dict], path: str, epc_limit: int = 250, non_epc_limit: int = 50
) -> dict:
    result = build_detail_fetch_population(catalog, epc_limit, non_epc_limit)
    write_json(path, {"generated_at": iso_now(), **result})
    return result
