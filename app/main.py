from fastapi import FastAPI

from app.config import get_settings
from app.db import connect

app = FastAPI(title="prop-mind")


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
