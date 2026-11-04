from app.schemas import SearchFilters
from app.search.hybrid import where_from_filters


def test_where_from_filters_parametrized():
    sql, params = where_from_filters(
        SearchFilters(city="Алматы", rooms=[2, 3], price_max_kzt=40_000_000, features=["near_metro"])
    )
    assert sql == (
        "deal_type = %(deal_type)s AND city = %(city)s AND rooms = ANY(%(rooms)s)"
        " AND price_kzt <= %(price_max)s AND features @> %(features)s::text[]"
    )
    assert params == {
        "deal_type": "sale",
        "city": "Алматы",
        "rooms": [2, 3],
        "price_max": 40_000_000,
        "features": ["near_metro"],
    }
    assert "Алматы" not in sql  # значения только в параметрах


def test_query_text_prefers_semantic_part():
    from app.search.hybrid import query_text

    assert query_text(SearchFilters(semantic_query=" вид на горы "), "тихая с видом на горы") == "вид на горы"
    assert query_text(SearchFilters(), "двушка в Астане") == "двушка в Астане"


def _db():
    import psycopg
    import pytest

    from app.db import connect

    try:
        return connect()
    except psycopg.OperationalError:
        pytest.skip("нет базы")


def test_hybrid_respects_filters_and_fuses_both_rankings():
    from app.search.hybrid import hybrid_search, vector_search

    with _db() as conn:
        f = SearchFilters(city="Алматы", deal_type="rent", rooms=[2])
        where, params = where_from_filters(f)
        allowed = {r[0] for r in conn.execute(f"SELECT source_id FROM listings WHERE {where}", params)}
        sid, qvec = conn.execute(
            f"SELECT source_id, embedding::text FROM listings WHERE {where} ORDER BY source_id LIMIT 1", params
        ).fetchone()
        hyb = hybrid_search(conn, f, qvec, "кот животные")
        assert hyb and set(hyb) <= allowed
        assert hyb == hybrid_search(conn, f, qvec, "кот животные")  # детерминировано
        assert sid == vector_search(conn, f, qvec)[0]  # вектор объекта — первый у векторного поиска
        assert sid in hyb[:2]  # и в гибриде остаётся наверху
        empty = hybrid_search(conn, f, qvec, "")  # пустой текст — только векторная часть
        assert empty[:10] == vector_search(conn, f, qvec)[:10]


def test_search_relaxes_soft_filters_in_order_only_when_empty(monkeypatch):
    from app.search import hybrid

    seen = []

    def fake(conn, f, qvec, qtext=None):
        seen.append((bool(f.features), bool(f.conditions), bool(f.districts)))
        return [] if f.conditions else ["x"]  # пусто, пока задано состояние

    monkeypatch.setattr(hybrid, "hybrid_search", fake)
    f = SearchFilters(
        city="Алматы", rooms=[5], districts=["Турксибский"], conditions=["renovated"], features=["parking"]
    )
    assert hybrid.search(None, f, "v", "t") == (["x"], ["features", "conditions"])
    assert seen == [(True, True, True), (False, True, True), (False, False, True)]  # район не тронут

    seen.clear()
    no_features = f.model_copy(update={"features": []})
    assert hybrid.search(None, no_features, "v", "t") == (["x"], ["conditions"])  # незаданный шаг пропущен

    monkeypatch.setattr(hybrid, "hybrid_search", lambda *a: ["y"])
    assert hybrid.search(None, f, "v", "t") == (["y"], [])  # нашлось строго — ничего не ослаблено


def test_search_never_relaxes_hard_filters(monkeypatch):
    from app.search import hybrid

    seen = []
    monkeypatch.setattr(hybrid, "vector_search", lambda conn, f, qvec: seen.append(f) or [])
    f = SearchFilters(
        city="Астана", deal_type="rent", rooms=[4], price_max_kzt=1, area_min_m2=200, features=["furnished"]
    )
    assert hybrid.search(None, f, "v", "t", mode="vector") == ([], ["features"])  # честный ноль
    assert all(
        (x.city, x.deal_type, x.rooms, x.price_max_kzt, x.area_min_m2) == ("Астана", "rent", [4], 1, 200) for x in seen
    )


def test_search_relaxes_on_real_data_astana_metro():
    from app.search.hybrid import search

    with _db() as conn:
        f = SearchFilters(city="Астана", rooms=[1], features=["near_metro"])
        qvec = conn.execute("SELECT embedding::text FROM listings WHERE city = 'Астана' LIMIT 1").fetchone()[0]
        ranked, relaxed = search(conn, f, qvec, "однушка у метро")
        assert relaxed == ["features"] and ranked
        where, params = where_from_filters(f.model_copy(update={"features": []}))
        allowed = {r[0] for r in conn.execute(f"SELECT source_id FROM listings WHERE {where}", params)}
        assert set(ranked) <= allowed
