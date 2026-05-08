# Avito Pusher

Local script that scrapes Avito catalogs in headless Chromium (Playwright) and
POSTs them to the bot's `/api/duff89/webhook` with HMAC-SHA256.

Why local: Avito blocks datacenter IPs (Railway, ScraperAPI free) hard. A
residential IP + real browser bypasses ~all of that.

## Quick start (Windows)

See [README_WINDOWS.md](./README_WINDOWS.md) for the full step-by-step guide
(including Task Scheduler setup).

## Quick start (Linux/macOS)

```bash
cd pusher
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env  # then edit
cp targets.example.json targets.json  # then edit
python pusher.py
```

## Required env vars

- `WEBHOOK_URL` — full URL of the bot's webhook
  (e.g. `https://avito-reseller-bot-bot.up.railway.app/api/duff89/webhook`)
- `DUFF_WEBHOOK_SECRET` — must match the bot's `DUFF_WEBHOOK_SECRET` env var
  (set on Railway → Service → Variables).

## targets.json

Array of objects. Each object describes one Avito catalog URL:

```json
{
  "label": "iPhone Moscow",
  "url": "https://www.avito.ru/moskva/telefony?q=iphone&pmin=15000&pmax=80000",
  "category": "telefony",
  "region": "moskva",
  "max_items": 30
}
```

`category` and `region` must match your Mini App catalog filters exactly. The
bot's `DuffPushSource` filters the buffer by them at fetch time so a single
push can serve multiple users.

## Idempotency

The webhook dedupes on `(source, external_id)` — re-running the pusher won't
create duplicates. Buffer entries older than 24h are pruned automatically by
the bot.
