import pytest

from app import service
from app.config import Settings, load_config
from app.costs import record
from app.llm.answer import AnswerResult
from app.pipeline import Retrieval
from app.schemas import SearchFilters

CFG = load_config("config/demo.yaml")
ROW = ("syn-00001", 230_000, 2, 50.0, "Ауэзовский", "renovated", ["furnished"], "Соседи спокойные.")


@pytest.fixture
def logged(monkeypatch):
    rows = []
    monkeypatch.setattr(service, "log", lambda payload, r, answer: rows.append((payload, r, answer)))
    monkeypatch.setattr(service, "connect", lambda: __import__("contextlib").nullcontext(None))
    return rows


def test_events_order_and_payload(monkeypatch, logged):
    f = SearchFilters(city="Алматы", semantic_query="спокойные соседи")

    def fake_retrieve(conn, q, cfg, s, on_stage=None, on_filters=None):
        on_stage("parse")
        on_filters(f)
        record("rerank-2.5", 6000)
        return Retrieval(q, f, ["features"], [ROW], timings_ms={"search": 2})

    def fake_answer(q, filters, relaxed, rows, cfg, key, on_text=None):
        on_text("Подходит [1]")
        record("claude-sonnet-5-5", 1000, 100)
        return AnswerResult(
            model="claude-sonnet-5-5",
            text="Подходит [syn-00001]",
            raw_text="Подходит [1]",
            cited=["syn-00001"],
            served_model="claude-sonnet-5-5",
            ttft_ms=900,
            total_ms=3000,
        )

    monkeypatch.setattr(service, "retrieve", fake_retrieve)
    monkeypatch.setattr(service, "generate_answer", fake_answer)
    events = []
    out = service.run_search("q", CFG, Settings(), on_event=lambda n, d: events.append(n))
    assert events == ["status", "filters", "results", "status", "delta", "answer", "done"]
    assert out["results"][0]["source_id"] == "syn-00001" and out["relaxed"] == ["features"]
    assert out["answer"] == "Подходит [syn-00001]"
    assert out["timings_ms"] == {"search": 2, "ttft": 900, "answer": 3000}
    assert out["cost_usd"] == pytest.approx((6000 * 0.05 + 1000 * 2.0 + 100 * 10.0) / 1e6)
    assert len(logged) == 1


def test_clarification_skips_answer(monkeypatch, logged):
    f = SearchFilters(city="Астана", needs_clarification="Метро есть только в Алматы.")
    monkeypatch.setattr(
        service,
        "retrieve",
        lambda conn, q, cfg, s, on_stage=None, on_filters=None: (
            on_filters(f) or Retrieval(q, f, clarification=f.needs_clarification)
        ),
    )
    monkeypatch.setattr(service, "generate_answer", lambda *a, **k: pytest.fail("answer called"))
    events = []
    out = service.run_search("у метро в Астане", CFG, Settings(), on_event=lambda n, d: events.append(n))
    assert events == ["filters", "clarification", "done"] and out["clarification"]
    assert "answer" not in out and len(logged) == 1


def test_error_is_logged_and_reraised(monkeypatch, logged):
    def boom(*a, **k):
        raise RuntimeError("voyage down")

    monkeypatch.setattr(service, "retrieve", boom)
    with pytest.raises(RuntimeError):
        service.run_search("q", CFG, Settings())
    payload, r, answer = logged[0]
    assert payload["error"] == "RuntimeError: voyage down" and r is None and answer is None


def test_log_failure_does_not_break_search(monkeypatch):
    monkeypatch.setattr(service, "connect", lambda: __import__("contextlib").nullcontext(None))
    f = SearchFilters(needs_clarification="?")
    monkeypatch.setattr(
        service, "retrieve", lambda conn, q, cfg, s, on_stage=None, on_filters=None: Retrieval(q, f, clarification="?")
    )
    monkeypatch.setattr(service, "log", lambda *a: (_ for _ in ()).throw(OSError("db down")))
    assert service.run_search("q", CFG, Settings())["clarification"] == "?"


def test_log_row_masks_pii_and_records_fallback(monkeypatch):
    sent = {}

    class Conn:
        def execute(self, sql, params):
            sent.update(params)

    monkeypatch.setattr(service, "connect", lambda: __import__("contextlib").nullcontext(Conn()))
    payload = {
        "query": "двушка, звоните +7 701 123 45 67",
        "filters": {"city": "Алматы"},
        "relaxed": [],
        "latency_ms": 10,
        "timings_ms": {"search": 1},
        "cost_usd": 0.01,
        "error": None,
    }
    r = Retrieval("q", SearchFilters(), [], [ROW])
    a = AnswerResult(
        model="claude-sonnet-5-5", text="", raw_text="", invalid_refs=[7], served_model="claude-opus-4-8", fallback=True
    )
    service.log(payload, r, a)
    assert sent["query"] == "двушка, звоните [телефон]"
    assert sent["result_source_ids"] == ["syn-00001"] and sent["invalid_refs"] == 1
    assert sent["answer_model"] == "claude-opus-4-8" and sent["fallback"] is True


def test_filter_chips_and_card_labels():
    f = SearchFilters(
        city="Алматы",
        deal_type="rent",
        rooms=[2, 3],
        price_max_kzt=300_000,
        districts=["Медеуский"],
        conditions=["renovated"],
        features=["furnished"],
        semantic_query="тихий двор",
    )
    assert service.filter_chips(f, CFG) == [
        "Алматы",
        "аренда",
        "2/3 комн.",
        "до 300 тыс",
        "Медеуский р-н",
        "с ремонтом",
        "с мебелью",
        "«тихий двор»",
    ]
    assert service.filter_chips(SearchFilters(price_min_kzt=20_000_000, price_max_kzt=25_500_000), CFG)[1:] == [
        "20 млн – 25.5 млн"
    ]
    c = service.card(ROW, CFG)
    assert c["condition_label"] == "с ремонтом" and c["feature_labels"] == ["с мебелью"]
