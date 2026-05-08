"""Boot the FastAPI app standalone (no Telegram polling) for E2E testing.

Listens on 127.0.0.1:8000 with a fresh SQLite at /tmp/test_app.db.
If /tmp/parsed_items.json exists (parsed real Avito listings), seeds 5 of them
into seen_items so /api/feed returns real cards even when the cloud source is
captcha-locked.

Usage:
    BOT_TOKEN="$BOT_TOKEN" SCRAPER_API_KEY="$SCRAPER_API_KEY" \\
        bot/.venv/bin/python .claude/skills/testing-mini-app/scripts/run_test_api.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[4] / "bot"
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from app.api import create_api_app  # noqa: E402
from app.db import create_engine, create_session_factory, ping_db  # noqa: E402
from app.migrations import create_all  # noqa: E402
from app.models import SeenItem  # noqa: E402
from app.repos import create_catalog, list_catalogs, upsert_user  # noqa: E402

DB_PATH = "/tmp/test_app.db"
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"
TG_USER_ID = 8675309  # must match mint_initdata.py


async def bootstrap_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    engine = create_engine(DB_URL)
    await ping_db(engine)
    await create_all(engine)
    sf = create_session_factory(engine)
    parsed_path = "/tmp/parsed_items.json"
    items = json.load(open(parsed_path)) if os.path.exists(parsed_path) else []
    async with sf() as session:
        user = await upsert_user(session, tg_user_id=TG_USER_ID, chat_id=TG_USER_ID)
        await create_catalog(
            session,
            user_id=user.id,
            source="avito",
            display_name="iPhone Moscow",
            category="telefony",
            region="moskva",
            query="iphone",
            select_now=True,
        )
        await session.flush()
        catalogs = await list_catalogs(session, user_id=user.id)
        sub = catalogs[0]
        # Seed up to 5 fresh items spaced 1 minute apart so newest sort
        # returns the parsed list in expected order.
        now = datetime.utcnow()
        for i, it in enumerate(items[:5]):
            session.add(
                SeenItem(
                    user_id=user.id,
                    subscription_id=sub.id,
                    source="avito_public_web",
                    external_id=str(it["external_id"]),
                    url=str(it["url"]),
                    title=str(it["title"]),
                    price=int(it["price"]) if it.get("price") is not None else None,
                    city=it.get("city"),
                    photo_url=it.get("photo_url"),
                    description=it.get("description"),
                    is_mock=False,
                    first_seen_at=now - timedelta(minutes=i),
                )
            )
        await session.commit()
        print(
            f"[run_test_api] seeded {min(5, len(items))} items into seen_items",
            flush=True,
        )
    return engine, sf


async def main():
    bot_token = os.environ["BOT_TOKEN"]
    cloud_key = os.environ.get("SCRAPER_API_KEY", "")
    engine, sf = await bootstrap_db()
    api_app = create_api_app(
        session_factory=sf,
        bot_token=bot_token,
        allowed_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
        ],
        source_proxy_url="",
        max_requests_per_minute=20,
        scraper_provider="scraperapi",
        scraper_api_key=cloud_key,
        duff_webhook_secret="",
    )
    config = uvicorn.Config(
        api_app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    print("[run_test_api] API ready on :8000", flush=True)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
