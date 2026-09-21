from a8_automation.conversion_action_diagnostic import (
    build_diagnostic_report,
    collect_conversion_action_texts,
    keyword_frequency,
    match_count_distribution,
    sample_texts_by_match_count,
    select_target_ids,
)


def test_select_target_ids_excludes_excluded_clear():
    catalog = {
        "a": {"program_id": "a", "screening_status": "eligible_clear"},
        "b": {"program_id": "b", "screening_status": "needs_language_review"},
        "c": {"program_id": "c", "screening_status": "excluded_clear"},
        "d": {"program_id": "d"},  # no screening_status at all -- not a target
    }
    assert sorted(select_target_ids(catalog)) == ["a", "b"]


def test_collect_conversion_action_texts_skips_missing_and_empty():
    catalog = {
        "a": {"program_id": "a", "成果条件": "資料請求完了で成果"},
        "b": {"program_id": "b", "成果条件": ""},
        "c": {"program_id": "c"},
    }
    texts = collect_conversion_action_texts(catalog, ["a", "b", "c"])
    assert texts == {"a": "資料請求完了で成果"}


def test_keyword_frequency_counts_presence_only():
    texts = {
        "a": "資料請求完了で成果",
        "b": "無料会員登録完了後に成果",
        "c": "資料請求または無料会員登録",
    }
    freq = keyword_frequency(texts)
    assert freq["資料請求"] == 2
    assert freq["無料会員登録"] == 2
    assert freq["会員登録"] == 2  # substring of 無料会員登録, also counted


def test_match_count_distribution_groups_by_number_of_distinct_keyword_hits():
    texts = {
        "a": "全く未知の表現のみ",
        "b": "資料請求完了で成果",
        "c": "資料請求または無料会員登録",
    }
    dist = match_count_distribution(texts)
    assert dist[0] == 1  # "a" -- no candidate keyword found
    assert dist[1] == 1  # "b" -- single candidate keyword
    assert dist[3] == 1  # "c" -- 資料請求 + 無料会員登録 + 会員登録(部分一致)の3種ヒット


def test_sample_texts_by_match_count_filters_and_limits():
    texts = {str(i): "全く未知の表現" for i in range(20)}
    samples = sample_texts_by_match_count(texts, match_count=0, limit=5)
    assert len(samples) == 5
    assert all(s["matched_keywords"] == [] for s in samples)


def test_build_diagnostic_report_does_not_mutate_catalog_or_screening_status():
    catalog = {
        "a": {"program_id": "a", "screening_status": "eligible_clear", "成果条件": "資料請求完了で成果"},
        "b": {"program_id": "b", "screening_status": "needs_language_review", "成果条件": "未知の表現のみ"},
        "c": {"program_id": "c", "screening_status": "excluded_clear", "成果条件": "資料請求完了で成果"},
    }
    before = {pid: dict(record) for pid, record in catalog.items()}

    report = build_diagnostic_report(catalog)

    assert catalog == before  # no mutation -- diagnostic only
    assert report["target_total"] == 2
    assert report["texts_collected"] == 2
    assert report["texts_missing"] == 0
    assert report["keyword_frequency"]["資料請求"] == 1
    assert "0" in report["match_count_distribution"]
    assert "1" in report["match_count_distribution"]
    assert set(report["samples_by_match_count"].keys()) == {"0", "1", "2", "3", "4"}


def test_build_diagnostic_report_handles_empty_catalog():
    report = build_diagnostic_report({})
    assert report["target_total"] == 0
    assert report["texts_collected"] == 0
    assert report["keyword_frequency"] == {}
