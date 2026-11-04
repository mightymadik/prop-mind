"""Разметка relevant_source_ids для evals/cases.jsonl. Правила — docs/labeling.md.

semantic: empty — автоматически: все объекты, проходящие expected_filters (пересчитывается
при каждом запуске, "relevance": "auto"). semantic: required — вручную ("relevance": "manual").
Кейсы с уточнением не размечаются.

Кандидаты — пул из двух методов внутри эталонных фильтров кейса (expected_filters, без парсера):
топ по векторам и топ по полнотекстовому поиску, по очереди, без повторов, до 30 штук.
Метод кандидата и source_id не показываются (вместо него короткий хеш), порядок перемешан —
чтобы разметка не тянулась к одному методу и не выдавала подложенные объекты (seed-*).

    python scripts/label_relevant.py --config config/demo.yaml
    python scripts/label_relevant.py --config config/demo.yaml --relabel d08

Ввод: номера подходящих «1,4,7-9»; пустая строка — подходящих нет; «? N» — полное описание
кандидата N; s — пропустить; q — выйти.
"""

import argparse
import hashlib
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
JUDGED = "judged_source_ids"  # все оценённые объекты (и подходящие, и нет): база для «неразмеченных в топ-10»


def parse_selection(text: str, n: int) -> list[int]:
    """«1,4,7-9» → [1, 4, 7, 8, 9] (номера с 1). ValueError на мусор и номера вне 1..n."""
    picked: set[int] = set()
    text = text.replace(" ", "")
    if not text:
        return []
    for part in text.split(","):
        if not part:
            raise ValueError("пустой элемент между запятыми")
        lo, _, hi = part.partition("-")
        a, b = int(lo), int(hi or lo)
        if not 1 <= a <= b <= n:
            raise ValueError(f"номер вне 1..{n}: {part}")
        picked.update(range(a, b + 1))
    return sorted(picked)


def pick_one(text: str, n: int) -> int | None:
    """«3» → 3; пусто, диапазон, мусор или номер вне 1..n → None."""
    text = text.strip()
    return int(text) if text.isdigit() and 1 <= int(text) <= n else None


def interleave(*ranked: list[str], limit: int) -> list[str]:
    """Пул: по очереди из каждого списка, без повторов, не больше limit."""
    out: list[str] = []
    for i in range(max(map(len, ranked), default=0)):
        for r in ranked:
            if i < len(r) and r[i] not in out:
                out.append(r[i])
    return out[:limit]


def set_labels(
    lines: list[str], case_id: str, ids: list[str], relevance: str, judged: list[str], merge: bool = False
) -> list[str]:
    """Переписывает только строку нужного кейса; остальные строки остаются байт в байт.

    merge=True — дооценка: подходящие и оценённые добавляются к прежним, а не заменяют их.
    """
    if not set(ids) <= set(judged):
        raise ValueError(f"{case_id}: подходящие должны входить в оценённые")
    out = []
    for line in lines:
        if line.strip() and json.loads(line)["id"] == case_id:
            case = json.loads(line)
            new_ids = set(ids) | set(case.get(FIELD, [])) if merge else set(ids)
            new_judged = set(judged) | set(case.get(JUDGED, [])) if merge else set(judged)
            new = {FIELD: sorted(new_ids), JUDGED: sorted(new_judged), "relevance": relevance}
            if any(case.get(k) != v for k, v in new.items()):
                case |= new
                line = json.dumps(case, ensure_ascii=False) + "\n"
        out.append(line)
    return out


def labeling_mode(case: dict) -> str | None:
    """auto — эталон = все объекты под фильтрами; manual — размечает человек; None — не размечаем."""
    if case.get("split") == "relax" or case["expected_filters"].get("needs_clarification"):
        return None  # relax — проверка ослабления фильтров, эталона релевантности нет
    return {"empty": "auto", "required": "manual"}.get(case["semantic"])


def needs_labels(case: dict, relabel: str | None) -> bool:
    if labeling_mode(case) != "manual":
        return False
    return case["id"] == relabel if relabel else FIELD not in case


def auto_labels(conn, cases: list[dict]) -> dict[str, list[str]]:
    """Для semantic: empty — все source_id, проходящие expected_filters."""
    out = {}
    for c in cases:
        if labeling_mode(c) == "auto":
            where, params = where_from_filters(SearchFilters.model_validate(c["expected_filters"]))
            out[c["id"]] = [r[0] for r in conn.execute(f"SELECT source_id FROM listings WHERE {where}", params)]
    return out


def ref(source_id: str) -> str:
    """Короткая нейтральная метка вместо source_id: по ней не видно, подложен ли объект."""
    return hashlib.sha1(source_id.encode()).hexdigest()[:6]


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


def format_row(i: int, row: tuple) -> str:
    """Строка таблицы кандидата; её же повторяет «? N» — чтобы сверять по хешу и комнатам."""
    sid, price, rooms, area, district, cond, feats, desc = row
    return (
        f"  {i:>2} {ref(sid)} {fmt_price(price):>9} {rooms or '-'}к {area or '-':>5}м² "
        f"{district or '-':<13} {cond or '-':<10} {','.join(feats) or '-':<22} {desc[:80]}"
    )


def ask(case_id: str, rows: list[tuple], read=input, write=print) -> list[str] | str:
    """Диалог по одному кейсу → выбранные source_id, либо "s" / "q". Сохранение — только после подтверждения."""
    while True:
        answer = read(f"  {case_id} — подходящие (1,4,7-9 / пусто = нет / ? N / s / q): ").strip()
        if answer in ("s", "q"):
            return answer
        if answer.startswith("?"):
            k = pick_one(answer[1:], len(rows))
            if k:
                write(f"  {case_id}, кандидат {k}:\n{format_row(k, rows[k - 1])}\n      {rows[k - 1][-1]}")
            else:
                write(f"  ошибка: нужен один номер 1..{len(rows)}")
            continue
        try:
            picked = parse_selection(answer, len(rows))
        except ValueError as e:
            write(f"  ошибка: {e}")
            continue
        chosen = ", ".join(f"{i} ({ref(rows[i - 1][0])})" for i in picked) or "ничего — подходящих нет"
        write(f"  {case_id}: отмечено {chosen}")
        if not read("  сохранить? Enter — да, любой другой ввод — выбрать заново: ").strip():
            return [rows[i - 1][0] for i in picked]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--relabel", help="id кейса, который разметить заново")
    args = ap.parse_args()
    cfg = load_config(args.config)
    api_key = get_settings().voyage_api_key

    cases = [json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip()]
    todo = [c for c in cases if needs_labels(c, args.relabel)]
    with connect() as conn:
        lines = CASES.open(encoding="utf-8").readlines()
        auto = auto_labels(conn, cases)
        for cid, ids in auto.items():
            lines = set_labels(lines, cid, ids, "auto", judged=ids)  # оценено всё под фильтрами
        CASES.write_text("".join(lines), encoding="utf-8")
        print("авто (все объекты под фильтрами): " + ", ".join(f"{k}={len(v)}" for k, v in auto.items()))
        print(f"к ручной разметке: {len(todo)} кейсов. Правила: docs/labeling.md\n")
        for n, case in enumerate(todo, 1):
            total, rows = candidates(conn, case, cfg, api_key)
            print(f"[{n}/{len(todo)}] {case['id']}: {case['query']}")
            print(f"  фильтры: {case['expected_filters']}  | под фильтры подходит объектов: {total}")
            for i, row in enumerate(rows, 1):
                print(format_row(i, row))
            result = ask(case["id"], rows)
            if result == "q":
                break
            if result == "s":
                continue
            lines = CASES.open(encoding="utf-8").readlines()
            judged = [r[0] for r in rows]
            CASES.write_text("".join(set_labels(lines, case["id"], result, "manual", judged)), encoding="utf-8")
            print(f"  {case['id']}: записано {len(result)} шт.\n")


if __name__ == "__main__":
    main()
