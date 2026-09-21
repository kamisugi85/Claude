from a8_automation.screen import screen_catalog, screen_items


def test_screen_catalog_excludes_only_confident_matches():
    catalog = {
        "1": {"program_id": "1", "category": "回線", "備考": "SNS紹介OK"},
        "2": {"program_id": "2", "category": "アダルト"},
        "3": {"program_id": "3", "備考": "現在募集停止中です"},
        "4": {"program_id": "4", "category": "回線", "備考": "特になし"},
    }
    excluded_ids, reason_counts, overall_counts = screen_catalog(catalog)

    assert excluded_ids == {"2", "3"}
    assert any(r.startswith("tiktok_policy:category_keyword") for r in reason_counts)
    assert any(r.startswith("status_keyword:") for r in reason_counts)
    assert overall_counts["likely_ok"] == 1  # program 1
    assert overall_counts["likely_excluded"] == 1  # program 2 (risky category)
    # program 3 is excluded via the status keyword (a separate, independent
    # signal) but its own SNS/TikTok wording is unclear on its own terms;
    # program 4's is unclear too -- both correctly need AI/human judgment.
    assert overall_counts["needs_ai_or_human"] == 2


def test_screen_items_keeps_everything_not_in_excluded_ids_no_score_cutoff():
    items = [
        {"program_id": "1", "score": 5},
        {"program_id": "2", "score": 9999},  # high score but excluded -- must still be dropped
        {"program_id": "3", "score": 0.1},  # low score but not excluded -- must be kept
    ]
    excluded_ids = {"2"}
    result = screen_items(items, excluded_ids)
    assert [i["program_id"] for i in result] == ["1", "3"]
