"""TikTok Competitive Intelligence基盤で使うデータ構造の定義(ドキュメントを
兼ねたキー一覧)。実行時のバリデーションは各モジュールの関数側で行う。
"""
from __future__ import annotations

# A8案件から受け取る入力(既存A8 Collector/エクスポート済みデータからそのまま
# 取り出すだけで、新たな取得・推測は行わない)。
PROGRAM_INPUT_FIELDS = [
    "program_id",
    "program_name",
    "category",
    "reward",
    "conversion_conditions",
    "sns_tiktok_conditions",
    "prohibited_expressions",
    "material_conditions",
    "advertiser_specific_restrictions",
]

# Research Job(Claude Code -> Work)で渡す1案件分の調査依頼。
RESEARCH_JOB_PROGRAM_FIELDS = [
    "program_id",
    "program_name",
    "occupation_keyword",
    "search_queries",
]

# Work(TikTokへの実アクセスを行う側)が返す、投稿1件ごとのデータ。
# 取得できない項目はnullのままとし、推測値を入れないこと。
POST_RESULT_FIELDS = [
    "program_id",
    "search_query",
    "video_url",
    "video_id",
    "creator_handle",
    "posted_at",
    "view_count",
    "like_count",
    "comment_count",
    "share_count",
    "save_count",
    "duration_seconds",
    "hook_first_3s",
    "structure_summary",
    "has_face",
    "has_person",
    "ai_character_like",
    "is_ugc_style",
    "is_broll",
    "is_conversation_or_skit",
    "caption_style",
    "narration_style",
    "cta_text",
    "ad_pr_likeness",
    "top_comment_themes",
    "observed_at",
    "notes",
]

# Work結果として最低限これが無いと使い物にならない必須キー(値がnullなのは
# 許容するが、キー自体は無いとダブり判定・突合ができないため必須とする)。
POST_RESULT_REQUIRED_KEYS = ["program_id", "search_query", "video_url"]
