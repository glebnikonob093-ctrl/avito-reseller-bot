# Pusher (Avito → бот) на Windows

Этот скрипт раз в N минут открывает каталоги Avito в headless-Chromium у тебя на компьютере, парсит карточки и шлёт их в бот через `POST /api/duff89/webhook`. Бот складывает их в буфер `pushed_listings`, а Mini App при клике «Тест Avito» сначала смотрит туда (`used_source=duff_push`) и только если пусто — лезет в ScraperAPI.

Зачем нужен этот pusher:

- Avito жёстко блокирует датацентровые IP (Railway, Vercel, ScraperAPI free) — отдаёт капчу.
- На твоём домашнем интернете и в реальном Chromium капчи почти нет.
- ScraperAPI становится резервным источником, не основным.

---

## 1. Что нужно

- Windows 10/11.
- Python 3.11+ ([скачать](https://www.python.org/downloads/windows/)). При установке поставь галку **Add Python to PATH**.
- Интернет (твой обычный, не VPN с серверного провайдера).
- Один раз — выставить **DUFF_WEBHOOK_SECRET** на Railway (см. шаг 2).

---

## 2. Один раз: общий секрет на Railway

Бот ничего не примет без секрета — webhook вернёт 404.

1. Сгенерируй случайную строку (~40 символов). Например в PowerShell:
   ```powershell
   -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | % {[char]$_})
   ```
2. Открой [Railway dashboard](https://railway.com/) → проект `avito-reseller-bot` → **Variables**.
3. Добавь переменную `DUFF_WEBHOOK_SECRET` = твоя случайная строка. Сохрани.
4. Дождись авто-пересборки (~1 минута). После этого `GET /api/source-status` должен показать `duff_webhook_enabled: true`.

---

## 3. Один раз: установка pusher

1. Открой `cmd.exe` или PowerShell, перейди в папку `pusher` репозитория:
   ```cmd
   cd C:\path\to\avito-reseller-bot\pusher
   ```
2. Запусти setup:
   ```cmd
   setup.bat
   ```
   Скрипт:
   - создаст виртуальное окружение `.venv`,
   - поставит зависимости (`playwright`, `requests`, `python-dotenv`),
   - скачает Chromium для Playwright (~150 МБ),
   - создаст `.env` и `targets.json` из примеров.

3. Открой `.env` в Блокноте и вставь:
   - `WEBHOOK_URL` = `https://avito-reseller-bot-bot.up.railway.app/api/duff89/webhook`
   - `DUFF_WEBHOOK_SECRET` = **тот же** что на Railway.

4. Открой `targets.json` в Блокноте и пропиши свои каталоги. Формат:
   ```json
   [
     {
       "label": "iPhone Москва 15-80к",
       "url": "https://www.avito.ru/moskva/telefony?q=iphone&pmin=15000&pmax=80000",
       "category": "telefony",
       "region": "moskva",
       "max_items": 30
     }
   ]
   ```
   ⚠️ Важно: `category` и `region` должны **точно совпадать** с теми, что у тебя в каталоге Mini App (см. вкладка «Каталоги»). Иначе бот не сматчит push с твоей подпиской и `Тест Avito` всё равно будет пустым.

---

## 4. Тестовый запуск

```cmd
run.bat
```

Что должно произойти:
- В консоли — лог типа:
  ```
  scraping target: iPhone Москва 15-80к (telefony, moskva)
    parsed 30 items from www.avito.ru
  pushed 30 items → inserted=27 skipped=3
  ```
- Открой бота → Mini App → нажми «Тест Avito (5)» — должны появиться реальные карточки. В баннере увидишь `Использован источник: duff_push`.

Если получил `webhook HTTP 404`:
- `DUFF_WEBHOOK_SECRET` не выставлен на Railway. См. шаг 2.

Если получил `webhook HTTP 401`:
- Секрет на Railway и в `.env` отличается. Перепроверь.

Если `parsed 0 items` для всех таргетов:
- Avito прислал капчу. Открой `.env`, поставь `HEADLESS=false`, запусти ещё раз — Chromium откроется с UI, посмотри что Avito показывает. Часто помогает один раз вручную залогиниться: профиль сохранится в `.browser-profile/` и следующие запуски пройдут без капчи.

---

## 5. Автозапуск через Task Scheduler

1. Открой **Task Scheduler** (Планировщик заданий).
2. **Create Task** (не Basic Task).
3. **General**:
   - Name: `Avito Pusher`
   - Run whether user is logged on or not — на твой выбор.
4. **Triggers** → **New**:
   - Begin: `On a schedule` → `Daily`, repeat every `15 minutes` for `1 day`. Или **At log on**, чтобы стартовало с логином.
5. **Actions** → **New**:
   - Action: `Start a program`
   - Program/script: `C:\path\to\avito-reseller-bot\pusher\run.bat`
   - Start in: `C:\path\to\avito-reseller-bot\pusher`  ⚠️ **обязательно** укажи Start in, иначе Python не найдёт `.env` и `targets.json`.
6. **Conditions**:
   - сними галку «Start the task only if the computer is on AC power», если работаешь от батареи.
7. Сохрани. Run Now → проверь что в Last Run Result у задачи `0x0` (успех).

Логи в консоль идут в stdout. Хочешь сохранять в файл — поправь `run.bat`:
```bat
python pusher.py >> logs\pusher.log 2>&1
```

---

## 6. Что писать в `targets.json`

Каждый объект = один URL Avito + явные `category` и `region` (обязательно те же, что в Mini App каталоге, иначе матча не будет). Поддерживаются любые URL поиска: с `?q=`, `?pmin=`, `?pmax=`, и т.д.

Совет: создавай столько таргетов, сколько у тебя каталогов в боте. Например на 5 каталогов = 5 объектов в `targets.json`. Один запуск pusher = один POST в webhook со всеми объявлениями.

---

## 7. Траблшутинг

| Симптом | Причина |
| --- | --- |
| `webhook HTTP 404 detail="Duff89 webhook is disabled"` | Не задан `DUFF_WEBHOOK_SECRET` на Railway. |
| `webhook HTTP 401 detail="Bad signature"` | Секрет в `.env` ≠ секрету на Railway. |
| `Chromium не запустился` (Playwright) | `python -m playwright install chromium` — повтори. |
| `parsed 0 items` для всех таргетов | Капча. Запусти с `HEADLESS=false`, реши капчу один раз — профиль сохранится. |
| `Тест Avito` всё равно пустой | `category`/`region` в `targets.json` не совпадают с каталогом в Mini App. Проверь буквально: `moskva` ≠ `moscow`. |

Если что-то непонятно — оставь комментарий на PR в GitHub, я смотрю.
