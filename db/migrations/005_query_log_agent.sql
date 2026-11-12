-- агент (v2, 6.4): режим запроса, вызванные инструменты, история диалога (только счётчики). Идемпотентно.
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'v1';
ALTER TABLE query_log DROP CONSTRAINT IF EXISTS query_log_mode_check;
ALTER TABLE query_log ADD CONSTRAINT query_log_mode_check CHECK (mode IN ('v1', 'v2'));
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS tool_calls TEXT[];
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS history_turns INT;
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS history_rejected INT;
