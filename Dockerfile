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
# порт — из $PORT (Cloud Run задаёт 8080), локально и за Caddy — 8000.
# Заголовки X-Forwarded-* uvicorn принимает только от FORWARDED_ALLOW_IPS (по умолчанию 127.0.0.1),
# иначе IP для лимитов можно подделать.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
