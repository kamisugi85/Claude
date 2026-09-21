"""Creative Intelligence生成(単純な再生数ランキングにはしない)。

投稿日・アカウント規模・再生数・エンゲージメントを可能な限り分離して集計し、
フック/尺/形式/人物有無/CTA/コメントの悩みテーマ等を頻度ベースで可視化する。
「勝ち構造」の抽象化(ターゲット名指し→悩み→意外性→3ポイント→CTA、のような
テンプレート)は、実データが一定件数そろってから初めて意味を持つ判断であり、
データが無い/少ない状態で機械的にでっち上げない(data_sufficiencyで明示する)。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from .utils import iso_now, write_json

_MIN_SAMPLE_FOR_TREND = 5


def _bool_distribution(posts: List[dict], field: str) -> dict:
    counter = Counter()
    for p in posts:
        v = p.get(field)
        key = "true" if v is True else "false" if v is False else "unknown"
        counter[key] += 1
    return dict(counter)


def _value_distribution(posts: List[dict], field: str) -> dict:
    counter = Counter(p.get(field) for p in posts if p.get(field) is not None)
    return dict(counter.most_common())


def _duration_buckets(posts: List[dict]) -> dict:
    buckets = Counter()
    for p in posts:
        d = p.get("duration_seconds")
        if d is None:
            continue
        if d <= 15:
            buckets["<=15s"] += 1
        elif d <= 30:
            buckets["16-30s"] += 1
        elif d <= 60:
            buckets["31-60s"] += 1
        else:
            buckets["60s+"] += 1
    return dict(buckets)


def _engagement_rates(posts: List[dict]) -> List[dict]:
    """view_count・like_count・comment_countが揃っている投稿だけを対象に、
    (いいね+コメント)/再生数を計算する。欠損がある投稿は対象外(捏造しない)。"""
    rates = []
    for p in posts:
        views = p.get("view_count")
        likes = p.get("like_count")
        comments = p.get("comment_count")
        if views and likes is not None and comments is not None and views > 0:
            rates.append({
                "video_id": p.get("video_id") or p.get("video_url"),
                "engagement_rate": round((likes + comments) / views, 4),
                "view_count": views,
            })
    return rates


def _comment_theme_frequency(posts: List[dict]) -> dict:
    counter = Counter()
    for p in posts:
        for theme in (p.get("top_comment_themes") or []):
            counter[theme] += 1
    return dict(counter.most_common())


def summarize_program(program_id: str, posts: List[dict]) -> dict:
    posts_with_dates = [p for p in posts if p.get("posted_at")]

    return {
        "program_id": program_id,
        "post_count": len(posts),
        "posts_with_posted_at_count": len(posts_with_dates),
        "data_sufficiency": (
            "no_data" if len(posts) == 0 else
            "low_sample_treat_as_directional_only" if len(posts) < _MIN_SAMPLE_FOR_TREND else
            "ok"
        ),
        "format_distribution": {
            "has_face": _bool_distribution(posts, "has_face"),
            "has_person": _bool_distribution(posts, "has_person"),
            "ai_character_like": _bool_distribution(posts, "ai_character_like"),
            "is_ugc_style": _bool_distribution(posts, "is_ugc_style"),
            "is_broll": _bool_distribution(posts, "is_broll"),
            "is_conversation_or_skit": _bool_distribution(posts, "is_conversation_or_skit"),
        },
        "duration_distribution": _duration_buckets(posts),
        "caption_style_distribution": _value_distribution(posts, "caption_style"),
        "narration_style_distribution": _value_distribution(posts, "narration_style"),
        "ad_pr_likeness_distribution": _value_distribution(posts, "ad_pr_likeness"),
        "cta_text_samples": [p["cta_text"] for p in posts if p.get("cta_text")][:20],
        "engagement_rates": _engagement_rates(posts),
        "comment_theme_frequency": _comment_theme_frequency(posts),
        "recency_trend_analysis": (
            "posted_atが取得できた投稿が無いため、時系列比較(伸びている形式/飽和している形式)は実施不可。"
            if not posts_with_dates else
            "posted_atがある投稿が少数のため、参考程度の傾向にとどめる。" if len(posts_with_dates) < _MIN_SAMPLE_FOR_TREND else
            "posted_atに基づく時系列比較が可能な件数が揃っている。"
        ),
        "winning_structure_template": None,  # 実データが十分集まってから人間/Claudeが要約する(機械的な断定はしない)
    }


def build_creative_intelligence(posts: List[dict], program_ids: List[str]) -> dict:
    by_program: Dict[str, List[dict]] = {pid: [] for pid in program_ids}
    for p in posts:
        pid = p.get("program_id")
        if pid in by_program:
            by_program[pid].append(p)

    programs = {pid: summarize_program(pid, plist) for pid, plist in by_program.items()}

    return {
        "generated_at": iso_now(),
        "source": "claude_code",
        "note": (
            "単純な再生数ランキングではない。投稿日・エンゲージメント・形式を分離して集計している。"
            "各program_idのdata_sufficiencyを必ず確認し、no_data/low_sampleの場合は"
            "傾向として扱わないこと。"
        ),
        "total_posts_ingested": len(posts),
        "programs": programs,
    }


def save_creative_intelligence(result: dict, path: str) -> None:
    write_json(path, result)
