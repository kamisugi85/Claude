from __future__ import annotations

from typing import Optional

from .classification import classify_program
from .diff_store import load_snapshot
from .exclusion import evaluate_exclusion
from .utils import iso_now, read_json, write_json

# A8側の項目名に依存する自由記述フィールド。存在しないものはNoneのまま保持する
# (推測しない)。
# 注意: A8側の実際の見出しは全角の「ＮＧ」(U+FF2E/U+FF27)であり、半角の「NG」ではない。
_CONDITION_FIELDS = ["否認条件", "備考", "リスティングＮＧワード", "禁止事項"]


def build_candidate_export(record: dict) -> dict:
    """ChatGPT/Claudeが共通で読む、案件選定に必要な最小限のフィールドだけの
    エクスポート用レコードを作る。未取得の項目は推測せずnull(None)のままにする。
    """
    verdict = classify_program(record)
    exclusion = evaluate_exclusion(record)

    relevant_conditions = {field: record.get(field) for field in _CONDITION_FIELDS}

    return {
        "program_id": record.get("program_id"),
        "program_name": record.get("name"),
        "category": record.get("category"),
        "reward": record.get("reward"),
        "conversion_action": record.get("成果条件"),
        "sns_status": verdict["tiktok_verdict"],
        "relevant_conditions": relevant_conditions,
        "screening_status": verdict["tiktok_overall"],
        "exclusion_reason": exclusion["exclusion_reason"] if exclusion else None,
        "source_updated_at": record.get("checked_at"),
    }


def run_export(settings) -> dict:
    """data/state/ai_candidates.json (screen.pyが出力した、ルール除外後の
    候補リスト)を元に、ChatGPT/Claude向けの共通データファイルを作る。
    4,475件のカタログ全体は出力しない -- 候補のみ。
    """
    catalog = load_snapshot(settings.latest_snapshot_path)
    ai_candidates = read_json(settings.ai_candidates_path, default={"items": []})

    exported = []
    for item in ai_candidates.get("items", []):
        program_id = item.get("program_id")
        record = catalog.get(program_id, item)
        exported.append(build_candidate_export(record))

    payload = {"generated_at": iso_now(), "count": len(exported), "items": exported}
    write_json(settings.candidates_export_path, payload)
    return payload
