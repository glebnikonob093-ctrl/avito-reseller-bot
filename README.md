# avito-reseller-bot

Telegram-бот + Mini App + бэкенд для перепродажников: автоматический поиск
свежих объявлений (пока — Avito), пайплайн «Новый → Написал → Торг → Купил →
Продал», дил-скоринг и админ-диагностика. Работает на одном Python-процессе,
с SQLite/Postgres под капотом.

* Бот: aiogram 3 (long-polling)
* API/Mini App: FastAPI + Uvicorn
* Frontend: React + Vite (`bot/webapp`)
* Источники объявлений: ScraperAPI/ScrapingBee (cloud), прямой парсинг Avito
  (httpx + UA-rotation), внешний push-pipeline (Duff89/`parser_avito`),
  mock-фолбэк для разработки

## Структура

```
bot/
  app/
    main.py              # точка входа, оркестрация
    api.py               # FastAPI endpoints (Mini App + webhooks)
    bot_router.py        # обработчики aiogram
    config.py            # Settings из .env
    models.py            # SQLAlchemy
    services/            # users, catalogs, deals, scoring, ...
    sources/
      base.py
      registry.py        # SourceRegistry (push -> cloud -> public -> mock)
      avito_cloud_scrape.py
      avito_public_web.py
      duff_push.py       # читает буфер pushed_listings
      mock.py
  webapp/                # Mini App (Vite)
  data/app.db            # SQLite (dev)
```

## Требования

* Python 3.12 (создаётся `bot/.venv`)
* Node.js 20+ для Mini App
* Доступ к `BOT_TOKEN` от @BotFather

## Быстрый запуск (локально)

```bash
cd bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # затем впишите BOT_TOKEN и пр.

# Backend + бот
python -m app.main

# Frontend (в отдельной вкладке)
cd webapp
npm install
npm run dev
```

После этого Mini App доступен на `http://localhost:5173`,
API — на `http://127.0.0.1:8000`.

## Переменные окружения

| Имя | Назначение |
| --- | --- |
| `BOT_TOKEN` | токен бота от @BotFather (обязателен) |
| `WEBAPP_URL` | публичный URL Mini App (для кнопки в боте) |
| `DATABASE_URL` | по умолчанию `sqlite+aiosqlite:///./data/app.db` |
| `API_HOST` / `API_PORT` | где слушает FastAPI (по умолч. `127.0.0.1:8000`) |
| `POLL_INTERVAL_SECONDS` | период опроса источников (сек.) |
| `SCRAPER_PROVIDER` | `scraperapi` / `scrapingbee` / `none` |
| `SCRAPER_API_KEY` | ключ облачного парсера |
| `SOURCE_PROXY_URL` | прокси для прямого парсинга Avito |
| `MAX_REQUESTS_PER_MINUTE` | бюджет запросов прямого парсинга |
| `ENABLE_MOCK_FALLBACK` | включить mock-данные, когда все источники пусты |
| `DUFF_WEBHOOK_SECRET` | секрет для подписи webhook от Duff89/parser_avito |

## Архитектура источников

Все источники реализуют один интерфейс `ListingsSource` (см.
`bot/app/sources/base.py`) и регистрируются в `SourceRegistry`. Регистр
работает по принципу **push-first → cloud → public → mock**:

1. **`DuffPushSource`** (key: `duff_push`) — читает буфер `pushed_listings`,
   куда внешний воркер (Duff89/`parser_avito`) кладёт свежие карточки через
   `POST /api/duff89/webhook`. Включается, только если задан
   `DUFF_WEBHOOK_SECRET`. Подписки сравниваются с буфером в момент чтения,
   так что один push обслуживает много пользователей с разными фильтрами.
2. **`AvitoCloudScrapeSource`** (key: `avito_cloud_scrape`) — облачный
   парсер (ScraperAPI/ScrapingBee). Несколько ретраев с экспоненциальным
   бэкоффом, классификация ошибок (`auth:401`, `rate_limited`, `5xx`,
   `timeout`, `captcha`, `empty`).
3. **`AvitoPublicWebSource`** (key: `avito_public_web`) — прямой парсинг с
   ротацией User-Agent, опциональным прокси и теми же reason-кодами.
4. **`MockListingsSource`** (key: `mock`) — последний рубеж. По умолчанию
   выключен; включается явно через `ENABLE_MOCK_FALLBACK=true`.

Если источник вернул не-ok причину, мы её не глотаем: ответ сохраняется в
`debug.primary` / `debug.fallback` и доступен через `/api/feed/live` и
`/api/source-status`.

## Endpoints (для Mini App)

| Метод | URL | Назначение |
| --- | --- | --- |
| `GET` | `/api/health` | health-check |
| `GET` | `/api/me` | профиль и роль текущего пользователя |
| `GET` | `/api/feed` | накопленная лента из БД |
| `GET` | `/api/feed/live` | живая выборка (push -> cloud -> public) |
| `GET` | `/api/source-status` | состояние источников (видно админу полностью) |
| `GET` | `/api/notifications` | последние уведомления |
| `POST` | `/api/work-status` | смена статуса в воронке |
| `GET/POST/PATCH/DELETE` | `/api/catalogs[/...]` | CRUD каталогов-фильтров |
| `GET` | `/api/categories` | список категорий |
| `GET` | `/api/cities?q=` | поиск населённых пунктов |
| `POST` | `/api/duff89/webhook` | приём батча от внешнего парсера |

`/api/feed/live` всегда возвращает поля `ok`, `reason`, `used_source`,
`user_message` и `debug`. Mini App использует их для понятного объяснения,
почему ленты сейчас нет (тайм-аут источника, captcha, не настроен ключ
облака и т.п.) и предлагает кнопку «Повторить запрос».

## Webhook Duff89 / `parser_avito`

Внешний воркер должен раз в N секунд опрашивать Avito (или другой источник)
и слать в наш API готовые карточки. Это снимает с бота нагрузку и обходит
анти-бот защиту в одном месте.

```
POST /api/duff89/webhook
X-Signature: sha256=<HMAC_SHA256(DUFF_WEBHOOK_SECRET, body)>
Content-Type: application/json

{
  "source": "avito",
  "items": [
    {
      "external_id": "1234567890",
      "url": "https://www.avito.ru/...",
      "title": "iPhone 12 64GB",
      "price": 25000,
      "city": "Москва",
      "category": "phones",
      "region": "moskva",
      "photo_url": "https://...",
      "description": "...",
      "seller_profile_url": "https://...",
      "published_at": "2026-05-07T22:00:00+00:00"
    }
  ]
}
```

Ответ: `{"ok": true, "inserted": N, "skipped": M}` (skipped — дубликаты по
`(source, external_id)`).

Что делается на стороне бота:

* Подпись проверяется через `hmac.compare_digest` — без секрета endpoint
  отдаёт 404, без подписи 401.
* Запись идёт в таблицу `pushed_listings`. На каждом вызове вычищаются
  записи старше 24 часов, чтобы буфер не разбухал.
* `DuffPushSource` забирает свежие записи, фильтрует по
  `category/region/query/price_min/price_max` каждой подписки и отдаёт в
  `SourceRegistry` как обычный источник.

Минимальный воркер должен ставить себе свой бэкофф и UA-rotation, чтобы
не словить блок Avito.

## Diagnostics

Админ-команда `/admin` в боте показывает версии, ENV, метрики
SourceRegistry. Mini App в режиме админа дополнительно рисует строку
«Источники: cloud=… · proxy=… · duff=…».

## Безопасность

* `bot/.env` и `.venv` в `.gitignore`. Никогда не комитить настоящие
  токены — если случайно закоммитили, сразу отзовите токен у @BotFather.
* Webhook требует HMAC-SHA256 подпись; без секрета endpoint выключен.
* Mini App-запросы валидируются через Telegram `initData` HMAC.
