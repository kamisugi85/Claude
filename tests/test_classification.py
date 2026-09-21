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


def test_listing_ng_words_field_never_drives_sns_judgment_alone():
    # リスティングNGワード(実際は全角ＮＧ)is about search-ad bidding/keyword
    # restrictions, not SNS/TikTok distribution eligibility -- per project
    # policy it must never by itself cause an SNS/TikTok verdict either way.
    record = {"リスティングＮＧワード": "SNSでの紹介はNGです。TikTok NG。"}
    result = classify_sns_promotion(record)
    assert result["sns_basis"] == "no_explicit_restriction_found"


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


def test_no_sns_mention_defaults_to_allowed_not_held_on_silence_alone():
    # Project policy: A8 itself officially supports TikTok affiliate
    # promotion, so the mere absence of the word "SNS"/"TikTok" must never by
    # itself force a hold -- only an explicit restriction should.
    record = {"備考": "その他の注意事項はありません。", "成果条件": "WEB申込完了"}
    result = classify_sns_promotion(record)
    assert result["sns_verdict"] == "allowed"
    assert result["tiktok_verdict"] == "allowed"
    assert result["sns_basis"] == "no_explicit_restriction_found"


def test_sns_limited_to_other_platforms_excludes_tiktok():
    record = {"備考": "掲載可能SNSはInstagramのみとさせていただきます。"}
    result = classify_sns_promotion(record)
    assert result["tiktok_verdict"] == "prohibited"
    assert result["sns_basis"] == "sns_limited_to_other_platforms_excludes_tiktok"


def test_whitelist_pattern_does_not_trigger_when_tiktok_is_in_the_list():
    record = {"備考": "掲載可能SNSはInstagram・TikTokのみとさせていただきます。"}
    result = classify_sns_promotion(record)
    assert result["tiktok_verdict"] == "allowed"


def test_prohibition_heading_on_its_own_line_still_matches_sns_below_it():
    # 実データで確認した回帰バグ: A8の実際の表記は見出しと本文が改行で分かれて
    # いることが多く(「【禁止事項】\n・SNSでの掲載・投稿。」)、デフォルトの`.`は
    # 改行にマッチしないため、この形式だと明確な禁止文言を見逃し allowed に
    # 誤判定していた(案件: オクトパスエナジー s00000026570001)。
    record = {
        "備考": (
            "【禁止事項】\n"
            "・SNSでの掲載・投稿。\n"
            "※投稿形態(テキスト、画像、動画)は問わずになります。\n"
            "\n"
            "例：X(旧Twitter)、instagram、Tiktok、Youtube"
        )
    }
    result = classify_sns_promotion(record)
    assert result["sns_verdict"] == "prohibited"
    assert result["tiktok_verdict"] == "prohibited"
    assert result["sns_basis"] == "sns_explicit_ng"


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


def test_overall_likely_ok_when_no_explicit_restriction_found():
    record = {"category": "回線", "備考": "特になし"}
    result = classify_program(record)
    assert result["tiktok_overall"] == "likely_ok"


def test_overall_needs_ai_or_human_on_conditional_wording():
    record = {"category": "回線", "備考": "SNSアカウントの投稿について、事前にご相談ください。"}
    result = classify_program(record)
    assert result["tiktok_overall"] == "needs_ai_or_human"
