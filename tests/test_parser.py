import json

import pytest

from app.config import Settings, load_config
from app.llm import parser
from app.schemas import CatalogFilters

CFG = load_config("config/demo.yaml")
SETTINGS = Settings(anthropic_api_key="k")


def test_parse_query_passes_config_and_retries_once(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("no structured output")
        return CatalogFilters(city="Алматы")

    monkeypatch.setattr(parser, "call_structured", fake)
    f = parser.parse_catalog_query("двушка в Алматы", CFG, SETTINGS)
    assert isinstance(f, CatalogFilters) and f.city == "Алматы"
    assert len(calls) == 2
    assert calls[0]["model"] == CFG.models["parser"] and calls[0]["user"] == "двушка в Алматы"


def test_parse_query_gives_up_after_retry(monkeypatch):
    def fake(**kw):
        raise RuntimeError("no structured output")

    monkeypatch.setattr(parser, "call_structured", fake)
    with pytest.raises(RuntimeError):
        parser.parse_catalog_query("x", CFG, SETTINGS)


def test_catalog_prompt_and_schema():
    from datetime import date

    from app.schemas import build_catalog_parser_schema

    text = parser.render_catalog_prompt(CFG, today=date(2026, 11, 9))
    assert "$" not in text and "Сегодня 9 ноября 2026 года" in text
    assert "mountains — вид на горы" in text and "installment — рассрочка от застройщика" in text
    schema = json.dumps(build_catalog_parser_schema(CFG).model_json_schema())
    assert schema.count("anyOf") <= 16  # лимит API на nullable/union-поля в structured outputs
