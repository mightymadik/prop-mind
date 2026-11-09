"""CatalogSource → ЖК (search_text + эмбеддинги) и квартиры → Postgres.

    uv run python scripts/sync_catalog.py --config config/demo.yaml

Upsert по source_id; эмбеддинг ЖК пересчитывается только там, где изменился search_text. Синхронизация
полная: ЖК и квартиры, которых больше нет в источнике, удаляются.
"""

import argparse
from datetime import UTC, datetime

from app.config import AppConfig, get_settings, load_config
from app.db import connect
from app.search.embeddings import embed, to_pgvector
from app.search.text import build_complex_text
from app.sources.base import CatalogSource
from app.sources.synthetic import SyntheticCatalogSource

C_COLS = [
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
]
U_COLS = [
    "source_id",
    "complex_id",
    "rooms",
    "area_m2",
    "floor",
    "floors_total",
    "price_kzt",
    "finishing",
    "status",
    "windows",
    "view",
    "orientation",
]


def upsert_sql(table: str, cols: list[str], extra: str = "") -> str:
    return f"""
INSERT INTO {table} ({", ".join(cols)}{", search_text" if extra else ""})
VALUES ({", ".join(f"%({c})s" for c in cols)}{", %(search_text)s" if extra else ""})
ON CONFLICT (source_id) DO UPDATE SET
  {", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])}{extra},
  updated_at = now()
"""


C_UPSERT = upsert_sql(
    "complexes",
    C_COLS,
    """,
  search_text = EXCLUDED.search_text,
  embedding = CASE WHEN complexes.search_text = EXCLUDED.search_text THEN complexes.embedding END""",
)
U_UPSERT = upsert_sql("units", U_COLS)


def make_source(cfg: AppConfig) -> CatalogSource:
    if cfg.source == "synthetic_jsonl":
        return SyntheticCatalogSource(cfg.catalog.complexes_path, cfg.catalog.units_path, cfg.field_map)
    raise ValueError(f"unknown source: {cfg.source}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    args = ap.parse_args()
    cfg, settings = load_config(args.config), get_settings()
    source = make_source(cfg)
    # ponytail: всегда полная синхронизация; хранить время последней — когда появится прод-адаптер
    since = datetime.min.replace(tzinfo=UTC)

    with connect() as conn:
        seen_c = []
        for c in source.fetch_complexes(since):
            conn.execute(C_UPSERT, c.model_dump() | {"search_text": build_complex_text(c, cfg.catalog, cfg.moderation)})
            seen_c.append(c.source_id)
        seen_u = []
        with conn.cursor() as cur:
            rows = [u.model_dump() for u in source.fetch_units(since)]
            cur.executemany(U_UPSERT, rows)
            seen_u = [r["source_id"] for r in rows]
        gone_u = conn.execute("DELETE FROM units WHERE NOT source_id = ANY(%s)", (seen_u,)).rowcount
        gone_c = conn.execute("DELETE FROM complexes WHERE NOT source_id = ANY(%s)", (seen_c,)).rowcount
        conn.commit()
        print(f"ЖК: {len(seen_c)} (удалено {gone_c}), квартир: {len(seen_u)} (удалено {gone_u})")

        todo = conn.execute("SELECT id, search_text FROM complexes WHERE embedding IS NULL ORDER BY id").fetchall()
        bs = cfg.embeddings.batch_size
        for i in range(0, len(todo), bs):
            chunk = todo[i : i + bs]
            vecs = embed([t for _, t in chunk], cfg.embeddings, settings.voyage_api_key)
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE complexes SET embedding = %s::vector WHERE id = %s",
                    [(to_pgvector(v), id_) for (id_, _), v in zip(chunk, vecs, strict=True)],
                )
            conn.commit()
            print(f"эмбеддинги ЖК: {min(i + bs, len(todo))}/{len(todo)}")


if __name__ == "__main__":
    main()
