-- query_log для /search: выдача по source_id, ответ модели, фолбэк, этапы. Идемпотентно.
ALTER TABLE query_log DROP COLUMN IF EXISTS result_ids;  -- BIGINT[]: наружу отдаём source_id
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS relaxed TEXT[];
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS clarification TEXT;
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS result_source_ids TEXT[];
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS answer_model TEXT;
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS fallback BOOLEAN;
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS invalid_refs INT;
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS timings_ms JSONB;
