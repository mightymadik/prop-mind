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
