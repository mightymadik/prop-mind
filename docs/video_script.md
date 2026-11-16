# Сценарий видео (2–3 минуты)

Озвучка на русском, субтитры на английском. Запись экрана: демо https://79.76.99.122.sslip.io/chat,
десктоп 1440 px. Длительность — ориентир; итог ~2:30.

| Время | Кадр | Текст за кадром | Субтитры (EN) |
| --- | --- | --- | --- |
| 0:00–0:15 | Пустой экран чата: заголовок, строка «70 ЖК · 3 073 свободных квартир», карточки примеров. | Обычный поиск квартир — это двадцать фильтров. А люди думают иначе: «тихий ЖК для семьи с детьми, чтобы рядом школа». prop-mind ищет по такому описанию. | Apartment search usually means twenty filters. People think differently: "a quiet complex for a family, with a school nearby". prop-mind searches by that description. |
| 0:15–0:45 | Клик по примеру «Тихий ЖК для семьи с детьми, рядом школа». Видно: метка «Ищу варианты…» → плашки условий → карточки ЖК → короткий ответ со ссылками «№ n». Навести и кликнуть «№ 2» — карточка подсвечивается. | Агент передаёт запрос в поиск: отдельная модель разбирает его в строгие фильтры — вот они плашками, — а ЖК находит база, а не модель. Каждая рекомендация — ссылка на карточку; номера сверяются с найденным, выдумать ЖК модель не может. | The agent hands the query to search: a separate model turns it into strict filters — the chips — and the database, not the model, finds the complexes. Every recommendation links to a card; links are checked against the results, so the model can't invent a complex. |
| 0:45–1:05 | Ввести «а что со вторым? сколько там трёшек?». Ответ про ЖК № 2 из прошлого ответа с актуальными цифрами. | Чат помнит диалог. «Второй» — это второй ЖК из прошлого ответа; данные агент берёт из базы заново, а не из своей памяти. | The chat remembers the dialog. "The second one" is the second complex from the previous answer; the agent fetches fresh data from the database, not from memory. |
| 1:05–1:25 | Ввести «посчитай ипотеку на самую дешёвую трёшку там: взнос 20%, 20 лет». Ответ с ежемесячным платежом и оговоркой «пример, не оферта». | Платёж считает инструмент, а не модель: ставку, срок и минимальный взнос берёт из конфигурации. Модель только выбирает программу и объясняет результат. | The payment comes from a tool, not the model: rate, term and minimum down payment come from the config. The model only picks the program and explains the result. |
| 1:25–1:40 | Нажать «Новый диалог». Ввести «квартира у метро в Астане». Появляется уточняющий вопрос. | В Астане нет метро. Система не делает вид, что нашла, а переспрашивает. А если полностью подходящих нет, ответ так и начинается: «Полностью подходящих нет». | Astana has no metro. The system doesn't pretend — it asks back. And when nothing fully matches, the answer starts with exactly that. |
| 1:40–1:55 | Открыть `/demo-host`, нажать кнопку виджета, popup с тем же чатом. | Тот же чат встраивается на любой сайт одной строкой. Лимиты по IP и дневной бюджет защищают ключи. | The same chat embeds into any site with one line. Per-IP limits and a daily budget protect the keys. |
| 1:55–2:15 | README на GitHub: лестница Recall@10 62% → 96%, таблица prompt injection 0 из 55, таблица задержки. | Главное — качество в цифрах. Recall@10 на смысловых запросах: 62% у простого векторного поиска, 96% после гибрида и реранкера. Ноль успешных атак prompt injection из 55, включая подделку истории диалога. Ответ — около семи секунд. | Quality in numbers. Recall@10 on semantic queries: 62% for plain vector search, 96% with hybrid search and a reranker. Zero of 55 prompt-injection attacks succeed, including forged dialog history. An answer takes about seven seconds. |
| 2:15–2:30 | Схема архитектуры из README, затем ссылка на репозиторий и демо. | Агент — Claude Sonnet, разбор — Claude Haiku, поиск — Postgres с pgvector, реранк — Voyage. Код, evals и все решения — в репозитории. Демо открыто — попробуйте сами. | Agent — Claude Sonnet, parsing — Claude Haiku, search — Postgres with pgvector, rerank — Voyage. Code, evals and every decision are in the repository. The demo is open — try it. |

## Проверить перед записью

- На сервере — текущий код (`chat.mode: v2`): `curl https://<домен>/chat/config` → `{"mode": "v2"}`.
- Прогреть сервер одним запросом; дневной бюджет не исчерпан (у агента — своя доля, ~70 запросов в
  день при $1).
- Прогнать запросы сценария заранее: тексты моделей меняются от прогона к прогону. Если у второго ЖК
  нет трёшек или госпрограммы — заменить вопрос («сколько там двушек?») и озвучку.
- Чат очистить кнопкой «Новый диалог» или перезагрузкой страницы.
- Ответ агента приходит целиком через 6–8 секунд — в монтаже паузу ожидания можно ускорить, метку
  «Ищу варианты…» оставить в кадре.
- Субтитры сверить с финальной озвучкой по таймкодам.
