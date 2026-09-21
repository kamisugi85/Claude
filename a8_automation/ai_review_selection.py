from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from .conversion_action_diagnostic import CANDIDATE_KEYWORDS, select_target_ids
from .diff_store import promote_snapshot
from .scoring import parse_number
from .utils import iso_now, write_json

# 「申込/申し込み/契約/成約/加盟」は成果条件テキスト上ほぼ全域に出現する補助語で、
# それ単独では実際の成果地点を意味しない(conversion_action_diagnosticの診断結果
# より判明)。多様性確認では、この補助語を除いた実質語が1種類だけヒットする場合
# のみ「安全に分類できた」ものとして扱う。新たな構文解析・分類精度向上は行わない。
_AUXILIARY_KEYWORDS = {"申込", "申し込み", "契約", "成約", "加盟"}
_SUBSTANTIVE_KEYWORDS = [kw for kw in CANDIDATE_KEYWORDS if kw not in _AUXILIARY_KEYWORDS]


def safe_conversion_action_category(text: Optional[str]) -> Optional[str]:
    """実質語(補助語を除く)が1種類だけヒットする場合のみ、その語をカテゴリ
    として返す。それ以外(0件、2件以上)はNone(=分類不能)。ハードな順位付け
    には使わず、多様性確認の参考情報としてのみ利用する。"""
    if not text:
        return None
    matched = [kw for kw in _SUBSTANTIVE_KEYWORDS if kw in text]
    if len(matched) == 1:
        return matched[0]
    return None


def _sorted_by(pids: List[str], catalog: Dict[str, dict], field: str) -> List[str]:
    with_value = [(pid, parse_number(catalog[pid].get(field))) for pid in pids]
    with_value = [(pid, v) for pid, v in with_value if v is not None]
    with_value.sort(key=lambda pv: pv[1], reverse=True)
    return [pid for pid, _ in with_value]


def select_epc_tier(pids: List[str], catalog: Dict[str, dict], limit: int) -> List[str]:
    pids_with_epc = [pid for pid in pids if parse_number(catalog[pid].get("epc")) is not None]
    return _sorted_by(pids_with_epc, catalog, "epc")[:limit]


def select_non_epc_tier(pids: List[str], catalog: Dict[str, dict], exclude_ids: set, limit: int) -> List[str]:
    """EPC欠損案件だけを対象にした独立枠。報酬額→確定率の順で優先順位付けする。
    架空のEPC(報酬額×確定率等)は一切計算せず、どちらも無い案件も除外はせず
    末尾に回すだけ。"""
    pool = [pid for pid in pids if pid not in exclude_ids and parse_number(catalog[pid].get("epc")) is None]

    reward_ranked = _sorted_by([pid for pid in pool if parse_number(catalog[pid].get("reward")) is not None], catalog, "reward")
    remaining = [pid for pid in pool if pid not in set(reward_ranked)]

    rate_ranked = _sorted_by([pid for pid in remaining if parse_number(catalog[pid].get("conversion_rate")) is not None], catalog, "conversion_rate")
    no_signal = [pid for pid in remaining if pid not in set(rate_ranked)]

    return (reward_ranked + rate_ranked + no_signal)[:limit]


def _range(values: List[float]) -> dict:
    return {"max": max(values), "min": min(values)} if values else {"max": None, "min": None}


def build_ai_review_selection(catalog: Dict[str, dict], epc_limit: int = 45, non_epc_limit: int = 15) -> dict:
    """297件(excluded_clearを除く)から、高性能AI評価に回す母集団をPythonのみで
    抽出する。単一の合成スコアは作らない。EPCあり枠とEPC欠損枠を独立に選定し、
    成果地点・業種は選抜基準にはせず、事後の多様性確認にのみ使う。"""
    target_ids = select_target_ids(catalog)

    epc_tier = select_epc_tier(target_ids, catalog, epc_limit)
    non_epc_tier = select_non_epc_tier(target_ids, catalog, exclude_ids=set(epc_tier), limit=non_epc_limit)
    population = epc_tier + non_epc_tier

    selection_reasons: Dict[str, dict] = {}
    for rank, pid in enumerate(epc_tier, start=1):
        selection_reasons[pid] = {
            "tier": "epc",
            "reason": f"epc_rank_{rank}",
            "rank": rank,
        }

    reward_ranked = _sorted_by([pid for pid in non_epc_tier if parse_number(catalog[pid].get("reward")) is not None], catalog, "reward")
    reward_ranked_in_tier = [pid for pid in reward_ranked if pid in non_epc_tier]
    rate_pool = [pid for pid in non_epc_tier if pid not in set(reward_ranked_in_tier)]
    rate_ranked = _sorted_by([pid for pid in rate_pool if parse_number(catalog[pid].get("conversion_rate")) is not None], catalog, "conversion_rate")
    no_signal = [pid for pid in rate_pool if pid not in set(rate_ranked)]

    for rank, pid in enumerate(reward_ranked_in_tier, start=1):
        selection_reasons[pid] = {"tier": "non_epc", "reason": f"non_epc_reward_rank_{rank}", "rank": rank}
    for rank, pid in enumerate(rate_ranked, start=1):
        selection_reasons[pid] = {"tier": "non_epc", "reason": f"non_epc_rate_rank_{rank}", "rank": rank}
    for rank, pid in enumerate(no_signal, start=1):
        selection_reasons[pid] = {"tier": "non_epc", "reason": f"non_epc_no_signal_rank_{rank}", "rank": rank}

    epc_values = [parse_number(catalog[pid].get("epc")) for pid in epc_tier]
    non_epc_reward_values = [parse_number(catalog[pid].get("reward")) for pid in non_epc_tier if parse_number(catalog[pid].get("reward")) is not None]
    non_epc_rate_values = [parse_number(catalog[pid].get("conversion_rate")) for pid in non_epc_tier if parse_number(catalog[pid].get("conversion_rate")) is not None]

    category_counts: Counter = Counter()
    unclassified_count = 0
    for pid in population:
        cat = safe_conversion_action_category(catalog[pid].get("成果条件"))
        if cat is None:
            unclassified_count += 1
        else:
            category_counts[cat] += 1

    industry_counts = Counter(catalog[pid].get("category") or "(不明)" for pid in population)

    problems: List[str] = []
    if len(epc_tier) < epc_limit:
        problems.append(f"EPCあり枠が目標{epc_limit}件に対し{len(epc_tier)}件しか確保できませんでした(EPCあり案件自体が不足)。")
    if len(non_epc_tier) < non_epc_limit:
        problems.append(f"EPCなし枠が目標{non_epc_limit}件に対し{len(non_epc_tier)}件しか確保できませんでした(EPC欠損案件自体が不足)。")

    return {
        "target_total": len(target_ids),
        "population_count": len(population),
        "epc_tier_count": len(epc_tier),
        "non_epc_tier_count": len(non_epc_tier),
        "population": population,
        "epc_tier": epc_tier,
        "non_epc_tier": non_epc_tier,
        "selection_reasons": selection_reasons,
        "epc_range": _range(epc_values),
        "non_epc_reward_range": _range(non_epc_reward_values),
        "non_epc_rate_range": _range(non_epc_rate_values),
        "non_epc_no_signal_count": len(no_signal),
        "conversion_action_diversity": {
            "classified_counts": dict(category_counts.most_common()),
            "unclassified_count": unclassified_count,
        },
        "industry_distribution": dict(industry_counts.most_common()),
        "problems": problems,
    }


def save_ai_review_selection(
    catalog: Dict[str, dict],
    latest_snapshot_path: str,
    report_path: str,
    epc_limit: int = 45,
    non_epc_limit: int = 15,
) -> dict:
    """60件抽出を実行し、Program Masterには選定結果をタグとして記録する
    (削除は一切行わない)。60件そのもののレポートも別途保存する。"""
    result = build_ai_review_selection(catalog, epc_limit, non_epc_limit)
    selected_at = iso_now()

    for pid, info in result["selection_reasons"].items():
        record = catalog[pid]
        catalog[pid] = {
            **record,
            "ai_review_selected": True,
            "ai_review_tier": info["tier"],
            "ai_review_reason": info["reason"],
            "ai_review_rank": info["rank"],
            "ai_review_selected_at": selected_at,
        }

    promote_snapshot(catalog, latest_snapshot_path)

    report = {"generated_at": selected_at, **result}
    write_json(report_path, report)
    return report
