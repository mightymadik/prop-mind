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
