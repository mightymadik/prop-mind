from app.config import RerankConfig
from app.costs import record
from app.search.embeddings import voyage_post

VOYAGE_RERANK_URL = "https://api.voyageai.com/v1/rerank"


def rerank(query: str, documents: list[str], model: str, api_key: str) -> list[int]:
    """Индексы documents по убыванию релевантности запросу (Voyage /v1/rerank)."""
    if not documents:
        return []
    out = voyage_post(VOYAGE_RERANK_URL, {"query": query, "documents": documents, "model": model}, api_key)
    record(model, out.get("usage", {}).get("total_tokens", 0))
    return [d["index"] for d in sorted(out["data"], key=lambda d: -d["relevance_score"])]


def rerank_ids(
    conn, ids: list[str], query: str, cfg: RerankConfig, api_key: str, model: str | None = None
) -> list[str]:
    """Переранжирует первые cfg.candidates source_id по search_text; хвост за ними не трогает."""
    if cfg.provider != "voyage":
        raise ValueError(f"unknown rerank provider: {cfg.provider}")
    head, tail = ids[: cfg.candidates], ids[cfg.candidates :]
    text = dict(
        conn.execute("SELECT source_id, search_text FROM listings WHERE source_id = ANY(%s)", (head,)).fetchall()
    )
    order = rerank(query, [text[i] for i in head], model or cfg.model, api_key)
    return [head[i] for i in order] + tail
