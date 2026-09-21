from __future__ import annotations

from collections import Counter
from typing import Dict, List

from .scoring import parse_number
from .scraper import has_detail_fields

# 集計対象の主要項目。reward/epc/conversion_rateは一覧レベル(全件にキー自体は
# 存在)なので「数値として解釈できるか」を、それ以外は詳細ページ由来のフィールド
# なので「値が存在するか」を見る。
_SNS_JUDGMENT_TEXT_FIELDS = ["備考", "否認条件", "成果条件", "リスティングNGワード"]
_DETAIL_TEXT_FIELDS = ["成果条件", "否認条件", "備考", "禁止事項", "リスティングNGワード"]

# 一覧レベルの固定フィールド(見出しの頻度集計から除外するため)。
_LIST_LEVEL_KEYS = {
    "program_id",
    "detail_url",
    "name",
    "reward",
    "epc",
    "conversion_rate",
    "category",
    "start_date",
    "checked_at",
    "url",
    "excluded",
    "exclusion_reason",
    "judged_at",
}


def compute_population_quality(catalog: Dict[str, dict], population_ids: List[str]) -> dict:
    """300件の候補母集団だけを対象にした、詳細取得の完了状況と主要項目の
    取得件数/欠損率を集計する(カタログ全体4,475件は対象にしない)。
    """
    records = [catalog[pid] for pid in population_ids if pid in catalog]
    total = len(records)
    detailed = [r for r in records if has_detail_fields(r)]
    detailed_count = len(detailed)

    def rate(n: int) -> float:
        return (n / total) if total else 0.0

    fields = {
        "reward(報酬)": {
            "count": sum(1 for r in records if parse_number(r.get("reward")) is not None),
        },
        "epc": {
            "count": sum(1 for r in records if parse_number(r.get("epc")) is not None),
        },
        "conversion_rate(確定率)": {
            "count": sum(1 for r in records if parse_number(r.get("conversion_rate")) is not None),
        },
    }
    for name in _DETAIL_TEXT_FIELDS:
        fields[name] = {"count": sum(1 for r in records if r.get(name))}
    fields["sns_status(実データに基づく判定)"] = {
        "count": sum(1 for r in records if any(r.get(f) for f in _SNS_JUDGMENT_TEXT_FIELDS))
    }

    for info in fields.values():
        info["rate"] = rate(info["count"])

    return {
        "population_total": total,
        "detail_fetched_count": detailed_count,
        "detail_fetched_rate": rate(detailed_count),
        "fields": fields,
    }


def distinct_detail_headings(catalog: Dict[str, dict], population_ids: List[str]) -> Counter:
    """300件の詳細取得済みレコードに実際に現れた見出し名(一覧レベルの固定
    フィールドを除く)の出現頻度。「禁止事項」「リスティングNGワード」が
    0件の場合、ページ側にその見出しが無いのか、抽出側の問題かを切り分ける
    手がかりにする(抽出は見出しタグを機械的に拾う方式で、特定の語句と
    一致させているわけではないため、ここに出てこない=そのページに存在
    しなかった可能性が高いことを示す)。
    """
    counter: Counter = Counter()
    for pid in population_ids:
        record = catalog.get(pid)
        if not record or not has_detail_fields(record):
            continue
        for key in record:
            if key in _LIST_LEVEL_KEYS:
                continue
            counter[key] += 1
    return counter
