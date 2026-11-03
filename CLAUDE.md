# prop-mind

AI-поиск недвижимости: пользователь пишет запрос обычным языком, система
находит объекты и объясняет выбор. Портфолио-проект + основа для прода.

Полный дизайн: @docs/DESIGN.md — при любом сомнении сверяйся с ним.

## Поток запроса

```
запрос → parse_query (LLM → SearchFilters) → hybrid search (SQL-фильтры + FTS + pgvector, RRF)
       → rerank (топ-50 → топ-10) → answer (LLM, ссылки [id], проверка id) → ответ
офлайн: ListingSource (адаптер) → sync_index.py → search_text + embedding → Postgres
```

Принцип: объекты находит база, LLM только понимает запрос и объясняет ответ.
v1 — фиксированный workflow. v2 (tool use) — только после v1 и evals.

## Стек

- Python 3.12, FastAPI, Pydantic v2, pydantic-settings
- Postgres 16 + pgvector, psycopg 3
- Anthropic SDK (через обёртку `app/llm/client.py`, провайдер заменяемый)
- pytest; docker compose

## Команды

- Окружение: `uv sync` (Python 3.12, `.venv`); `cp .env.example .env` и вписать ключи
- Запуск: `docker compose up` (порт БД на хосте — `DB_PORT` в `.env`)
- Тесты: `uv run pytest`
- Синтетика: `uv run python scripts/generate_listings.py --config config/demo.yaml --count 2500`
- Индексация: `uv run python scripts/sync_index.py --config config/demo.yaml`
- Evals: `uv run python evals/run_evals.py --config config/demo.yaml` (шаг 2)
- Git-хук атрибуции: `git config core.hooksPath .githooks` (один раз, вручную)

(Обновляй этот раздел, когда команды появятся или изменятся.)

## Правила кода

- Типизация везде, модели данных — Pydantic. Для перечислений в схемах LLM — `Literal`, не `Enum`.
- SQL только с параметрами, никакой склейки строк из пользовательского ввода.
- Имена моделей, ключи, лимиты — только в конфиге / env, не в коде.
- Промпты лежат файлами в `app/llm/prompts/`, не строками в коде.
- Ядро не знает о конкретной базе данных: доступ к данным только через `ListingSource`.
- Никаких реальных данных компании, `config/prod.yaml`, ключей и `.env` в репозитории.
  В git только `.env.example` и `config/demo.yaml`.
- Новая логика → тест в `tests/`. Изменение промпта, модели или поиска → прогон evals,
  результат сохраняется в `evals/results/`.

## Git

- Коммиты маленькие: один логический кусок шага = один коммит (схема БД, адаптер,
  docker-compose и т. д. — отдельно). Никогда один коммит на весь шаг или проект.
- Формат Conventional Commits на английском: `feat(search): add RRF hybrid query`,
  `test(parser): ...`, `chore: ...`, `docs: ...`.
- Перед коммитом: тесты зелёные, `git add` только файлы этого куска (не `git add -A`).
- Перед каждым коммитом покажи список файлов и сообщение и дождись моего «ок».
- Не добавляй в сообщения коммитов и PR строки `Co-Authored-By` и «Generated with
  Claude Code». Не меняй git config, не делай push, rebase и force без просьбы.

## Как работать со мной (Claude Code)

- Делай один шаг плана за раз (см. «План работ» в DESIGN.md); не забегай вперёд.
- Перед крупным изменением — сначала план, потом код.
- После шага: тесты зелёные, коротко перечисли, что сделано и что проверить вручную.
- Не добавляй зависимости и фреймворки агентов (LangChain и т. п.) без явной просьбы.
- Если в дизайне есть противоречие или пробел — спроси, а не угадывай.
