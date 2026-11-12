CREATE EXTENSION IF NOT EXISTS vector;

-- новостройки (6.3): ЖК → квартиры. Смысловой текст и эмбеддинг — у ЖК, жёсткие фильтры — по квартирам
CREATE TABLE IF NOT EXISTS complexes (
  id                  BIGSERIAL PRIMARY KEY,
  source_id           TEXT NOT NULL UNIQUE,
  city                TEXT NOT NULL,
  district            TEXT NOT NULL,
  name                TEXT NOT NULL,
  developer           TEXT NOT NULL,
  housing_class       TEXT NOT NULL,          -- ключ из config.catalog.classes
  completion_year     SMALLINT NOT NULL,
  completion_quarter  SMALLINT NOT NULL CHECK (completion_quarter BETWEEN 1 AND 4),
  delivered           BOOLEAN NOT NULL DEFAULT false,
  features            TEXT[] NOT NULL DEFAULT '{}',   -- config.catalog.complex_features
  payment             TEXT[] NOT NULL DEFAULT '{}',   -- config.catalog.payment
  description         TEXT NOT NULL,
  search_text         TEXT NOT NULL,
  tsv                 tsvector GENERATED ALWAYS AS (to_tsvector('russian', search_text)) STORED,
  embedding           vector(1024),           -- = embeddings.dim в config
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complexes_embedding_idx ON complexes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS complexes_tsv_idx ON complexes USING gin (tsv);
CREATE INDEX IF NOT EXISTS complexes_filter_idx ON complexes (city, district, housing_class);

CREATE TABLE IF NOT EXISTS units (
  id                  BIGSERIAL PRIMARY KEY,
  source_id           TEXT NOT NULL UNIQUE,
  complex_id          TEXT NOT NULL REFERENCES complexes (source_id) ON DELETE CASCADE,
  rooms               SMALLINT NOT NULL,
  area_m2             NUMERIC(6,1) NOT NULL,
  floor               SMALLINT NOT NULL,
  floors_total        SMALLINT NOT NULL CHECK (floor BETWEEN 1 AND floors_total),
  price_kzt           BIGINT NOT NULL,
  finishing           TEXT NOT NULL CHECK (finishing IN ('rough', 'pre_finish', 'finished')),
  status              TEXT NOT NULL DEFAULT 'free' CHECK (status IN ('free', 'reserved', 'sold')),
  windows             TEXT NOT NULL CHECK (windows IN ('yard', 'street', 'both')),
  view                TEXT NOT NULL CHECK (view IN ('mountains', 'city', 'park', 'river', 'none')),
  orientation         TEXT[] NOT NULL DEFAULT '{}',   -- north / east / south / west
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS units_complex_idx ON units (complex_id);
CREATE INDEX IF NOT EXISTS units_filter_idx ON units (status, rooms, price_kzt);

CREATE TABLE query_log (
  id                 BIGSERIAL PRIMARY KEY,
  query              TEXT NOT NULL,
  filters            JSONB,
  relaxed            TEXT[],                -- какие мягкие фильтры поиск снял
  clarification      TEXT,                  -- парсер переспросил — поиска не было
  result_source_ids  TEXT[],                -- выдача в порядке показа
  answer_model       TEXT,                  -- модель, которая фактически ответила
  fallback           BOOLEAN,               -- сработал серверный фолбэк
  invalid_refs       INT,                   -- номера вне списка, вырезанные из ответа
  error              TEXT,                  -- если запрос упал
  latency_ms         INT,
  timings_ms         JSONB,                 -- по этапам: parse, embed, search, rerank, ttft, answer
  cost_usd           NUMERIC(10,6),         -- оценка сверху: бесплатные токены не учитываются
  flagged_source_ids TEXT[],                -- показанные объекты с признаками вставки или набивки — на модерацию
  mode               TEXT NOT NULL DEFAULT 'v1' CHECK (mode IN ('v1', 'v2')),  -- v1 — workflow, v2 — агент
  tool_calls         TEXT[],                -- v2: вызванные инструменты по порядку, без аргументов
  history_turns      INT,                   -- v2: сколько реплик истории пришло (текст не пишем)
  history_rejected   INT,                   -- v2: реплики ассистента с неверной или просроченной подписью
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
