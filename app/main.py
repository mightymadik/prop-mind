import contextvars
import json
import math
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings, load_config
from app.db import connect
from app.limits import RateLimiter, budget_left, frame_ancestors
from app.llm.parser import parse_query
from app.schemas import SearchFilters
from app.service import run_search

app = FastAPI(title="prop-mind")
WEB = Path(__file__).resolve().parent.parent / "web"
# сборка React-страницы (npm run build в web/chat-app); в git её нет — собирает Docker или разработчик
CHAT_DIST = WEB / "chat-app" / "dist"
# /static/chat — раньше общего /static: иначе запросы к ресурсам сборки уйдут в web/
app.mount("/static/chat", StaticFiles(directory=CHAT_DIST, check_dir=False), name="chat-assets")
app.mount("/static", StaticFiles(directory=WEB), name="static")
cfg = load_config(get_settings().config_path)
limiter = RateLimiter(cfg.limits.requests_per_minute_per_ip)
BUDGET_EXHAUSTED = "Лимит демо на сегодня исчерпан — попробуйте завтра."

COMPLEX_FIELDS = (
    "source_id",
    "city",
    "district",
    "name",
    "developer",
    "housing_class",
    "completion_year",
    "completion_quarter",
    "delivered",
    "features",
    "payment",
    "description",
)
UNIT_FIELDS = (
    "source_id",
    "rooms",
    "area_m2",
    "floor",
    "floors_total",
    "price_kzt",
    "finishing",
    "windows",
    "view",
    "orientation",
)


class QueryIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    site_key: str | None = Field(None, max_length=64)


def guard(request: Request, site_key: str | None) -> None:
    """Перед вызовом моделей: site-key → лимит IP (в памяти) → дневной бюджет (по query_log)."""
    if site_key is not None and site_key not in cfg.sites:
        raise HTTPException(403, "unknown site key")
    ip = request.client.host if request.client else "unknown"  # за прокси — из X-Forwarded-For (uvicorn)
    wait = limiter.check(ip)
    if wait:
        raise HTTPException(429, "Слишком много запросов — подождите минуту.", {"Retry-After": str(math.ceil(wait))})
    try:
        with connect() as conn:
            ok = budget_left(conn, cfg)
    except Exception as e:  # noqa: BLE001 — без базы бюджет не проверить: не тратим ключи вслепую
        raise HTTPException(503, "service unavailable") from e
    if not ok:
        raise HTTPException(429, BUDGET_EXHAUSTED)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    try:
        with connect() as conn:
            complexes, units = conn.execute(
                "SELECT (SELECT count(*) FROM complexes), (SELECT count(*) FROM units WHERE status = 'free')"
            ).fetchone()
        db = "ok"
    except Exception as e:  # noqa: BLE001 — health отдаёт причину, а не 500
        db, complexes, units = f"error: {e.__class__.__name__}", None, None
    return {
        "db": db,
        "complexes": complexes,
        "free_units": units,
        "anthropic_key": bool(s.anthropic_api_key),
        "voyage_key": bool(s.voyage_api_key),
    }


@app.post("/parse")
def parse(body: QueryIn, request: Request) -> SearchFilters:
    """Только разбор запроса в фильтры: для отладки и evals."""
    guard(request, body.site_key)
    try:
        return parse_query(body.query, cfg, get_settings())
    except Exception as e:  # noqa: BLE001 — сбой LLM → 502, а не 500 со стектрейсом
        raise HTTPException(502, f"parser failed: {e.__class__.__name__}") from e


@app.get("/complexes/{source_id}")
def complex_card(source_id: str) -> dict:
    """Карточка ЖК и его свободные квартиры — для сайта, который открывает объект из виджета."""
    with connect() as conn:
        row = conn.execute(f"SELECT {', '.join(COMPLEX_FIELDS)} FROM complexes WHERE source_id = %s", (source_id,))
        row = row.fetchone()
        if row is None:
            raise HTTPException(404, "complex not found")
        units = conn.execute(
            f"SELECT {', '.join(UNIT_FIELDS)} FROM units WHERE complex_id = %s AND status = 'free' "
            "ORDER BY rooms, price_kzt, source_id",
            (source_id,),
        ).fetchall()
    d = dict(zip(COMPLEX_FIELDS, row, strict=True))
    d["units"] = [dict(zip(UNIT_FIELDS, u, strict=True)) | {"area_m2": float(u[2])} for u in units]
    return d


def page_csp(ancestors: str) -> str:
    """Без инлайн-скриптов и сторонних ресурсов; встраивать в iframe — только разрешённым сайтам."""
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        f"connect-src 'self'; frame-ancestors {ancestors}; base-uri 'none'; form-action 'self'"
    )


@app.get("/chat")
def chat_page(site_key: str | None = None) -> FileResponse:
    """Страница чата; ?embed=1 — режим виджета, ?stream=0 — без стриминга (Quick Tunnel)."""
    if site_key is not None and site_key not in cfg.sites:
        raise HTTPException(403, "unknown site key")
    index = CHAT_DIST / "index.html"
    if not index.exists():
        raise HTTPException(503, "chat page is not built: cd web/chat-app && npm ci && npm run build")
    return FileResponse(index, headers={"Content-Security-Policy": page_csp(frame_ancestors(cfg, site_key))})


@app.get("/widget.js")
def widget() -> FileResponse:
    return FileResponse(
        WEB / "widget.js", media_type="text/javascript", headers={"Cache-Control": "public, max-age=300"}
    )


@app.get("/demo-host")
def demo_host() -> FileResponse:
    """Тестовая страница «чужого» сайта с виджетом (тот же домен — для проверки встраивания)."""
    return FileResponse(WEB / "demo-host.html", headers={"Content-Security-Policy": page_csp("'none'")})


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/search")
def search_endpoint(body: QueryIn, request: Request):
    """{query} → разбор, поиск, ответ. С `Accept: text/event-stream` — SSE: filters, results, delta*, answer
    (или clarification), done; при сбое — error. Номер [n] в delta — n-й объект из события results."""
    guard(request, body.site_key)  # до начала потока: отказ — обычный HTTP 403/429/503
    settings = get_settings()
    if "text/event-stream" not in request.headers.get("accept", ""):
        try:
            return run_search(body.query, cfg, settings)
        except Exception as e:  # noqa: BLE001 — сбой модели или поиска → 502 без стектрейса
            raise HTTPException(502, f"search failed: {e.__class__.__name__}") from e

    events: queue.Queue = queue.Queue()
    done = object()

    def work() -> None:
        try:
            run_search(body.query, cfg, settings, on_event=lambda name, data: events.put(sse(name, data)))
        except Exception as e:  # noqa: BLE001 — в стриме ошибка уходит событием
            events.put(sse("error", {"error": f"search failed: {e.__class__.__name__}"}))
        finally:
            events.put(done)

    # contextvars (счётчик стоимости) в поток сами не переходят
    threading.Thread(target=contextvars.copy_context().run, args=(work,), daemon=True).start()

    def stream():
        while (item := events.get()) is not done:
            yield item

    return StreamingResponse(stream(), media_type="text/event-stream")
