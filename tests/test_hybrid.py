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
