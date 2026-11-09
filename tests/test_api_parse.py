import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas import SearchFilters

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    """Каждый тест — со своим лимитером и без похода в базу за бюджетом."""
    monkeypatch.setattr(main, "limiter", main.RateLimiter(1000))
    monkeypatch.setattr(main, "budget_left", lambda conn, cfg: True)
    monkeypatch.setattr(main, "connect", lambda: __import__("contextlib").nullcontext(None))


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


def test_complex_card(monkeypatch):
    import psycopg

    from app.db import connect

    monkeypatch.setattr(main, "connect", connect)  # карточке нужна настоящая база
    try:
        r = client.get("/complexes/cx-001")
    except psycopg.OperationalError:
        pytest.skip("нет базы")
    d = r.json()
    assert r.status_code == 200 and d["source_id"] == "cx-001" and "embedding" not in d and "search_text" not in d
    assert d["units"] and all(isinstance(u["area_m2"], float) for u in d["units"])
    assert client.get("/complexes/no-such-id").status_code == 404


def test_rate_limit_per_ip(monkeypatch):
    monkeypatch.setattr(main, "limiter", main.RateLimiter(2))
    monkeypatch.setattr(main, "run_search", lambda q, cfg, s, on_event=None: {"answer": "ok"})
    codes = [client.post("/search", json={"query": "двушка"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.post("/search", json={"query": "двушка"})
    assert int(r.headers["retry-after"]) >= 1 and "подождите" in r.json()["detail"]


def test_budget_exhausted_blocks_before_models(monkeypatch):
    monkeypatch.setattr(main, "budget_left", lambda conn, cfg: False)
    monkeypatch.setattr(main, "run_search", lambda *a, **k: pytest.fail("models called"))
    monkeypatch.setattr(main, "parse_query", lambda *a, **k: pytest.fail("models called"))
    for path in ("/search", "/parse"):
        r = client.post(path, json={"query": "двушка"})
        assert r.status_code == 429 and r.json()["detail"] == main.BUDGET_EXHAUSTED
    r = client.post("/search", json={"query": "двушка"}, headers={"accept": "text/event-stream"})
    assert r.status_code == 429  # отказ до начала потока


def test_unknown_site_key_and_db_down(monkeypatch):
    monkeypatch.setattr(main, "run_search", lambda q, cfg, s, on_event=None: {"answer": "ok"})
    assert client.post("/search", json={"query": "q", "site_key": "pub_demo"}).status_code == 200
    assert client.post("/search", json={"query": "q", "site_key": "pub_evil"}).status_code == 403

    def down():
        raise OSError("db down")

    monkeypatch.setattr(main, "connect", down)
    assert client.post("/search", json={"query": "q"}).status_code == 503


def test_forwarded_ip_ignored_without_trusted_proxy(monkeypatch):
    monkeypatch.setattr(main, "limiter", main.RateLimiter(1))
    monkeypatch.setattr(main, "run_search", lambda q, cfg, s, on_event=None: {"answer": "ok"})
    assert client.post("/search", json={"query": "q"}, headers={"x-forwarded-for": "1.1.1.1"}).status_code == 200
    # другой X-Forwarded-For не даёт обойти лимит: без доверенного прокси заголовок не учитывается
    assert client.post("/search", json={"query": "q"}, headers={"x-forwarded-for": "2.2.2.2"}).status_code == 429


@pytest.fixture
def chat_dist(tmp_path, monkeypatch):
    """Подставная сборка: тесты маршрутов и CSP не зависят от Node."""
    (tmp_path / "index.html").write_text(
        '<!doctype html><div id="root"></div><script type="module" src="/static/chat/assets/x.js"></script>',
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "CHAT_DIST", tmp_path)
    return tmp_path


def test_chat_page_csp_and_assets(chat_dist):
    r = client.get("/chat")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert client.get("/chat?site_key=pub_demo").status_code == 200
    assert client.get("/chat?site_key=pub_evil").status_code == 403
    for path in ("/static/widget.css", "/widget.js", "/demo-host"):
        assert client.get(path).status_code == 200, path
    assert "javascript" in client.get("/widget.js").headers["content-type"]


def test_chat_page_not_built_is_503(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CHAT_DIST", tmp_path / "missing")
    r = client.get("/chat")
    assert r.status_code == 503 and "npm run build" in r.json()["detail"]


def test_chat_page_has_no_inline_script(chat_dist):
    import re

    html = client.get("/chat").text
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)  # строгая CSP: только внешние скрипты


def test_real_build_has_no_inline_code_and_assets_are_served():
    """Настоящая сборка Vite (если собрана): ресурсы отдаются, инлайн-скриптов и стилей нет."""
    import re

    index = main.CHAT_DIST / "index.html"
    if not index.exists():
        pytest.skip("сборка не собрана: cd web/chat-app && npm run build")
    html = index.read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html) and "<style" not in html
    for src in re.findall(r'(?:src|href)="(/static/chat/[^"]+)"', html):
        assert client.get(src).status_code == 200, src
