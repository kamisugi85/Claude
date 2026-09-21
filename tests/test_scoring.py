from a8_automation.scoring import build_shortlist, parse_number, score_record


def test_parse_number_extracts_first_number():
    assert parse_number("68.67") == 68.67
    assert parse_number("71.42%") == 71.42
    assert parse_number("16,000円") == 16000.0
    assert parse_number("") is None
    assert parse_number(None) is None


def test_score_prefers_epc_when_present():
    record = {"epc": "68.67", "reward": "16000円", "conversion_rate": "71.42%"}
    assert score_record(record) == 68.67


def test_score_is_none_without_epc_reward_times_rate_is_not_a_substitute():
    # conversion_rate is A8's 確定率 (approval rate), not a click-through
    # rate, so reward x conversion_rate is not a real EPC and must not be
    # fabricated as one.
    record = {"reward": "16000円", "conversion_rate": "50%"}
    assert score_record(record) is None


def test_score_none_when_no_usable_numbers():
    assert score_record({"category": "占い"}) is None


def test_build_shortlist_ranks_by_score_and_respects_top_n():
    records = {
        "a": {"epc": "10"},
        "b": {"epc": "50"},
        "c": {"category": "no numbers"},
        "d": {"epc": "30"},
    }
    shortlist = build_shortlist(records, top_n=2)
    assert [r["program_id"] for r in shortlist] == ["b", "d"]
