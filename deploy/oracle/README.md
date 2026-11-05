# Демо на Oracle Cloud Always Free (Arm VM)

Одна VM: Postgres + pgvector, API и Caddy (HTTPS) из `docker-compose.yml` рядом с этим файлом.
Все образы есть под arm64.

> **Не открывай демо в интернет, пока не готовы лимиты по IP и дневной бюджет (шаг 5, п. 1).**
> Без них любой посетитель тратит твои ключи Anthropic и Voyage.

## Условия Always Free (по документации Oracle, проверено 2026-11-05)

- Ampere A1 (Arm): **2 OCPU и 12 ГБ RAM** в сумме (1 500 OCPU-часов и 9 000 ГБ-часов в месяц),
  до двух VM; блочные тома — 200 ГБ в сумме; исходящий трафик — 10 ТБ в месяц.
- Только в **домашнем регионе** аккаунта. Ошибка «out of host capacity» — временно нет мест:
  другой availability domain или повторить позже.
- **Простаивающие VM Oracle может забрать:** если за 7 дней 95-й перцентиль CPU, сеть и память
  (для A1) — все ниже 20%. Демо почти всё время простаивает. Проверь в консоли актуальную политику
  для своего типа аккаунта; если риск реальный — держи данные так, чтобы VM можно было поднять
  заново за 10 минут (этот README + дамп базы).

Источник: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## 1. VM

1. Compute → Instances → Create: образ **Ubuntu 24.04 (aarch64)**, shape **VM.Standard.A1.Flex**,
   1 OCPU / 6 ГБ (хватает с запасом; вторая половина лимита остаётся свободной). Публичный IP — да,
   SSH-ключ — свой.
2. Сеть: в Security List подсети VCN добавь Ingress `0.0.0.0/0` TCP **80** и **443**.
3. На самой VM образ Oracle закрывает порты в iptables — открой:
   ```bash
   sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -j ACCEPT
   sudo netfilter-persistent save
   ```

## 2. Docker и код

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/<you>/prop-mind.git && cd prop-mind/deploy/oracle
cp .env.example .env && nano .env   # DOMAIN, POSTGRES_PASSWORD, ключи
```

**Домен без покупки:** `DOMAIN=<публичный-IP-через-дефисы>.sslip.io` (например,
`203-0-113-7.sslip.io`) — Caddy выпустит на него сертификат Let's Encrypt.

## 3. Запуск

```bash
docker compose up -d --build
docker compose logs -f caddy   # дождись «certificate obtained»
```

Схема базы создаётся при первом старте из `db/schema.sql`. Миграции при обновлении:
`docker compose exec api python scripts/migrate.py`.

## 4. Данные

Вариант А — **перенести базу с Mac** (без новых вызовов Voyage, те же эмбеддинги, что в evals):

```bash
# на Mac, из корня проекта
docker compose exec -T db pg_dump -U prop -d prop --data-only -t listings | gzip > listings.sql.gz
scp listings.sql.gz ubuntu@<IP>:~/
# на VM
gunzip -c ~/listings.sql.gz | docker compose exec -T db psql -U prop -d prop
```

Вариант Б — **пересчитать на VM** (~125 тыс. токенов Voyage, меньше $0,01):
`docker compose exec api python scripts/sync_index.py --config config/demo.yaml`.

Проверка: `curl https://$DOMAIN/health` → `"db": "ok", "listings": 1048`.

## 5. Обслуживание

- Обновление: `git pull && docker compose up -d --build`.
- Бэкап раз в сутки (cron): `docker compose exec -T db pg_dump -U prop -d prop | gzip > ~/backup-$(date +%F).sql.gz`.
- Расход на модели ограничен `limits.daily_budget_usd` в конфиге.
