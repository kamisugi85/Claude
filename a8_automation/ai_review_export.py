from __future__ import annotations

import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional

from .classification import classify_program
from .diff_store import load_snapshot
from .utils import iso_now, read_json, write_json

# A8側の詳細ページに「広告主名」に相当する見出しがあれば汎用抽出(scraper.py の
# extract_program_detail)でそのままキーとして拾われる。見出し名が確定していない
# ため、想定される表記ゆれだけを確認し、無ければ推測せずNoneのままにする。
_ADVERTISER_NAME_KEYS = ["広告主名", "広告主"]

# エクスポートの個別フィールドとして明示的に扱うキー。ここに無いキーは
# other_detail_fields にそのまま(加工せず)含める。
_MAPPED_FIELD_KEYS = {
    "program_id",
    "name",
    "url",
    "detail_url",
    "reward",
    "epc",
    "conversion_rate",
    "category",
    "start_date",
    "checked_at",
    "score",
    "excluded",
    "exclusion_reason",
    "judged_at",
    "screening_status",
    "screening_reason",
    "screened_at",
    "ai_review_selected",
    "ai_review_tier",
    "ai_review_reason",
    "ai_review_rank",
    "ai_review_selected_at",
    "成果条件",
    "否認条件",
    "備考",
    *_ADVERTISER_NAME_KEYS,
}


def _find_advertiser_name(record: dict) -> Optional[str]:
    for key in _ADVERTISER_NAME_KEYS:
        value = record.get(key)
        if value:
            return value
    return None


def build_ai_review_export_record(record: dict) -> dict:
    """60件それぞれについて、ChatGPT/Claudeが独立評価できるだけの情報を
    そのまま(加工・推測せず)書き出す。未取得のフィールドはnullのまま。"""
    verdict = classify_program(record)
    other_detail_fields = {k: v for k, v in record.items() if k not in _MAPPED_FIELD_KEYS}

    return {
        "program_id": record.get("program_id"),
        "program_name": record.get("name"),
        "advertiser_name": _find_advertiser_name(record),
        "category": record.get("category"),
        "reward": record.get("reward"),
        "epc": record.get("epc"),
        "conversion_rate": record.get("conversion_rate"),
        "sns_condition": {
            "sns_verdict": verdict.get("sns_verdict"),
            "tiktok_verdict": verdict.get("tiktok_verdict"),
            "sns_basis": verdict.get("sns_basis"),
        },
        "conversion_action_raw_text": record.get("成果条件"),
        "rejection_condition": record.get("否認条件"),
        "remarks": record.get("備考"),
        "other_detail_fields": other_detail_fields,
        "ai_review_tier": record.get("ai_review_tier"),
        "ai_review_reason": record.get("ai_review_reason"),
        "ai_review_rank": record.get("ai_review_rank"),
        "screening_status": record.get("screening_status"),
        "screening_reason": record.get("screening_reason"),
        "detail_url": record.get("detail_url") or record.get("url"),
        "source_updated_at": record.get("checked_at"),
    }


def build_ai_review_export(catalog: Dict[str, dict], selection: dict) -> List[dict]:
    population = selection.get("epc_tier", []) + selection.get("non_epc_tier", [])
    return [build_ai_review_export_record(catalog[pid]) for pid in population if pid in catalog]


def _history_path_for(latest_path: str, date_str: str, suffix: str = "") -> str:
    base, ext = os.path.splitext(latest_path)
    if base.endswith("_latest"):
        base = base[: -len("_latest")]
    return f"{base}_{date_str}{suffix}{ext}"


def _payload_content_equal(a: Optional[dict], b: dict) -> bool:
    # generated_at(実行のたびに変わるタイムスタンプ)を除いた実質的な内容
    # (count/items)だけを比較する。
    if a is None:
        return False
    return a.get("count") == b.get("count") and a.get("items") == b.get("items")


def save_with_history(payload: dict, latest_path: str, date_str: Optional[str] = None) -> dict:
    """`_latest.json`を参照用に更新しつつ、日付付き履歴ファイルも保存する
    (データガバナンス方針: 履歴は上書き・削除しない)。

    - 同日・同一内容(count/itemsが一致)の履歴ファイルが既にあれば、無意味な
      重複は作らずそのまま残す。
    - 同日でも内容が異なる場合(同日に再実行して結果が変わった等)は、既存の
      履歴ファイルを上書きせず、連番を付けた別ファイルとして保存する。
    - 過去の日付の履歴ファイルには一切触れない。
    """
    if date_str is None:
        date_str = datetime.now().astimezone().strftime("%Y%m%d")

    write_json(latest_path, payload)

    suffix = ""
    n = 1
    while True:
        history_path = _history_path_for(latest_path, date_str, suffix)
        if not os.path.exists(history_path):
            write_json(history_path, payload)
            return {"history_path": history_path, "history_written": True}
        if _payload_content_equal(read_json(history_path, default=None), payload):
            return {"history_path": history_path, "history_written": False}
        n += 1
        suffix = f"_{n}"


def run_ai_review_export(settings) -> Optional[dict]:
    catalog = load_snapshot(settings.latest_snapshot_path)
    selection = read_json(settings.ai_review_selection_path, default=None)
    if not selection:
        return None

    items = build_ai_review_export(catalog, selection)
    payload = {"generated_at": iso_now(), "count": len(items), "items": items}
    history = save_with_history(payload, settings.ai_review_export_path)
    return {**payload, "history_path": history["history_path"], "history_written": history["history_written"]}


def copy_to_shared_folder(export_path: str, target_dir: Optional[str]) -> dict:
    """Google Drive API等は一切使わず、Google Drive for Desktopがローカルに
    同期しているフォルダへ、書き出し済みのファイルをそのままコピーするだけ。
    target_dirが未設定、または実際にそのフォルダが存在しない(Google Drive for
    Desktopが未起動/未同期/パス違い等)場合は、何もせずその旨を返す。ファイル名
    はexport_pathと同じものをそのまま使う(リネームしない)。
    """
    if not target_dir:
        return {"copied": False, "reason": "target_dir_not_configured", "target_path": None}
    if not os.path.isdir(target_dir):
        return {"copied": False, "reason": "target_dir_not_found", "target_path": target_dir}

    filename = os.path.basename(export_path)
    target_path = os.path.join(target_dir, filename)
    shutil.copy2(export_path, target_path)
    return {"copied": True, "reason": None, "target_path": target_path}
