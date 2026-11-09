"""Полная LLM-разметка relevant_source_ids для смысловых кейсов новостроек — с ручной проверкой.

1) review — для каждого смыслового кейса LLM решает «подходит / нет» по ВСЕМ ЖК, у которых есть свободная
   квартира под expected_filters (правила — docs/labeling.md, раздел про ЖК), и пишет таблицу в файл.
   В консоль — только прогресс.
2) Человек проверяет файл, правит «да»/«нет» и ставит в шапке «Проверено: да».
3) apply — переносит решения в cases_newbuild.jsonl: relevant_source_ids, judged_source_ids,
   "relevance": "llm_reviewed".

    uv run python scripts/label_catalog.py review --file evals/catalog_labels_review.md
    uv run python scripts/label_catalog.py apply --file evals/catalog_labels_review.md
"""

import argparse
import json
import re
from pathlib import Path
from string import Template

from pydantic import BaseModel

from app.config import AppConfig, get_settings, load_config
from app.db import connect
from app.llm.answer import completion_text
from app.llm.client import call_structured, load_prompt
from app.schemas import SearchFilters
from app.search.catalog import COMPLEX_FIELDS, complex_where, unit_where

CASES = Path("evals/cases_newbuild.jsonl")
RULES_DOC = Path("docs/labeling.md")
RULES_HEADING = "## Разметка ЖК (новостройки, 6.3)"
RELEVANCE = "llm_reviewed"
REVIEWED_MARK = "Проверено: да"


class Decision(BaseModel):
    n: int
    relevant: bool
    reason: str


class Decisions(BaseModel):
    items: list[Decision]


def labeling_rules(doc: str, heading: str = RULES_HEADING) -> str:
    start = doc.index(heading) + len(heading)
    end = doc.find("\n## ", start)
    return doc[start : end if end != -1 else None].strip()


def candidates(conn, case: dict) -> list[dict]:
    """Все ЖК со свободной квартирой под expected_filters, по source_id."""
    f = SearchFilters.model_validate(case["expected_filters"])
    cw, cp = complex_where(f)
    uw, up = unit_where(f)
    rows = conn.execute(
        f"SELECT {', '.join('c.' + x for x in COMPLEX_FIELDS)} FROM complexes c WHERE {cw} "
        f"AND EXISTS (SELECT 1 FROM units u WHERE u.complex_id = c.source_id AND {uw}) ORDER BY c.source_id",
        {**cp, **up},
    ).fetchall()
    return [dict(zip(COMPLEX_FIELDS, r, strict=True)) for r in rows]


def candidate_block(complexes: list[dict], cfg: AppConfig) -> str:
    cat = cfg.catalog
    lines = []
    for n, c in enumerate(complexes, 1):
        infra = ", ".join(cat.complex_features.get(x, x) for x in c["features"]) or "-"
        lines.append(
            f"[{n}] ЖК {c['name']} — {cat.classes.get(c['housing_class'], c['housing_class'])}, {c['district']} р-н, "
            f"{c['city']}; {completion_text(c)}; инфраструктура: {infra}\n    {c['description']}"
        )
    return "\n".join(lines)


def decide(case: dict, complexes: list[dict], cfg, settings, rules: str) -> list[Decision]:
    system = Template(load_prompt("label_relevance.md")).substitute(rules=rules)
    user = f"Запрос: {case['query']}\n\nКандидаты:\n{candidate_block(complexes, cfg)}"
    for _ in range(2):  # один повтор, если вернулись не все номера
        out = call_structured(
            api_key=settings.anthropic_api_key,
            model=cfg.models["labeler"],
            system=system,
            user=user,
            output=Decisions,
            max_tokens=16000,
        )
        by_n = {d.n: d for d in out.items}
        if set(by_n) == set(range(1, len(complexes) + 1)):
            return [by_n[n] for n in range(1, len(complexes) + 1)]
    raise RuntimeError(f"{case['id']}: LLM вернула решения не по всем кандидатам")


def cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def render_review(sections: list[tuple[dict, list[dict], list[Decision]]], model: str) -> str:
    out = [
        "# Проверка LLM-разметки ЖК (новостройки)",
        "",
        f"Модель: `{model}`. Правила: `docs/labeling.md`, раздел про ЖК. Разметка полная: все ЖК со свободной",
        "квартирой под фильтры кейса. Решение — `да` / `нет`; правь прямо в таблице. Когда проверишь всё,",
        "замени строку ниже на «Проверено: да».",
        "",
        "Проверено: нет",
    ]
    for case, complexes, decisions in sections:
        n_yes = sum(d.relevant for d in decisions)
        out += [
            "",
            f"## {case['id']}: {case['query']}",
            "",
            f"Кандидатов: {len(complexes)}, подходит по LLM: {n_yes}.",
            "",
            "| кейс | ЖК | id | решение | причина |",
            "| --- | --- | --- | --- | --- |",
        ]
        for c, d in zip(complexes, decisions, strict=True):
            verdict = "да" if d.relevant else "нет"
            out.append(f"| {case['id']} | {cell(c['name'])} | {c['source_id']} | {verdict} | {cell(d.reason)} |")
    return "\n".join(out) + "\n"


ROW = re.compile(r"^\|\s*(\w+)\s*\|[^|]*\|\s*(cx-\d+)\s*\|\s*(\S+)\s*\|")


def parse_review(text: str) -> dict[str, dict[str, bool]]:
    """Таблица → {case_id: {source_id: подходит}}. ValueError, если не отмечено проверенным или решение не да/нет."""
    if not any(line.strip() == REVIEWED_MARK for line in text.splitlines()):
        raise ValueError(f"файл не отмечен проверенным: нужна строка «{REVIEWED_MARK}»")
    out: dict[str, dict[str, bool]] = {}
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        case_id, sid, decision = m.groups()
        if decision.lower() not in ("да", "нет"):
            raise ValueError(f"{case_id} {sid}: решение должно быть «да» или «нет», а не «{decision}»")
        out.setdefault(case_id, {})[sid] = decision.lower() == "да"
    return out


def apply_labels(cases: list[dict], decisions: dict[str, dict[str, bool]]) -> list[dict]:
    out = []
    for c in cases:
        d = decisions.get(c["id"])
        if d is not None:
            c = c | {
                "relevant_source_ids": sorted(s for s, ok in d.items() if ok),
                "judged_source_ids": sorted(d),
                "relevance": RELEVANCE,
            }
        out.append(c)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["review", "apply"])
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--file", type=Path, default=Path("evals/catalog_labels_review.md"))
    ap.add_argument("--only", nargs="+", help="только эти кейсы")
    args = ap.parse_args()
    cases = [json.loads(ln) for ln in CASES.read_text(encoding="utf-8").splitlines() if ln.strip()]

    if args.cmd == "apply":
        decisions = parse_review(args.file.read_text(encoding="utf-8"))
        CASES.write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in apply_labels(cases, decisions)), encoding="utf-8"
        )
        print(f"applied: {len(decisions)} кейсов")
        return

    cfg, settings = load_config(args.config), get_settings()
    rules = labeling_rules(RULES_DOC.read_text(encoding="utf-8"))
    todo = [c for c in cases if c["semantic"] == "required" and (not args.only or c["id"] in args.only)]
    sections = []
    with connect() as conn:
        for c in todo:
            complexes = candidates(conn, c)
            decisions = decide(c, complexes, cfg, settings, rules) if complexes else []
            sections.append((c, complexes, decisions))
            print(f"{c['id']}: кандидатов {len(complexes)}")
    args.file.write_text(render_review(sections, cfg.models["labeler"]), encoding="utf-8")
    print(f"review: {args.file}")


if __name__ == "__main__":
    main()
