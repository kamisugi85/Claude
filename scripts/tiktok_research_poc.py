#!/usr/bin/env python3
"""TikTok Researcher -- 最小技術PoC(1案件のみ、本番実装ではない)。

目的: 「保育士人材バンク」を対象に、ログイン・APIキー・新規登録・CAPTCHA
回避・アクセス制限回避を一切行わずに、既知のTikTok投稿URLからメタデータを
取得・構造化できるかを検証する。

使う方式: TikTok公式oEmbed API(https://www.tiktok.com/oembed)。公開・
キー不要・ログイン不要で、TikTok自身が埋め込み用途として提供している
公式エンドポイント(非公式スクレイピングではない)。

このPoCの範囲外(このスクリプトではやらないこと):
- キーワード→投稿URLの発見(検索)。今回はClaudeのWeb検索で事前に見つけた
  URLを data/tiktok_research/poc/candidate_urls.json に入力として与える。
  plain Pythonスクリプトから同等の検索を無登録・無料で自動化する方法は
  今回のSTEP1調査では見つからなかった(詳細は完了報告を参照)。
- 再生数・いいね数・コメント数・シェア数・投稿日時・動画尺の取得。
  oEmbedはこれらを一切返さないため、常にnullのまま出力し、推測で埋めない。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_HERE)
_INPUT_PATH = os.path.join(_BASE_DIR, "data", "tiktok_research", "poc", "candidate_urls.json")
_OUTPUT_PATH = os.path.join(_BASE_DIR, "data", "tiktok_research", "poc", "oembed_results.json")

_OEMBED_ENDPOINT = "https://www.tiktok.com/oembed"
_VIDEO_ID_PATTERN = re.compile(r"/video/(\d+)")
_HASHTAG_PATTERN = re.compile(r"#\S+")


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def extract_video_id(url: str) -> Optional[str]:
    m = _VIDEO_ID_PATTERN.search(url)
    return m.group(1) if m else None


def extract_hashtags(caption: Optional[str]) -> list:
    return _HASHTAG_PATTERN.findall(caption or "")


def fetch_oembed(url: str, timeout: float = 15.0) -> dict:
    query = urllib.parse.urlencode({"url": url})
    request = urllib.request.Request(
        f"{_OEMBED_ENDPOINT}?{query}", headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def build_record(candidate: dict) -> dict:
    url = candidate["url"]
    record = {
        "source_keyword": candidate.get("source_keyword"),
        "url": url,
        "video_id": extract_video_id(url),
        "fetched_at": iso_now(),
        "fetch_status": None,
        "author_handle": None,
        "caption_text": None,
        "hashtags": None,
        "thumbnail_url": None,
        # oEmbedでは取得不可な項目 -- 推測せず常にnullのまま出力する
        "view_count": None,
        "like_count": None,
        "comment_count": None,
        "share_count": None,
        "posted_at": None,
        "video_duration_seconds": None,
    }
    try:
        data = fetch_oembed(url)
    except urllib.error.HTTPError as e:
        record["fetch_status"] = f"http_error_{e.code}"
        return record
    except urllib.error.URLError as e:
        record["fetch_status"] = f"url_error_{e.reason}"
        return record

    record["fetch_status"] = "ok"
    record["author_handle"] = data.get("author_name")
    record["caption_text"] = data.get("title")
    record["hashtags"] = extract_hashtags(data.get("title"))
    record["thumbnail_url"] = data.get("thumbnail_url")
    return record


def main() -> int:
    with open(_INPUT_PATH, "r", encoding="utf-8") as f:
        candidates = json.load(f)["candidates"]

    records = [build_record(c) for c in candidates]
    ok_count = sum(1 for r in records if r["fetch_status"] == "ok")

    print(f"取得試行: {len(records)}件 / 成功: {ok_count}件")
    for r in records:
        print(f"  [{r['fetch_status']}] {r['url']}")

    payload = {"generated_at": iso_now(), "count": len(records), "items": records}
    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"保存先: {_OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
