import json

from tiktok_research.program_input import build_program_input, load_program_inputs_from_export


def make_export_record(**overrides):
    record = {
        "program_id": "s000001",
        "program_name": "テスト案件",
        "category": "就職・転職",
        "reward": "5000円",
        "conversion_action_raw_text": "登録完了で成果",
        "sns_condition": {"sns_verdict": "allowed", "tiktok_verdict": "allowed", "sns_basis": "no_explicit_restriction_found"},
        "rejection_condition": "虚偽の場合は否認",
        "remarks": "人物画像はNGです",
        "other_detail_fields": {"リスティングＮＧワード": "テスト案件、類似ワード", "商品リンク": "人物画像の使用はNGです"},
    }
    record.update(overrides)
    return record


def test_build_program_input_maps_all_required_fields_without_fabrication():
    record = make_export_record()
    result = build_program_input(record)

    assert result["program_id"] == "s000001"
    assert result["program_name"] == "テスト案件"
    assert result["category"] == "就職・転職"
    assert result["reward"] == "5000円"
    assert result["conversion_conditions"] == "登録完了で成果"
    assert result["sns_tiktok_conditions"]["tiktok_verdict"] == "allowed"
    assert result["prohibited_expressions"]["listing_ng_words"] == "テスト案件、類似ワード"
    assert result["material_conditions"] == "人物画像の使用はNGです"
    assert result["advertiser_specific_restrictions"] == "虚偽の場合は否認"


def test_build_program_input_leaves_missing_fields_as_null():
    record = make_export_record(remarks=None, other_detail_fields={})
    result = build_program_input(record)
    assert result["prohibited_expressions"] is None
    assert result["material_conditions"] is None


def test_load_program_inputs_from_export_filters_by_program_id(tmp_path):
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps({
        "items": [make_export_record(program_id="a"), make_export_record(program_id="b")]
    }), encoding="utf-8")

    result = load_program_inputs_from_export(str(export_path), program_ids=["b"])
    assert len(result) == 1
    assert result[0]["program_id"] == "b"


def test_load_program_inputs_from_export_returns_all_when_no_filter(tmp_path):
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps({
        "items": [make_export_record(program_id="a"), make_export_record(program_id="b")]
    }), encoding="utf-8")

    result = load_program_inputs_from_export(str(export_path))
    assert len(result) == 2
