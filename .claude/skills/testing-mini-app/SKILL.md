---
name: testing-mini-app
description: End-to-end test the avito-reseller-bot Mini App + FastAPI backend on the local box. Use when verifying real-feed flows, error-banner UX, source-classification, parser regressions, or any /api/feed* changes. Covers seeding real Avito items, minting Telegram initData, stubbing window.Telegram.WebApp, and the ScraperAPI captcha caveat.
---

# Testing the avito-reseller-bot Mini App end-to-end

The webapp is a Telegram Mini App. The backend is FastAPI + aiogram polling, but for testing we run **only** the FastAPI app — no Telegram polling. Mini App auth normally comes from `window.Telegram.WebApp.initData`; we mint a valid HMAC-SHA256-signed initData with the real `BOT_TOKEN` and inject it into a stubbed `window.Telegram.WebApp` so the React app passes the `verify_init_data` check on every request.

## Devin Secrets Needed

- **BOT_TOKEN** — Telegram bot token from @BotFather. Required to sign initData. The backend's `verify_init_data` uses the same token, so the SAME token must be set in the test rig and used by `mint_initdata.py`.
- **SCRAPER_API_KEY** — ScraperAPI key for the cloud source. Optional; the rig will still boot without it, but `/api/feed/live` will skip cloud and go straight to the public/mock fallback chain.

Both are user-scope. `request_secret` for either with `should_save=true, save_scope="user"` if they're missing.

## Helper scripts (committed in this directory)

- `scripts/mint_initdata.py` — prints a URL-encoded `query_id=...&user=...&auth_date=...&hash=...` string to stdout. Run as `BOT_TOKEN=$BOT_TOKEN python3 .claude/skills/testing-mini-app/scripts/mint_initdata.py > /tmp/init_data.txt`.
- `scripts/run_test_api.py` — wipes `/tmp/test_app.db`, creates user + catalog, optionally seeds real items from `/tmp/parsed_items.json` (parsed by `_extract_listings`), and starts uvicorn on `127.0.0.1:8000`. Reads `BOT_TOKEN` and `SCRAPER_API_KEY` from env.
- `scripts/patch_dist.py` — reads `/tmp/init_data.txt`, injects a `<script>` stub before the React module bundle in `bot/webapp/dist/index.html`, **and removes the `telegram.org/js/telegram-web-app.js` CDN script tag**. The CDN script must be removed because outside a real Telegram WebView it sets `initData=""`, which races with our stub and causes the React app to hang on "Инициализирую Mini App…" indefinitely.

## Recipe (full E2E)

```bash
cd /home/ubuntu/repos/avito-reseller-bot

# 0. Make sure node_modules CLIs are executable. The repo committed bin
#    files without +x so vite/tsc/esbuild fail with 'Permission denied'.
chmod +x bot/webapp/node_modules/.bin/*

# 1. Mint initData (signed with the real BOT_TOKEN).
BOT_TOKEN="$BOT_TOKEN" python3 .claude/skills/testing-mini-app/scripts/mint_initdata.py > /tmp/init_data.txt

# 2. (Optional but recommended) parse a real Avito HTML fixture so the DB has real cards.
#    Save a fresh /tmp/avito.html via:
#      curl -sL -A 'Mozilla/5.0 ...' https://www.avito.ru/moskva/telefony > /tmp/avito.html
#    Then run a one-liner that calls bot.app.sources.avito_public_web._extract_listings
#    and writes /tmp/parsed_items.json with title/price/url/external_id/photo_url/description.

# 3. Build the Mini App and patch the dist HTML with the Telegram stub.
(cd bot/webapp && node_modules/.bin/vite build)
python3 .claude/skills/testing-mini-app/scripts/patch_dist.py

# 4. Start the seeded API and a static server for the dist.
bot/.venv/bin/python .claude/skills/testing-mini-app/scripts/run_test_api.py &
(cd bot/webapp/dist && python3 -m http.server 5173 --bind 127.0.0.1) &
sleep 3

# 5. Smoke checks (no browser yet).
curl -s http://127.0.0.1:8000/api/health
curl -s -H "X-Telegram-Init-Data: $(cat /tmp/init_data.txt)" \
     http://127.0.0.1:8000/api/feed?limit=5
curl -s -H "X-Telegram-Init-Data: $(cat /tmp/init_data.txt)" \
     http://127.0.0.1:8000/api/source-status

# 6. Browser check (recorded if the change is UI-visible).
sudo apt-get install -y wmctrl 2>/dev/null
google-chrome http://127.0.0.1:5173/ &
sleep 2
wmctrl -a "Avito Reseller Mini App" && wmctrl -r :ACTIVE: -b add,maximized_vert,maximized_horz

# 7. Tear down.
pkill -f run_test_api.py
pkill -f "http.server 5173"
git checkout -- bot/webapp/dist/index.html bot/webapp/node_modules/.bin/
rm -f bot/webapp/tsconfig.tsbuildinfo /tmp/test_app.db
```

## Key gotchas (DO NOT skip)

1. **Remove the `telegram.org/js/telegram-web-app.js` CDN script tag in `dist/index.html`.** Adding a stub AFTER it isn't enough — the CDN script can race or be cached, and the page will hang on "Инициализирую Mini App…" with `initData=""`. `patch_dist.py` does this.
2. **The same `BOT_TOKEN` must be used to mint initData AND passed to `create_api_app(bot_token=...)`.** Otherwise `verify_init_data` returns `(False, "hmac_mismatch")` and every API call returns 401.
3. **`create_catalog` requires `source="avito"`** as a keyword argument. The bootstrap script does this; if you write your own, copy the call.
4. **ScraperAPI free tier currently returns 100% captcha on Avito.** This may or may not be true when you read this — check by probing `https://api.scraperapi.com/?api_key=$SCRAPER_API_KEY&url=...&render=false` directly. If it's still captcha-locked: that's expected, the test plan should accept EITHER cloud-success OR cloud-fail/fallback as a valid pass for `/api/feed/live`. The PR's friendly-banner UX is designed for exactly this case; the goal is to verify HTTP 200 + a Russian `user_message` + no raw `provider_auth:` / `cloud:…; public:…` tokens leaked into the UI.
5. **The `/api/feed/live` admin debug line `Использован источник: <name> · cloud:…; public:…` is gated by `userRole === "admin"`.** End-users only see the friendly text + retry button. The test rig user has `tg_user_id=8675309` and IS admin in the test DB (set by upsert_user when they're the only user). Don't flag this debug line as a leak — it's intentional.

## What a passing test pass looks like

- `/api/health` → 200, `status=="ok"`, ISO8601 timestamp.
- `/api/feed` → returns the seeded items with non-null titles/prices/photos.
- `/api/feed/live` (real key) → 200; either real cloud items OR friendly-RU `user_message` with `used_source=avito_public_web`.
- `/api/feed/live` (bogus key) → 200; `primary.reason="provider_auth:401"`; `user_message` is Russian, contains no `provider_auth:` / `cloud:` / `public:` / `RetryError` substrings.
- `/api/source-status` → 200 with the documented shape.
- Mini App at :5173 → real cards rendered, no boot-fallback ("Приложение не загрузилось"), no JS console errors.
- Click `Тест Avito (5)` chip → either `Live-источник: ок` or `Live-источник: проблема` banner with friendly RU + working `Повторить запрос` button.

## When this skill might be broken

- If `bot/webapp/dist/index.html` changes shape, `patch_dist.py`'s `marker = "<script type=\"module\""` may not match anymore — adjust the marker.
- If `app.repos.create_catalog` signature changes (it took `source` as keyword), update `run_test_api.py`.
- If `verify_init_data` changes the data_check_string canonicalization, update `mint_initdata.py` to match (currently sorts fields by key and joins with `\n`).
