from a8_automation.scraper import _RESERVED_FIELD_NAMES, dedupe_case_insensitive


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
