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
from app.search.hybrid import query_text, vector_search, where_from_filters

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


def run_search(args, cfg, settings) -> None:
    cases = [c for c in load_cases(args.split) if "relevant_source_ids" in c]
    filters = {c["id"]: SearchFilters.model_validate(c["expected_filters"]) for c in cases}
    texts = [query_text(filters[c["id"]], c["query"]) for c in cases]
    # эмбеддинги запросов одним батчем: на бесплатном тарифе Voyage 3 запроса в минуту
    bs = cfg.embeddings.batch_size
    vecs = [
        v
        for i in range(0, len(texts), bs)
        for v in embed(texts[i : i + bs], cfg.embeddings, settings.voyage_api_key, input_type="query")
    ]

    results = []
    with connect() as conn:
        for c, vec in zip(cases, vecs, strict=True):
            f = filters[c["id"]]
            where, params = where_from_filters(f)
            n_candidates = conn.execute(f"SELECT count(*) FROM listings WHERE {where}", params).fetchone()[0]
            t = time.perf_counter()
            ranked = vector_search(conn, f, to_pgvector(vec))
            latency = int((time.perf_counter() - t) * 1000)
            relevant = c["relevant_source_ids"]
            results.append(
                {
                    "id": c["id"],
                    "query": c["query"],
                    "tags": c["tags"],
                    "relevance": c.get("relevance"),
                    "group": "ranking" if ranking_matters(c, n_candidates) else "other",
                    "n_candidates": n_candidates,
                    "n_relevant": len(relevant),
                    "latency_ms": latency,
                    **(search_metrics(ranked, relevant) if relevant else {"hits": 0, "recall": None, "rr": None}),
                    "top10": ranked[:K],
                }
            )

    summary = summarize_search(results)
    now = datetime.now(UTC)
    report = {
        "layer": "search",
        "mode": args.mode,
        "filters": "gold",
        "split": args.split,
        "date": now.isoformat(timespec="seconds"),
        "config": args.config,
        "embeddings": cfg.embeddings.model_dump(),
        "summary": summary,
        "results": results,
    }
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_search_{args.mode}_{args.split}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def line(name: str, a: dict) -> str:
        if not a["n"]:
            return f"  {name:22} n=0"
        return f"  {name:22} n={a['n']:<3} Recall@10 {a['recall_at_10']:.0%}  MRR {a['mrr']:.2f}"

    print(
        f"search/{args.mode}/{args.split} (gold filters): {summary['cases']} cases, "
        f"SQL p50 {summary['sql_latency_ms']['p50']} ms, p95 {summary['sql_latency_ms']['p95']} ms"
    )
    print(line("all", summary))
    for g, a in summary["by_group"].items():
        print(line(f"group {g}", a))
    for t, a in summary["by_tag"].items():
        print(line(f"tag {t}", a))
    for r in results:
        if r["recall"] is not None and r["recall"] < 1:
            print(
                f"  MISS {r['id']} [{r['group']}] recall {r['recall']:.0%} rr {r['rr']:.2f} "
                f"({r['hits']}/{min(r['n_relevant'], K)}, candidates {r['n_candidates']})"
            )
    if summary["empty_relevant"]:
        print(f"  без подходящих (не в среднем): {', '.join(summary['empty_relevant'])}")
    print(f"report: {out.relative_to(ROOT.parent)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--layer", default="parse", choices=["parse", "search"])
    ap.add_argument("--mode", default="vector", choices=["vector"], help="search: метод поиска")
    ap.add_argument("--split", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--repeats", type=int, default=1, help="parse: прогонов на кейс, оценка разброса")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = load_config(args.config)
    settings = get_settings()
    if args.layer == "search":
        run_search(args, cfg, settings)
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
