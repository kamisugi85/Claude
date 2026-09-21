from a8_automation.export_candidates import build_candidate_export


def test_maps_known_fields_and_nulls_missing_ones():
    record = {
        "program_id": "1",
        "name": "TestProgram",
        "category": "回線",
        "reward": "1000円",
        "成果条件": "WEB申込完了",
        "否認条件": "不正申込の場合",
        "checked_at": "2026-09-22T00:00:00+09:00",
    }
    result = build_candidate_export(record)

    assert result["program_id"] == "1"
    assert result["program_name"] == "TestProgram"
    assert result["category"] == "回線"
    assert result["reward"] == "1000円"
    assert result["conversion_action"] == "WEB申込完了"
    assert result["source_updated_at"] == "2026-09-22T00:00:00+09:00"
    assert result["relevant_conditions"]["否認条件"] == "不正申込の場合"
    # not present in the record at all -- must be null, never guessed
    assert result["relevant_conditions"]["備考"] is None
    assert result["relevant_conditions"]["リスティングNGワード"] is None
    assert result["relevant_conditions"]["禁止事項"] is None


def test_screening_status_and_sns_status_come_from_classification():
    record = {"program_id": "1", "category": "アダルト"}
    result = build_candidate_export(record)
    assert result["screening_status"] == "likely_excluded"
    assert result["exclusion_reason"] is not None


def test_non_excluded_record_has_null_exclusion_reason():
    record = {"program_id": "1", "category": "回線"}
    result = build_candidate_export(record)
    assert result["exclusion_reason"] is None
