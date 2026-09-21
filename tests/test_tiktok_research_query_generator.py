from tiktok_research.query_generator import generate_search_queries, occupation_for_program


def test_generate_search_queries_includes_occupation_and_worry_axes():
    queries = generate_search_queries("保育士", limit=8)
    assert queries[0] == "保育士"
    assert "保育士 辞めたい" in queries
    assert "保育士 給料" in queries
    assert "保育士 人間関係" in queries
    assert len(queries) <= 8


def test_generate_search_queries_respects_limit():
    queries = generate_search_queries("看護師", limit=3)
    assert len(queries) == 3
    assert queries[0] == "看護師"


def test_occupation_for_program_known_poc_programs():
    assert occupation_for_program("s00000001248025") == "保育士"
    assert occupation_for_program("s00000001248024") == "看護師"
    assert occupation_for_program("s00000026823003") == "介護士"


def test_occupation_for_program_unknown_returns_none_not_a_guess():
    assert occupation_for_program("s99999999999999") is None
