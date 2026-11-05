from fastapi.testclient import TestClient

from app import main
from app.schemas import SearchFilters

client = TestClient(main.app)


def test_parse_returns_filters(monkeypatch):
    monkeypatch.setattr(main, "parse_query", lambda q, cfg, s: SearchFilters(city="Астана", rooms=[1]))
    r = client.post("/parse", json={"query": "однушка в Астане"})
    assert r.status_code == 200
    assert r.json()["city"] == "Астана" and r.json()["rooms"] == [1]


def test_parse_validates_input():
    assert client.post("/parse", json={"query": ""}).status_code == 422
    assert client.post("/parse", json={"query": "x" * 501}).status_code == 422


def test_parse_llm_failure_is_502(monkeypatch):
    def boom(*a):
        raise RuntimeError("no structured output")

    monkeypatch.setattr(main, "parse_query", boom)
    r = client.post("/parse", json={"query": "двушка"})
    assert r.status_code == 502 and "RuntimeError" in r.json()["detail"]


def test_search_json_and_sse(monkeypatch):
    def fake_run(q, cfg, s, on_event=None):
        if on_event:
            on_event("results", {"results": []})
            on_event("done", {"latency_ms": 1})
        return {"query": q, "answer": "ok"}

    monkeypatch.setattr(main, "run_search", fake_run)
    assert client.post("/search", json={"query": "двушка"}).json()["answer"] == "ok"
    r = client.post("/search", json={"query": "двушка"}, headers={"accept": "text/event-stream"})
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text == 'event: results\ndata: {"results": []}\n\nevent: done\ndata: {"latency_ms": 1}\n\n'


def test_search_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(main, "run_search", boom)
    assert client.post("/search", json={"query": "двушка"}).status_code == 502
    r = client.post("/search", json={"query": "двушка"}, headers={"accept": "text/event-stream"})
    assert "event: error" in r.text and "RuntimeError" in r.text
    assert client.post("/search", json={"query": ""}).status_code == 422


def test_listing_card():
    import psycopg
    import pytest

    try:
        r = client.get("/listings/syn-00001")
    except psycopg.OperationalError:
        pytest.skip("нет базы")
    assert r.status_code == 200 and r.json()["source_id"] == "syn-00001" and "embedding" not in r.json()
    assert client.get("/listings/no-such-id").status_code == 404
