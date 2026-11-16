import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(main, "limiter", main.RateLimiter(1000))
    monkeypatch.setattr(main, "budget_left", lambda conn, cfg: True)
    monkeypatch.setattr(main, "agent_budget_left", lambda conn, cfg: True)
    monkeypatch.setattr(main, "connect", lambda: __import__("contextlib").nullcontext(None))


def fake_agent(seen):
    def run(q, history, cfg, s, on_event=None):
        seen.append(history)
        if on_event:
            on_event("tool", {"label": "ищу варианты…"})
            on_event("done", {"latency_ms": 1})
        return {"query": q, "answer": "ok", "turn": None}

    return run


def test_agent_json_and_sse(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "run_agent_search", fake_agent(seen))
    history = [{"role": "user", "text": "двушка"}, {"role": "assistant", "text": "x", "source_ids": ["cx-1"]}]
    r = client.post("/agent", json={"query": "а подешевле?", "history": history})
    assert r.status_code == 200 and r.json()["answer"] == "ok"
    assert [t.role for t in seen[0]] == ["user", "assistant"] and seen[0][1].source_ids == ["cx-1"]
    r = client.post("/agent", json={"query": "двушка"}, headers={"accept": "text/event-stream"})
    assert r.text == 'event: tool\ndata: {"label": "ищу варианты…"}\n\nevent: done\ndata: {"latency_ms": 1}\n\n'


@pytest.mark.parametrize(
    "history",
    [
        [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]}],
        [{"role": "tool", "text": "x"}],
        [{"role": "user", "text": "x"}] * 21,
        [{"role": "assistant", "text": "x", "source_ids": [f"cx-{i}" for i in range(11)]}],
    ],
)
def test_agent_rejects_bad_history(monkeypatch, history):
    monkeypatch.setattr(main, "run_agent_search", lambda *a, **k: pytest.fail("agent called"))
    assert client.post("/agent", json={"query": "q", "history": history}).status_code == 422


def test_agent_disabled_unknown_key_and_budget(monkeypatch):
    monkeypatch.setattr(main, "run_agent_search", lambda *a, **k: pytest.fail("agent called"))
    assert client.post("/agent", json={"query": "q", "site_key": "nope"}).status_code == 403
    monkeypatch.setattr(main, "agent_budget_left", lambda conn, cfg: False)
    assert client.post("/agent", json={"query": "q"}).status_code == 429  # доля агента исчерпана
    off = main.cfg.model_copy(update={"agent": main.cfg.agent.model_copy(update={"enabled": False})})
    monkeypatch.setattr(main, "cfg", off)
    assert client.post("/agent", json={"query": "q"}).status_code == 404


def test_agent_failure_is_502_or_error_event(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(main, "run_agent_search", boom)
    assert client.post("/agent", json={"query": "q"}).status_code == 502
    r = client.post("/agent", json={"query": "q"}, headers={"accept": "text/event-stream"})
    assert "event: error" in r.text and "RuntimeError" in r.text


@pytest.mark.parametrize(("mode", "enabled", "expected"), [("v2", True, "v2"), ("v1", True, "v1"), ("v2", False, "v1")])
def test_chat_config_mode(monkeypatch, mode, enabled, expected):
    c = main.cfg.model_copy(
        update={
            "chat": main.cfg.chat.model_copy(update={"mode": mode}),
            "agent": main.cfg.agent.model_copy(update={"enabled": enabled}),
        }
    )
    monkeypatch.setattr(main, "cfg", c)
    assert client.get("/chat/config").json() == {"mode": expected}  # агент выключен — страница идёт в v1


def test_v1_search_does_not_check_agent_budget(monkeypatch):
    monkeypatch.setattr(main, "agent_budget_left", lambda conn, cfg: pytest.fail("agent budget checked"))
    monkeypatch.setattr(main, "run_search", lambda q, cfg, s, on_event=None: {"answer": "ok"})
    assert client.post("/search", json={"query": "q"}).status_code == 200
