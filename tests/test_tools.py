import pytest

from app.config import load_config
from app.tools import ToolError, mortgage_payment, price_stats

cfg = load_config("config/demo.yaml")


def with_program(**over):
    c = cfg.model_copy(deep=True)
    c.mortgage["mortgage"] = c.mortgage["mortgage"].model_copy(update=over)
    return c


def test_annuity_matches_reference_value():
    # 10 млн на 10 лет под 12% годовых — справочный платёж 143 470,95 ₸
    c = with_program(rate_pct=12.0, min_down_pct=0, max_loan_kzt=None)
    r = mortgage_payment("mortgage", price=10_000_000, down_payment=0, years=10, cfg=c)
    assert r.monthly_kzt == 143_471 and r.loan_kzt == 10_000_000
    assert r.total_kzt == 17_216_514 and r.overpayment_kzt == 7_216_514
    assert (r.as_of, r.source) == (c.mortgage["mortgage"].as_of, "пример, не оферта")


def test_installment_with_zero_rate_is_equal_parts():
    r = mortgage_payment("installment", price=30_000_000, down_payment=9_000_000, years=2, cfg=cfg)
    assert r.rate_pct == 0 and r.monthly_kzt == 875_000
    assert r.total_kzt == 21_000_000 and r.overpayment_kzt == 0


def test_rate_and_limits_come_from_config_not_caller():
    r = mortgage_payment("state_program", price=40_000_000, down_payment=8_000_000, years=25, cfg=cfg)
    assert r.rate_pct == cfg.mortgage["state_program"].rate_pct
    with pytest.raises(TypeError):
        mortgage_payment("state_program", price=40_000_000, down_payment=8_000_000, years=25, cfg=cfg, rate=1.0)


@pytest.mark.parametrize(
    ("program", "price", "down", "years", "msg"),
    [
        ("crypto", 30_000_000, 9_000_000, 10, "неизвестная программа"),
        ("mortgage", 0, 0, 10, "больше нуля"),
        ("mortgage", 30_000_000, 30_000_000, 10, "меньше цены"),
        ("mortgage", 30_000_000, 5_000_000, 10, "не меньше 20%"),
        ("mortgage", 30_000_000, 6_000_000, 26, "от 1 до 25"),
        ("installment", 30_000_000, 9_000_000, 3, "от 1 до 2"),
        ("state_program", 60_000_000, 12_000_000, 20, "не больше 36000000"),
    ],
)
def test_invalid_input_is_rejected(program, price, down, years, msg):
    with pytest.raises(ToolError, match=msg):
        mortgage_payment(program, price, down, years, cfg)


class FakeConn:
    def __init__(self, row):
        self.row, self.calls = row, []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self.row


def test_price_stats_hides_medians_below_threshold():
    conn = FakeConn((4, 50e6, 1e6, 0.9e6, 1.1e6))
    s = price_stats(conn, cfg, "Алматы", district="Медеуский", rooms=2, housing_class="business")
    assert s.count == 4 and not s.enough_data and s.median_kzt_per_m2 is None
    sql, params = conn.calls[0]
    assert "Медеуский" not in sql and params["districts"] == ["Медеуский"] and params["rooms"] == [2]
    assert "u.status = 'free'" in sql

    s = price_stats(FakeConn((5, 50.4e6, 1.0004e6, 0.9e6, 1.1e6)), cfg, "Алматы")
    assert s.enough_data and (s.median_price_kzt, s.median_kzt_per_m2) == (50_400_000, 1_000_400)


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"city": "Шымкент"}, "город"),
        ({"city": "Астана", "district": "Медеуский"}, "нет района"),
        ({"city": "Алматы", "housing_class": "elite"}, "класс"),
    ],
)
def test_price_stats_validates_input(kw, msg):
    with pytest.raises(ToolError, match=msg):
        price_stats(FakeConn(None), cfg, **kw)


def test_price_stats_on_real_data():
    from tests.test_catalog_search import _db

    with _db() as conn:
        s = price_stats(conn, cfg, "Алматы")
        assert s.enough_data and s.p25_kzt_per_m2 <= s.median_kzt_per_m2 <= s.p75_kzt_per_m2
        empty = price_stats(conn, cfg, "Астана", district="Нура", rooms=9)
        assert empty.count == 0 and not empty.enough_data


def test_get_listing_on_real_data():
    from app.tools import get_listing

    with _db_conn() as conn:
        sid = conn.execute("SELECT source_id FROM complexes ORDER BY source_id LIMIT 1").fetchone()[0]
        hit = get_listing(conn, sid)
        assert hit.complex["source_id"] == sid
        free = conn.execute("SELECT count(*) FROM units WHERE complex_id = %s AND status = 'free'", (sid,)).fetchone()
        assert sum(g.count for g in hit.groups) == free[0]
        with pytest.raises(ToolError, match="нет в каталоге"):
            get_listing(conn, "cx-нет-такого")


def test_get_listing_without_free_units_is_a_card_not_an_error():
    from app.llm.answer import hit_line
    from app.tools import get_listing

    class Conn:
        def execute(self, sql, params):
            self.sql = sql
            return self

        def fetchone(self):
            return (1,)

        def __iter__(self):  # fetch_hits: ЖК есть, свободных квартир нет
            if "FROM complexes" in self.sql:
                return iter([("cx-1", "Алматы", "Медеуский", "Т", "Д", "business", 2027, 3, False, [], [], "Тихо.")])
            return iter([])

    hit = get_listing(Conn(), "cx-1")
    assert hit.groups == []
    assert "Свободные квартиры: нет" in hit_line(2, hit, cfg.catalog, "Свободные квартиры")


def _db_conn():
    from tests.test_catalog_search import _db

    return _db()
