from a8_automation.candidate_quality import compute_population_quality, distinct_detail_headings

LIST_ONLY = {
    "detail_url": "u",
    "name": "n",
    "start_date": "d",
    "checked_at": "t",
    "category": "cat",
}


def make_record(pid, epc=None, reward=None, conversion_rate=None, extra=None):
    record = {
        "program_id": pid,
        "epc": epc or "",
        "reward": reward or "",
        "conversion_rate": conversion_rate or "",
        **LIST_ONLY,
    }
    if extra:
        record.update(extra)
    return record


def test_population_quality_only_counts_the_given_population_not_whole_catalog():
    catalog = {
        "a": make_record("a", epc="10", extra={"成果条件": "x"}),
        "b": make_record("b"),  # not in population -- must not affect counts
    }
    report = compute_population_quality(catalog, ["a"])
    assert report["population_total"] == 1
    assert report["detail_fetched_count"] == 1
    assert report["fields"]["epc"]["count"] == 1
    assert report["fields"]["成果条件"]["count"] == 1


def test_field_counts_and_rates():
    catalog = {
        "a": make_record("a", epc="10", reward="1000", extra={"成果条件": "x", "否認条件": "y"}),
        "b": make_record("b", reward="500"),  # no epc, no detail fields
    }
    report = compute_population_quality(catalog, ["a", "b"])
    assert report["population_total"] == 2
    assert report["detail_fetched_count"] == 1
    assert report["fields"]["epc"]["count"] == 1
    assert report["fields"]["epc"]["rate"] == 0.5
    assert report["fields"]["reward(報酬)"]["count"] == 2
    assert report["fields"]["成果条件"]["count"] == 1
    assert report["fields"]["否認条件"]["count"] == 1
    assert report["fields"]["禁止事項"]["count"] == 0
    assert report["fields"]["sns_status(実データに基づく判定)"]["count"] == 1


def test_distinct_headings_excludes_list_level_keys_and_counts_extras():
    catalog = {
        "a": make_record("a", extra={"成果条件": "x", "否認条件": "y"}),
        "b": make_record("b", extra={"成果条件": "z"}),
        "c": make_record("c"),  # list-only, not detail-fetched -- excluded
    }
    headings = distinct_detail_headings(catalog, ["a", "b", "c"])
    assert headings["成果条件"] == 2
    assert headings["否認条件"] == 1
    assert "epc" not in headings
    assert "program_id" not in headings


def test_empty_population_does_not_divide_by_zero():
    report = compute_population_quality({}, [])
    assert report["population_total"] == 0
    assert report["fields"]["epc"]["rate"] == 0.0
