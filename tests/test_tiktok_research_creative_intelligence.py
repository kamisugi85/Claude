from tiktok_research.creative_intelligence import build_creative_intelligence, summarize_program


def make_post(**overrides):
    post = {
        "program_id": "s000001",
        "has_face": False,
        "has_person": False,
        "is_ugc_style": True,
        "cta_text": "プロフィールへ",
        "duration_seconds": 22,
        "view_count": 10000,
        "like_count": 500,
        "comment_count": 20,
        "top_comment_themes": ["給料が低い"],
    }
    post.update(overrides)
    return post


def test_summarize_program_reports_no_data_when_empty():
    summary = summarize_program("s000001", [])
    assert summary["post_count"] == 0
    assert summary["data_sufficiency"] == "no_data"
    assert summary["winning_structure_template"] is None


def test_summarize_program_flags_low_sample_under_threshold():
    posts = [make_post() for _ in range(3)]
    summary = summarize_program("s000001", posts)
    assert summary["data_sufficiency"] == "low_sample_treat_as_directional_only"


def test_summarize_program_does_not_rank_by_views_alone():
    posts = [make_post(video_id=str(i), view_count=1000 * (i + 1)) for i in range(6)]
    summary = summarize_program("s000001", posts)
    # 出力にview_count単体のランキングフィールドが無いこと(エンゲージメント率と分離)
    assert "view_count_ranking" not in summary
    assert "engagement_rates" in summary
    assert "format_distribution" in summary


def test_summarize_program_never_fabricates_engagement_when_views_missing():
    posts = [make_post(view_count=None)]
    summary = summarize_program("s000001", posts)
    assert summary["engagement_rates"] == []


def test_summarize_program_reports_no_recency_trend_without_posted_at():
    posts = [make_post() for _ in range(6)]  # posted_atなし
    summary = summarize_program("s000001", posts)
    assert "時系列比較" in summary["recency_trend_analysis"]
    assert "不可" in summary["recency_trend_analysis"]


def test_build_creative_intelligence_separates_by_program_id():
    posts = [make_post(program_id="a"), make_post(program_id="b"), make_post(program_id="b")]
    result = build_creative_intelligence(posts, program_ids=["a", "b", "c"])
    assert result["programs"]["a"]["post_count"] == 1
    assert result["programs"]["b"]["post_count"] == 2
    assert result["programs"]["c"]["post_count"] == 0
    assert result["programs"]["c"]["data_sufficiency"] == "no_data"
