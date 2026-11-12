import pytest

from app.config import load_config
from app.limits import RateLimiter, agent_budget_left, budget_left, frame_ancestors, spent_today_usd

CFG = load_config("config/demo.yaml")


def test_sliding_window_per_key():
    rl = RateLimiter(limit=2, window=60)
    assert rl.check("a", now=0) == 0 and rl.check("a", now=10) == 0
    assert rl.check("a", now=20) == pytest.approx(40)  # первый из двух выйдет из окна на 60-й секунде
    assert rl.check("b", now=20) == 0  # другой IP — свой счётчик
    assert rl.check("a", now=60.5) == 0  # окно сдвинулось


def test_frame_ancestors_self_plus_site_origins():
    cfg = CFG.model_copy(update={"sites": {"pub_x": type(CFG.sites["pub_demo"])(origins=["https://ex.kz"])}})
    assert frame_ancestors(cfg, "pub_x") == "'self' https://ex.kz"
    assert frame_ancestors(cfg, None) == "'self'" and frame_ancestors(cfg, "nope") == "'self'"


def test_budget_counts_today_only_in_configured_timezone():
    import psycopg

    from app.db import connect

    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("нет базы")
    with conn:
        conn.execute("SAVEPOINT t")
        before = spent_today_usd(conn, CFG)
        conn.execute("INSERT INTO query_log (query, cost_usd) VALUES ('t', 1.5)")
        conn.execute(
            "INSERT INTO query_log (query, cost_usd, created_at) VALUES ('old', 100, now() - interval '2 days')"
        )
        assert spent_today_usd(conn, CFG) == pytest.approx(before + 1.5)  # вчерашнее не считается
        tight = CFG.model_copy(update={"limits": CFG.limits.model_copy(update={"daily_budget_usd": before + 1})})
        assert not budget_left(conn, tight)
        v2 = spent_today_usd(conn, CFG, "v2")
        conn.execute("INSERT INTO query_log (query, cost_usd, mode) VALUES ('a', 0.4, 'v2')")
        assert spent_today_usd(conn, CFG, "v2") == pytest.approx(v2 + 0.4)  # доля агента — только mode='v2'
        agent_tight = CFG.model_copy(update={"agent": CFG.agent.model_copy(update={"daily_budget_usd": v2 + 0.3})})
        roomy = CFG.model_copy(update={"agent": CFG.agent.model_copy(update={"daily_budget_usd": v2 + 1})})
        assert not agent_budget_left(conn, agent_tight) and agent_budget_left(conn, roomy)
        conn.execute("ROLLBACK TO SAVEPOINT t")
        conn.rollback()
