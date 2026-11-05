from app import pipeline
from app.config import Settings, load_config
from app.schemas import SearchFilters

CFG = load_config("config/demo.yaml")


def test_clarification_skips_search(monkeypatch):
    monkeypatch.setattr(pipeline, "search", lambda *a: (_ for _ in ()).throw(AssertionError("search called")))
    f = SearchFilters(city="Астана", needs_clarification="Метро есть только в Алматы. Искать там?")
    r = pipeline.retrieve(None, "у метро в Астане", CFG, Settings(), filters=f)
    assert r.clarification == f.needs_clarification and r.rows == []


def test_retrieve_reranks_and_cuts_to_top_k(monkeypatch):
    ids = [f"syn-{i:05d}" for i in range(30)]
    seen = {}
    monkeypatch.setattr(pipeline, "embed", lambda texts, cfg, key, input_type: [[0.0, 1.0]])
    monkeypatch.setattr(pipeline, "search", lambda conn, f, v, text, mode: (ids, ["features"]))

    def fake_rerank(conn, got, text, cfg, key):
        seen["text"], seen["n"] = text, len(got)
        return list(reversed(got))

    monkeypatch.setattr(pipeline, "rerank_ids", fake_rerank)
    monkeypatch.setattr(pipeline, "fetch_rows", lambda conn, top: [(sid,) for sid in top])
    f = SearchFilters(city="Алматы", semantic_query="тихий двор")
    r = pipeline.retrieve(None, "тихая квартира", CFG, Settings(), filters=f)
    assert seen == {"text": "тихий двор", "n": 30}  # реранк по смысловой части
    assert [x[0] for x in r.rows] == list(reversed(ids))[: CFG.answer.top_k]
    assert r.relaxed == ["features"] and {"embed", "search", "rerank"} <= set(r.timings_ms)
