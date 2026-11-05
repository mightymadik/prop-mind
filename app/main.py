import contextvars
import json
import queue
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings, load_config
from app.db import connect
from app.llm.parser import parse_query
from app.schemas import SearchFilters
from app.service import run_search

app = FastAPI(title="prop-mind")
cfg = load_config(get_settings().config_path)

LISTING_SQL = """SELECT source_id, city, district, complex_name, deal_type, rooms, area_m2, floor, floors_total,
                        year_built, condition, price_kzt, features, description
                 FROM listings WHERE source_id = %s"""
LISTING_FIELDS = (
    "source_id",
    "city",
    "district",
    "complex_name",
    "deal_type",
    "rooms",
    "area_m2",
    "floor",
    "floors_total",
    "year_built",
    "condition",
    "price_kzt",
    "features",
    "description",
)


class QueryIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    try:
        with connect() as conn:
            listings = conn.execute("SELECT count(*) FROM listings").fetchone()[0]
        db = "ok"
    except Exception as e:  # noqa: BLE001 — health отдаёт причину, а не 500
        db, listings = f"error: {e.__class__.__name__}", None
    return {
        "db": db,
        "listings": listings,
        "anthropic_key": bool(s.anthropic_api_key),
        "voyage_key": bool(s.voyage_api_key),
    }


@app.post("/parse")
def parse(body: QueryIn) -> SearchFilters:
    """Только разбор запроса в фильтры: для отладки и evals."""
    try:
        return parse_query(body.query, cfg, get_settings())
    except Exception as e:  # noqa: BLE001 — сбой LLM → 502, а не 500 со стектрейсом
        raise HTTPException(502, f"parser failed: {e.__class__.__name__}") from e


@app.get("/listings/{source_id}")
def listing(source_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(LISTING_SQL, (source_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "listing not found")
    d = dict(zip(LISTING_FIELDS, row, strict=True))
    d["area_m2"] = float(d["area_m2"]) if d["area_m2"] is not None else None
    return d


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/search")
def search_endpoint(body: QueryIn, request: Request):
    """{query} → разбор, поиск, ответ. С `Accept: text/event-stream` — SSE: filters, results, delta*, answer
    (или clarification), done; при сбое — error. Номер [n] в delta — n-й объект из события results."""
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
