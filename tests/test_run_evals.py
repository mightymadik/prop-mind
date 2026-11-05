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


def test_search_metrics_recall_capped_at_k_and_rr():
    from evals.run_evals import search_metrics

    ranked = [f"x{i}" for i in range(20)]
    assert search_metrics(ranked, ["x2", "x15"]) == {"hits": 1, "recall": 0.5, "rr": 0.333}
    many = [f"x{i}" for i in range(0, 40, 2)]  # 20 подходящих: потолок — 10 из топ-10
    assert search_metrics(ranked, many)["recall"] == 0.5
    assert search_metrics(ranked, ranked) == {"hits": 10, "recall": 1.0, "rr": 1.0}
    assert search_metrics(ranked, ["nope"]) == {"hits": 0, "recall": 0.0, "rr": 0.0}


def test_ranking_matters_needs_semantic_and_more_than_k_candidates():
    from evals.run_evals import ranking_matters

    assert ranking_matters({"semantic": "required"}, 11)
    assert not ranking_matters({"semantic": "required"}, 10)
    assert not ranking_matters({"semantic": "empty"}, 500)


def test_summarize_search_groups_and_excludes_empty():
    from evals.run_evals import summarize_search

    def r(id_, group, recall, rr, n_rel=3, tags=("simple",)):
        return {
            "id": id_,
            "group": group,
            "recall": recall,
            "rr": rr,
            "n_relevant": n_rel,
            "tags": list(tags),
            "latency_ms": 5,
        }

    s = summarize_search(
        [
            r("a", "ranking", 0.5, 1.0, tags=("semantic",)),
            r("b", "other", 1.0, 1.0),
            r("c", "other", 0.0, 0.0),
            r("e", "other", None, None, n_rel=0),
        ]
    )
    assert (s["n"], s["recall_at_10"], s["mrr"]) == (3, 0.5, 0.667)
    assert s["by_group"]["ranking"] == {"n": 1, "recall_at_10": 0.5, "mrr": 1.0}
    assert s["by_group"]["other"] == {"n": 2, "recall_at_10": 0.5, "mrr": 0.5}
    assert s["empty_relevant"] == ["e"] and "semantic" in s["by_tag"]


def test_unjudged_lists_top_objects_without_judgement():
    from evals.run_evals import unjudged

    case = {"judged_source_ids": ["a", "b"]}
    passes_gold = {"a", "b", "x", "y"}
    assert unjudged(["a", "x", "b", "y"], case, passes_gold) == ["x", "y"]
    assert unjudged(["a"], {}, {"a"}) == ["a"]  # нет разметки — всё неразмечено
    # вне эталонных фильтров (ошибка парсера) — заведомо не подходит, дооценивать нечего
    assert unjudged(["z"], case, passes_gold) == []


def test_embed_queries_uses_cache_keyed_by_model_and_text(monkeypatch, tmp_path):
    from app.config import EmbeddingsConfig, Settings
    from evals import run_evals

    calls = []

    def fake_embed(texts, cfg, api_key, input_type):
        calls.append(list(texts))
        return [[float(len(t)), float(len(calls))] for t in texts]

    monkeypatch.setattr(run_evals, "embed", fake_embed)

    class Cfg:
        embeddings = EmbeddingsConfig(provider="voyage", model="m1", dim=2, batch_size=2)

    cache = tmp_path / "cache.jsonl"
    first = run_evals.embed_queries(["a", "bb", "a", "ccc"], Cfg, Settings(), cache)
    assert calls == [["a", "bb"], ["ccc"]]  # без повторов, батчами
    again = run_evals.embed_queries(["ccc", "a"], Cfg, Settings(), cache)
    assert calls == [["a", "bb"], ["ccc"]]  # всё из кэша, API не вызывался
    assert again == [first[3], first[0]]  # те же векторы, что в первый раз

    Cfg.embeddings = EmbeddingsConfig(provider="voyage", model="m2", dim=2, batch_size=2)
    run_evals.embed_queries(["a"], Cfg, Settings(), cache)
    assert calls[-1] == ["a"]  # другая модель — другой ключ
