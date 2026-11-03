import pytest
from anthropic.lib._parse._transform import transform_schema
from pydantic import ValidationError

from app.config import load_config
from app.schemas import SearchFilters, build_parser_schema

CFG = load_config("config/demo.yaml")
Parser = build_parser_schema(CFG)


def test_parser_schema_takes_literals_from_config():
    props = Parser.model_json_schema()["properties"]
    assert props["city"]["anyOf"][0]["enum"] == CFG.cities
    assert props["features"]["items"]["enum"] == list(CFG.features)
    assert "Есильский" in props["districts"]["anyOf"][0]["items"]["enum"]
    assert props["city"]["description"] == SearchFilters.model_fields["city"].description


EMPTY = dict(
    city=None,
    deal_type="sale",
    rooms=None,
    price_min_kzt=None,
    price_max_kzt=None,
    area_min_m2=None,
    districts=None,
    conditions=None,
    features=[],
    semantic_query="",
    needs_clarification=None,
)


def test_parser_output_is_plain_search_filters():
    f = Parser(**{**EMPTY, "city": "Алматы", "features": ["near_metro"], "districts": ["Бостандыкский"]})
    assert isinstance(f, SearchFilters)
    assert Parser(**EMPTY).model_dump() == SearchFilters().model_dump()  # «всё пусто» от LLM == фильтров нет
    with pytest.raises(ValidationError):
        Parser(**{**EMPTY, "city": "Шымкент"})
    assert SearchFilters(city="Шымкент").city == "Шымкент"  # код не ограничен конфигом


def test_parser_schema_accepted_by_sdk_transform():
    schema = transform_schema(Parser)
    assert schema["additionalProperties"] is False


def test_parser_schema_requires_every_field():
    assert set(transform_schema(Parser)["required"]) == set(SearchFilters.model_fields)
    with pytest.raises(ValidationError):
        Parser(**{k: v for k, v in EMPTY.items() if k != "city"})  # пропущенный ключ — ошибка
