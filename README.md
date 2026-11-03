# prop-mind

AI-поиск недвижимости: запрос обычным языком → подходящие объекты и объяснение выбора.
Объекты находит база (SQL-фильтры + полнотекстовый поиск + pgvector), LLM только понимает запрос и объясняет результат.

Дизайн: [docs/DESIGN.md](docs/DESIGN.md).

## Запуск

```bash
cp .env.example .env            # ANTHROPIC_API_KEY, VOYAGE_API_KEY
docker compose up -d            # Postgres + pgvector, API на :8000
uv sync
uv run python scripts/generate_listings.py --config config/demo.yaml --count 2500
uv run python scripts/sync_index.py --config config/demo.yaml
curl localhost:8000/health
```

## Результаты evals

Будут после шагов 2–4.
