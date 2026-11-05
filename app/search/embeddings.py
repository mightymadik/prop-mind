import time
from typing import Literal

import httpx

from app.config import EmbeddingsConfig
from app.costs import record

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
RETRIES = 5


def voyage_post(url: str, payload: dict, api_key: str) -> dict:
    """POST в Voyage с повтором на 429 (бесплатный тариф: 3 запроса и 10K токенов в минуту)."""
    # ponytail: повтор только на 429 с линейной паузой; общий rate limiter — если станет узко
    for attempt in range(1, RETRIES + 1):
        resp = httpx.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=60)
        if resp.status_code != 429 or attempt == RETRIES:
            break
        time.sleep(float(resp.headers.get("retry-after", 20 * attempt)))
    resp.raise_for_status()
    return resp.json()


def embed(
    texts: list[str],
    cfg: EmbeddingsConfig,
    api_key: str,
    input_type: Literal["document", "query"] = "document",
) -> list[list[float]]:
    if cfg.provider != "voyage":
        raise ValueError(f"unknown embeddings provider: {cfg.provider}")
    payload = {"input": texts, "model": cfg.model, "input_type": input_type, "output_dimension": cfg.dim}
    out = voyage_post(VOYAGE_URL, payload, api_key)
    record(cfg.model, out.get("usage", {}).get("total_tokens", 0))
    data = sorted(out["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def to_pgvector(vec: list[float]) -> str:
    """Текстовый литерал для `%s::vector` — без отдельной зависимости pgvector."""
    return "[" + ",".join(map(str, vec)) + "]"
