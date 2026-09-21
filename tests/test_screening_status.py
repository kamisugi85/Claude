from a8_automation.screening_status import classify_screening_status


def test_status_keyword_is_excluded_clear():
    record = {"program_id": "1", "備考": "現在、募集停止中です"}
    result = classify_screening_status(record)
    assert result["screening_status"] == "excluded_clear"
    assert "status_keyword" in result["screening_reason"]


def test_explicit_tiktok_ng_is_excluded_clear():
    record = {"program_id": "1", "category": "回線", "備考": "TikTokでの紹介はNGとします。"}
    result = classify_screening_status(record)
    assert result["screening_status"] == "excluded_clear"


def test_risky_category_is_excluded_clear():
    record = {"program_id": "1", "category": "ギャンブル"}
    result = classify_screening_status(record)
    assert result["screening_status"] == "excluded_clear"
    assert "category_keyword" in result["screening_reason"]


def test_sns_limited_to_other_platforms_is_excluded_clear():
    record = {"program_id": "1", "category": "回線", "備考": "掲載可能SNSはInstagramのみとさせていただきます。"}
    result = classify_screening_status(record)
    assert result["screening_status"] == "excluded_clear"


def test_explicit_sns_ok_is_eligible_clear():
    record = {"program_id": "1", "category": "回線", "備考": "SNSでの紹介OKです。"}
    result = classify_screening_status(record)
    assert result["screening_status"] == "eligible_clear"


def test_no_mention_at_all_is_eligible_clear_not_held():
    record = {"program_id": "1", "category": "回線", "備考": "特になし"}
    result = classify_screening_status(record)
    assert result["screening_status"] == "eligible_clear"


def test_conditional_wording_is_needs_language_review():
    record = {"program_id": "1", "category": "回線", "備考": "SNSアカウントの投稿について、事前にご相談ください。"}
    result = classify_screening_status(record)
    assert result["screening_status"] == "needs_language_review"


def test_listing_ng_words_alone_never_causes_exclusion():
    record = {
        "program_id": "1",
        "category": "回線",
        "リスティングＮＧワード": "弊社名, ブランド名, TikTok",
    }
    result = classify_screening_status(record)
    assert result["screening_status"] == "eligible_clear"
