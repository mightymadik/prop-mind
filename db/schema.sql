CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE listings (
  id            BIGSERIAL PRIMARY KEY,
  source_id     TEXT NOT NULL UNIQUE,         -- id объекта в источнике (ListingSource)
  city          TEXT NOT NULL,
  district      TEXT,
  complex_name  TEXT,                         -- ЖК
  deal_type     TEXT NOT NULL CHECK (deal_type IN ('sale', 'rent')),
  rooms         SMALLINT,
  area_m2       NUMERIC(6,1),
  floor         SMALLINT,
  floors_total  SMALLINT,
  year_built    SMALLINT,
  condition     TEXT CHECK (condition IN ('rough', 'pre_finish', 'renovated', 'designer')),
  price_kzt     BIGINT NOT NULL,
  features      TEXT[] NOT NULL DEFAULT '{}',
  description   TEXT NOT NULL,
  search_text   TEXT NOT NULL,
  tsv           tsvector GENERATED ALWAYS AS (to_tsvector('russian', search_text)) STORED,
  embedding     vector(1024),                 -- = embeddings.dim в config
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON listings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON listings USING gin (tsv);
CREATE INDEX ON listings USING gin (features);
CREATE INDEX ON listings (city, deal_type, rooms, price_kzt);

CREATE TABLE query_log (
  id          BIGSERIAL PRIMARY KEY,
  query       TEXT NOT NULL,
  filters     JSONB,
  result_ids  BIGINT[],
  latency_ms  INT,
  cost_usd    NUMERIC(10,6),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
