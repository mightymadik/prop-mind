"""LLM-разметка relevant_source_ids для смысловых кейсов с ручной проверкой.

1) review — LLM решает «подходит / нет» по пулу кандидатов (тот же пул, что у label_relevant.py)
   по правилам из docs/labeling.md и пишет таблицу в evals/llm_labels_review.md.
   В консоль — только прогресс: ни тексты запросов, ни решения не выводятся.
2) Человек проверяет файл, правит «да»/«нет» и ставит в шапке «Проверено: да».
3) apply — переносит решения в cases.jsonl с "relevance": "llm_reviewed".

    python scripts/llm_label.py review --config config/demo.yaml
    python scripts/llm_label.py apply --config config/demo.yaml
"""

import argparse
import json
import re
from pathlib import Path
from string import Template

from pydantic import BaseModel

from app.config import get_settings, load_config
from app.db import connect
from app.llm.client import call_structured, load_prompt
from scripts.label_relevant import CASES, candidates, fmt_price, labeling_mode, ref, set_labels

REVIEW = Path("evals/llm_labels_review.md")
RULES_DOC = Path("docs/labeling.md")
RULES_HEADING = "## Ручная разметка: что считать подходящим"
RELEVANCE = "llm_reviewed"
REVIEWED_MARK = "Проверено: да"


class Decision(BaseModel):
    n: int
    relevant: bool
    reason: str


class Decisions(BaseModel):
    items: list[Decision]


def labeling_rules(doc: str) -> str:
    """Раздел правил из docs/labeling.md — единственный источник правил для человека и LLM."""
    start = doc.index(RULES_HEADING) + len(RULES_HEADING)
    end = doc.find("\n## ", start)
    return doc[start : end if end != -1 else None].strip()


def candidate_block(rows: list[tuple], feature_labels: dict[str, str]) -> str:
    lines = []
    for n, (_, price, rooms, area, district, cond, feats, desc) in enumerate(rows, 1):
        feats_txt = ", ".join(feature_labels.get(f, f) for f in feats) or "-"
        lines.append(
            f"[{n}] {fmt_price(price)}, {rooms or '-'} комн., {area or '-'} м², {district or '-'}, "
            f"{cond or '-'}, признаки: {feats_txt}\n    {desc}"
        )
    return "\n".join(lines)


def decide(case: dict, rows: list[tuple], cfg, settings, rules: str) -> list[Decision]:
    system = Template(load_prompt("label_relevance.md")).substitute(rules=rules)
    user = f"Запрос: {case['query']}\n\nКандидаты:\n{candidate_block(rows, cfg.features)}"
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
        if set(by_n) == set(range(1, len(rows) + 1)):
            return [by_n[n] for n in range(1, len(rows) + 1)]
    raise RuntimeError(f"{case['id']}: LLM вернула решения не по всем кандидатам")


def cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ").strip()


def render_review(sections: list[tuple[dict, list[tuple], list[Decision]]], model: str) -> str:
    out = [
        "# Проверка LLM-разметки relevant_source_ids",
        "",
        f"Модель: `{model}`. Правила: `docs/labeling.md`. Решение — `да` / `нет`; правь прямо в таблице.",
        "Причину можно не трогать. Когда проверишь всё, замени строку ниже на «Проверено: да».",
        "",
        "Проверено: нет",
    ]
    for case, rows, decisions in sections:
        out += [
            "",
            f"## {case['id']}: {case['query']}",
            "",
            "| кейс | номер | хеш | решение | причина |",
            "| --- | --- | --- | --- | --- |",
        ]
        for n, (row, d) in enumerate(zip(rows, decisions, strict=True), 1):
            out.append(f"| {case['id']} | {n} | {ref(row[0])} | {'да' if d.relevant else 'нет'} | {cell(d.reason)} |")
    return "\n".join(out) + "\n"


ROW = re.compile(r"^\|\s*(\w+)\s*\|\s*(\d+)\s*\|\s*([0-9a-f]{6})\s*\|\s*(\S+)\s*\|")


def parse_review(text: str) -> dict[str, dict[str, bool]]:
    """Таблица → {case_id: {хеш: подходит}}. ValueError, если файл не отмечен проверенным или решение не да/нет."""
    if not any(line.strip() == REVIEWED_MARK for line in text.splitlines()):
        raise ValueError(f"файл не отмечен проверенным: нужна строка «{REVIEWED_MARK}»")
    out: dict[str, dict[str, bool]] = {}
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        case_id, _, h, decision = m.groups()
        if decision.lower() not in ("да", "нет"):
            raise ValueError(f"{case_id} {h}: решение должно быть «да» или «нет», а не «{decision}»")
        out.setdefault(case_id, {})[h] = decision.lower() == "да"
    return out


def resolve_refs(refs: set[str], source_ids: list[str]) -> dict[str, str]:
    """Хеш → source_id по всем объектам базы; ValueError, если хеш не найден или неоднозначен."""
    by_ref: dict[str, list[str]] = {}
    for sid in source_ids:
        by_ref.setdefault(ref(sid), []).append(sid)
    out = {}
    for h in refs:
        found = by_ref.get(h, [])
        if len(found) != 1:
            raise ValueError(f"хеш {h}: найдено объектов {len(found)}, нужен ровно один")
        out[h] = found[0]
    return out


def targets(cases: list[dict]) -> list[dict]:
    return [c for c in cases if labeling_mode(c) == "manual"]


def review(cfg, settings) -> None:
    cases = [json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip()]
    rules = labeling_rules(RULES_DOC.read_text(encoding="utf-8"))
    sections = []
    with connect() as conn:
        for c in targets(cases):
            _, rows = candidates(conn, c, cfg, settings.voyage_api_key)
            sections.append((c, rows, decide(c, rows, cfg, settings, rules)))
            print(f"{c['id']}: размечено кандидатов {len(rows)}")  # без запроса и решений
    REVIEW.write_text(render_review(sections, cfg.models["labeler"]), encoding="utf-8")
    print(f"готово: {REVIEW} — проверь, поправь и поставь «{REVIEWED_MARK}»")


def apply(cfg) -> None:
    decisions = parse_review(REVIEW.read_text(encoding="utf-8"))
    cases = [json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip()]
    expected = {c["id"] for c in targets(cases)}
    if set(decisions) != expected:
        raise SystemExit(f"в файле кейсы {sorted(decisions)}, ожидались {sorted(expected)}")
    with connect() as conn:
        all_ids = [r[0] for r in conn.execute("SELECT source_id FROM listings")]
    sid = resolve_refs({h for d in decisions.values() for h in d}, all_ids)
    lines = CASES.open(encoding="utf-8").readlines()
    for case_id, d in decisions.items():
        lines = set_labels(lines, case_id, [sid[h] for h, ok in d.items() if ok], RELEVANCE)
    CASES.write_text("".join(lines), encoding="utf-8")
    print(f"перенесено в {CASES}: {len(decisions)} кейсов, relevance={RELEVANCE}")  # без решений


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["review", "apply"])
    ap.add_argument("--config", default="config/demo.yaml")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.command == "review":
        review(cfg, get_settings())
    else:
        apply(cfg)


if __name__ == "__main__":
    main()
