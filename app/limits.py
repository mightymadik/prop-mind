"""Защита публичного демо: лимит запросов с IP, дневной бюджет на модели, site-key виджета."""

import threading
import time
from collections import deque

from app.config import AppConfig

BUDGET_SQL = """
SELECT coalesce(sum(cost_usd), 0) FROM query_log
WHERE created_at >= (date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s)
"""


class RateLimiter:
    """Скользящее окно: не больше `limit` запросов за `window` секунд с одного ключа (IP)."""

    # ponytail: в памяти одного процесса — для одной VM / одного экземпляра Cloud Run;
    # при нескольких экземплярах — Redis с теми же правилами
    def __init__(self, limit: int, window: float = 60.0):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> float:
        """0 — запрос разрешён и учтён; иначе через сколько секунд можно повторить."""
        now = time.monotonic() if now is None else now
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return q[0] + self.window - now
            q.append(now)
            if len(self.hits) > 10_000:  # не копим IP бесконечно
                for k in [k for k, v in self.hits.items() if not v]:
                    del self.hits[k]
            return 0.0


def spent_today_usd(conn, cfg: AppConfig) -> float:
    return float(conn.execute(BUDGET_SQL, {"tz": cfg.limits.timezone}).fetchone()[0])


def budget_left(conn, cfg: AppConfig) -> bool:
    return spent_today_usd(conn, cfg) < cfg.limits.daily_budget_usd


def frame_ancestors(cfg: AppConfig, site_key: str | None) -> str:
    """Значение CSP frame-ancestors для страницы чата: свой домен + сайты этого site-key."""
    origins = cfg.sites[site_key].origins if site_key in cfg.sites else []
    return " ".join(["'self'", *origins])
