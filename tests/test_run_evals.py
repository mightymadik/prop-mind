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


def test_parsed_from_report_restores_filters_and_errors():
    from evals.run_evals import parsed_from_report

    report = {
        "results": {
            "gold/hybrid": [{"id": "d01", "filters": {"city": "Астана"}}],
            "parsed/hybrid": [
                {"id": "d01", "filters": {"city": "Астана", "rooms": [2], "semantic_query": "тихо"}},
                {"id": "d02", "filters": "RuntimeError: no structured output"},
            ],
        }
    }
    got = parsed_from_report(report)
    assert got["d01"] == SearchFilters(city="Астана", rooms=[2], semantic_query="тихо")
    assert got["d02"] == "RuntimeError: no structured output"


def test_search_mode_validation():
    import argparse

    import pytest

    from evals.run_evals import search_mode

    assert search_mode("hybrid") == "hybrid" and search_mode("rerank:rerank-3") == "rerank:rerank-3"
    for bad in ("bm25", "rerank:", "rerank"):
        with pytest.raises(argparse.ArgumentTypeError):
            search_mode(bad)


def test_rerank_cache_computes_once_and_persists(tmp_path):
    from evals.run_evals import RerankCache

    calls = []
    path = tmp_path / "cache.jsonl"
    c = RerankCache(path)
    assert c.get_or_compute("m", "q", ["a", "b"], lambda: calls.append(1) or ["b", "a"]) == ["b", "a"]
    assert RerankCache(path).get_or_compute("m", "q", ["a", "b"], lambda: calls.append(1) or []) == ["b", "a"]
    assert calls == [1]  # второй раз — из файла
    RerankCache(path).get_or_compute("m2", "q", ["a", "b"], lambda: calls.append(1) or ["a", "b"])
    assert calls == [1, 1]  # другая модель — другой ключ


def test_search_variant_validation():
    import argparse

    import pytest

    from evals.run_evals import search_variant

    assert search_variant("gold/vector") == "gold/vector"
    assert search_variant("parsed/rerank:rerank-2.5") == "parsed/rerank:rerank-2.5"
    for bad in ("vector", "oracle/vector", "parsed/bm25"):
        with pytest.raises(argparse.ArgumentTypeError):
            search_variant(bad)


def _answer_rows(n: int = 8) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "id": f"d{i:02d}",
                "query": f"запрос {i}",
                "filters": {},
                "relaxed": [],
                "clarification": None,
                "ids": ["syn-1"],
                "answers": {
                    "m-sonnet": {"text": f"ответ S{i}", "ttft_ms": 1500, "cost_usd": 0.009},
                    "m-haiku": {"text": f"ответ H{i}", "ttft_ms": 800, "cost_usd": 0.0004},
                },
            }
        )
    return rows


def test_blind_review_hides_models_and_metrics_and_shuffles():
    from evals.run_evals import render_blind_review

    md, key = render_blind_review(_answer_rows(), ["m-sonnet", "m-haiku"], seed="s1")
    assert "m-sonnet" not in md and "m-haiku" not in md
    assert "1500" not in md and "0.009" not in md and "мс" not in md  # метрики выдали бы модель
    assert len({k["A"] for k in key.values()}) == 2  # в разных кейсах A — разные модели
    assert render_blind_review(_answer_rows(), ["m-sonnet", "m-haiku"], seed="s1")[1] == key  # воспроизводимо


def test_blind_review_key_matches_texts():
    from evals.run_evals import render_blind_review

    md, key = render_blind_review(_answer_rows(3), ["m-sonnet", "m-haiku"], seed="x")
    for cid, k in key.items():
        sec = md.split(f"## {cid}:")[1]
        a_text = sec.split("### Ответ A")[1].split("### Ответ B")[0]
        expected = "S" if k["A"] == "m-sonnet" else "H"
        assert f"ответ {expected}{int(cid[1:])}" in a_text


def test_grades_parse_and_reveal():
    from evals.run_evals import parse_grades, reveal

    text = (
        "## d01: q\n\nОценка A (1–5): 4\nОценка B (1–5): 2\n"
        "## d02: q\nОценка A (1–5): 3\nОценка B (1–5): 3\n"
        "## d03: q\nОценка A (1–5): \n"  # не оценён — пропускается
    )
    grades = parse_grades(text)
    assert grades == {"d01": {"A": 4, "B": 2}, "d02": {"A": 3, "B": 3}}
    key = {"d01": {"A": "s", "B": "h"}, "d02": {"A": "h", "B": "s"}}
    r = reveal(grades, key)
    assert r["s"]["scores"] == {"d01": 4, "d02": 3} and r["s"]["mean"] == 3.5 and r["s"]["wins"] == 1
    assert r["h"]["scores"] == {"d01": 2, "d02": 3} and r["h"]["wins"] == 0


def test_grades_accept_any_dash():
    from evals.run_evals import parse_grades

    text = "## d01: q\nОценка A (1-5): 5\nОценка B (1—5): 3 \n"
    assert parse_grades(text) == {"d01": {"A": 5, "B": 3}}


def test_answer_checks_by_case_kind():
    from evals.run_evals import answer_checks

    row = {"id": "r01", "relaxed": ["features", "conditions"], "ids": ["a"]}
    checks = answer_checks(row, {"split": "relax"})
    assert list(checks) == ["mentions_relaxation"] and "признаки, состояние" in checks["mentions_relaxation"]
    assert list(answer_checks({"id": "d16", "relaxed": [], "ids": ["a"]}, {})) == ["states_no_match"]
    labeled_empty = {"relevance": "llm_reviewed", "relevant_source_ids": []}
    assert list(answer_checks({"id": "x1", "relaxed": [], "ids": ["a"]}, labeled_empty)) == ["states_no_match"]
    auto_empty = {"relevance": "auto", "relevant_source_ids": []}
    assert answer_checks({"id": "x2", "relaxed": [], "ids": ["a"]}, auto_empty) == {}
    # обратная проверка — только если среди показанных есть подходящий и ничего не ослаблялось
    has = {"relevance": "auto", "relevant_source_ids": ["a", "z"]}
    assert list(answer_checks({"id": "x3", "relaxed": [], "ids": ["a", "b"]}, has)) == ["no_false_no_match"]
    assert answer_checks({"id": "x4", "relaxed": [], "ids": ["b"]}, has) == {}  # подходящий не показан


def test_expand_cases_splits_and_dedupes():
    from evals.run_evals import expand_cases

    cases = {"d01": {"split": "dev"}, "d02": {"split": "dev"}, "r01": {"split": "relax"}, "h01": {"split": "holdout"}}
    assert expand_cases(["d02", "split:dev", "split:relax"], cases) == ["d02", "d01", "r01"]


def test_judge_fills_criterion(monkeypatch):
    from app.config import Settings, load_config
    from evals import run_evals

    sent = {}
    monkeypatch.setattr(
        run_evals, "call_structured", lambda **kw: sent.update(kw) or run_evals.Verdict(verdict=True, reason="ok")
    )
    cfg = load_config("config/demo.yaml")
    v = run_evals.judge("КРИТЕРИЙ-X", "двушка", {"relaxed": ["features"], "ids": ["a"]}, "ОТВЕТ", cfg, Settings())
    assert v.verdict and sent["model"] == cfg.models["judge"]
    assert "КРИТЕРИЙ-X" in sent["system"] and "$check" not in sent["system"]
    assert "ОТВЕТ" in sent["user"] and "двушка" in sent["user"]
    assert "признаки" in sent["user"] and "features" not in sent["user"]


def test_claims_no_match_phrases():
    from evals.run_evals import claims_no_match

    assert claims_no_match("Полностью подходящих нет: свет не подтверждён.") == "Полностью подходящих нет"
    assert claims_no_match("К сожалению, ничего не подходит.") == "ничего не подходит"
    assert claims_no_match("Подходящих вариантов нет.") == "Подходящих вариантов нет"
    for ok in ("Все десять вариантов подходят.", "Лучшие варианты: [syn-1].", "Не подходит только [syn-2]."):
        assert claims_no_match(ok) is None
