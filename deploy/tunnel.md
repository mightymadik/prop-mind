# Показать демо с Mac через Cloudflare Tunnel

Быстрый способ показать работающую версию, не разворачивая сервер. Mac должен быть включён,
а ссылка живёт, пока запущен `cloudflared`.

> **Ограничение Quick Tunnel: Server-Sent Events не поддерживаются** (документация Cloudflare).
> Стриминг ответа через `trycloudflare.com` не работает — открывай чат в режиме без стриминга
> (`/chat?stream=0`, появится вместе со страницей чата): ответ придёт целиком через несколько секунд.
> Ещё: до 200 одновременных запросов, без гарантий доступности, адрес меняется при каждом запуске.
> Источник: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/

```bash
brew install cloudflared
docker compose up -d                          # API на localhost:8000
cloudflared tunnel --url http://localhost:8000
# в выводе — адрес вида https://<слова>.trycloudflare.com
```

Открыть: `https://<слова>.trycloudflare.com/chat?stream=0`. Остановить — Ctrl+C.

**Со стримингом** нужен именованный туннель (бесплатный, но домен должен быть подключён к
Cloudflare). Поддержку SSE в именованном туннеле документация прямо не описывает — проверь на
своём домене перед показом.

Пока туннель открыт, демо доступно всем, у кого есть ссылка: лимиты по IP и дневной бюджет должны
быть включены.
