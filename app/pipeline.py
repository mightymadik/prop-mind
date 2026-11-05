"""Поиск от текста запроса до топа объектов: разбор → гибрид (с ослаблением) → реранк. Без ответа LLM."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import AppConfig, Settings
from app.llm.parser import parse_query
from app.schemas import SearchFilters
from app.search.embeddings import embed, to_pgvector
from app.search.hybrid import query_text, search
from app.search.rerank import rerank_ids

ROW_SQL = """SELECT source_id, price_kzt, rooms, area_m2, district, condition, features, description
             FROM listings WHERE source_id = ANY(%s)"""


@dataclass
class Retrieval:
    query: str
    filters: SearchFilters
    relaxed: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)  # топ объектов в порядке выдачи, формат ROW_SQL
    clarification: str | None = None  # парсер переспросил — поиска не было
    timings_ms: dict[str, int] = field(default_factory=dict)


def fetch_rows(conn, ids: list[str]) -> list[tuple]:
    by_id = {r[0]: r for r in conn.execute(ROW_SQL, (ids,)).fetchall()}
    return [by_id[i] for i in ids]


def retrieve(
    conn,
    query: str,
    cfg: AppConfig,
    settings: Settings,
    filters: SearchFilters | None = None,
    embed_fn: Callable[[str], str] | None = None,
    rerank_fn: Callable[[object, list[str], str], list[str]] | None = None,
) -> Retrieval:
    """filters, embed_fn (текст → pgvector) и rerank_fn можно подменить — evals с зафиксированным разбором
    и кэшами; по умолчанию — parse_query и живые вызовы Voyage, как в продукте."""
    timings: dict[str, int] = {}

    def lap(name: str, t: float) -> None:
        timings[name] = int((time.perf_counter() - t) * 1000)

    t = time.perf_counter()
    if filters is None:
        filters = parse_query(query, cfg, settings)
        lap("parse", t)
    if filters.needs_clarification:
        return Retrieval(query, filters, clarification=filters.needs_clarification, timings_ms=timings)

    text = query_text(filters, query)
    t = time.perf_counter()
    if embed_fn:
        qvec = embed_fn(text)
    else:
        qvec = to_pgvector(embed([text], cfg.embeddings, settings.voyage_api_key, input_type="query")[0])
    lap("embed", t)
    t = time.perf_counter()
    ranked, relaxed = search(conn, filters, qvec, text, "hybrid")
    lap("search", t)
    t = time.perf_counter()
    if ranked:
        if rerank_fn:
            ranked = rerank_fn(conn, ranked, text)
        else:
            ranked = rerank_ids(conn, ranked, text, cfg.rerank, settings.voyage_api_key)
    lap("rerank", t)
    top = ranked[: cfg.answer.top_k]
    return Retrieval(query, filters, relaxed, fetch_rows(conn, top), timings_ms=timings)
