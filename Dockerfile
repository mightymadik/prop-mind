# 1) Сборка страницы чата (React + Vite) — Node нужен только здесь
FROM node:22-slim AS chat
WORKDIR /chat
COPY web/chat-app/package.json web/chat-app/package-lock.json ./
RUN npm ci
COPY web/chat-app/ ./
RUN npm run build

# 2) Приложение: FastAPI раздаёт API и собранную статику, Node-сервера нет
FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY config ./config
COPY db ./db
COPY scripts ./scripts
COPY data ./data
COPY web ./web
COPY --from=chat /chat/dist ./web/chat-app/dist
# порт — из $PORT (Cloud Run задаёт 8080), локально и за Caddy — 8000.
# Заголовки X-Forwarded-* uvicorn принимает только от FORWARDED_ALLOW_IPS (по умолчанию 127.0.0.1),
# иначе IP для лимитов можно подделать.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
