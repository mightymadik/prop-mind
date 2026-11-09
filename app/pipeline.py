"""Поиск от запроса до топа ЖК: разбор → гибрид по ЖК (с ослаблением) → реранк → группы квартир. Без ответа LLM."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import AppConfig, Settings
from app.llm.parser import parse_query
from app.schemas import SearchFilters
from app.search import catalog
from app.search.catalog import ComplexHit
from app.search.embeddings import embed, to_pgvector
from app.search.rerank import rerank_ids


@dataclass
class Retrieval:
    query: str
    filters: SearchFilters
    relaxed: list[str] = field(default_factory=list)
    hits: list[ComplexHit] = field(default_factory=list)  # топ ЖК в порядке выдачи с группами квартир
    clarification: str | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)


def retrieve(
    conn,
    query: str,
    cfg: AppConfig,
    settings: Settings,
    filters: SearchFilters | None = None,
    embed_fn: Callable[[str], str] | None = None,
    rerank_fn: Callable[[object, list[str], str], list[str]] | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_filters: Callable[[SearchFilters], None] | None = None,
) -> Retrieval:
    """Новостройки: разбор → поиск ЖК (с ослаблением) → реранк ЖК → топ ЖК с группами свободных квартир.
    filters, embed_fn, rerank_fn подменяются в evals: зафиксированный разбор и кэши."""
    timings: dict[str, int] = {}
    stage = on_stage or (lambda name: None)

    def lap(name: str, t: float) -> None:
        timings[name] = int((time.perf_counter() - t) * 1000)

    t = time.perf_counter()
    if filters is None:
        stage("parse")
        filters = parse_query(query, cfg, settings)
        lap("parse", t)
    if on_filters:
        on_filters(filters)
    if filters.needs_clarification:
        return Retrieval(query, filters, clarification=filters.needs_clarification, timings_ms=timings)

    text = catalog.query_text(filters, query)
    stage("search")
    t = time.perf_counter()
    if embed_fn:
        qvec = embed_fn(text)
    else:
        qvec = to_pgvector(embed([text], cfg.embeddings, settings.voyage_api_key, input_type="query")[0])
    lap("embed", t)
    t = time.perf_counter()
    ranked, relaxed = catalog.search(conn, filters, qvec, text, "hybrid")
    lap("search", t)
    t = time.perf_counter()
    if ranked:
        stage("rerank")
        if rerank_fn:
            ranked = rerank_fn(conn, ranked, text)
        else:
            ranked = rerank_ids(conn, ranked, text, cfg.rerank, settings.voyage_api_key)
    lap("rerank", t)
    top = ranked[: cfg.answer.top_k]
    hits = catalog.fetch_hits(conn, top, catalog.relaxed_filters(filters, relaxed))
    return Retrieval(query, filters, relaxed, hits, timings_ms=timings)
