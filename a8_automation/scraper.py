from __future__ import annotations

import csv
import json
import os
import re
from typing import Dict, List

from .utils import ensure_dir


def load_targets(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    targets = []
    for item in raw:
        targets.append(
            {
                **item,
                "expected_path_pattern": (
                    re.compile(item["expected_path_pattern"]) if item.get("expected_path_pattern") else None
                ),
            }
        )
    return targets


def load_csv_column_map(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_programs(page) -> Dict[str, dict]:
    """!!! 要検証・要調整 !!!

    実際のA8管理画面(閲覧ページ)のDOM構造に合わせてセレクタを調整してください。
    ここでの実装は一般的な `data-program-id` / `data-field` 属性を持つ構造を仮定した
    プレースホルダです。本番運用前に必ず実サイトのHTMLを確認して書き換えてください。
    """
    records: Dict[str, dict] = {}
    rows = page.query_selector_all("[data-program-id]")
    for row in rows:
        program_id = row.get_attribute("data-program-id")
        if not program_id:
            continue

        def _text(selector: str) -> str:
            el = row.query_selector(selector)
            return el.inner_text().strip() if el else ""

        records[program_id] = {
            "program_id": program_id,
            "name": _text("[data-field='name']"),
            "reward": _text("[data-field='reward']"),
            "reward_condition": _text("[data-field='reward_condition']"),
            "sns_condition": _text("[data-field='sns_condition']"),
            "approval_condition": _text("[data-field='approval_condition']"),
            "rejection_condition": _text("[data-field='rejection_condition']"),
            "prohibited_items": _text("[data-field='prohibited_items']"),
        }
    return records


def download_csv(page, target: dict, download_dir: str, run_id: str) -> str:
    ensure_dir(download_dir)
    selector = target.get("download_trigger_selector")
    with page.expect_download() as download_info:
        if selector:
            page.click(selector)
        else:
            page.goto(target["url"])
    download = download_info.value
    dest_path = os.path.join(download_dir, f"{run_id}_{target['name']}.csv")
    download.save_as(dest_path)
    return dest_path


def parse_csv(csv_path: str, column_map: dict) -> Dict[str, dict]:
    id_column = column_map["id_column"]
    columns = column_map["columns"]
    records: Dict[str, dict] = {}

    with open(csv_path, "r", encoding=column_map.get("encoding", "utf-8-sig"), newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            program_id = row.get(id_column)
            if not program_id:
                continue
            record = {field: row.get(csv_col, "") for field, csv_col in columns.items()}
            record["program_id"] = program_id
            records[program_id] = record
    return records
