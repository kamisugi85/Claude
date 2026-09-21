"""A8案件データの受け取り(既存Collectorの再構築・再取得は一切行わない)。

既にa8_automation側で書き出し済みのエクスポート形式(例:
a8_ai_review_selection_60_latest.json や a8_claude_scripts_9_provisional_*.json
が参照しているのと同じProgram Master由来のレコード)を、そのまま読み取って
必要フィールドだけを取り出す。新たなスクレイピング・APIコールは行わない。
不明な項目は推測せずnullのままにする。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from .schema import PROGRAM_INPUT_FIELDS


def build_program_input(record: dict) -> dict:
    """a8_ai_review_selection_60_latest.json 等のエクスポート済みレコード1件から、
    TikTok Research Jobに必要な最小限のフィールドだけを取り出す。
    """
    other = record.get("other_detail_fields") or {}

    prohibited_expressions = {
        "listing_ng_words": other.get("リスティングＮＧワード"),
        "remarks_prohibited_notes": record.get("remarks"),
    }
    if prohibited_expressions["listing_ng_words"] is None and prohibited_expressions["remarks_prohibited_notes"] is None:
        prohibited_expressions = None

    program_input = {
        "program_id": record.get("program_id"),
        "program_name": record.get("program_name"),
        "category": record.get("category"),
        "reward": record.get("reward"),
        "conversion_conditions": record.get("conversion_action_raw_text"),
        "sns_tiktok_conditions": record.get("sns_condition"),
        "prohibited_expressions": prohibited_expressions,
        "material_conditions": other.get("商品リンク"),
        "advertiser_specific_restrictions": record.get("rejection_condition"),
    }
    assert set(program_input.keys()) == set(PROGRAM_INPUT_FIELDS)
    return program_input


def load_program_inputs_from_export(export_path: str, program_ids: Optional[List[str]] = None) -> List[dict]:
    """エクスポート済みJSON(items配列を持つ形式)から、指定したprogram_idだけを
    抽出してProgram Input化する。program_idsを指定しない場合は全件変換する。
    """
    with open(export_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    if program_ids is not None:
        wanted = set(program_ids)
        items = [i for i in items if i.get("program_id") in wanted]

    return [build_program_input(item) for item in items]
