"""Синтетика новостроек: ЖК (тексты — Claude) и квартиры (код, цены — по калибровке).

    uv run python scripts/generate_catalog.py --config config/demo.yaml --complexes 70 --seed 6

Поля ЖК и все квартиры генерирует код с фиксированным seed — повторный запуск даёт то же самое.
Названия и описания ЖК, уже записанные в complexes_path, переиспользуются (Claude не вызывается);
квартиры каждый раз пересчитываются целиком. В конце — сводка распределений и цен за м².
"""

import argparse
import csv
import json
import random
import statistics
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from app.config import AppConfig, get_settings, load_config
from app.llm.client import call_structured, load_prompt
from app.schemas import Complex, Unit

BATCH = 10
METRO = {"Алмалинский": 0.6, "Ауэзовский": 0.5, "Бостандыкский": 0.5, "Медеуский": 0.3, "Жетысуский": 0.2}
BUSINESS_SHARE = {"Медеуский": 0.4, "Бостандыкский": 0.4, "Есильский": 0.4}  # где бизнес есть, но не здесь — 0.2
# горы — к югу от Алматы: насколько их видно из района (× доля высоты этажа, только окна на юг)
MOUNTAINS = {"Медеуский": 0.8, "Бостандыкский": 0.8, "Наурызбайский": 0.8, "Алмалинский": 0.45, "Ауэзовский": 0.45}
MOUNTAINS_LOW = 0.2
EMBANKMENT = {"Есильский": 0.5}  # доля ЖК на набережной Есиля; в других районах — нет
AREA = {1: (35, 45), 2: (52, 68), 3: (75, 95), 4: (100, 125)}
ROOMS = {"comfort": [30, 40, 25, 5], "business": [15, 35, 35, 15]}
FINISHING = {"comfort": {"rough": 25, "pre_finish": 50, "finished": 25}, "business": {"pre_finish": 40, "finished": 60}}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
SIDES = list(OPPOSITE)


def load_calibration(path: str) -> dict[tuple[str, str, str], tuple[int, int]]:
    with open(path, encoding="utf-8") as f:
        return {
            (r["city"], r["district"], r["class"]): (int(r["min_kzt_per_m2"]), int(r["max_kzt_per_m2"]))
            for r in csv.DictReader(f)
        }


def plan_complexes(n: int, cfg: AppConfig, calib: dict, rng: random.Random) -> list[dict]:
    """Поля ЖК без текстов. Сначала по одному ЖК на каждую строку калибровки (город × район × класс),
    остальные — случайно. embankment — внутренний признак генератора (для вида на реку)."""
    out = []
    fixed = sorted(calib)
    for i in range(1, n + 1):
        if i <= len(fixed):
            city, district, cls = fixed[i - 1]
        else:
            city = rng.choices(cfg.cities, weights=[6, 4][: len(cfg.cities)])[0]
            district = rng.choice(cfg.districts[city])
            has_business = (city, district, "business") in calib
            business = has_business and rng.random() < BUSINESS_SHARE.get(district, 0.2)
            cls = "business" if business else "comfort"
        business = cls == "business"
        delivered = rng.random() < 0.3
        year, quarter = (
            (rng.randint(2022, 2026), rng.randint(1, 4)) if delivered else (rng.randint(2027, 2029), rng.randint(1, 4))
        )
        if district == "Медеуский":
            floors = rng.randint(5, 12)
        else:
            floors = rng.randint(12, 25) if business else rng.randint(9, 16)
        feats = [
            k
            for k, p in {
                "near_metro": METRO.get(district, 0) if city == "Алматы" else 0,
                "near_school": 0.5,
                "kindergarten": 0.4 if business else 0.5,
                "near_park": 0.35,
                "closed_yard": 0.8 if business else 0.3,
                "parking": 0.95 if business else 0.4,
            }.items()
            if rng.random() < p
        ]
        payment = [
            k
            for k, p in {
                "mortgage": 0.95,
                "installment": 0.1 if delivered else 0.6,
                "state_program": 0 if business else 0.5,
            }.items()
            if rng.random() < p
        ]
        embankment = rng.random() < EMBANKMENT.get(district, 0)
        out.append(
            {
                "source_id": f"cx-{i:03d}",
                "city": city,
                "district": district,
                "housing_class": cls,
                "completion_year": year,
                "completion_quarter": quarter,
                "delivered": delivered,
                "features": feats,
                "payment": payment,
                "floors_total": floors,
                "embankment": embankment,
            }
        )
    return out


def choose_view(c: dict, floor: int, floors: int, windows: str, orientation: list[str], rng: random.Random) -> str:
    """Горы → река → парк → город → нет: первое сработавшее правило."""
    height = floor / floors
    if c["city"] == "Алматы" and "south" in orientation:
        if rng.random() < MOUNTAINS.get(c["district"], MOUNTAINS_LOW) * height:
            return "mountains"
    if c["city"] == "Астана" and floor >= 2:
        p = (0.6 if windows in ("street", "both") else 0) if c["embankment"] else 0.02
        if rng.random() < p:
            return "river"
    if "near_park" in c["features"] and rng.random() < 0.3:
        return "park"
    return "city" if height > 0.6 else "none"


def unit_price(area: float, rate_range: tuple[int, int], coef: float, max_dev: float, rng: random.Random) -> int:
    """Площадь × ставка из коридора × поправки; ставка не дальше max_dev от коридора, шаг 100 тыс ₸."""
    lo, hi = rate_range
    rate = rng.uniform(lo, hi) * coef
    lo_price, hi_price = lo * (1 - max_dev) * area, hi * (1 + max_dev) * area
    price = round(area * rate / 100_000) * 100_000
    return int(min(max(price, -(-lo_price // 100_000) * 100_000), hi_price // 100_000 * 100_000))


def make_units(c: dict, start: int, cfg: AppConfig, calib: dict, rng: random.Random) -> list[dict]:
    k = cfg.catalog.price_coefs
    floors = c["floors_total"]
    rate_range = calib[(c["city"], c["district"], c["housing_class"])]
    status_w = [55, 10, 35] if c["delivered"] else [75, 10, 15]
    out = []
    for j in range(rng.randint(30, 60)):
        rooms = rng.choices([1, 2, 3, 4], weights=ROOMS[c["housing_class"]])[0]
        a, b = AREA[rooms]
        area = round(rng.uniform(a, b) * (1.15 if c["housing_class"] == "business" else 1), 1)
        floor = rng.randint(1, floors)
        fin_w = FINISHING[c["housing_class"]]
        finishing = rng.choices(list(fin_w), weights=list(fin_w.values()))[0]
        windows = rng.choices(["yard", "street", "both"], weights=[40, 25, 35] if rooms >= 3 else [45, 40, 15])[0]
        first = rng.choice(SIDES)
        if windows == "both":  # сквозная — противоположные стороны, угловая — соседние
            second = (
                OPPOSITE[first]
                if rng.random() < 0.5
                else rng.choice([s for s in SIDES if s not in (first, OPPOSITE[first])])
            )
            orientation = [first, second]
        else:
            orientation = [first]
        view = choose_view(c, floor, floors, windows, orientation, rng)
        coef = 1.0
        if floor == 1:
            coef *= k["first_floor"]
        elif floor == floors:
            coef *= k["last_floor"]
        elif floor > floors * 0.8:
            coef *= k["top_fifth"]
        coef *= {"rough": k["rough"], "finished": k["finished"]}.get(finishing, 1.0)
        if c["delivered"]:
            coef *= k["delivered"]
        if view in ("mountains", "river"):
            coef *= k["view_premium"]
        out.append(
            {
                "source_id": f"u-{start + j:05d}",
                "complex_id": c["source_id"],
                "rooms": rooms,
                "area_m2": area,
                "floor": floor,
                "floors_total": floors,
                "price_kzt": unit_price(area, rate_range, coef, k["max_deviation"], rng),
                "finishing": finishing,
                "status": rng.choices(["free", "reserved", "sold"], weights=status_w)[0],
                "windows": windows,
                "view": view,
                "orientation": orientation,
            }
        )
    return out


class ComplexText(BaseModel):
    name: str
    developer: str
    description: str


class ComplexTexts(BaseModel):
    complexes: list[ComplexText]


def describe(batch: list[dict], taken: list[tuple[str, str]], cfg: AppConfig, settings) -> list[ComplexText]:
    cat = cfg.catalog
    lines = []
    for n, c in enumerate(batch, 1):
        status = "сдан" if c["delivered"] else f"сдача {c['completion_quarter']} кв. {c['completion_year']}"
        lines.append(
            f"{n}. {c['city']}, {c['district']} р-н, {cat.classes[c['housing_class']]}, {c['floors_total']} этажей, "
            f"{status}; инфраструктура: {', '.join(cat.complex_features[f] for f in c['features']) or 'нет'}; "
            f"покупка: {', '.join(cat.payment[p] for p in c['payment'])}; "
            f"на набережной: {'да' if c['embankment'] else 'нет'}"
        )
    used = dict(Counter(d for _, d in taken)) or "нет"
    user = (
        f"Названия уже заняты: {', '.join(n for n, _ in taken) or 'нет'}.\n"
        f"Застройщики уже использованы (не больше 3 ЖК на застройщика): {used}.\n\n"
        f"ЖК ({len(batch)}):\n" + "\n".join(lines)
    )
    out = call_structured(
        api_key=settings.anthropic_api_key,
        model=cfg.models["generator"],
        system=load_prompt("generate_complexes.md"),
        user=user,
        output=ComplexTexts,
        max_tokens=16000,
    )
    if len(out.complexes) != len(batch):
        raise RuntimeError(f"модель вернула {len(out.complexes)} ЖК вместо {len(batch)}")
    return out.complexes


def summary(complexes: list[dict], units: list[dict], calib: dict) -> None:
    by_id = {c["source_id"]: c for c in complexes}
    print(f"ЖК: {len(complexes)}, квартир: {len(units)}")
    print("классы:", dict(Counter((c["city"], c["housing_class"]) for c in complexes)))
    print("вид:", dict(Counter(u["view"] for u in units)), "статус:", dict(Counter(u["status"] for u in units)))
    print("\nМедиана цены за м² (тыс ₸) по районам и классам — рядом коридор калибровки:")
    groups: dict[tuple, list[float]] = {}
    for u in units:
        c = by_id[u["complex_id"]]
        groups.setdefault((c["city"], c["district"], c["housing_class"]), []).append(u["price_kzt"] / u["area_m2"])
    for key in sorted(calib):
        lo, hi = calib[key]
        rates = groups.get(key)
        med = f"{statistics.median(rates) / 1000:6.0f}" if rates else "     —"
        print(f"  {key[0]:7} {key[1]:14} {key[2]:9} {med}   [{lo // 1000}–{hi // 1000}]  квартир: {len(rates or [])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    ap.add_argument("--complexes", type=int, default=70)
    ap.add_argument("--seed", type=int, default=6)
    args = ap.parse_args()
    cfg, settings = load_config(args.config), get_settings()
    cat = cfg.catalog
    calib = load_calibration(cat.price_calibration_path)
    rng = random.Random(args.seed)
    plan = plan_complexes(args.complexes, cfg, calib, rng)

    cpath = Path(cat.complexes_path)
    known = {}
    if cpath.exists():
        known = {r["source_id"]: r for r in map(json.loads, cpath.read_text(encoding="utf-8").splitlines()) if r}
    complexes = []
    todo = [c for c in plan if c["source_id"] not in known]
    texts: dict[str, ComplexText] = {}
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        taken = [(r["name"], r["developer"]) for r in known.values()] + [(t.name, t.developer) for t in texts.values()]
        for c, t in zip(batch, describe(batch, taken, cfg, settings), strict=True):
            texts[c["source_id"]] = t
        print(f"тексты ЖК: {len(texts)}/{len(todo)}")
    for c in plan:
        t = texts.get(c["source_id"])
        words = t.model_dump() if t else {k: known[c["source_id"]][k] for k in ("name", "developer", "description")}
        row = {k: v for k, v in c.items() if k != "floors_total"} | words
        Complex.model_validate(row)
        complexes.append(row | {"floors_total": c["floors_total"]})
    names = [c["name"] for c in complexes]
    dup = [n for n, k in Counter(names).items() if k > 1]
    if dup:
        raise SystemExit(f"повторяющиеся названия ЖК: {dup} — удали их строки из {cpath} и запусти снова")
    busy = [d for d, k in Counter(c["developer"] for c in complexes).items() if k > 3]
    if busy:
        raise SystemExit(f"у застройщиков больше 3 ЖК: {busy} — удали их строки из {cpath} и запусти снова")

    units = []
    for c in complexes:
        units += make_units(c, len(units) + 1, cfg, calib, rng)
    for u in units:
        Unit.model_validate(u)

    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in complexes), encoding="utf-8")
    Path(cat.units_path).write_text("".join(json.dumps(u, ensure_ascii=False) + "\n" for u in units), encoding="utf-8")
    summary(complexes, units, calib)


if __name__ == "__main__":
    main()
