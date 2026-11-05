import httpx

from app.config import RerankConfig
from app.search import embeddings, rerank

CFG = RerankConfig(provider="voyage", model="rerank-3", candidates=2)
REQ = httpx.Request("POST", rerank.VOYAGE_RERANK_URL)


class FakeConn:
    def execute(self, sql, params):
        ids = params[0]
        return type("R", (), {"fetchall": lambda self: [(i, f"текст {i}") for i in ids]})()


def test_rerank_orders_by_score_and_sends_documents(monkeypatch):
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(json)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]},
            request=REQ,
        )

    monkeypatch.setattr(embeddings.httpx, "post", post)
    assert rerank.rerank_ids(FakeConn(), ["a", "b", "c"], "тихо", CFG, "k") == ["b", "a", "c"]  # хвост не тронут
    assert sent == {"query": "тихо", "documents": ["текст a", "текст b"], "model": "rerank-3"}


def test_rerank_model_override_and_empty(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        embeddings.httpx,
        "post",
        lambda url, headers, json, timeout: (
            sent.update(json) or httpx.Response(200, json={"data": [{"index": 0, "relevance_score": 1}]}, request=REQ)
        ),
    )
    assert rerank.rerank_ids(FakeConn(), ["a"], "q", CFG, "k", model="rerank-2.5") == ["a"]
    assert sent["model"] == "rerank-2.5"
    assert rerank.rerank("q", [], "rerank-3", "k") == []
