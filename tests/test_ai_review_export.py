from a8_automation.ai_review_export import build_ai_review_export, build_ai_review_export_record


def make_selected_record(pid, **extra):
    record = {
        "program_id": pid,
        "name": f"プログラム{pid}",
        "category": "回線",
        "reward": "1000",
        "epc": "10",
        "conversion_rate": "50",
        "成果条件": "資料請求完了で成果",
        "否認条件": "不備がある場合は否認",
        "備考": "SNSでの紹介OKです。",
        "ai_review_selected": True,
        "ai_review_tier": "epc",
        "ai_review_reason": "epc_rank_1",
        "ai_review_rank": 1,
        "screening_status": "eligible_clear",
        "screening_reason": "sns_ok_implies_tiktok_ok",
        "detail_url": "https://media-console.a8.net/detail?programId=" + pid,
        "checked_at": "2026-09-21T00:00:00+09:00",
    }
    record.update(extra)
    return record


def test_build_record_maps_known_fields_without_fabricating_missing_ones():
    record = make_selected_record("p1")
    exported = build_ai_review_export_record(record)

    assert exported["program_id"] == "p1"
    assert exported["program_name"] == "プログラムp1"
    assert exported["advertiser_name"] is None  # 未取得 -- 推測しない
    assert exported["category"] == "回線"
    assert exported["reward"] == "1000"
    assert exported["epc"] == "10"
    assert exported["conversion_rate"] == "50"
    assert exported["conversion_action_raw_text"] == "資料請求完了で成果"
    assert exported["rejection_condition"] == "不備がある場合は否認"
    assert exported["remarks"] == "SNSでの紹介OKです。"
    assert exported["ai_review_tier"] == "epc"
    assert exported["ai_review_reason"] == "epc_rank_1"
    assert exported["ai_review_rank"] == 1
    assert exported["screening_status"] == "eligible_clear"
    assert exported["sns_condition"]["tiktok_verdict"] == "allowed"


def test_advertiser_name_is_picked_up_when_present_as_a_generic_heading():
    record = make_selected_record("p1", **{"広告主名": "株式会社サンプル"})
    exported = build_ai_review_export_record(record)
    assert exported["advertiser_name"] == "株式会社サンプル"


def test_unmapped_detail_fields_are_passed_through_without_being_dropped():
    record = make_selected_record("p1", **{"リスティングＮＧワード": "弊社名, ブランド名"})
    exported = build_ai_review_export_record(record)
    assert exported["other_detail_fields"]["リスティングＮＧワード"] == "弊社名, ブランド名"
    # マッピング済みフィールドはother_detail_fieldsに重複して入らない
    assert "成果条件" not in exported["other_detail_fields"]
    assert "reward" not in exported["other_detail_fields"]


def test_build_ai_review_export_follows_selection_population_order_and_skips_missing():
    catalog = {
        "a": make_selected_record("a"),
        "b": make_selected_record("b"),
        "c": make_selected_record("c"),
    }
    selection = {"epc_tier": ["b", "a"], "non_epc_tier": ["c", "missing"]}
    items = build_ai_review_export(catalog, selection)
    assert [item["program_id"] for item in items] == ["b", "a", "c"]
