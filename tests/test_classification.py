from a8_automation.classification import classify_category_risk, classify_program, classify_sns_promotion


def test_explicit_sns_ng_is_prohibited():
    record = {"備考": "SNSへの投稿は一切NGです。"}
    result = classify_sns_promotion(record)
    assert result["sns_verdict"] == "prohibited"
    assert result["tiktok_verdict"] == "prohibited"


def test_explicit_tiktok_ng_overrides_general_sns_ok():
    record = {"備考": "SNS投稿OKですが、TikTokでの紹介はNGとします。"}
    result = classify_sns_promotion(record)
    assert result["tiktok_verdict"] == "prohibited"


def test_explicit_sns_ok_implies_tiktok_ok_by_project_rule():
    record = {"備考": "SNSでの紹介OKです。"}
    result = classify_sns_promotion(record)
    assert result["sns_verdict"] == "allowed"
    assert result["tiktok_verdict"] == "allowed"
    assert result["sns_basis"] == "sns_ok_implies_tiktok_ok"


def test_conditional_wording_is_not_forced_into_allowed_or_prohibited():
    record = {"備考": "SNSアカウントの投稿について、広告主サービスに関連の無い投稿はNGとします。"}
    result = classify_sns_promotion(record)
    assert result["sns_verdict"] == "conditional"


def test_no_mention_at_all_is_unclear_never_guessed():
    record = {"備考": "その他の注意事項はありません。", "成果条件": "WEB申込完了"}
    result = classify_sns_promotion(record)
    assert result["sns_verdict"] == "unclear"
    assert result["tiktok_verdict"] == "unclear"


def test_category_risk_high_for_known_keyword():
    result = classify_category_risk({"category": "ギャンブル・カジノ"})
    assert result["category_risk"] == "high"


def test_category_risk_low_for_ordinary_category():
    result = classify_category_risk({"category": "回線"})
    assert result["category_risk"] == "low"


def test_category_risk_unknown_when_missing():
    result = classify_category_risk({})
    assert result["category_risk"] == "unknown"


def test_overall_likely_excluded_on_explicit_sns_ng():
    record = {"category": "回線", "備考": "SNSでの紹介は禁止します。"}
    result = classify_program(record)
    assert result["tiktok_overall"] == "likely_excluded"


def test_overall_likely_excluded_on_risky_category_even_if_sns_ok():
    record = {"category": "アダルト", "備考": "SNS投稿OKです。"}
    result = classify_program(record)
    assert result["tiktok_overall"] == "likely_excluded"


def test_overall_likely_ok_when_explicit_and_safe():
    record = {"category": "回線", "備考": "SNSでの紹介OKです。"}
    result = classify_program(record)
    assert result["tiktok_overall"] == "likely_ok"


def test_overall_needs_ai_or_human_when_unclear():
    record = {"category": "回線", "備考": "特になし"}
    result = classify_program(record)
    assert result["tiktok_overall"] == "needs_ai_or_human"
