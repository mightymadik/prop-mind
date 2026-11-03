from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings, load_config
from app.db import connect
from app.llm.parser import parse_query
from app.schemas import SearchFilters

app = FastAPI(title="prop-mind")
cfg = load_config(get_settings().config_path)


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
