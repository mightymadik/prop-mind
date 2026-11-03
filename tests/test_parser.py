import pytest

from app.config import Settings, load_config
from app.llm import parser
from app.schemas import SearchFilters

CFG = load_config("config/demo.yaml")
SETTINGS = Settings(anthropic_api_key="k")


def test_prompt_rendered_from_config():
    text = parser.render_prompt(CFG)
    assert "$" not in text
    assert "Астана: Есильский" in text
    assert "near_metro — рядом метро" in text


def test_parse_query_passes_config_and_retries_once(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise RuntimeError("no structured output")
        return SearchFilters(city="Алматы")

    monkeypatch.setattr(parser, "call_structured", fake)
    f = parser.parse_query("двушка в Алматы", CFG, SETTINGS)
    assert isinstance(f, SearchFilters) and f.city == "Алматы"
    assert len(calls) == 2
    assert calls[0]["model"] == CFG.models["parser"] and calls[0]["user"] == "двушка в Алматы"


def test_parse_query_gives_up_after_retry(monkeypatch):
    def fake(**kw):
        raise RuntimeError("no structured output")

    monkeypatch.setattr(parser, "call_structured", fake)
    with pytest.raises(RuntimeError):
        parser.parse_query("x", CFG, SETTINGS)
