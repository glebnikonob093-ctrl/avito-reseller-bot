from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    poll_interval_seconds: int
    max_new_items_per_run: int
    default_region: str
    default_category: str
    max_requests_per_minute: int
    webapp_url: str
    api_host: str
    api_port: int
    telegram_proxy_url: str
    source_proxy_url: str
    scraper_provider: str
    scraper_api_key: str
    enable_mock_fallback: bool
    duff_webhook_secret: str


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required (set it in bot/.env)")
    return Settings(
        bot_token=bot_token,
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/app.db"),
        poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS", 180),
        max_new_items_per_run=_get_int("MAX_NEW_ITEMS_PER_RUN", 15),
        default_region=os.getenv("DEFAULT_REGION", "moskva"),
        default_category=os.getenv("DEFAULT_CATEGORY", "telefony"),
        max_requests_per_minute=_get_int("MAX_REQUESTS_PER_MINUTE", 20),
        webapp_url=os.getenv("WEBAPP_URL", ""),
        api_host=os.getenv("API_HOST", "127.0.0.1"),
        api_port=_get_int("API_PORT", 8000),
        telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL", ""),
        source_proxy_url=os.getenv("SOURCE_PROXY_URL", ""),
        scraper_provider=os.getenv("SCRAPER_PROVIDER", "scraperapi").strip().lower(),
        scraper_api_key=os.getenv("SCRAPER_API_KEY", "").strip(),
        enable_mock_fallback=_get_bool("ENABLE_MOCK_FALLBACK", False),
        duff_webhook_secret=os.getenv("DUFF_WEBHOOK_SECRET", "").strip(),
    )

