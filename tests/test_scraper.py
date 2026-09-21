from a8_automation.scraper import (
    _RESERVED_FIELD_NAMES,
    dedupe_case_insensitive,
    has_detail_fields,
    select_catalog_backfill_candidates,
    select_detail_candidates,
)

LIST_ONLY_RECORD = {
    "program_id": "x",
    "detail_url": "u",
    "name": "n",
    "reward": "r",
    "epc": "e",
    "conversion_rate": "c",
    "category": "cat",
    "start_date": "d",
    "checked_at": "2026-01-01T00:00:00+09:00",
}
DETAILED_RECORD = {**LIST_ONLY_RECORD, "成果条件": "..."}


def test_drops_section_colliding_with_reserved_field_case_insensitively():
    sections = {"EPC": "12.3", "成果条件": "30日以内"}
    result = dedupe_case_insensitive(sections, _RESERVED_FIELD_NAMES)
    assert "EPC" not in result
    assert result["成果条件"] == "30日以内"


def test_drops_duplicate_headings_that_only_differ_by_case():
    sections = {"Note": "a", "note": "b"}
    result = dedupe_case_insensitive(sections, reserved=set())
    assert len(result) == 1
    assert result["Note"] == "a"


def test_keeps_unrelated_sections_untouched():
    sections = {"否認条件": "x", "禁止事項": "y"}
    result = dedupe_case_insensitive(sections, _RESERVED_FIELD_NAMES)
    assert result == sections


def test_has_detail_fields():
    assert has_detail_fields(DETAILED_RECORD) is True
    assert has_detail_fields(LIST_ONLY_RECORD) is False


def test_select_detail_candidates_prioritizes_new_over_backfill():
    records = {"new1": LIST_ONLY_RECORD, "old_undetailed": LIST_ONLY_RECORD, "old_detailed": LIST_ONLY_RECORD}
    previous_snapshot = {
        "old_undetailed": LIST_ONLY_RECORD,  # known, but never got detail -- backfill candidate
        "old_detailed": DETAILED_RECORD,  # already has detail -- should not be re-fetched
    }
    result = select_detail_candidates(records, previous_snapshot)
    assert result == ["new1", "old_undetailed"]


def test_select_detail_candidates_backfills_when_nothing_new():
    records = {"a": LIST_ONLY_RECORD, "b": LIST_ONLY_RECORD}
    previous_snapshot = {"a": LIST_ONLY_RECORD, "b": DETAILED_RECORD}
    result = select_detail_candidates(records, previous_snapshot)
    assert result == ["a"]


def test_catalog_backfill_pulls_from_whole_snapshot_not_just_this_runs_pages():
    previous_snapshot = {
        "a": LIST_ONLY_RECORD,
        "b": DETAILED_RECORD,  # already detailed -- skip
        "c": LIST_ONLY_RECORD,
        "d": LIST_ONLY_RECORD,
    }
    result = select_catalog_backfill_candidates(previous_snapshot, exclude_ids=set(), limit=2)
    assert result == ["a", "c"]


def test_catalog_backfill_excludes_ids_already_selected_this_run():
    previous_snapshot = {"a": LIST_ONLY_RECORD, "c": LIST_ONLY_RECORD}
    result = select_catalog_backfill_candidates(previous_snapshot, exclude_ids={"a"}, limit=5)
    assert result == ["c"]


def test_catalog_backfill_respects_zero_limit():
    previous_snapshot = {"a": LIST_ONLY_RECORD}
    assert select_catalog_backfill_candidates(previous_snapshot, exclude_ids=set(), limit=0) == []
