from datetime import date
from string import Template

import anthropic
from pydantic import ValidationError

from app.config import AppConfig, Settings
from app.llm.client import call_structured, load_prompt
from app.schemas import CatalogFilters, SearchFilters, build_catalog_parser_schema, build_parser_schema

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


CATALOG_PROMPT_FILE = "parse_catalog.md"
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()


def render_catalog_prompt(cfg: AppConfig, today: date | None = None) -> str:
    cat, today = cfg.catalog, today or date.today()

    def inline(d: dict[str, str]) -> str:
        return ", ".join(f"{k} — {v}" for k, v in d.items())

    def lines(d: dict[str, str]) -> str:
        return "\n".join(f"  {k} — {v}" for k, v in d.items())

    return Template(load_prompt(CATALOG_PROMPT_FILE)).substitute(
        today=f"{today.day} {MONTHS[today.month - 1]} {today.year} года",
        districts="\n".join(f"  {city}: {', '.join(ds)}" for city, ds in cfg.districts.items()),
        classes=inline(cat.classes),
        features=lines(cat.complex_features),
        payment=lines(cat.payment),
        finishing=inline(cat.finishing),
        windows=inline(cat.windows),
        views=inline(cat.views),
        orientations=inline(cat.orientations),
    )


def parse_catalog_query(
    query: str, cfg: AppConfig, settings: Settings, client: anthropic.Anthropic | None = None
) -> CatalogFilters:
    """Текст запроса → CatalogFilters (новостройки). Один повтор, если ответ не прошёл валидацию."""
    kwargs = dict(
        api_key=settings.anthropic_api_key,
        model=cfg.models["parser"],
        system=render_catalog_prompt(cfg),
        user=query,
        output=build_catalog_parser_schema(cfg),
        client=client,
    )
    try:
        return call_structured(**kwargs)
    except (ValidationError, RuntimeError):
        return call_structured(**kwargs)
