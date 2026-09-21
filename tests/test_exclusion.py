from a8_automation.exclusion import apply_exclusion, evaluate_exclusion


def test_no_exclusion_for_ordinary_record():
    record = {"program_id": "1", "name": "Program A", "成果条件": "30日以内の利用"}
    assert evaluate_exclusion(record) is None
    assert apply_exclusion(record) == record


def test_excludes_on_status_keyword():
    record = {"program_id": "1", "name": "Program A", "備考": "現在、募集停止中です"}
    verdict = evaluate_exclusion(record)
    assert verdict is not None
    assert verdict["excluded"] is True
    assert "募集停止" in verdict["exclusion_reason"]
    assert "judged_at" in verdict


def test_apply_exclusion_merges_verdict_into_record():
    record = {"program_id": "1", "name": "Program A", "備考": "掲載終了となりました"}
    result = apply_exclusion(record)
    assert result["excluded"] is True
    assert result["program_id"] == "1"


def test_apply_exclusion_leaves_unmatched_record_unchanged():
    record = {"program_id": "1", "name": "Program A"}
    assert apply_exclusion(record) is record or apply_exclusion(record) == record
