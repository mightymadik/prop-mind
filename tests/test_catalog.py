import csv
import json
import re
from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from app.config import load_config
from app.schemas import Unit, View
from app.sources.synthetic import SyntheticCatalogSource

CFG = load_config("config/demo.yaml")
EPOCH = datetime.min.replace(tzinfo=UTC)


def test_price_calibration_covers_config():
    rows = list(csv.DictReader(open(CFG.catalog.price_calibration_path, encoding="utf-8")))
    keys = {(r["city"], r["district"], r["class"]) for r in rows}
    assert len(keys) == len(rows)  # без дублей
    for city, districts in CFG.districts.items():
        for d in districts:
            assert (city, d, "comfort") in keys, (city, d)  # комфорт есть во всех районах
    assert {r["class"] for r in rows} <= set(CFG.catalog.classes)
    assert all(0 < int(r["min_kzt_per_m2"]) < int(r["max_kzt_per_m2"]) for r in rows)


def test_view_vocabulary_matches_schema_and_config():
    views = set(get_args(View))
    assert views == set(CFG.catalog.views) and "river" in views
    for path in ("db/schema.sql", "db/migrations/003_catalog.sql"):
        check = re.search(r"CHECK \(view IN \(([^)]*)\)\)", open(path, encoding="utf-8").read()).group(1)
        assert set(re.findall(r"'(\w+)'", check)) == views, path


def test_catalog_source_reads_complexes_and_units(tmp_path):
    c = {
        "source_id": "cx-1",
        "city": "Алматы",
        "district": "Медеуский",
        "name": "ЖК Тестовый",
        "developer": "Тест Строй",
        "housing_class": "business",
        "completion_year": 2027,
        "completion_quarter": 2,
        "features": ["near_park"],
        "payment": ["mortgage"],
        "description": "Двор без машин.",
    }
    u = {
        "source_id": "u-1",
        "complex_id": "cx-1",
        "rooms": 2,
        "area_m2": 61.5,
        "floor": 9,
        "floors_total": 16,
        "price_kzt": 80_000_000,
        "finishing": "pre_finish",
        "windows": "yard",
        "view": "mountains",
        "orientation": ["south", "east"],
    }
    (tmp_path / "c.jsonl").write_text(json.dumps(c, ensure_ascii=False) + "\n", encoding="utf-8")
    (tmp_path / "u.jsonl").write_text(json.dumps(u, ensure_ascii=False) + "\n", encoding="utf-8")
    src = SyntheticCatalogSource(tmp_path / "c.jsonl", tmp_path / "u.jsonl", CFG.field_map)
    [cx] = src.fetch_complexes(EPOCH)
    [unit] = src.fetch_units(EPOCH)
    assert cx.housing_class == "business" and not cx.delivered
    assert unit.status == "free" and unit.orientation == ["south", "east"]
    assert Unit.model_validate(u | {"view": "river"}).view == "river"  # река / набережная
    with pytest.raises(ValidationError):
        Unit.model_validate(u | {"windows": "roof"})


def test_complex_search_text_puts_fields_before_description():
    from app.schemas import Complex
    from app.search.text import build_complex_text

    c = Complex(
        source_id="cx",
        city="Астана",
        district="Есильский",
        name="Тест",
        developer="Тест Строй",
        housing_class="comfort",
        completion_year=2027,
        completion_quarter=3,
        features=["near_park"],
        payment=["installment"],
        description="Двор без машин.",
    )
    assert build_complex_text(c, CFG.catalog) == (
        "ЖК Тест, новостройка, комфорт-класс, Есильский район, Астана, застройщик Тест Строй, "
        "сдача 3 квартал 2027 года, рядом парк, рассрочка от застройщика. Двор без машин."
    )
