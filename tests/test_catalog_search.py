import pytest

from app.schemas import CatalogFilters
from app.search import catalog
from app.search.catalog import complex_where, group_units, relaxed_filters, unit_where


def test_where_clauses_are_parametrized():
    f = CatalogFilters(
        city="Алматы",
        classes=["business"],
        completion_max_year=2027,
        payment=["installment"],
        rooms=[2, 3],
        price_max_kzt=60_000_000,
        not_first_floor=True,
        not_last_floor=True,
        views=["mountains"],
        orientations=["south", "east"],
    )
    cw, cp = complex_where(f)
    uw, up = unit_where(f)
    assert "(c.completion_year, c.completion_quarter) <= (%(cy)s, %(cq)s)" in cw and cp["cq"] == 4  # год без квартала
    assert "c.delivered OR" in cw  # сданный дом подходит под любой срок
    assert uw.startswith("u.status = 'free'") and "u.floor > 1" in uw and "u.floor < u.floors_total" in uw
    assert "u.orientation && %(orientations)s::text[]" in uw  # любая из сторон
    assert up["views"] == ["mountains"] and cp["payment"] == ["installment"]
    assert "Алматы" not in cw and "mountains" not in uw  # значения только в параметрах
    assert complex_where(CatalogFilters()) == ("true", {})


def test_search_relaxes_soft_filters_in_order_only_when_empty(monkeypatch):
    calls = []

    def fake(conn, f, qvec, qtext):
        calls.append(f)
        return ["cx-1"] if not f.views else []

    monkeypatch.setattr(catalog, "hybrid_search", fake)
    f = CatalogFilters(city="Астана", features=["near_park"], views=["mountains"], completion_max_year=2027)
    ids, relaxed = catalog.search(None, f, "[0]", "q")
    assert ids == ["cx-1"] and relaxed == ["features", "views"]  # payment и др. не заданы — пропущены
    assert calls[-1].completion_max_year == 2027 and calls[-1].city == "Астана"  # срок и город не тронуты
    assert relaxed_filters(f, relaxed) == calls[-1]


def test_search_never_relaxes_hard_filters(monkeypatch):
    monkeypatch.setattr(catalog, "hybrid_search", lambda *a: [])
    f = CatalogFilters(city="Астана", rooms=[5], price_max_kzt=1, classes=["business"], not_first_floor=True)
    assert catalog.search(None, f, "[0]", "q") == ([], [])  # честный ноль


def test_group_units_by_rooms():
    rows = [(1, "u1", 40.0, 20_000_000), (2, "u3", 60.5, 41_000_000), (2, "u2", 58.0, 47_000_000)]
    g1, g2 = group_units(rows)
    assert (g1.rooms, g1.count, g1.unit_ids) == (1, 1, ["u1"])
    assert (g2.count, g2.area_min, g2.area_max, g2.price_min, g2.price_max) == (2, 58.0, 60.5, 41_000_000, 47_000_000)


def _db():
    import psycopg

    from app.db import connect

    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("нет базы")
    if not conn.execute("SELECT to_regclass('complexes') IS NOT NULL AND EXISTS (SELECT 1 FROM complexes)").fetchone()[
        0
    ]:
        pytest.skip("каталог не проиндексирован: scripts/sync_catalog.py")
    return conn


def test_hits_respect_filters_on_real_data():
    f = CatalogFilters(city="Алматы", rooms=[2, 3], price_max_kzt=70_000_000, not_first_floor=True, views=["mountains"])
    with _db() as conn:
        qvec = conn.execute("SELECT embedding::text FROM complexes ORDER BY source_id LIMIT 1").fetchone()[0]
        ids, relaxed = catalog.search(conn, f, qvec, "тихий двор")
        assert ids and relaxed == [] and ids == catalog.search(conn, f, qvec, "тихий двор")[0]  # детерминировано
        hits = catalog.fetch_hits(conn, ids, f)
        unit_ids = [u for h in hits for g in h.groups for u in g.unit_ids]
        assert all(h.groups for h in hits)  # у каждого найденного ЖК есть подходящие квартиры
        rows = conn.execute(
            "SELECT c.city, u.rooms, u.price_kzt, u.floor, u.view, u.status FROM units u "
            "JOIN complexes c ON c.source_id = u.complex_id WHERE u.source_id = ANY(%s)",
            (unit_ids,),
        ).fetchall()
        assert len(rows) == len(unit_ids)
        for city, rooms, price, floor, view, status in rows:
            assert (city, view, status) == ("Алматы", "mountains", "free")
            assert rooms in (2, 3) and price <= 70_000_000 and floor > 1


def test_rerank_rejects_unknown_table():
    from app.config import RerankConfig
    from app.search.rerank import rerank_ids

    with pytest.raises(ValueError):
        rerank_ids(None, ["x"], "q", RerankConfig(provider="voyage", model="m"), "k", table="users; --")
