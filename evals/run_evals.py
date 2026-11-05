"""Evals по evals/cases.jsonl, два слоя по отдельности.

parse  — SearchFilters от parse_query против expected_filters.
search — поиск на эталонных expected_filters против relevant_source_ids: Recall@10, MRR.

    python evals/run_evals.py --config config/demo.yaml                       # parse, dev
    python evals/run_evals.py --config config/demo.yaml --layer search        # search, dev
    python evals/run_evals.py --config config/demo.yaml --split holdout       # один раз, в конце

Отчёт: evals/results/<UTC-время>_<layer>_..._<split>.json
"""

import argparse
import hashlib
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings, load_config
from app.db import connect
from app.llm.client import PROMPTS_DIR
from app.llm.parser import PROMPT_FILE, parse_query
from app.schemas import SearchFilters
from app.search.embeddings import embed, to_pgvector
from app.search.hybrid import query_text, search, where_from_filters

ROOT = Path(__file__).resolve().parent
K = 10
EXACT_FIELDS = [f for f in SearchFilters.model_fields if f not in ("semantic_query", "needs_clarification")]
DEFAULTS = SearchFilters().model_dump()


def _norm(v: Any) -> Any:
    if isinstance(v, list):
        return sorted(v) or None  # [] и None — одно и то же: «фильтра нет»
    return v


def compare_filters(got: SearchFilters, case: dict) -> dict[str, bool]:
    """Поле → совпало ли. Проверяются только поля, которые надо проверять в этом кейсе."""
    expected = case["expected_filters"]
    if expected.get("needs_clarification"):
        return {"needs_clarification": bool(got.needs_clarification)}

    g = got.model_dump()
    checks = {f: _norm(g[f]) == _norm(expected.get(f, DEFAULTS[f])) for f in EXACT_FIELDS}
    checks["needs_clarification"] = not got.needs_clarification
    mode = case["semantic"]
    if mode != "any":
        checks["semantic_query"] = bool(got.semantic_query.strip()) == (mode == "required")
    return checks


def summarize(cases: list[dict]) -> dict:
    """cases: [{id, tags, runs: [{ok, checks, error, latency_ms, ...}]}], у всех одинаковое число runs."""
    repeats = len(cases[0]["runs"])
    per_repeat = [round(sum(c["runs"][i]["ok"] for c in cases) / len(cases), 3) for i in range(repeats)]
    runs = [r for c in cases for r in c["runs"]]

    fields: dict[str, list[bool]] = {}
    for r in runs:
        for f, ok in r["checks"].items():
            fields.setdefault(f, []).append(ok)
    tags = sorted({t for c in cases for t in c["tags"]})
    lat = sorted(r["latency_ms"] for r in runs)
    return {
        "cases": len(cases),
        "repeats": repeats,
        "fully_correct": {
            "mean": round(statistics.fmean(per_repeat), 3),
            "stdev": round(statistics.stdev(per_repeat), 3) if repeats > 1 else 0.0,
            "min": min(per_repeat),
            "max": max(per_repeat),
            "per_repeat": per_repeat,
        },
        "flaky": [c["id"] for c in cases if 0 < c["success_rate"] < 1],
        "errors": sum(r["error"] is not None for r in runs),
        "by_field": {f: round(sum(v) / len(v), 3) for f, v in fields.items()},
        "by_tag": {
            t: {"n": len(cs), "success_rate": round(statistics.fmean(c["success_rate"] for c in cs), 3)}
            for t in tags
            for cs in [[c for c in cases if t in c["tags"]]]
        },
        "latency_ms": {"p50": int(statistics.median(lat)), "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))]},
    }


def run_case(case: dict, cfg, settings) -> dict:
    t = time.perf_counter()
    try:
        got = parse_query(case["query"], cfg, settings)
        error = None
    except Exception as e:  # noqa: BLE001 — упавший прогон = неверный, eval продолжается
        got, error = None, f"{e.__class__.__name__}: {e}"
    latency = int((time.perf_counter() - t) * 1000)
    checks = compare_filters(got, case) if got else {"error": False}
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "error": error,
        "latency_ms": latency,
        # относительно SearchFilters: у схемы для LLM дефолтов нет, exclude_defaults не сработал бы
        "got": {k: v for k, v in got.model_dump().items() if _norm(v) != _norm(DEFAULTS[k])} if got else None,
    }


def search_metrics(ranked: list[str], relevant: list[str], k: int = K) -> dict:
    """Recall@k = попаданий / min(|relevant|, k) — потолок 100% и при relevant > k; RR — по топ-k."""
    top = ranked[:k]
    hits = len(set(top) & set(relevant))
    rr = next((1 / (i + 1) for i, x in enumerate(top) if x in relevant), 0.0)
    return {"hits": hits, "recall": round(hits / min(len(relevant), k), 3), "rr": round(rr, 3)}


def ranking_matters(case: dict, n_candidates: int) -> bool:
    """Есть смысловые требования и под фильтры больше k объектов — порядок решает, что попадёт в топ."""
    return case["semantic"] == "required" and n_candidates > K


def summarize_search(results: list[dict]) -> dict:
    scored = [r for r in results if r["n_relevant"] > 0]

    def agg(rs: list[dict]) -> dict:
        if not rs:
            return {"n": 0, "recall_at_10": None, "mrr": None}
        return {
            "n": len(rs),
            "recall_at_10": round(statistics.fmean(r["recall"] for r in rs), 3),
            "mrr": round(statistics.fmean(r["rr"] for r in rs), 3),
        }

    tags = sorted({t for r in scored for t in r["tags"]})
    lat = sorted(r["latency_ms"] for r in results)
    return {
        "cases": len(results),
        **agg(scored),
        "by_group": {g: agg([r for r in scored if r["group"] == g]) for g in ("ranking", "other")},
        "by_tag": {t: agg([r for r in scored if t in r["tags"]]) for t in tags},
        "empty_relevant": [r["id"] for r in results if r["n_relevant"] == 0],
        "sql_latency_ms": {"p50": int(statistics.median(lat)), "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))]},
    }


def load_cases(split: str) -> list[dict]:
    cases = [json.loads(line) for line in (ROOT / "cases.jsonl").open(encoding="utf-8") if line.strip()]
    cases = [c for c in cases if c["split"] == split]
    if not cases:
        raise SystemExit(f"no cases with split={split}")
    return cases


QUERY_CACHE = ROOT / "query_embeddings.jsonl"


def embed_queries(texts: list[str], cfg, settings, cache_path: Path | None = None) -> list[str]:
    """Эмбеддинги запросов с кэшем (модель + тип ввода + размерность + текст).

    Voyage для одного текста в разных запросах даёт слегка разные векторы — кэш делает прогоны
    воспроизводимыми. Новые тексты считаются одним батчем (бесплатный тариф: 3 запроса в минуту)
    и дописываются в кэш.
    """
    cache_path = cache_path or QUERY_CACHE
    e = cfg.embeddings

    def key(text: str) -> tuple:
        return (e.model, "query", e.dim, text)

    cache: dict[tuple, list[float]] = {}
    if cache_path.exists():
        for line in cache_path.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                cache[(r["model"], r["input_type"], r["dim"], r["text"])] = r["embedding"]
    missing = list(dict.fromkeys(t for t in texts if key(t) not in cache))
    bs = e.batch_size
    for i in range(0, len(missing), bs):
        chunk = missing[i : i + bs]
        vecs = embed(chunk, e, settings.voyage_api_key, input_type="query")
        with cache_path.open("a", encoding="utf-8") as f:
            for t, v in zip(chunk, vecs, strict=True):
                cache[key(t)] = v
                row = {"model": e.model, "input_type": "query", "dim": e.dim, "text": t, "embedding": v}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return [to_pgvector(cache[key(t)]) for t in texts]


def relaxed_filters(f: SearchFilters, relaxed: list[str]) -> SearchFilters:
    return f.model_copy(update={k: [] if k == "features" else None for k in relaxed})


def run_relax(args, cfg, settings) -> None:
    """Набор relax: строгие фильтры дают 0. Проверяем, что сняты ровно ожидаемые мягкие фильтры
    и что весь топ-10 проходит оставшиеся фильтры."""
    cases = load_cases("relax")
    filters = {c["id"]: SearchFilters.model_validate(c["expected_filters"]) for c in cases}
    texts = [query_text(filters[c["id"]], c["query"]) for c in cases]
    vecs = embed_queries(texts, cfg, settings)
    by_mode: dict[str, list[dict]] = {m: [] for m in args.modes}
    with connect() as conn:
        for c, vec, text in zip(cases, vecs, texts, strict=True):
            f = filters[c["id"]]
            for mode in args.modes:
                ranked, relaxed = search(conn, f, vec, text, mode)
                top = ranked[:K]
                where, params = where_from_filters(relaxed_filters(f, relaxed))
                ok_rows = conn.execute(
                    f"SELECT count(*) FROM listings WHERE source_id = ANY(%(top)s) AND {where}", {**params, "top": top}
                ).fetchone()[0]
                by_mode[mode].append(
                    {
                        "id": c["id"],
                        "query": c["query"],
                        "expected_relaxed": c["expected_relaxed"],
                        "relaxed": relaxed,
                        "relaxed_ok": relaxed == c["expected_relaxed"],
                        "n_results": len(ranked),
                        "top_respects_remaining": bool(top) and ok_rows == len(top),
                        "top10": top,
                    }
                )
    now = datetime.now(UTC)
    summary = {
        m: {
            "cases": len(rs),
            "relaxed_ok": sum(r["relaxed_ok"] for r in rs),
            "top_respects_remaining": sum(r["top_respects_remaining"] for r in rs),
        }
        for m, rs in by_mode.items()
    }
    report = {
        "layer": "relax",
        "modes": args.modes,
        "date": now.isoformat(timespec="seconds"),
        "summary": summary,
        "results": by_mode,
    }
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_relax_{'+'.join(args.modes)}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for m, rs in by_mode.items():
        s = summary[m]
        print(
            f"relax/{m}: снято верно {s['relaxed_ok']}/{s['cases']}, "
            f"топ-10 проходит оставшиеся фильтры {s['top_respects_remaining']}/{s['cases']}"
        )
        for r in rs:
            flag = "ok " if r["relaxed_ok"] and r["top_respects_remaining"] else "FAIL"
            print(
                f"  {flag} {r['id']} снято {r['relaxed']} (ждали {r['expected_relaxed']}), результатов {r['n_results']}"
            )
    print(f"report: {out.relative_to(ROOT.parent)}")


def unjudged(top: list[str], case: dict, passes_gold: set[str]) -> list[str]:
    """Объекты топа, проходящие эталонные фильтры, но ещё не оценённые: их релевантность неизвестна.

    Объект, не проходящий эталонные фильтры (ошибка парсера в сквозном прогоне), заведомо не подходит —
    нарушено явное условие запроса, — поэтому неразмеченным не считается.
    """
    judged = set(case.get("judged_source_ids", []))
    return [x for x in top if x not in judged and x in passes_gold]


def parse_all(cases: list[dict], cfg, settings, workers: int) -> dict[str, SearchFilters | str]:
    """id кейса → фильтры от парсера, либо текст ошибки."""

    def one(c: dict) -> SearchFilters | str:
        try:
            return parse_query(c["query"], cfg, settings)
        except Exception as e:  # noqa: BLE001 — упавший разбор = промах кейса, прогон продолжается
            return f"{e.__class__.__name__}: {e}"

    with ThreadPoolExecutor(workers) as pool:
        return dict(zip((c["id"] for c in cases), pool.map(one, cases), strict=True))


def run_search(args, cfg, settings) -> None:
    """Все методы (--modes) × источники фильтров (--filters) в одном прогоне, на одной разметке.

    gold — эталонные expected_filters: качество поиска отдельно от разбора.
    parsed — фильтры от parse_query, как в реальном запросе: сквозной прогон.
    """
    cases = [c for c in load_cases(args.split) if "relevant_source_ids" in c]
    gold = {c["id"]: SearchFilters.model_validate(c["expected_filters"]) for c in cases}
    sources: dict[str, dict[str, SearchFilters | str]] = {}
    if "gold" in args.filters:
        sources["gold"] = dict(gold)
    if "parsed" in args.filters:
        sources["parsed"] = parse_all(cases, cfg, settings, args.workers)

    texts = {
        (src, c["id"]): query_text(f, c["query"]) if isinstance(f := sources[src][c["id"]], SearchFilters) else ""
        for src in sources
        for c in cases
    }
    uniq = sorted({t for t in texts.values() if t})
    vec_of = dict(zip(uniq, embed_queries(uniq, cfg, settings), strict=True))

    by_mode: dict[str, list[dict]] = {f"{src}/{m}": [] for src in sources for m in args.modes}
    with connect() as conn:
        for c in cases:
            g_where, g_params = where_from_filters(gold[c["id"]])
            n_candidates = conn.execute(f"SELECT count(*) FROM listings WHERE {g_where}", g_params).fetchone()[0]
            relevant = c["relevant_source_ids"]
            for src, fs in sources.items():
                f = fs[c["id"]]
                parsed_ok = None
                if src == "parsed":
                    parsed_ok = isinstance(f, SearchFilters) and all(compare_filters(f, c).values())
                for mode in args.modes:
                    clarified = not isinstance(f, SearchFilters) or bool(f.needs_clarification)
                    t = time.perf_counter()
                    if clarified:  # парсер переспросил или упал — выдачи нет
                        ranked, relaxed = [], []
                    else:
                        text = texts[(src, c["id"])]
                        ranked, relaxed = search(conn, f, vec_of[text], text, mode)
                    latency = int((time.perf_counter() - t) * 1000)
                    top = ranked[:K]
                    passes_gold = {
                        r[0]
                        for r in conn.execute(
                            f"SELECT source_id FROM listings WHERE source_id = ANY(%(top)s) AND {g_where}",
                            {**g_params, "top": top},
                        )
                    }
                    by_mode[f"{src}/{mode}"].append(
                        {
                            "id": c["id"],
                            "query": c["query"],
                            "tags": c["tags"],
                            "relevance": c.get("relevance"),
                            "group": "ranking" if ranking_matters(c, n_candidates) else "other",
                            "n_candidates": n_candidates,
                            "n_relevant": len(relevant),
                            "latency_ms": latency,
                            **(
                                search_metrics(ranked, relevant)
                                if relevant
                                else {"hits": 0, "recall": None, "rr": None}
                            ),
                            "filters": f.model_dump(exclude_defaults=True) if isinstance(f, SearchFilters) else f,
                            "parsed_ok": parsed_ok,
                            "clarified": clarified,
                            "relaxed": relaxed,
                            "top_outside_gold": len(top) - len(passes_gold),
                            "unjudged_top10": unjudged(top, c, passes_gold),
                            "top10": top,
                        }
                    )

    summaries = {}
    for mode, results in by_mode.items():
        summaries[mode] = summarize_search(results)
        summaries[mode]["unjudged_at_10"] = {r["id"]: len(r["unjudged_top10"]) for r in results if r["unjudged_top10"]}
    now = datetime.now(UTC)
    report = {
        "layer": "search",
        "modes": args.modes,
        "filters": args.filters,
        "split": args.split,
        "date": now.isoformat(timespec="seconds"),
        "config": args.config,
        "embeddings": cfg.embeddings.model_dump(),
        "summary": summaries,
        "results": by_mode,
    }
    tag = "+".join(args.modes) + ("" if args.filters == ["gold"] else "_" + "+".join(args.filters))
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_search_{tag}_{args.split}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def line(name: str, a: dict) -> str:
        if not a["n"]:
            return f"  {name:22} n=0"
        return f"  {name:22} n={a['n']:<3} Recall@10 {a['recall_at_10']:.0%}  MRR {a['mrr']:.2f}"

    for mode, results in by_mode.items():
        summary = summaries[mode]
        print(
            f"search/{mode}/{args.split}: {summary['cases']} cases, "
            f"SQL p50 {summary['sql_latency_ms']['p50']} ms, p95 {summary['sql_latency_ms']['p95']} ms"
        )
        print(line("all", summary))
        for g, a in summary["by_group"].items():
            print(line(f"group {g}", a))
        if args.verbose:
            for t, a in summary["by_tag"].items():
                print(line(f"tag {t}", a))
        for r in results:
            if r["recall"] is not None and r["recall"] < 1:
                print(
                    f"  MISS {r['id']} [{r['group']}] recall {r['recall']:.0%} rr {r['rr']:.2f} "
                    f"({r['hits']}/{min(r['n_relevant'], K)}, candidates {r['n_candidates']})"
                )
        if mode.startswith("parsed/"):
            print(
                f"  разбор полностью верен: {sum(bool(r['parsed_ok']) for r in results)}/{len(results)}, "
                f"переспросил: {[r['id'] for r in results if r['clarified']] or 'нет'}, "
                f"объектов вне эталонных фильтров в топ-10: {sum(r['top_outside_gold'] for r in results)}"
            )
        if summary["unjudged_at_10"]:
            total = sum(summary["unjudged_at_10"].values())
            print(f"  НЕРАЗМЕЧЕННЫХ в топ-10: {total} — {summary['unjudged_at_10']} (дооценка: llm_label.py extend)")
        if summary["empty_relevant"]:
            print(f"  без подходящих (не в среднем): {', '.join(summary['empty_relevant'])}")
    print(f"report: {out.relative_to(ROOT.parent)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--layer", default="parse", choices=["parse", "search", "relax"])
    ap.add_argument(
        "--modes", nargs="+", default=["vector", "hybrid"], choices=["vector", "hybrid"], help="search: методы"
    )
    ap.add_argument("--verbose", action="store_true", help="search: разбивка по тегам")
    ap.add_argument(
        "--filters", nargs="+", default=["gold"], choices=["gold", "parsed"], help="search: источник фильтров"
    )
    ap.add_argument("--split", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--repeats", type=int, default=1, help="parse: прогонов на кейс, оценка разброса")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = load_config(args.config)
    settings = get_settings()
    if args.layer == "search":
        run_search(args, cfg, settings)
    elif args.layer == "relax":
        run_relax(args, cfg, settings)
    else:
        run_parse(args, cfg, settings)


def run_parse(args, cfg, settings) -> None:
    cases = load_cases(args.split)

    jobs = [c for c in cases for _ in range(args.repeats)]
    with ThreadPoolExecutor(args.workers) as pool:
        runs = list(pool.map(lambda c: run_case(c, cfg, settings), jobs))
    results = []
    for i, c in enumerate(cases):
        rs = runs[i * args.repeats : (i + 1) * args.repeats]
        results.append(
            {
                "id": c["id"],
                "query": c["query"],
                "tags": c["tags"],
                "expected": c["expected_filters"],
                "semantic": c["semantic"],
                "success_rate": round(sum(r["ok"] for r in rs) / len(rs), 3),
                "runs": rs,
            }
        )

    summary = summarize(results)
    prompt = (PROMPTS_DIR / PROMPT_FILE).read_bytes()
    now = datetime.now(UTC)
    report = {
        "layer": "parse",
        "split": args.split,
        "date": now.isoformat(timespec="seconds"),
        "config": args.config,
        "model": cfg.models["parser"],
        "prompt": PROMPT_FILE,
        "prompt_sha": hashlib.sha256(prompt).hexdigest()[:12],
        "summary": summary,
        "results": results,
    }
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_parse_{args.split}_x{args.repeats}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    fc = summary["fully_correct"]
    print(
        f"parse/{args.split} x{args.repeats}: fully correct {fc['mean']:.0%} ± {fc['stdev']:.0%} "
        f"(min {fc['min']:.0%}, max {fc['max']:.0%}; {summary['cases']} cases, {summary['errors']} errors), "
        f"p50 {summary['latency_ms']['p50']} ms, p95 {summary['latency_ms']['p95']} ms"
    )
    for f, acc in summary["by_field"].items():
        print(f"  field {f:20} {acc:.0%}")
    for t, s in summary["by_tag"].items():
        print(f"  tag   {t:20} {s['success_rate']:.0%} (n={s['n']})")
    for r in results:
        if r["success_rate"] < 1:
            bad_runs = [x for x in r["runs"] if not x["ok"]]
            bad = sorted({f for x in bad_runs for f, ok in x["checks"].items() if not ok})
            print(
                f"  FAIL {r['id']} {r['success_rate']:.0%} ok, fields {bad} {bad_runs[0]['error'] or ''}"
                f"\n       expected {r['expected']}\n       got      {bad_runs[0]['got']}"
            )
    print(f"report: {out.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
