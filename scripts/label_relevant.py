"""Ручная разметка relevant_source_ids для evals/cases.jsonl. Правила — docs/labeling.md.

Кандидаты — пул из двух методов внутри эталонных фильтров кейса (expected_filters, без парсера):
топ по векторам и топ по полнотекстовому поиску, по очереди, без повторов, до 30 штук.
Метод кандидата не показывается, порядок перемешан — чтобы разметка не тянулась к одному методу.

    python scripts/label_relevant.py --config config/demo.yaml
    python scripts/label_relevant.py --config config/demo.yaml --relabel d08

Ввод: номера подходящих «1,4,7-9»; пустая строка — подходящих нет; s — пропустить; q — выйти.
"""

import argparse
import json
import random
from pathlib import Path

from app.config import get_settings, load_config
from app.db import connect
from app.schemas import SearchFilters
from app.search.embeddings import embed, to_pgvector
from app.search.hybrid import where_from_filters

CASES = Path("evals/cases.jsonl")
POOL = 30
FIELD = "relevant_source_ids"


def parse_selection(text: str, n: int) -> list[int]:
    """«1,4,7-9» → [1, 4, 7, 8, 9] (номера с 1). ValueError на мусор и номера вне 1..n."""
    picked: set[int] = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        a, b = int(lo), int(hi or lo)
        if not 1 <= a <= b <= n:
            raise ValueError(f"номер вне 1..{n}: {part}")
        picked.update(range(a, b + 1))
    return sorted(picked)


def interleave(*ranked: list[str], limit: int) -> list[str]:
    """Пул: по очереди из каждого списка, без повторов, не больше limit."""
    out: list[str] = []
    for i in range(max(map(len, ranked), default=0)):
        for r in ranked:
            if i < len(r) and r[i] not in out:
                out.append(r[i])
    return out[:limit]


def set_labels(lines: list[str], case_id: str, ids: list[str]) -> list[str]:
    """Переписывает только строку нужного кейса; остальные строки остаются байт в байт."""
    out = []
    for line in lines:
        if line.strip() and json.loads(line)["id"] == case_id:
            case = json.loads(line)
            case[FIELD] = sorted(ids)
            line = json.dumps(case, ensure_ascii=False) + "\n"
        out.append(line)
    return out


def needs_labels(case: dict, relabel: str | None) -> bool:
    if case["expected_filters"].get("needs_clarification"):
        return False
    return case["id"] == relabel if relabel else FIELD not in case


def fmt_price(p: int) -> str:
    return f"{p / 1e6:.1f} млн" if p >= 1_000_000 else f"{p / 1e3:.0f} тыс"


def candidates(conn, case: dict, cfg, api_key: str) -> tuple[int, list[tuple]]:
    filters = SearchFilters.model_validate(case["expected_filters"])
    where, params = where_from_filters(filters)
    total = conn.execute(f"SELECT count(*) FROM listings WHERE {where}", params).fetchone()[0]
    qvec = to_pgvector(embed([case["query"]], cfg.embeddings, api_key, input_type="query")[0])
    vec = conn.execute(
        f"SELECT source_id FROM listings WHERE {where} ORDER BY embedding <=> %(qvec)s::vector LIMIT {POOL}",
        {**params, "qvec": qvec},
    ).fetchall()
    # OR вместо AND: для пула нужны частичные совпадения, а не только тексты со всеми словами
    fts = conn.execute(
        f"""SELECT source_id FROM listings,
                   CAST(replace(plainto_tsquery('russian', %(qtext)s)::text, '&', '|') AS tsquery) AS q
            WHERE {where} AND tsv @@ q ORDER BY ts_rank_cd(tsv, q) DESC LIMIT {POOL}""",
        {**params, "qtext": case["query"]},
    ).fetchall()
    ids = interleave([r[0] for r in vec], [r[0] for r in fts], limit=POOL)
    random.Random(case["id"]).shuffle(ids)  # воспроизводимо для одного кейса
    rows = {
        r[0]: r
        for r in conn.execute(
            """SELECT source_id, price_kzt, rooms, area_m2, district, condition, features, description
               FROM listings WHERE source_id = ANY(%s)""",
            (ids,),
        ).fetchall()
    }
    return total, [rows[i] for i in ids]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--relabel", help="id кейса, который разметить заново")
    args = ap.parse_args()
    cfg = load_config(args.config)
    api_key = get_settings().voyage_api_key

    todo = [
        c for c in map(json.loads, filter(str.strip, CASES.open(encoding="utf-8"))) if needs_labels(c, args.relabel)
    ]
    print(f"к разметке: {len(todo)} кейсов. Правила: docs/labeling.md\n")
    with connect() as conn:
        for n, case in enumerate(todo, 1):
            total, rows = candidates(conn, case, cfg, api_key)
            print(f"[{n}/{len(todo)}] {case['id']}: {case['query']}")
            print(f"  фильтры: {case['expected_filters']}  | под фильтры подходит объектов: {total}")
            for i, (sid, price, rooms, area, district, cond, feats, desc) in enumerate(rows, 1):
                print(
                    f"  {i:>2} {sid} {fmt_price(price):>9} {rooms or '-'}к {area or '-':>5}м² "
                    f"{district or '-':<13} {cond or '-':<10} {','.join(feats) or '-':<22} {desc[:80]}"
                )
            while True:
                answer = input("  подходящие (1,4,7-9 / пусто = нет / s / q): ").strip()
                if answer in ("s", "q"):
                    break
                try:
                    picked = parse_selection(answer, len(rows))
                    break
                except ValueError as e:
                    print(f"  ошибка: {e}")
            if answer == "q":
                break
            if answer == "s":
                continue
            ids = [rows[i - 1][0] for i in picked]
            lines = CASES.open(encoding="utf-8").readlines()
            CASES.write_text("".join(set_labels(lines, case["id"], ids)), encoding="utf-8")
            print(f"  записано: {len(ids)} шт.\n")


if __name__ == "__main__":
    main()
