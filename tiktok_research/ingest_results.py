"""Workが取得したTikTok調査結果の取り込み(重複排除・整形)。

Workの出力形式は {"job_id": ..., "posts": [post_result, ...]} を想定。
schema.POST_RESULT_REQUIRED_KEYS が欠けているレコードは取り込まず、
理由付きで別途報告する(黙って捨てない)。値の推測補完は一切行わない。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .schema import POST_RESULT_FIELDS, POST_RESULT_REQUIRED_KEYS
from .utils import iso_now, read_json, write_json


def validate_post_result(record: dict) -> List[str]:
    """必須キーの欠落だけを検証する(値がnullなのは許容、キー自体の欠落のみ問題視)。"""
    return [key for key in POST_RESULT_REQUIRED_KEYS if key not in record]


def _normalize(record: dict) -> dict:
    """schemaに無い余計なキーは落とさず保持しつつ、既知フィールドがなければ
    nullで埋める(Work側の実装差異を吸収するため。値の中身は推測しない)。"""
    normalized = {field: record.get(field) for field in POST_RESULT_FIELDS}
    extra = {k: v for k, v in record.items() if k not in POST_RESULT_FIELDS}
    normalized["_work_extra_fields"] = extra or None
    return normalized


def dedupe_posts(posts: List[dict]) -> List[dict]:
    """video_id、それが無ければvideo_urlをキーに重複排除する
    (同じ検索語で複数回出てきた投稿を二重集計しないため)。"""
    seen = set()
    deduped = []
    for post in posts:
        key = post.get("video_id") or post.get("video_url")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(post)
    return deduped


def load_work_results(paths: List[str]) -> Tuple[List[dict], List[dict]]:
    """複数のWork結果ファイルを読み込み、(有効なpost一覧, 却下されたレコード一覧)を返す。"""
    valid_posts = []
    rejected = []
    for path in paths:
        data = read_json(path, default=None)
        if data is None:
            rejected.append({"source_file": path, "reason": "file_not_found_or_unreadable"})
            continue
        for post in data.get("posts", []):
            missing = validate_post_result(post)
            if missing:
                rejected.append({"source_file": path, "record": post, "reason": f"missing_required_keys:{missing}"})
                continue
            valid_posts.append(_normalize(post))
    return valid_posts, rejected


def ingest(result_paths: List[str], output_path: str) -> dict:
    valid_posts, rejected = load_work_results(result_paths)
    deduped = dedupe_posts(valid_posts)

    payload = {
        "generated_at": iso_now(),
        "source": "claude_code_ingest",
        "count": len(deduped),
        "rejected_count": len(rejected),
        "posts": deduped,
    }
    write_json(output_path, payload)

    return {
        "ingested_count": len(deduped),
        "duplicate_count": len(valid_posts) - len(deduped),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "output_path": output_path,
    }
