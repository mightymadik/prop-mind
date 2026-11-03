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


def test_parser_output_is_plain_search_filters():
    f = Parser(city="Алматы", features=["near_metro"], districts=["Бостандыкский"])
    assert isinstance(f, SearchFilters)
    assert Parser().features == [] and Parser().deal_type == "sale"
    with pytest.raises(ValidationError):
        Parser(city="Шымкент")
    assert SearchFilters(city="Шымкент").city == "Шымкент"  # код не ограничен конфигом


def test_parser_schema_accepted_by_sdk_transform():
    schema = transform_schema(Parser)
    assert schema["additionalProperties"] is False
