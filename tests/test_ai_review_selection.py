from a8_automation.ai_review_selection import (
    build_ai_review_selection,
    safe_conversion_action_category,
    save_ai_review_selection,
    select_epc_tier,
    select_non_epc_tier,
)


def make_record(pid, screening_status="eligible_clear", epc=None, reward=None, conversion_rate=None, extra=None):
    record = {
        "program_id": pid,
        "screening_status": screening_status,
        "category": "回線",
        "epc": epc or "",
        "reward": reward or "",
        "conversion_rate": conversion_rate or "",
    }
    if extra:
        record.update(extra)
    return record


def test_safe_conversion_action_category_requires_exactly_one_substantive_hit():
    assert safe_conversion_action_category("資料請求完了で成果") == "資料請求"
    assert safe_conversion_action_category("資料請求または無料相談で成果") is None  # 2種ヒット
    assert safe_conversion_action_category("WEB申込後、30日以内に成約") is None  # 補助語のみ
    assert safe_conversion_action_category(None) is None
    assert safe_conversion_action_category("") is None


def test_select_epc_tier_ranks_by_epc_descending_and_respects_limit():
    catalog = {
        "a": make_record("a", epc="5"),
        "b": make_record("b", epc="10"),
        "c": make_record("c", epc="1"),
        "d": make_record("d"),  # no epc -- excluded from this tier
    }
    tier = select_epc_tier(["a", "b", "c", "d"], catalog, limit=2)
    assert tier == ["b", "a"]


def test_select_non_epc_tier_prefers_reward_then_rate_then_no_signal_never_excludes():
    catalog = {
        "a": make_record("a", reward="1000"),
        "b": make_record("b", conversion_rate="50"),
        "c": make_record("c"),  # no reward, no rate -- still included, not excluded
        "d": make_record("d", epc="3"),  # has epc -- excluded from non-epc tier
    }
    tier = select_non_epc_tier(["a", "b", "c", "d"], catalog, exclude_ids=set(), limit=10)
    assert tier == ["a", "b", "c"]


def test_build_ai_review_selection_excludes_excluded_clear_and_respects_60_split():
    catalog = {}
    for i in range(50):
        catalog[f"epc{i}"] = make_record(f"epc{i}", epc=str(100 - i))
    for i in range(20):
        catalog[f"noepc{i}"] = make_record(f"noepc{i}", reward=str(100 - i))
    catalog["excluded"] = make_record("excluded", screening_status="excluded_clear", epc="9999")

    result = build_ai_review_selection(catalog, epc_limit=45, non_epc_limit=15)

    assert result["epc_tier_count"] == 45
    assert result["non_epc_tier_count"] == 15
    assert result["population_count"] == 60
    assert "excluded" not in result["population"]
    # EPCあり枠は最高EPCから45件
    assert result["epc_tier"][0] == "epc0"
    assert result["epc_range"]["max"] == 100.0
    assert result["epc_range"]["min"] == 56.0


def test_build_ai_review_selection_does_not_fabricate_epc_and_reports_shortfall():
    catalog = {f"epc{i}": make_record(f"epc{i}", epc=str(i)) for i in range(3)}
    result = build_ai_review_selection(catalog, epc_limit=45, non_epc_limit=15)
    assert result["epc_tier_count"] == 3
    assert result["non_epc_tier_count"] == 0
    assert any("EPCあり枠" in p for p in result["problems"])


def test_conversion_action_diversity_does_not_penalize_unclassified():
    catalog = {
        "a": make_record("a", epc="10", extra={"成果条件": "資料請求完了で成果"}),
        "b": make_record("b", epc="9", extra={"成果条件": "資料請求または無料相談"}),  # 分類不能(2種)
        "c": make_record("c", epc="8"),  # 成果条件なし -- 分類不能
    }
    result = build_ai_review_selection(catalog, epc_limit=45, non_epc_limit=15)
    diversity = result["conversion_action_diversity"]
    assert diversity["classified_counts"] == {"資料請求": 1}
    assert diversity["unclassified_count"] == 2
    # 分類不能でも母集団からは除外されていない
    assert set(result["population"]) == {"a", "b", "c"}


def test_save_ai_review_selection_tags_program_master_without_deleting(tmp_path):
    catalog = {
        "a": make_record("a", epc="10"),
        "b": make_record("b", epc="5"),
        "c": make_record("c"),  # not selected (no epc, no reward/rate, but still within limit since pool small)
        "untouched": make_record("untouched", screening_status="excluded_clear"),
    }
    snapshot_path = str(tmp_path / "latest.json")
    report_path = str(tmp_path / "report.json")

    report = save_ai_review_selection(catalog, snapshot_path, report_path, epc_limit=2, non_epc_limit=1)

    assert report["epc_tier_count"] == 2
    assert catalog["a"]["ai_review_selected"] is True
    assert catalog["a"]["ai_review_tier"] == "epc"
    assert "ai_review_rank" in catalog["a"]
    assert "untouched" in catalog  # nothing deleted
    assert "ai_review_selected" not in catalog["untouched"]
