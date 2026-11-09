-- объекты с признаками prompt injection или набивки, показанные в выдаче, — на модерацию. Идемпотентно.
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS flagged_source_ids TEXT[];
