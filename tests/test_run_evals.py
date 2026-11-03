from app.schemas import SearchFilters
from evals.run_evals import compare_filters, summarize


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
    assert compare_filters(
        SearchFilters(city="Астана", needs_clarification="Метро в Астане нет. Искать в Алматы?"), c
    ) == {"needs_clarification": True}
    assert compare_filters(SearchFilters(city="Астана"), c) == {"needs_clarification": False}
    assert compare_filters(SearchFilters(needs_clarification="?"), case({}))["needs_clarification"] is False


def run(ok: bool, latency: int = 100) -> dict:
    return {"ok": ok, "checks": {"city": ok}, "error": None, "latency_ms": latency}


def test_summarize_repeats_mean_spread_and_flaky():
    cases = [
        {"id": "a", "tags": ["simple"], "success_rate": 1.0, "runs": [run(True), run(True), run(True)]},
        {
            "id": "b",
            "tags": ["simple", "slang"],
            "success_rate": 1 / 3,
            "runs": [run(True, 300), run(False), run(False)],
        },
    ]
    s = summarize(cases)
    assert s["repeats"] == 3
    assert s["fully_correct"]["per_repeat"] == [1.0, 0.5, 0.5]
    assert s["fully_correct"]["mean"] == 0.667 and s["fully_correct"]["stdev"] == 0.289
    assert s["fully_correct"]["min"] == 0.5 and s["fully_correct"]["max"] == 1.0
    assert s["flaky"] == ["b"]
    assert s["by_field"]["city"] == 0.667
    assert s["by_tag"]["slang"] == {"n": 1, "success_rate": 0.333}
    assert s["by_tag"]["simple"]["success_rate"] == 0.667
    assert s["latency_ms"]["p50"] == 100


def test_summarize_single_repeat_has_zero_spread():
    s = summarize([{"id": "a", "tags": ["t"], "success_rate": 0.0, "runs": [run(False)]}])
    assert s["fully_correct"]["stdev"] == 0.0 and s["flaky"] == []


def test_run_case_reports_got_without_defaults(monkeypatch):
    from app.config import Settings, load_config
    from app.schemas import build_parser_schema
    from evals import run_evals

    cfg = load_config("config/demo.yaml")
    empty = {k: v for k, v in SearchFilters().model_dump().items()}
    monkeypatch.setattr(
        run_evals, "parse_query", lambda q, c, s: build_parser_schema(cfg)(**{**empty, "city": "Алматы"})
    )
    r = run_evals.run_case({"query": "q", "expected_filters": {"city": "Алматы"}, "semantic": "empty"}, cfg, Settings())
    assert r["ok"] and r["got"] == {"city": "Алматы"}
