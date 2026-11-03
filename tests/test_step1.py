import json
import os
from datetime import UTC, datetime

from app.config import load_config
from app.schemas import Listing
from app.search.text import build_search_text
from app.sources.synthetic import SyntheticJsonlSource

CFG = load_config("config/demo.yaml")
EPOCH = datetime.min.replace(tzinfo=UTC)


def test_demo_config_consistent():
    assert set(CFG.districts) == set(CFG.cities)
    assert CFG.embeddings.dim == 1024  # = vector(1024) в db/schema.sql


def test_synthetic_source_maps_fields_and_respects_since(tmp_path):
    p = tmp_path / "l.jsonl"
    raw = {
        "source_id": "syn-1",
        "city": "Алматы",
        "deal_type": "sale",
        "room_count": 2,
        "price": 40_000_000,
        "text": "Евродвушка",
        "features": ["near_metro"],
    }
    p.write_text(json.dumps(raw, ensure_ascii=False) + "\n\n", encoding="utf-8")
    src = SyntheticJsonlSource(p, CFG.field_map)

    [listing] = list(src.fetch_changed(EPOCH))
    assert (listing.rooms, listing.price_kzt, listing.description) == (2, 40_000_000, "Евродвушка")

    os.utime(p, (0, 0))
    assert list(src.fetch_changed(datetime(2000, 1, 1, tzinfo=UTC))) == []


def test_search_text_puts_fields_before_description():
    listing = Listing(
        source_id="x",
        city="Алматы",
        district="Бостандыкский",
        complex_name="Хан Тенгри",
        deal_type="sale",
        rooms=2,
        area_m2=64.0,
        condition="renovated",
        price_kzt=1,
        features=["near_metro"],
        description="Тихий двор.",
    )
    assert build_search_text(listing, CFG.features) == (
        "2-комнатная квартира, продажа, 64 м², ЖК Хан Тенгри, Бостандыкский район, Алматы, "
        "с ремонтом, рядом метро. Тихий двор."
    )
