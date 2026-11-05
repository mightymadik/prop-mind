# Запасной вариант: Google Cloud Run + Neon (Postgres с pgvector)

API — контейнер в Cloud Run (масштабируется до нуля), база — Neon Free.

> **Не открывай демо в интернет, пока не готовы лимиты по IP и дневной бюджет (шаг 5, п. 1).**

## Условия бесплатных тарифов (по документации, проверено 2026-11-05)

**Cloud Run** (request-based billing), в месяц: 2 млн запросов, 180 000 vCPU-секунд,
360 000 ГБ-секунд памяти, 1 ГБ исходящего трафика из Северной Америки. **Нужен billing-аккаунт**
(Google Cloud Free Tier без него недоступен); лимиты — на billing-аккаунт. Artifact Registry —
0,5 ГБ хранения бесплатно (наш образ ~200 МБ).
Источник: https://docs.cloud.google.com/free/docs/free-cloud-features

**Neon Free:** 1 ГБ хранения на проект (наши 1048 объектов с эмбеддингами — единицы МБ),
100 CU-часов на проект в месяц (0,25 CU ≈ 400 часов работы), засыпает через 5 минут простоя
(отключить нельзя — первый запрос после сна ждёт пробуждения), 5 ГБ исходящего трафика.
При исчерпании CU-часов или трафика база приостанавливается до следующего месяца; данные не
удаляются. **pgvector есть на всех планах**, включая Free, HNSW поддерживается (до 2000 измерений —
наши 1024 проходят). Источники: https://neon.com/docs/introduction/plans,
https://neon.com/docs/extensions/pgvector

Что не проверено по документации: точные бесплатные лимиты Cloud Build (сборка `--source`) — их
нет на странице Free Tier, проверь в консоли до первой сборки.

## 1. Neon

1. Создай проект в регионе **AWS US East** (рядом с `us-central1` Cloud Run — бесплатный
   egress Cloud Run считается из Северной Америки).
2. Скопируй connection string (с `?sslmode=require`) — это `DATABASE_URL`.
3. Схема и данные с Mac (psql — из контейнера, ставить ничего не нужно):

```bash
export NEON_URL='postgresql://...neon.tech/neondb?sslmode=require'
PSQL="docker run --rm -i pgvector/pgvector:pg16 psql"
$PSQL "$NEON_URL" < db/schema.sql                  # CREATE EXTENSION vector внутри
docker compose exec -T db pg_dump -U prop -d prop --data-only -t listings | $PSQL "$NEON_URL"
$PSQL "$NEON_URL" -c "SELECT count(*), count(embedding) FROM listings"   # 1048, 1048
```

## 2. Cloud Run

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

cp deploy/cloudrun/env.yaml.example deploy/cloudrun/env.yaml   # заполни; файл в .gitignore
gcloud run deploy prop-mind --source . --region us-central1 \
  --allow-unauthenticated --max-instances 1 --memory 512Mi --cpu 1 --timeout 300 \
  --env-vars-file deploy/cloudrun/env.yaml
```

- `--max-instances 1`: лимит по IP хранится в памяти процесса — с одним экземпляром он общий.
  После масштабирования до нуля счётчики IP обнуляются; дневной бюджет считается по `query_log` в
  базе и обнулением не сбрасывается.
- Ключи в переменных окружения видны всем, у кого есть доступ к проекту. Для общего проекта —
  Secret Manager и `--set-secrets`.
- Холодный старт: запуск контейнера (несколько секунд) + пробуждение Neon. Первый запрос после
  простоя заметно медленнее.

Проверка: `curl https://<url>.run.app/health`.
