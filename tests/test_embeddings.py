import httpx
import pytest

from app.config import EmbeddingsConfig
from app.search import embeddings

CFG = EmbeddingsConfig(provider="voyage", model="m", dim=2)
REQ = httpx.Request("POST", embeddings.VOYAGE_URL)
OK = httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}, request=REQ)
LIMIT = httpx.Response(429, headers={"retry-after": "0"}, request=REQ)


def test_retries_on_429_then_returns_in_input_order(monkeypatch):
    replies = [LIMIT, LIMIT, OK]
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: replies.pop(0))
    assert embeddings.embed(["a", "b"], CFG, "k") == [[1, 0], [0, 1]]


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: LIMIT)
    with pytest.raises(httpx.HTTPStatusError):
        embeddings.embed(["a"], CFG, "k")
