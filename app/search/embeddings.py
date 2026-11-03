from typing import Literal

import httpx

from app.config import EmbeddingsConfig

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


def embed(
    texts: list[str],
    cfg: EmbeddingsConfig,
    api_key: str,
    input_type: Literal["document", "query"] = "document",
) -> list[list[float]]:
    if cfg.provider != "voyage":
        raise ValueError(f"unknown embeddings provider: {cfg.provider}")
    resp = httpx.post(
        VOYAGE_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"input": texts, "model": cfg.model, "input_type": input_type, "output_dimension": cfg.dim},
        timeout=60,
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def to_pgvector(vec: list[float]) -> str:
    """Текстовый литерал для `%s::vector` — без отдельной зависимости pgvector."""
    return "[" + ",".join(map(str, vec)) + "]"
