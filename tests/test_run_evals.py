from evals.run_evals import compare_filters, summarize
from app.schemas import SearchFilters


def case(expected: dict, semantic: str = "empty") -> dict:
    return {"expected_filters": expected, "semantic": semantic}


def test_exact_match_lists_as_sets_and_missing_fields_must_be_default():
    c = case({"city": "Алматы", "rooms": [3, 2]})
    assert all(compare_filters(SearchFilters(city="Алматы", rooms=[2, 3]), c).values())

    checks = compare_filters(SearchFilters(city="Алматы", rooms=[2, 3], features=["parking"]), c)
    assert checks["features"] is False  # лишний фильтр — ошибка
    assert compare_filters(SearchFilters(city="Алматы", rooms=[2, 3], deal_type="rent"), c)["deal_type"] is False


def test_empty_list_equals_none():
    assert all(compare_filters(SearchFilters(districts=[], conditions=[]), case({})).values())


def test_semantic_modes():
    s = SearchFilters(semantic_query="тихий двор")
    assert compare_filters(s, case({}, "required"))["semantic_query"] is True
    assert compare_filters(s, case({}, "empty"))["semantic_query"] is False
    assert "semantic_query" not in compare_filters(s, case({}, "any"))


def test_clarification_checks_only_that_question_was_asked():
    c = case({"needs_clarification": True}, "any")
    assert compare_filters(SearchFilters(city="Астана", needs_clarification="Метро в Астане нет. Искать в Алматы?"), c) == {
        "needs_clarification": True
    }
    assert compare_filters(SearchFilters(city="Астана"), c) == {"needs_clarification": False}
    assert compare_filters(SearchFilters(needs_clarification="?"), case({}))["needs_clarification"] is False


def test_summarize_by_field_and_tag():
    rs = [
        {"ok": True, "checks": {"city": True}, "tags": ["simple"], "error": None, "latency_ms": 100},
        {"ok": False, "checks": {"city": False}, "tags": ["simple", "slang"], "error": None, "latency_ms": 300},
    ]
    s = summarize(rs)
    assert s["fully_correct"] == 0.5 and s["by_field"]["city"] == 0.5
    assert s["by_tag"]["slang"] == {"n": 1, "fully_correct": 0.0}
    assert s["latency_ms"]["p50"] == 200
