"""LLM-разметка relevant_source_ids для смысловых кейсов с ручной проверкой.

1) review — LLM решает «подходит / нет» по пулу кандидатов (тот же пул, что у label_relevant.py)
   по правилам из docs/labeling.md и пишет таблицу в evals/llm_labels_review.md.
   В консоль — только прогресс: ни тексты запросов, ни решения не выводятся.
2) Человек проверяет файл, правит «да»/«нет» и ставит в шапке «Проверено: да».
3) apply — переносит решения в cases.jsonl с "relevance": "llm_reviewed".

    python scripts/llm_label.py review --config config/demo.yaml
    python scripts/llm_label.py apply --config config/demo.yaml

Новая партия кейсов — отдельным файлом, не трогая уже проверенные:
    python scripts/llm_label.py review --only d21 d22 --file evals/llm_labels_review_v2.md

Дооценка: неразмеченные объекты топ-10 из отчёта поиска → короткий файл → apply --merge:
    python scripts/llm_label.py extend --report evals/results/<...>_search_vector+hybrid_dev.json \
        --file evals/llm_labels_extend_1.md
    python scripts/llm_label.py apply --merge --file evals/llm_labels_extend_1.md
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


def targets(cases: list[dict], only: list[str] | None = None) -> list[dict]:
    """Смысловые кейсы; с only — только перечисленные (и все они должны быть смысловыми)."""
    manual = [c for c in cases if labeling_mode(c) == "manual"]
    if not only:
        return manual
    unknown = set(only) - {c["id"] for c in manual}
    if unknown:
        raise SystemExit(f"не смысловые или неизвестные кейсы: {sorted(unknown)}")
    return [c for c in manual if c["id"] in only]


def review(cfg, settings, only: list[str] | None = None, path: Path = REVIEW) -> None:
    cases = [json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip()]
    rules = labeling_rules(RULES_DOC.read_text(encoding="utf-8"))
    sections = []
    with connect() as conn:
        for c in targets(cases, only):
            _, rows = candidates(conn, c, cfg, settings.voyage_api_key)
            sections.append((c, rows, decide(c, rows, cfg, settings, rules)))
            print(f"{c['id']}: размечено кандидатов {len(rows)}")  # без запроса и решений
    if path.exists():
        raise SystemExit(f"{path} уже есть — не перезаписываю проверенный файл; укажи другой --file")
    path.write_text(render_review(sections, cfg.models["labeler"]), encoding="utf-8")
    print(f"готово: {path} — проверь, поправь и поставь «{REVIEWED_MARK}»")


def unjudged_from_report(report: dict, cases: list[dict]) -> dict[str, list[str]]:
    """Кейс → неразмеченные объекты из топ-10 всех методов отчёта (порядок первого появления, без повторов)."""
    manual = {c["id"]: c for c in targets(cases)}
    out: dict[str, list[str]] = {}
    for results in report["results"].values():
        for r in results:
            if r["id"] not in manual:
                continue
            judged = set(manual[r["id"]].get("judged_source_ids", []))
            for sid in r["top10"]:
                if sid not in judged and sid not in out.setdefault(r["id"], []):
                    out[r["id"]].append(sid)
    return {k: v for k, v in out.items() if v}


def rows_by_ids(conn, ids: list[str]) -> list[tuple]:
    """Строки в формате пула кандидатов, в порядке ids."""
    rows = {
        r[0]: r
        for r in conn.execute(
            """SELECT source_id, price_kzt, rooms, area_m2, district, condition, features, description
               FROM listings WHERE source_id = ANY(%s)""",
            (ids,),
        ).fetchall()
    }
    return [rows[i] for i in ids]


def extend(cfg, settings, report_path: Path, path: Path) -> None:
    if path.exists():
        raise SystemExit(f"{path} уже есть — не перезаписываю проверенный файл; укажи другой --file")
    cases = {c["id"]: c for c in (json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip())}
    todo = unjudged_from_report(json.loads(report_path.read_text(encoding="utf-8")), list(cases.values()))
    if not todo:
        print("неразмеченных в топ-10 нет — дооценка не нужна")
        return
    rules = labeling_rules(RULES_DOC.read_text(encoding="utf-8"))
    sections = []
    with connect() as conn:
        for cid, ids in todo.items():
            rows = rows_by_ids(conn, ids)
            sections.append((cases[cid], rows, decide(cases[cid], rows, cfg, settings, rules)))
            print(f"{cid}: на дооценку {len(rows)}")  # без запроса и решений
    path.write_text(render_review(sections, cfg.models["labeler"]), encoding="utf-8")
    print(f"готово: {path} — проверь, поставь «{REVIEWED_MARK}», затем apply --merge --file {path}")


def apply(cfg, only: list[str] | None = None, path: Path = REVIEW, merge: bool = False) -> None:
    decisions = parse_review(path.read_text(encoding="utf-8"))
    cases = [json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip()]
    manual = {c["id"] for c in targets(cases)}
    if merge:  # дооценка: в файле только кейсы, где были неразмеченные
        if not set(decisions) <= manual:
            raise SystemExit(f"не смысловые кейсы в файле: {sorted(set(decisions) - manual)}")
    elif set(decisions) != (expected := {c["id"] for c in targets(cases, only)}):
        raise SystemExit(f"в файле кейсы {sorted(decisions)}, ожидались {sorted(expected)}")
    with connect() as conn:
        all_ids = [r[0] for r in conn.execute("SELECT source_id FROM listings")]
    sid = resolve_refs({h for d in decisions.values() for h in d}, all_ids)
    lines = CASES.open(encoding="utf-8").readlines()
    for case_id, d in decisions.items():
        relevant = [sid[h] for h, ok in d.items() if ok]
        lines = set_labels(lines, case_id, relevant, RELEVANCE, judged=[sid[h] for h in d], merge=merge)
    CASES.write_text("".join(lines), encoding="utf-8")
    print(f"перенесено в {CASES}: {len(decisions)} кейсов, relevance={RELEVANCE}")  # без решений


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["review", "extend", "apply"])
    ap.add_argument("--report", type=Path, help="extend: отчёт поиска (evals/results/*_search_*.json)")
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--only", nargs="+", help="id смысловых кейсов; по умолчанию все")
    ap.add_argument("--file", type=Path, default=REVIEW, help="файл проверки")
    ap.add_argument("--merge", action="store_true", help="apply: добавить к прежней разметке (дооценка)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.command == "review":
        review(cfg, get_settings(), args.only, args.file)
    elif args.command == "extend":
        extend(cfg, get_settings(), args.report, args.file)
    else:
        apply(cfg, args.only, args.file, args.merge)


if __name__ == "__main__":
    main()
