"""Evals новостроек по evals/cases_newbuild*.jsonl, слои по отдельности.

parse          — фильтры от парсера против expected_filters (доля полностью верных).
search — лестница поиска по ЖК против relevant_source_ids: Recall@10, MRR.
answer         — ответ на зафиксированной выдаче: проверки судьёй, чужие номера, цена, задержка.

    uv run python evals/run_evals.py --repeats 3                                   # parse, dev
    uv run python evals/run_evals.py --split holdout --repeats 3                   # один раз, в конце
    uv run python evals/run_evals.py --layer search --parsed-from <отчёт parse>
    uv run python evals/run_evals.py --layer answer --cases split:dev --models claude-sonnet-5-5 --judge \
        --parsed-from <отчёт parse>

Отчёт: evals/results/<UTC-время>_<layer>_..._<split>.json
"""

import argparse
import hashlib
import json
import random
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

from pydantic import BaseModel

from app.config import get_settings, load_config
from app.db import connect
from app.llm.answer import RELAX_TEXT, cost_usd, generate_answer
from app.llm.client import PROMPTS_DIR, call_structured, load_prompt
from app.llm.parser import PROMPT_FILE, parse_query
from app.pipeline import retrieve
from app.schemas import SearchFilters
from app.search import catalog
from app.search.embeddings import embed, to_pgvector
from app.search.rerank import rerank_ids

ROOT = Path(__file__).resolve().parent
K = 10
CASES = {"dev": ROOT / "cases_newbuild.jsonl", "holdout": ROOT / "cases_newbuild_holdout.jsonl"}


def _norm(v: Any) -> Any:
    if isinstance(v, list):
        return sorted(v) or None  # [] и None — одно и то же: «фильтра нет»
    return v


def _quarter(d: dict) -> dict:
    """Срок сдачи: год без квартала = до конца года (4 квартал) — в фильтре это одно и то же."""
    if d.get("completion_max_year") is not None and d.get("completion_max_quarter") is None:
        return d | {"completion_max_quarter": 4}
    return d


def compare_filters(got: SearchFilters, case: dict) -> dict[str, bool]:
    """Поле → совпало ли. Проверяются только поля, которые надо проверять в этом кейсе."""
    expected = case["expected_filters"]
    if expected.get("needs_clarification"):
        return {"needs_clarification": bool(got.needs_clarification)}

    fields = [f for f in SearchFilters.model_fields if f not in ("semantic_query", "needs_clarification")]
    defaults = SearchFilters().model_dump()
    g = _quarter(got.model_dump())
    e = _quarter({f: expected.get(f, defaults[f]) for f in fields})
    checks = {f: _norm(g[f]) == _norm(e[f]) for f in fields}
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
    defaults = SearchFilters().model_dump()
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "error": error,
        "latency_ms": latency,
        # относительно SearchFilters: у схемы для LLM дефолтов нет, exclude_defaults не сработал бы
        "got": {k: v for k, v in got.model_dump().items() if _norm(v) != _norm(defaults[k])} if got else None,
    }


def search_metrics(ranked: list[str], relevant: list[str], k: int = K) -> dict:
    """Recall@k = попаданий / min(|relevant|, k) — потолок 100% и при relevant > k; RR — по топ-k."""
    top = ranked[:k]
    hits = len(set(top) & set(relevant))
    rr = next((1 / (i + 1) for i, x in enumerate(top) if x in relevant), 0.0)
    return {"hits": hits, "recall": round(hits / min(len(relevant), k), 3), "rr": round(rr, 3)}


def load_cases(split: str) -> list[dict]:
    cases = [json.loads(line) for line in CASES[split].open(encoding="utf-8") if line.strip()]
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


RERANK_CACHE = ROOT / "rerank_cache.jsonl"


class RerankCache:
    """Кэш порядка от реранкера: ключ — модель + запрос + кандидаты. Прогоны воспроизводимы и бесплатны."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, list[str]] = {}
        if path.exists():
            for line in path.open(encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    self.data[self.key(r["model"], r["query"], r["candidates"])] = r["order"]

    @staticmethod
    def key(model: str, query: str, candidates: list[str]) -> str:
        return json.dumps([model, query, candidates], ensure_ascii=False)

    def get_or_compute(self, model: str, query: str, candidates: list[str], compute) -> list[str]:
        k = self.key(model, query, candidates)
        if k not in self.data:
            self.data[k] = compute()
            with self.path.open("a", encoding="utf-8") as f:
                row = {"model": model, "query": query, "candidates": candidates, "order": self.data[k]}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return self.data[k]


def pct(values: list[int], q: float) -> int:
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * q))] if q < 0.5 or len(v) > 1 else v[0]


GRADE = re.compile(r"^Оценка ([AB]) \(1\s*[-–—]\s*5\): *([1-5])\s*$")  # редактор может заменить «–» на «-»


def render_blind_review(rows: list[dict], models: list[str], seed: str) -> tuple[str, dict[str, dict[str, str]]]:
    """Файл для оценки вслепую: ответы двух моделей подписаны A/B в случайном порядке для каждого кейса,
    без названий моделей и без метрик (задержка и цена выдали бы модель). Соответствие — во втором значении."""
    if len(models) != 2:
        raise SystemExit("оценка вслепую — ровно для двух моделей")
    out = [
        "# Оценка ответов вслепую",
        "",
        "Ответы двух моделей подписаны A и B; в каждом кейсе порядок свой, случайный. Какая модель где —",
        "в файле .key.json рядом; не открывай его до конца оценки.",
        "Оцени каждый ответ от 1 до 5: соответствие запросу, честность расхождений, нет ли выдумок.",
        "Кейсы d16, h06, h07 — «подходящих по смыслу нет»: ответ должен прямо это сказать и предложить,",
        "что ослабить. r01 — фильтры ослаблялись: ответ должен назвать, что снято.",
        "Впиши цифру после двоеточия в строках «Оценка A/B»; потом: run_evals.py --layer answer-reveal.",
    ]
    key: dict[str, dict[str, str]] = {}
    for r in rows:
        out += ["", f"## {r['id']}: {r['query']}", ""]
        out.append(f"Фильтры: {r['filters']}; ослаблено: {r['relaxed'] or 'ничего'}")
        out.append("Кандидаты: " + (", ".join(f"[{i + 1}] {sid}" for i, sid in enumerate(r["ids"])) or "нет"))
        if r.get("clarification") or not all(m in r["answers"] for m in models):
            out.append(f"Ответов нет: {r.get('clarification') or 'не сгенерированы'}")
            continue
        order = list(models)
        random.Random(f"{seed}:{r['id']}").shuffle(order)
        key[r["id"]] = {"A": order[0], "B": order[1]}
        for label, m in zip("AB", order, strict=True):
            out += ["", f"### Ответ {label}", "", r["answers"][m]["text"], "", f"Оценка {label} (1–5): "]
    return "\n".join(out) + "\n", key


def parse_grades(text: str) -> dict[str, dict[str, int]]:
    """Файл оценки → {кейс: {A: оценка, B: оценка}}; кейсы без оценок пропускаются."""
    grades: dict[str, dict[str, int]] = {}
    case = None
    for line in text.splitlines():
        if line.startswith("## "):
            case = line[3:].split(":", 1)[0].strip()
        elif case and (m := GRADE.match(line.strip())):
            grades.setdefault(case, {})[m.group(1)] = int(m.group(2))
    return grades


def reveal(grades: dict[str, dict[str, int]], key: dict[str, dict[str, str]]) -> dict[str, dict]:
    """Оценки по моделям: среднее, по кейсам, сколько раз модель оценена выше."""
    per_model: dict[str, dict] = {}
    for case, g in grades.items():
        if case not in key or set(g) != {"A", "B"}:
            continue
        for label, score in g.items():
            m = per_model.setdefault(key[case][label], {"scores": {}, "wins": 0})
            m["scores"][case] = score
        a, b = key[case]["A"], key[case]["B"]
        if g["A"] != g["B"]:
            per_model[a if g["A"] > g["B"] else b]["wins"] += 1
    for m in per_model.values():
        m["mean"] = round(statistics.fmean(m["scores"].values()), 2)
    return per_model


def write_blind_review(results: list[dict], models: list[str], stamp: str) -> tuple[Path, Path]:
    md, key = render_blind_review(results, models, seed=stamp)
    review = ROOT / f"answer_review_{stamp}.md"
    key_path = ROOT / f"answer_review_{stamp}.key.json"
    review.write_text(md, encoding="utf-8")
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
    return review, key_path


class Verdict(BaseModel):
    verdict: bool
    reason: str


NO_MATCH_CLAIM = re.compile(r"(полностью\s+)?подходящих\s+(вариантов\s+|объектов\s+)?нет|ничего\s+не\s+подходит", re.I)


def claims_no_match(text: str) -> str | None:
    """Фраза «подходящих нет» в ответе, если есть."""
    m = NO_MATCH_CLAIM.search(text)
    return m.group(0) if m else None


def evaluate_check(name: str, check: str, query: str, row: dict, text: str, cfg, settings) -> Verdict:
    if name == "no_false_no_match":
        found = claims_no_match(text)
        return Verdict(verdict=found is None, reason=f"в ответе: «{found}»" if found else "фразы «подходящих нет» нет")
    return judge(check, query, row, text, cfg, settings)


def answer_checks(row: dict, case: dict) -> dict[str, str]:
    """Какие критерии проверять судьёй для этого ответа: имя → текст критерия."""
    checks = {}
    if row["relaxed"]:
        snyato = ", ".join(RELAX_TEXT.get(x, x) for x in row["relaxed"])
        checks["mentions_relaxation"] = (
            f"Ответ явно сообщает, что поиск ослабил условия запроса, и называет, какие именно: {snyato}."
        )
    no_match = case.get("relevance") in ("manual", "llm_reviewed") and case.get("relevant_source_ids") == []
    if no_match:
        checks["states_no_match"] = (
            "Первая фраза ответа — «Полностью подходящих нет: …» (или дословно близкая) и после двоеточия "
            "называет, какие пожелания не подтверждены описаниями; дальше идут ближайшие варианты с "
            "оговорками и предложение, что ослабить в запросе. Подача найденного как «лучших» или "
            "«подходящих» вариантов без такой первой фразы — не выполняет критерий."
        )
    shown_relevant = set(row.get("ids", [])) & set(case.get("relevant_source_ids") or [])
    if shown_relevant and not row["relaxed"]:
        checks["no_false_no_match"] = (
            "Ответ НЕ утверждает, что полностью подходящих объектов нет: среди показанных кандидатов "
            "подходящие есть. Если ответ начинается с «Полностью подходящих нет» или иначе говорит, что "
            "ничего не подходит, — критерий не выполнен."
        )
    return checks


def judge(check: str, query: str, row: dict, text: str, cfg, settings) -> Verdict:
    # по-русски: служебные ключи (features) судья принимал за требование назвать их дословно
    relaxed = ", ".join(RELAX_TEXT.get(x, x) for x in row["relaxed"]) or "ничего"
    context = f"Поиск ослабил условия: {relaxed}; кандидатов: {len(row['ids'])}"
    return call_structured(
        api_key=settings.anthropic_api_key,
        model=cfg.models["judge"],
        system=Template(load_prompt("judge_answer.md")).substitute(check=check),
        user=f"Запрос: {query}\n{context}\n\nОтвет ассистента:\n{text}",
        output=Verdict,
    )


def expand_cases(tokens: list[str], all_cases: dict[str, dict]) -> list[str]:
    """id кейсов; «split:dev» раскрывается во все кейсы набора."""
    out: list[str] = []
    for t in tokens:
        ids = [cid for cid, c in all_cases.items() if c["split"] == t[6:]] if t.startswith("split:") else [t]
        out += [cid for cid in ids if cid not in out]
    return out


def cached_retrieval_fns(cfg, settings):
    """embed_fn / rerank_fn для retrieve() поверх кэшей evals: прогоны воспроизводимы,
    лимит Voyage не мешает."""
    cache = RerankCache(RERANK_CACHE)
    model, n = cfg.rerank.model, cfg.rerank.candidates

    def embed_fn(text: str) -> str:
        return embed_queries([text], cfg, settings)[0]

    def rerank_fn(conn, ids: list[str], text: str) -> list[str]:
        head = cache.get_or_compute(
            model,
            text,
            ids[:n],
            lambda: rerank_ids(conn, ids[:n], text, cfg.rerank, settings.voyage_api_key),
        )
        return head + ids[n:]

    return embed_fn, rerank_fn


def run_answer(args, cfg, settings) -> None:
    """Ответ на одинаковых выдачах для каждой модели из --models. Тексты — в файл на проверку человеку,
    в консоль — только цифры и вердикты судьи (в наборе могут быть holdout-кейсы)."""
    all_cases = {c["id"]: c for c in load_cases("dev")}
    case_ids = expand_cases(args.cases, all_cases)
    frozen: dict = {}
    for p in args.parsed_from or []:
        frozen |= filters_from_report(json.loads(p.read_text(encoding="utf-8")))
    embed_fn, rerank_fn = cached_retrieval_fns(cfg, settings)
    results = []
    with connect() as conn:
        for cid in case_ids:
            c = all_cases[cid]
            f = frozen.get(cid)
            r = retrieve(
                conn,
                c["query"],
                cfg,
                settings,
                filters=f if isinstance(f, SearchFilters) else None,
                embed_fn=embed_fn,
                rerank_fn=rerank_fn,
            )
            shown = [h.complex["source_id"] for h in r.hits]
            row = {
                "id": cid,
                "split": c["split"],
                "query": c["query"],
                "filters": r.filters.model_dump(exclude_defaults=True),
                "relaxed": r.relaxed,
                "clarification": r.clarification,
                "ids": shown,
                "retrieval_ms": r.timings_ms,
                "answers": {},
            }
            row["no_match"] = "states_no_match" in answer_checks(row, c)
            if not r.clarification:
                for m in args.models:
                    a = generate_answer(c["query"], r.filters, r.relaxed, r.hits, cfg, settings.anthropic_api_key, m)
                    entry = {**a.__dict__, "cost_usd": round(cost_usd(a, cfg), 6), "checks": {}}
                    if args.judge:
                        for name, check in answer_checks(row, c).items():
                            v = evaluate_check(name, check, c["query"], row, a.text, cfg, settings)
                            entry["checks"][name] = {"verdict": v.verdict, "reason": v.reason}
                    row["answers"][m] = entry
                    verdicts = {k: v["verdict"] for k, v in entry["checks"].items()}
                    print(
                        f"{cid} {m}: первый токен {a.ttft_ms} мс, всего {a.total_ms} мс, "
                        f"${entry['cost_usd']:.5f}, чужих номеров {len(a.invalid_refs)}, stop {a.stop_reason}"
                        + (f", судья {verdicts}" if verdicts else "")
                    )
            else:
                print(f"{cid}: парсер переспросил — ответа нет")
            results.append(row)

    summary = {}
    for m in args.models:
        ans = [r["answers"][m] for r in results if m in r["answers"]]
        ttft = [a["ttft_ms"] for a in ans if a["ttft_ms"] is not None]
        total = [a["total_ms"] for a in ans]
        checks: dict[str, list[bool]] = {}
        for a in ans:
            for name, v in a["checks"].items():
                checks.setdefault(name, []).append(v["verdict"])
        summary[m] = {
            "answers": len(ans),
            "clarified": sum(bool(r["clarification"]) for r in results),
            "ttft_ms": {"p50": int(statistics.median(ttft)), "p95": pct(ttft, 0.95)},
            "total_ms": {"p50": int(statistics.median(total)), "p95": pct(total, 0.95)},
            "usd_per_answer": round(statistics.fmean(a["cost_usd"] for a in ans), 6),
            "answers_with_invalid_refs": sum(bool(a["invalid_refs"]) for a in ans),
            "refusals": sum(a["stop_reason"] == "refusal" for a in ans),
            "fallbacks": sum(bool(a.get("fallback")) for a in ans),
            "checks": {k: {"passed": sum(v), "of": len(v)} for k, v in checks.items()},
        }
    now = datetime.now(UTC)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    report = {
        "layer": "answer",
        "date": now.isoformat(timespec="seconds"),
        "models": args.models,
        "cases": case_ids,
        "parsed_from": [str(p) for p in args.parsed_from] if args.parsed_from else None,
        "summary": summary,
        "results": results,
    }
    tag = "+".join(args.models) + ("_" + args.tag if args.tag else "")
    out = ROOT / "results" / f"{stamp}_answer_{tag}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for m, s in summary.items():
        print(
            f"answer/{m}: {s['answers']} ответов (переспросил {s['clarified']}), "
            f"первый токен p50 {s['ttft_ms']['p50']} / p95 {s['ttft_ms']['p95']} мс, "
            f"всего p50 {s['total_ms']['p50']} / p95 {s['total_ms']['p95']} мс, ${s['usd_per_answer']:.5f} за ответ, "
            f"с чужими номерами {s['answers_with_invalid_refs']}, отказов {s['refusals']}, фолбэков {s['fallbacks']}"
            + "".join(f", {k} {v['passed']}/{v['of']}" for k, v in s["checks"].items())
        )
    print(f"report: {out.relative_to(ROOT.parent)}")
    if len(args.models) == 2:
        review, key_path = write_blind_review(results, args.models, stamp)
        print(f"на оценку вслепую: {review.relative_to(ROOT.parent)}\nключ: {key_path.relative_to(ROOT.parent)}")


def run_answer_judge(args, cfg, settings) -> None:
    """Перепроверка судьёй готового отчёта answer — без повторной генерации ответов. Пишет новый отчёт."""
    report = json.loads(args.report.read_text(encoding="utf-8"))
    all_cases = {c["id"]: c for c in load_cases("dev")}
    totals: dict[str, dict[str, list[bool]]] = {}
    for row in report["results"]:
        for m, a in row["answers"].items():
            a["checks"] = {}
            for name, check in answer_checks(row, all_cases[row["id"]]).items():
                v = evaluate_check(name, check, row["query"], row, a["text"], cfg, settings)
                a["checks"][name] = {"verdict": v.verdict, "reason": v.reason}
                totals.setdefault(m, {}).setdefault(name, []).append(v.verdict)
                print(f"{row['id']} {m}: {name} {v.verdict}")  # без причин: они цитируют ответ
    for m, checks in totals.items():
        report["summary"][m]["checks"] = {k: {"passed": sum(v), "of": len(v)} for k, v in checks.items()}
    report["rejudged_from"] = str(args.report)
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out = args.report.with_name(f"{stamp}_{args.report.name.split('_', 1)[1].removesuffix('.json')}_rejudged.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for m, checks in totals.items():
        print(f"answer/{m}: " + ", ".join(f"{k} {sum(v)}/{len(v)}" for k, v in checks.items()))
    print(f"report: {out}")


def run_answer_review(args) -> None:
    """Слепой файл оценки из готового отчёта answer — без повторных вызовов моделей."""
    report = json.loads(args.report.read_text(encoding="utf-8"))
    stamp = args.report.name.split("_", 1)[0]
    review, key_path = write_blind_review(report["results"], report["models"], stamp)
    print(f"на оценку вслепую: {review.relative_to(ROOT.parent)}\nключ: {key_path.relative_to(ROOT.parent)}")


def run_answer_reveal(args) -> None:
    grades = parse_grades(args.review.read_text(encoding="utf-8"))
    key = json.loads(args.review.with_suffix(".key.json").read_text(encoding="utf-8"))
    missing = sorted(set(key) - {c for c, g in grades.items() if set(g) == {"A", "B"}})
    if missing:
        raise SystemExit(f"не оценены (нужны обе оценки A и B): {missing}")
    for m, r in reveal(grades, key).items():
        print(f"{m}: средняя {r['mean']}, оценена выше в {r['wins']} кейсах, по кейсам {r['scores']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument(
        "--layer",
        default="parse",
        choices=["parse", "search", "answer", "answer-judge", "answer-review", "answer-reveal"],
    )
    ap.add_argument("--report", type=Path, help="answer-review: отчёт answer, из которого собрать слепой файл")
    ap.add_argument("--review", type=Path, help="answer-reveal: заполненный файл оценки")
    ap.add_argument("--cases", nargs="+", default=["split:dev"], help="answer: id кейсов или split:<набор>")
    ap.add_argument("--judge", action="store_true", help="answer: проверка судьёй (ослабление, «подходящих нет»)")
    ap.add_argument("--tag", help="answer: метка в имени отчёта")
    ap.add_argument("--models", nargs="+", default=["claude-sonnet-5-5", "claude-haiku-5-5"], help="answer: модели")
    ap.add_argument(
        "--parsed-from", type=Path, nargs="+", help="search/answer: взять фильтры от парсера из этих отчётов"
    )
    ap.add_argument("--split", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--repeats", type=int, default=1, help="parse: прогонов на кейс, оценка разброса")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = load_config(args.config)
    settings = get_settings()
    if args.layer == "search":
        run_search(args, cfg, settings)
    elif args.layer == "answer":
        run_answer(args, cfg, settings)
    elif args.layer == "answer-judge":
        run_answer_judge(args, cfg, settings)
    elif args.layer == "answer-review":
        run_answer_review(args)
    elif args.layer == "answer-reveal":
        run_answer_reveal(args)
    else:
        run_parse(args, cfg, settings)


LADDER = [  # (имя, фильтры, текст, метод) — та же лестница, что была на объявлениях (decisions.md)
    ("векторы по всему запросу", "gold", "query", "vector"),
    ("+ гибрид (векторы + полнотекстовый, RRF)", "gold", "query", "hybrid"),
    ("векторы по смысловой части", "parsed", "semantic", "vector"),
    ("гибрид по смысловой части", "parsed", "semantic", "hybrid"),
    ("+ реранкер", "parsed", "semantic", "rerank"),
]


def filters_from_report(report: dict) -> dict[str, SearchFilters | str]:
    """Фильтры парсера из отчёта parse (первый прогон): got — поля, отличные от умолчаний."""
    out: dict[str, SearchFilters | str] = {}
    for r in report["results"]:
        run = r["runs"][0]
        out[r["id"]] = SearchFilters.model_validate(run["got"]) if run["got"] is not None else run["error"]
    return out


def run_search(args, cfg, settings) -> None:
    """Лестница поиска по ЖК на размеченных смысловых кейсах: Recall@10 и MRR по шагам."""
    cases = [c for c in load_cases(args.split) if "relevant_source_ids" in c]
    if not args.parsed_from:
        raise SystemExit("нужен --parsed-from <отчёт parse>: фильтры парсера фиксируются по прогону")
    parsed = filters_from_report(json.loads(args.parsed_from[0].read_text(encoding="utf-8")))
    cache = RerankCache(RERANK_CACHE)
    rows = []
    with connect() as conn:
        for c in cases:
            gold = SearchFilters.model_validate(c["expected_filters"])
            pf = parsed.get(c["id"])
            row = {"id": c["id"], "n_relevant": len(c["relevant_source_ids"]), "steps": {}}
            for name, source, text_kind, method in LADDER:
                f = gold if source == "gold" else pf
                if not isinstance(f, SearchFilters) or f.needs_clarification:
                    row["steps"][name] = {"recall": 0.0, "rr": 0.0, "hits": 0, "note": "парсер не дал фильтры"}
                    continue
                text = c["query"] if text_kind == "query" else catalog.query_text(f, c["query"])
                [vec] = embed_queries([text], cfg, settings)
                ranked, relaxed = catalog.search(conn, f, vec, text, "vector" if method == "vector" else "hybrid")
                if method == "rerank" and ranked:
                    n = cfg.rerank.candidates
                    head = cache.get_or_compute(
                        cfg.rerank.model,
                        text,
                        ranked[:n],
                        lambda: rerank_ids(
                            conn, ranked[:n], text, cfg.rerank, settings.voyage_api_key, table="complexes"
                        ),
                    )
                    ranked = head + ranked[n:]
                if source == "gold":
                    row["n_candidates"] = len(ranked)
                if c["relevant_source_ids"]:  # без подходящих Recall не определён — кейс для честного ноля
                    row["steps"][name] = search_metrics(ranked, c["relevant_source_ids"]) | {"relaxed": relaxed}
            row["ranking_matters"] = c["semantic"] == "required" and row.get("n_candidates", 0) > K
            rows.append(row)

    scored = [r for r in rows if r["ranking_matters"] and r["n_relevant"] > 0]
    summary = {
        name: {
            "n": len(scored),
            "recall_at_10": round(statistics.fmean(r["steps"][name]["recall"] for r in scored), 3),
            "mrr": round(statistics.fmean(r["steps"][name]["rr"] for r in scored), 3),
        }
        for name, *_ in LADDER
    }
    now = datetime.now(UTC)
    report = {
        "layer": "search",
        "split": args.split,
        "date": now.isoformat(timespec="seconds"),
        "parsed_from": str(args.parsed_from[0]),
        "rerank_model": cfg.rerank.model,
        "embeddings": cfg.embeddings.model,
        "ranking_matters": [r["id"] for r in scored],
        "summary": summary,
        "results": rows,
    }
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_search_{args.split}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ids = ", ".join(r["id"] for r in scored)
    print(f"search/{args.split}: ранжирование решает — {len(scored)} кейсов ({ids})")
    for name, m in summary.items():
        print(f"  {name:45} Recall@10 {m['recall_at_10']:.0%}  MRR {m['mrr']:.2f}")
    for r in scored:
        print(
            f"  {r['id']} (подходящих {r['n_relevant']}, кандидатов {r['n_candidates']}): "
            + "  ".join(f"{s['recall']:.0%}/{s['rr']:.2f}" for s in r["steps"].values())
        )
    print(f"report: {out.relative_to(ROOT.parent)}")


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
    prompt_file = PROMPT_FILE
    prompt = (PROMPTS_DIR / prompt_file).read_bytes()
    now = datetime.now(UTC)
    report = {
        "layer": "parse",
        "split": args.split,
        "date": now.isoformat(timespec="seconds"),
        "config": args.config,
        "model": cfg.models["parser"],
        "prompt": prompt_file,
        "prompt_sha": hashlib.sha256(prompt).hexdigest()[:12],
        "summary": summary,
        "results": results,
    }
    kind = "parse"
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_{kind}_{args.split}_x{args.repeats}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    fc = summary["fully_correct"]
    print(
        f"{kind}/{args.split} x{args.repeats}: fully correct {fc['mean']:.0%} ± {fc['stdev']:.0%} "
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
