"""Подложенные объекты для кейсов, где под expected_filters меньше 3 объектов в базе.

На кейс: подходящие (все фильтры + intent) и обманки (фильтры без intent, или intent при
ровно одном нарушенном фильтре). Поля задаёт скрипт, LLM пишет только текст и мелкие поля.
Генератор получает поля и пересказ смысла (case["intent"]), но не текст запроса.

    python scripts/seed_listings.py --config config/demo.yaml
    python scripts/sync_index.py --config config/demo.yaml

Пишет в data_path (source_id seed-<case>-<nn>) и data/seed_manifest.jsonl.
"""

import argparse
import json
import random
import re
from pathlib import Path

from pydantic import BaseModel

from app.config import AppConfig, get_settings, load_config
from app.db import connect
from app.llm.client import call_structured, load_prompt
from app.schemas import SearchFilters
from app.search.hybrid import where_from_filters

CASES = Path("evals/cases.jsonl")
MANIFEST = Path("data/seed_manifest.jsonl")
MIN_EXISTING = 3
N_RELEVANT, N_DECOYS = 4, 4
CONDITIONS = ["rough", "pre_finish", "renovated", "designer"]
VIOLATIONS = ["price", "districts", "rooms", "conditions", "features", "area"]


class SeedText(BaseModel):
    n: int
    complex_name: str | None
    floor: int
    floors_total: int
    year_built: int
    text: str


class SeedBatch(BaseModel):
    items: list[SeedText]


def violations(f: SearchFilters, x: dict) -> list[str]:
    """Какие фильтры объект x (поля listings) нарушает. Зеркало where_from_filters."""
    v = []
    if x["deal_type"] != f.deal_type:
        v.append("deal_type")
    if f.city and x["city"] != f.city:
        v.append("city")
    if f.rooms and x["rooms"] not in f.rooms:
        v.append("rooms")
    if (f.price_min_kzt is not None and x["price_kzt"] < f.price_min_kzt) or (
        f.price_max_kzt is not None and x["price_kzt"] > f.price_max_kzt
    ):
        v.append("price")
    if f.area_min_m2 is not None and x["area_m2"] < f.area_min_m2:
        v.append("area")
    if f.districts and x["district"] not in f.districts:
        v.append("districts")
    if f.conditions and x["condition"] not in f.conditions:
        v.append("conditions")
    if f.features and not set(f.features) <= set(x["features"]):
        v.append("features")
    return v


def applicable_violations(f: SearchFilters, cfg: AppConfig) -> list[str]:
    """Фильтры, которые можно правдоподобно нарушить: заданы и перечисляют не все варианты."""
    city_districts = next((ds for ds in cfg.districts.values() if f.districts and f.districts[0] in ds), [])
    present = {
        "price": f.price_min_kzt is not None or f.price_max_kzt is not None,
        "districts": bool(f.districts) and len(set(city_districts) - set(f.districts)) > 0,
        "rooms": bool(f.rooms) and len(set(range(1, 6)) - set(f.rooms)) > 0,
        "conditions": bool(f.conditions) and len(set(CONDITIONS) - set(f.conditions)) > 0,
        "features": bool(f.features),
        "area": f.area_min_m2 is not None,
    }
    return [k for k in VIOLATIONS if present[k]]


def _step(deal: str) -> int:
    return 100_000 if deal == "sale" else 5_000


def base_fields(f: SearchFilters, cfg: AppConfig, rng: random.Random) -> dict:
    """Поля объекта, удовлетворяющие всем фильтрам f."""
    city = f.city or next((c for c, ds in cfg.districts.items() if f.districts and f.districts[0] in ds), None)
    city = city or rng.choice(cfg.cities)
    rooms = rng.choice(f.rooms) if f.rooms else rng.randint(1, 4)
    area = round(max(f.area_min_m2 or 0, 22 + rooms * 17) * rng.uniform(1.0, 1.2))
    if f.deal_type == "sale":
        lo, hi = rooms * 12_000_000, rooms * 20_000_000
    else:
        lo, hi = 100_000 + rooms * 80_000, 150_000 + rooms * 150_000
    lo = f.price_min_kzt if f.price_min_kzt is not None else min(lo, (f.price_max_kzt or lo / 0.6) * 0.6)
    hi = f.price_max_kzt if f.price_max_kzt is not None else max(hi, lo * 1.4)
    step = _step(f.deal_type)
    price = int(rng.uniform(lo, hi) // step * step)
    price = min(max(price, -(-int(lo) // step) * step), int(hi))  # округление не выводит из диапазона
    extra = [k for k in cfg.features if k not in f.features and not (k == "near_metro" and city != "Алматы")]
    return {
        "city": city,
        "district": rng.choice(f.districts) if f.districts else rng.choice(cfg.districts[city]),
        "deal_type": f.deal_type,
        "rooms": rooms,
        "area_m2": float(area),
        "condition": rng.choice(f.conditions) if f.conditions else rng.choice(CONDITIONS),
        "features": list(f.features) + rng.sample(extra, k=rng.randint(0, 1)),
        "price_kzt": price,
    }


def violate(x: dict, f: SearchFilters, kind: str, cfg: AppConfig, rng: random.Random) -> dict:
    """Копия x, где правдоподобно нарушен ровно один фильтр kind."""
    y = {**x, "features": list(x["features"])}
    step = _step(f.deal_type)
    if kind == "price":
        if f.price_max_kzt is not None:
            y["price_kzt"] = (int(f.price_max_kzt * rng.uniform(1.1, 1.3)) // step + 1) * step
        else:
            y["price_kzt"] = (int(f.price_min_kzt * rng.uniform(0.7, 0.9)) // step) * step
    elif kind == "districts":
        y["district"] = rng.choice([d for d in cfg.districts[x["city"]] if d not in f.districts])
    elif kind == "rooms":
        # ближайшее число комнат вне фильтра: при rooms=[1, 2] и x=1 соседей ±1 вне фильтра нет
        y["rooms"] = min((r for r in range(1, 6) if r not in f.rooms), key=lambda r: (abs(r - x["rooms"]), r))
    elif kind == "conditions":
        y["condition"] = rng.choice([c for c in CONDITIONS if c not in f.conditions])
    elif kind == "features":
        y["features"].remove(rng.choice(f.features))
    elif kind == "area":
        y["area_m2"] = float(round(f.area_min_m2 * rng.uniform(0.75, 0.9)))
    return y


def plan_case(case: dict, cfg: AppConfig) -> list[dict]:
    """Спецификации объектов кейса: роль, поля, задание к тексту. Воспроизводимо по id кейса."""
    f = SearchFilters.model_validate(case["expected_filters"])
    rng = random.Random(f"seed-{case['id']}")
    intent = case["intent"].strip()
    kinds = applicable_violations(f, cfg)
    specs = [{"role": "relevant", "violated": None} for _ in range(N_RELEVANT)]
    decoys = []
    for i in range(N_DECOYS):
        if intent and (i % 2 == 0 or not kinds):
            decoys.append({"role": "decoy", "violated": None})  # фильтры те же, смысла нет
        elif kinds:
            decoys.append({"role": "decoy", "violated": kinds[i % len(kinds)]})
    out = []
    for n, s in enumerate(specs + decoys, 1):
        x = base_fields(f, cfg, rng)
        if s["violated"]:
            x = violate(x, f, s["violated"], cfg, rng)
        if s["role"] == "decoy" and not s["violated"]:
            task = f"Не упоминай ни прямо, ни косвенно: {intent}. Можно описать противоположное."
        else:
            task = f"Естественно раскрой своими словами: {intent}" if intent else "Обычное объявление."
        out.append({"n": n, **s, "fields": x, "task": task})
    return out


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def shares_phrase(text: str, query: str, n: int = 3) -> bool:
    """Есть ли в тексте n слов запроса подряд. Проверка локальная, запрос никуда не уходит."""

    def grams(ws: list[str]) -> set[tuple[str, ...]]:
        return {tuple(ws[i : i + n]) for i in range(len(ws) - n + 1)}

    return bool(grams(words(text)) & grams(words(query)))


def to_raw(source_id: str, x: dict, t: SeedText, field_map: dict[str, str]) -> dict:
    """Поля listings → формат data_path (обратный field_map)."""
    row = {"source_id": source_id, **x, "description": t.text, "complex_name": t.complex_name}
    row |= {"floor": t.floor, "floors_total": t.floors_total, "year_built": t.year_built}
    back = {v: k for k, v in field_map.items()}
    return {back.get(k, k): v for k, v in row.items()}


def replace_seed_lines(lines: list[str], case_id: str, new: list[dict]) -> list[str]:
    """Убирает прежние строки seed-<case_id>-*, дописывает new. Остальные строки не трогает."""
    prefix = f"seed-{case_id}-"
    kept = [ln for ln in lines if not (ln.strip() and json.loads(ln)["source_id"].startswith(prefix))]
    return kept + [json.dumps(r, ensure_ascii=False) + "\n" for r in new]


def generate(case: dict, specs: list[dict], cfg: AppConfig, settings) -> dict[int, SeedText]:
    payload = [
        {
            "n": s["n"],
            "характеристики": {**s["fields"], "features": [cfg.features[k] for k in s["fields"]["features"]]},
            "задание": s["task"],
        }
        for s in specs
    ]
    for _ in range(3):  # ponytail: повтор всей пачки при пересечении с запросом; до 2 повторов
        batch = call_structured(
            api_key=settings.anthropic_api_key,
            model=cfg.models["generator"],
            system=load_prompt("seed_listings.md"),
            user=json.dumps(payload, ensure_ascii=False, indent=1),
            output=SeedBatch,
            max_tokens=16000,
        )
        texts = {t.n: t for t in batch.items if t.floor <= t.floors_total}
        if set(texts) == {s["n"] for s in specs} and not any(
            shares_phrase(t.text, case["query"]) for t in texts.values()
        ):
            return texts
    raise RuntimeError(f"{case['id']}: не удалось получить тексты без повторов запроса")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--regen", nargs="*", default=[], help="id кейсов, чьи seed-объекты сгенерировать заново")
    args = ap.parse_args()
    cfg = load_config(args.config)
    settings = get_settings()
    cases = [json.loads(ln) for ln in CASES.open(encoding="utf-8") if ln.strip()]
    data = Path(cfg.data_path)
    seeded = {json.loads(ln)["source_id"].split("-")[1] for ln in data.open(encoding="utf-8") if '"seed-' in ln}

    targets = []
    with connect() as conn:
        for c in cases:
            if c["expected_filters"].get("needs_clarification"):
                continue
            where, params = where_from_filters(SearchFilters.model_validate(c["expected_filters"]))
            sql = f"SELECT count(*) FROM listings WHERE {where} AND source_id NOT LIKE 'seed-%%'"
            if conn.execute(sql, params).fetchone()[0] < MIN_EXISTING:
                targets.append(c)

    done = [c["id"] for c in targets if c["id"] in seeded and c["id"] not in args.regen]
    if done:
        print(f"уже подложены, пропускаю (заново: --regen): {', '.join(done)}")
    targets = [c for c in targets if c["id"] not in done]

    missing = [c["id"] for c in targets if "intent" not in c]
    if missing:
        print(f"нужен intent (пересказ смысла, можно пустой) в cases.jsonl: {', '.join(missing)}")
    ready = [c for c in targets if "intent" in c]
    print(f"к генерации: {', '.join(c['id'] for c in ready) or 'нет'}")

    for c in ready:
        specs = plan_case(c, cfg)
        for s in specs:  # страховка: роль и нарушения ровно такие, как задумано
            got = violations(SearchFilters.model_validate(c["expected_filters"]), s["fields"])
            assert got == ([s["violated"]] if s["violated"] else []), (c["id"], s["n"], got)
        texts = generate(c, specs, cfg, settings)
        rows, manifest = [], []
        for s in specs:
            sid = f"seed-{c['id']}-{s['n']:02d}"
            rows.append(to_raw(sid, s["fields"], texts[s["n"]], cfg.field_map))
            manifest.append({"source_id": sid, "case_id": c["id"], "role": s["role"], "violated": s["violated"]})
        data.write_text(
            "".join(replace_seed_lines(data.read_text(encoding="utf-8").splitlines(True), c["id"], rows)),
            encoding="utf-8",
        )
        old = MANIFEST.read_text(encoding="utf-8").splitlines(True) if MANIFEST.exists() else []
        MANIFEST.write_text("".join(replace_seed_lines(old, c["id"], manifest)), encoding="utf-8")
        print(
            f"{c['id']}: {sum(s['role'] == 'relevant' for s in specs)} подходящих, "
            f"{sum(s['role'] == 'decoy' for s in specs)} обманок"
        )


if __name__ == "__main__":
    main()
