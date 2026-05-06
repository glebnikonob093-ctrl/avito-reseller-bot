# Avito Reseller Telegram Bot (MVP)

Telegram-бот, который позволяет пользователю создать подписки на категории/поисковые запросы, получать список **самых новых** объявлений и уведомления о новых позициях. Источник объявлений реализован через **адаптер** (можно заменить реализацию без переделки бота).

## Быстрый старт (Windows / PowerShell)

1) Создайте бота у `@BotFather` и получите токен.

2) В корне папки `bot/` создайте файл `.env`:

```env
BOT_TOKEN=123456:ABCDEF
DATABASE_URL=sqlite+aiosqlite:///./data/app.db
POLL_INTERVAL_SECONDS=180
MAX_NEW_ITEMS_PER_RUN=15
DEFAULT_REGION=moskva
DEFAULT_CATEGORY=telefony
MAX_REQUESTS_PER_MINUTE=20

# Mini App (необязательно)
WEBAPP_URL=
API_HOST=127.0.0.1
API_PORT=8000
TELEGRAM_PROXY_URL=
SOURCE_PROXY_URL=
```

3) Установите зависимости:

```powershell
cd bot
python -m pip install -r requirements.txt
```

4) Запустите:

```powershell
python -m app
```

## Mini App (Telegram WebApp)

В меню бота есть кнопка **«Лента (Mini App)»**. Она откроет веб-приложение внутри Telegram и покажет ленту новых объявлений (по данным из `seen_items`).

### Локальная разработка через туннель (Cloudflare Tunnel)

Mini App должен открываться по **публичному HTTPS URL**, поэтому для локальной разработки используем туннель.

1) Установите зависимости Python (API поднимается вместе с ботом):

```powershell
cd bot
python -m pip install -r requirements.txt
```

2) Запустите фронтенд:

```powershell
cd bot\webapp
npm install
npm run dev
```

3) Поставьте `cloudflared` (один раз) и поднимите туннели:

- **Туннель на фронтенд** (Vite):

```powershell
cloudflared tunnel --url http://localhost:5173
```

- **Туннель на API** (FastAPI внутри бота):
  - В `.env` установите `API_HOST=0.0.0.0` (чтобы API слушал не только localhost).
  - Затем:

```powershell
cloudflared tunnel --url http://localhost:8000
```

4) В `.env` укажите:
- `WEBAPP_URL=<https-URL_туннеля_на_5173>`
- Для фронтенда задайте, куда ходить за API (в новом окне PowerShell):

```powershell
cd bot\webapp
$env:VITE_API_BASE="<https-URL_туннеля_на_8000>"
npm run dev
```

5) В `@BotFather` (на стороне Telegram) разрешите домен Mini App:
- Откройте `@BotFather` → настройка бота → **Web App / Domain** (названия пунктов могут отличаться) → добавьте домен из `WEBAPP_URL`.

6) Запустите бота:

```powershell
cd bot
python -m app
```

7) В Telegram:
- Открой бота → `/start` (чтобы пользователь появился в БД)
- Нажми **«Лента (Mini App)»**

### Ошибка 530 в Mini App (Cloudflare)

Если в Mini App видно `530 The origin has been unregistered from Argo Tunnel`, это значит, что quick tunnel завершился/пересоздался и старый URL больше не живой.

Что делать:
- Держите процессы туннеля запущенными в отдельных окнах.
- Запускайте туннели через HTTP/2 (в сетях, где QUIC режется):

```powershell
cloudflared tunnel --protocol http2 --url http://localhost:5173
cloudflared tunnel --protocol http2 --url http://localhost:8000
```

- После получения новых URL обновите:
  - `WEBAPP_URL` в `.env`
  - `VITE_API_BASE` для `webapp`
- Перезапустите фронт и бота.

### Прокси для бота (HTTP/SOCKS)

Если сеть блокирует доступ к Telegram API (`api.telegram.org:443`), включите прокси:

```env
TELEGRAM_PROXY_URL=http://127.0.0.1:7890
# или
TELEGRAM_PROXY_URL=socks5://127.0.0.1:1080
```

Опционально, для запросов к источнику объявлений:

```env
SOURCE_PROXY_URL=http://127.0.0.1:7890
```

Затем перезапустите бота:

```powershell
python -m app
```

## Команды бота
- `/start` — старт и главное меню
- `/subs` — мои подписки

## Как работает мониторинг
- Для каждой подписки периодически запрашивается источник объявлений.
- Новые объявления дедуплицируются по `external_id` (или ссылке).
- Бот отправляет уведомления в чат пользователя.

## Важное про Авито
Авито может ограничивать автоматизированный доступ к публичным страницам. Этот репозиторий реализует **плагинную** архитектуру источника данных, чтобы при необходимости заменить способ получения объявлений (на официальный API/интеграцию или иной легальный источник) без переписывания логики бота.

