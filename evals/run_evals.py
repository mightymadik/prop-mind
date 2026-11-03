"""Evals слоя разбора: SearchFilters против expected_filters из evals/cases.jsonl.

    python evals/run_evals.py --config config/demo.yaml            # dev
    python evals/run_evals.py --config config/demo.yaml --split holdout   # один раз, в конце

Отчёт: evals/results/<UTC-время>_parse_<split>.json
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
from app.llm.client import PROMPTS_DIR
from app.llm.parser import PROMPT_FILE, parse_query
from app.schemas import SearchFilters

ROOT = Path(__file__).resolve().parent
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


def summarize(results: list[dict]) -> dict:
    def rate(rows: list[dict]) -> float:
        return round(sum(r["ok"] for r in rows) / len(rows), 3) if rows else 0.0

    fields: dict[str, list[bool]] = {}
    for r in results:
        for f, ok in r["checks"].items():
            fields.setdefault(f, []).append(ok)
    tags = sorted({t for r in results for t in r["tags"]})
    lat = sorted(r["latency_ms"] for r in results)
    return {
        "cases": len(results),
        "fully_correct": rate(results),
        "errors": sum(r["error"] is not None for r in results),
        "by_field": {f: round(sum(v) / len(v), 3) for f, v in fields.items()},
        "by_tag": {t: {"n": len(rs), "fully_correct": rate(rs)}
                   for t in tags for rs in [[r for r in results if t in r["tags"]]]},
        "latency_ms": {"p50": int(statistics.median(lat)), "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))]},
    }


def run_case(case: dict, cfg, settings) -> dict:
    t = time.perf_counter()
    try:
        got = parse_query(case["query"], cfg, settings)
        error = None
    except Exception as e:  # noqa: BLE001 — упавший кейс = неверный, прогон продолжается
        got, error = None, f"{e.__class__.__name__}: {e}"
    latency = int((time.perf_counter() - t) * 1000)
    checks = compare_filters(got, case) if got else {"error": False}
    return {
        "id": case["id"], "query": case["query"], "tags": case["tags"],
        "ok": all(checks.values()), "checks": checks, "error": error, "latency_ms": latency,
        "expected": case["expected_filters"], "semantic": case["semantic"],
        "got": got.model_dump(exclude_defaults=True) if got else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--split", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = load_config(args.config)
    settings = get_settings()
    cases = [json.loads(line) for line in (ROOT / "cases.jsonl").open(encoding="utf-8") if line.strip()]
    cases = [c for c in cases if c["split"] == args.split]
    if not cases:
        raise SystemExit(f"no cases with split={args.split}")

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda c: run_case(c, cfg, settings), cases))

    summary = summarize(results)
    prompt = (PROMPTS_DIR / PROMPT_FILE).read_bytes()
    now = datetime.now(UTC)
    report = {
        "layer": "parse", "split": args.split, "date": now.isoformat(timespec="seconds"),
        "config": args.config, "model": cfg.models["parser"],
        "prompt": PROMPT_FILE, "prompt_sha": hashlib.sha256(prompt).hexdigest()[:12],
        "summary": summary, "results": results,
    }
    out = ROOT / "results" / f"{now:%Y%m%dT%H%M%SZ}_parse_{args.split}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"parse/{args.split}: {summary['fully_correct']:.0%} fully correct "
          f"({summary['cases']} cases, {summary['errors']} errors), "
          f"p50 {summary['latency_ms']['p50']} ms, p95 {summary['latency_ms']['p95']} ms")
    for f, acc in summary["by_field"].items():
        print(f"  field {f:20} {acc:.0%}")
    for t, s in summary["by_tag"].items():
        print(f"  tag   {t:20} {s['fully_correct']:.0%} (n={s['n']})")
    for r in results:
        if not r["ok"]:
            bad = [f for f, ok in r["checks"].items() if not ok]
            print(f"  FAIL {r['id']} {bad} {r['error'] or ''}\n       expected {r['expected']}\n       got      {r['got']}")
    print(f"report: {out.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
