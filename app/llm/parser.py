from string import Template

import anthropic
from pydantic import ValidationError

from app.config import AppConfig, Settings
from app.llm.client import call_structured, load_prompt
from app.schemas import SearchFilters, build_parser_schema

PROMPT_FILE = "parse_query.md"


def render_prompt(cfg: AppConfig) -> str:
    return Template(load_prompt(PROMPT_FILE)).substitute(
        districts="\n".join(f"  {city}: {', '.join(ds)}" for city, ds in cfg.districts.items()),
        features="\n".join(f"  {key} — {label}" for key, label in cfg.features.items()),
    )


def parse_query(
    query: str, cfg: AppConfig, settings: Settings, client: anthropic.Anthropic | None = None
) -> SearchFilters:
    """Текст запроса → SearchFilters. Один повтор, если ответ не прошёл валидацию."""
    kwargs = dict(
        api_key=settings.anthropic_api_key,
        model=cfg.models["parser"],
        system=render_prompt(cfg),
        user=query,
        output=build_parser_schema(cfg),
        client=client,
    )
    try:
        return call_structured(**kwargs)
    except (ValidationError, RuntimeError):
        return call_structured(**kwargs)
