from __future__ import annotations

import csv
import json
import os
import re
from typing import Dict, List

from .utils import ensure_dir, iso_now


_PATH_PATTERN_KEYS = ["expected_path_pattern", "detail_expected_path_pattern"]

# extract_search_results() always produces exactly these 9 keys per record
# (including checked_at). A record with more than this many keys has had
# detail-page fields merged in; one with exactly this many is still
# list-level-only.
LIST_LEVEL_FIELD_COUNT = 9


def has_detail_fields(record: dict) -> bool:
    return len(record) > LIST_LEVEL_FIELD_COUNT


def select_detail_candidates(records: Dict[str, dict], previous_snapshot: Dict[str, dict]) -> List[str]:
    """Which program IDs (from this run's crawled records) should have their
    detail page fetched, in priority order: brand-new programs first, then
    already-known ones that still only have list-level fields (so a program
    that was list-only right after a big crawl eventually gets backfilled
    instead of being stuck without condition text forever).
    """
    new_ids = [pid for pid in records if pid not in previous_snapshot]
    backfill_ids = [
        pid for pid in records if pid in previous_snapshot and not has_detail_fields(previous_snapshot[pid])
    ]
    return new_ids + backfill_ids


def select_catalog_backfill_candidates(
    previous_snapshot: Dict[str, dict], exclude_ids: set, limit: int
) -> List[str]:
    """Extra backfill candidates pulled from the whole known catalog (not just
    this run's crawled pages), so detail-fetch capacity isn't wasted just
    because this run's page slice happened to contain few undetailed
    programs. Order follows dict iteration (insertion order), which is
    good enough -- there's no meaningful priority among them beyond "not
    detailed yet".
    """
    if limit <= 0:
        return []
    candidates = []
    for pid, record in previous_snapshot.items():
        if pid in exclude_ids or has_detail_fields(record):
            continue
        candidates.append(pid)
        if len(candidates) >= limit:
            break
    return candidates


def load_targets(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    targets = []
    for item in raw:
        compiled = {key: re.compile(item[key]) for key in _PATH_PATTERN_KEYS if item.get(key)}
        targets.append({**item, **compiled})
    return targets


def load_csv_column_map(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_SEARCH_RESULTS_JS = r"""
() => {
    const anchors = Array.from(document.querySelectorAll('a[href*="programId="]'));
    const seen = new Map();
    for (const a of anchors) {
        const m = a.href.match(/programId=([A-Za-z0-9]+)/);
        if (!m) continue;
        const programId = m[1];
        if (seen.has(programId)) continue;

        let card = a;
        for (let i = 0; i < 8 && card.parentElement; i++) {
            card = card.parentElement;
            if (card.innerText && card.innerText.includes('成果報酬')) break;
        }
        const text = card.innerText || '';

        function afterLabel(label) {
            const idx = text.indexOf(label);
            if (idx === -1) return '';
            const rest = text.slice(idx + label.length).split('\n').map(s => s.trim()).filter(Boolean);
            return rest.length ? rest[0] : '';
        }

        // The programId anchor itself is often an image link with no text
        // (e.g. the banner). Fall back to the longest anchor text inside the
        // card, which is usually the program title link.
        let name = (a.innerText || '').trim();
        if (!name) {
            let best = '';
            for (const cand of card.querySelectorAll('a')) {
                const t = (cand.innerText || '').trim();
                if (t.length > best.length && t.length < 200) best = t;
            }
            name = best;
        }

        seen.set(programId, {
            program_id: programId,
            detail_url: a.href,
            name: name,
            reward: afterLabel('成果報酬'),
            epc: afterLabel('EPC'),
            conversion_rate: afterLabel('確定率'),
            category: afterLabel('カテゴリ'),
            start_date: afterLabel('プログラム開始日'),
        });
    }

    const bodyText = document.body.innerText || '';
    const countMatch = bodyText.match(/該当件数\s*([\d,]+)\s*件/);
    const totalCount = countMatch ? parseInt(countMatch[1].replace(/,/g, ''), 10) : null;

    return { records: Array.from(seen.values()), totalCount };
}
"""


def extract_programs(page) -> Dict[str, dict]:
    """Generic single-page `browse` target extraction, for one-off pages
    outside the paginated search crawl. Reuses the same programId-link
    heuristic as extract_search_results.
    """
    records, _ = extract_search_results(page)
    return records


def extract_search_results(page):
    """!!! 要検証 !!!

    プログラム検索結果一覧から各プログラムを抽出する。プログラムID・詳細ページURLは
    リンクの `programId=` パラメータから取得しており、これはDOM構造に依存しないため
    比較的信頼できる。一方 成果報酬/EPC/確定率/カテゴリ 等はページのテキストレイアウト
    に依存したベストエフォートの抽出のため、実際の表示と一致するか確認が必要。

    Returns: (records: Dict[str, dict], total_count: Optional[int])
    """
    result = page.evaluate(_SEARCH_RESULTS_JS)
    checked_at = iso_now()
    records: Dict[str, dict] = {r["program_id"]: {**r, "checked_at": checked_at} for r in result["records"]}
    return records, result.get("totalCount")


_DETAIL_SECTIONS_JS = r"""
() => {
    const headingSelectors = ['h1', 'h2', 'h3', 'h4', 'dt', 'th'];
    const sections = {};
    const headings = Array.from(document.querySelectorAll(headingSelectors.join(',')));
    for (const h of headings) {
        const label = (h.innerText || '').trim();
        if (!label || label.length > 40) continue;
        let body = '';
        if (h.tagName === 'DT' && h.nextElementSibling && h.nextElementSibling.tagName === 'DD') {
            body = h.nextElementSibling.innerText || '';
        } else if (h.tagName === 'TH' && h.nextElementSibling && h.nextElementSibling.tagName === 'TD') {
            body = h.nextElementSibling.innerText || '';
        } else {
            let sib = h.nextElementSibling;
            let hops = 0;
            while (sib && hops < 3 && !(sib.innerText && sib.innerText.trim())) {
                sib = sib.nextElementSibling;
                hops++;
            }
            body = sib ? (sib.innerText || '') : '';
        }
        body = body.trim();
        if (body) sections[label] = body;
    }
    return sections;
}
"""


# List-crawl field names (case-insensitive). A detail-page heading that
# collides with one of these -- e.g. a section literally titled "EPC" next
# to our own lowercase "epc" -- gets skipped, since JSON is case-sensitive
# but several JSON consumers (PowerShell's ConvertFrom-Json among them)
# treat keys case-insensitively and error out on the duplicate.
_RESERVED_FIELD_NAMES = {
    "program_id",
    "url",
    "detail_url",
    "name",
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
}


def dedupe_case_insensitive(sections: Dict[str, str], reserved: set) -> Dict[str, str]:
    deduped: Dict[str, str] = {}
    seen_lower = {name.lower() for name in reserved}
    for label, body in sections.items():
        lowered = label.lower()
        if lowered in seen_lower:
            continue
        seen_lower.add(lowered)
        deduped[label] = body
    return deduped


def extract_program_detail(page, program_id: str, url: str) -> dict:
    """!!! 要検証 !!!

    プログラム詳細ページの「見出し + 本文」を汎用的に(見出しタグ名を手がかりに)抽出する。
    A8側が項目を追加・変更しても、決め打ちの6項目に縛られず自動的に追従できるようにする
    ための設計。実際の見出しタグ・クラス名が不明なため要検証。
    """
    sections = page.evaluate(_DETAIL_SECTIONS_JS)
    deduped = dedupe_case_insensitive(sections, _RESERVED_FIELD_NAMES)
    return {"program_id": program_id, "url": url, "checked_at": iso_now(), **deduped}


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
