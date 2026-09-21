from a8_automation.detail_candidates import (
    build_detail_fetch_population,
    select_epc_tier,
    select_non_epc_tier,
)

DETAILED_EXTRA = {"成果条件": "..."}


def make_record(pid, epc=None, reward=None, conversion_rate=None, detailed=False):
    # Always the full 9-key list-level shape (has_detail_fields() keys off
    # the exact field count), plus one extra key only when "detailed".
    record = {
        "program_id": pid,
        "detail_url": "u",
        "name": "n",
        "reward": reward or "",
        "epc": epc or "",
        "conversion_rate": conversion_rate or "",
        "category": "cat",
        "start_date": "d",
        "checked_at": "t",
    }
    if detailed:
        record = {**record, **DETAILED_EXTRA}
    return record


def test_epc_tier_sorted_descending_and_capped():
    catalog = {
        "a": make_record("a", epc="10"),
        "b": make_record("b", epc="50"),
        "c": make_record("c", epc="30"),
        "d": make_record("d"),  # no epc -- excluded from this tier
    }
    result = select_epc_tier(catalog, limit=2)
    assert result == ["b", "c"]


def test_non_epc_tier_never_fabricates_epc_and_prioritizes_reward_then_rate():
    catalog = {
        "a": make_record("a", reward="1000"),
        "b": make_record("b", reward="5000"),
        "c": make_record("c", conversion_rate="80%"),  # no reward
        "d": make_record("d"),  # no numeric signal at all -- kept, not excluded
    }
    result = select_non_epc_tier(catalog, exclude_ids=set(), limit=10)
    # reward-ranked first (b before a), then conversion_rate-only (c), then no-signal (d)
    assert result == ["b", "a", "c", "d"]


def test_non_epc_tier_excludes_epc_tier_ids():
    catalog = {
        "a": make_record("a", epc="10", reward="1000"),
        "b": make_record("b", reward="500"),
    }
    result = select_non_epc_tier(catalog, exclude_ids={"a"}, limit=10)
    assert "a" not in result
    assert result == ["b"]


def test_build_population_counts_already_detailed_vs_needs_fetch():
    catalog = {
        "epc1": make_record("epc1", epc="100", detailed=True),
        "epc2": make_record("epc2", epc="50"),
        "noepc1": make_record("noepc1", reward="9000", detailed=True),
        "noepc2": make_record("noepc2", reward="500"),
    }
    result = build_detail_fetch_population(catalog, epc_limit=2, non_epc_limit=2)

    assert result["epc_tier_count"] == 2
    assert result["non_epc_tier_count"] == 2
    assert result["population_count"] == 4
    assert result["already_detailed_count"] == 2
    assert result["needs_fetch_count"] == 2
    assert set(result["needs_fetch"]) == {"epc2", "noepc2"}


def test_no_epc_and_no_reward_and_no_rate_is_kept_not_dropped():
    catalog = {"a": make_record("a")}
    result = build_detail_fetch_population(catalog, epc_limit=250, non_epc_limit=50)
    assert result["population"] == ["a"]
    assert result["needs_fetch"] == ["a"]
