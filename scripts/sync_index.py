"""ListingSource → search_text + эмбеддинги → Postgres.

Upsert по source_id; эмбеддинг пересчитывается только там, где изменился search_text.
    python scripts/sync_index.py --config config/demo.yaml
"""

import argparse
from datetime import UTC, datetime

from app.config import AppConfig, get_settings, load_config
from app.db import connect
from app.search.embeddings import embed, to_pgvector
from app.search.text import build_search_text
from app.sources.base import ListingSource
from app.sources.synthetic import SyntheticJsonlSource

COLUMNS = [
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
]

UPSERT = f"""
INSERT INTO listings ({", ".join(COLUMNS)}, search_text)
VALUES ({", ".join(f"%({c})s" for c in COLUMNS)}, %(search_text)s)
ON CONFLICT (source_id) DO UPDATE SET
  {", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS[1:])},
  search_text = EXCLUDED.search_text,
  embedding = CASE WHEN listings.search_text = EXCLUDED.search_text
                   THEN listings.embedding END,
  updated_at = now()
"""


def make_source(cfg: AppConfig) -> ListingSource:
    if cfg.source == "synthetic_jsonl":
        return SyntheticJsonlSource(cfg.data_path, cfg.field_map)
    raise ValueError(f"unknown source: {cfg.source}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/demo.yaml")
    args = ap.parse_args()
    cfg = load_config(args.config)
    settings = get_settings()
    source = make_source(cfg)
    # ponytail: всегда полная синхронизация; хранить время последней — когда появится прод-адаптер
    since = datetime.min.replace(tzinfo=UTC)

    with connect() as conn:
        n = 0
        for listing in source.fetch_changed(since):
            row = listing.model_dump()
            row["search_text"] = build_search_text(listing, cfg.features, cfg.moderation)
            conn.execute(UPSERT, row)
            n += 1
        removed = list(source.fetch_removed(since))
        if removed:
            conn.execute("DELETE FROM listings WHERE source_id = ANY(%s)", (removed,))
        conn.commit()
        print(f"upserted {n}, removed {len(removed)}")

        todo = conn.execute("SELECT id, search_text FROM listings WHERE embedding IS NULL ORDER BY id").fetchall()
        bs = cfg.embeddings.batch_size
        for i in range(0, len(todo), bs):
            chunk = todo[i : i + bs]
            vecs = embed([t for _, t in chunk], cfg.embeddings, settings.voyage_api_key)
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE listings SET embedding = %s::vector WHERE id = %s",
                    [(to_pgvector(v), id_) for (id_, _), v in zip(chunk, vecs, strict=True)],
                )
            conn.commit()
            print(f"embedded {min(i + bs, len(todo))}/{len(todo)}")


if __name__ == "__main__":
    main()
