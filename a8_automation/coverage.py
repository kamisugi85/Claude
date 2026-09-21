from __future__ import annotations

from typing import Dict

from .scoring import parse_number
from .scraper import has_detail_fields

# ランキングに使えそうな客観的・数値的項目の候補。実際に使うかどうかは
# このカバレッジ確認の結果を見て判断する(★このファイルではランキング式は
# 一切決めない)。
_NUMERIC_FIELD_CHECKS = {
    "reward(報酬額)": lambda r: parse_number(r.get("reward")) is not None,
    "epc": lambda r: parse_number(r.get("epc")) is not None,
    "conversion_rate(確定率)": lambda r: parse_number(r.get("conversion_rate")) is not None,
    "start_date(新着判定用)": lambda r: bool(r.get("start_date")),
}

# SNS掲載可否は「判定材料となる本文が実際に存在するか」を見る。本文が無い
# レコードのsns_statusは既定値(allowed)であり、観測データではないため区別する。
_SNS_JUDGMENT_TEXT_FIELDS = ["備考", "否認条件", "成果条件", "リスティングNGワード"]


def compute_field_coverage(catalog: Dict[str, dict]) -> dict:
    total = len(catalog)
    counts = {name: 0 for name in _NUMERIC_FIELD_CHECKS}
    detail_fetched = 0
    conversion_action_present = 0  # 成果条件(=成果地点の説明)の有無
    sns_text_present = 0

    for record in catalog.values():
        for name, check in _NUMERIC_FIELD_CHECKS.items():
            if check(record):
                counts[name] += 1
        if has_detail_fields(record):
            detail_fetched += 1
        if record.get("成果条件"):
            conversion_action_present += 1
        if any(record.get(f) for f in _SNS_JUDGMENT_TEXT_FIELDS):
            sns_text_present += 1

    def rate(n: int) -> float:
        return (n / total) if total else 0.0

    fields = {name: {"count": counts[name], "rate": rate(counts[name])} for name in _NUMERIC_FIELD_CHECKS}
    fields["detail_page_fetched"] = {"count": detail_fetched, "rate": rate(detail_fetched)}
    fields["成果条件(成果地点)"] = {"count": conversion_action_present, "rate": rate(conversion_action_present)}
    fields["sns_status(実データに基づく判定)"] = {"count": sns_text_present, "rate": rate(sns_text_present)}
    fields["campaign_info(キャンペーン/報酬アップ)"] = {
        "count": 0,
        "rate": 0.0,
        "note": "未実装 -- まだ収集していない項目",
    }

    return {"total": total, "fields": fields}
