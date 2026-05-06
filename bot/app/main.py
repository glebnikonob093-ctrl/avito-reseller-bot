from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from dotenv import load_dotenv
import uvicorn
from telegram.ext import Application, ApplicationBuilder
from telegram.request import HTTPXRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, load_settings
from app.api import create_api_app
from app.db import create_engine, create_session_factory, ping_db
from app.handlers import register_handlers
from app.monitor import run_monitor_once
from app.migrations import create_all
from app.sources.avito_public_web import AvitoPublicWebSource
from app.sources.mock_listings import MockListingsSource
from app.sources.registry import SourceRegistry


def _ensure_sqlite_dir(database_url: str) -> None:
    if database_url.startswith("sqlite"):
        # sqlite+aiosqlite:///./data/app.db
        if "///" in database_url:
            path_str = database_url.split("///", 1)[1]
            if path_str.startswith("./"):
                path = Path(path_str).resolve()
            else:
                path = Path(path_str)
            path.parent.mkdir(parents=True, exist_ok=True)


async def _post_init(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]

    _ensure_sqlite_dir(settings.database_url)
    engine = create_engine(settings.database_url)
    await ping_db(engine)
    await create_all(engine)

    session_factory = create_session_factory(engine)
    sources = SourceRegistry(
        [
            AvitoPublicWebSource(
                max_requests_per_minute=settings.max_requests_per_minute,
                proxy_url=settings.source_proxy_url,
            ),
            MockListingsSource(),
        ]
    )

    app.bot_data["engine"] = engine
    app.bot_data["session_factory"] = session_factory
    app.bot_data["sources"] = sources

    # Mini App API server (FastAPI) in the same process.
    allowed_origins = [o for o in {settings.webapp_url, "http://localhost:5173"} if o]
    api_app = create_api_app(
        session_factory=session_factory,
        bot_token=settings.bot_token,
        allowed_origins=allowed_origins,
    )
    config = uvicorn.Config(
        api_app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        access_log=False,
    )
    api_server = uvicorn.Server(config)
    api_task = asyncio.create_task(api_server.serve())
    app.bot_data["api_server"] = api_server
    app.bot_data["api_task"] = api_task

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_monitor_once,
        trigger=IntervalTrigger(seconds=settings.poll_interval_seconds),
        args=[app],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    app.bot_data["scheduler"] = scheduler


async def _post_shutdown(app: Application) -> None:
    api_server = app.bot_data.get("api_server")
    api_task = app.bot_data.get("api_task")
    if api_server and api_task:
        try:
            api_server.should_exit = True
            await asyncio.wait_for(api_task, timeout=5)
        except Exception:
            pass

    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
    engine = app.bot_data.get("engine")
    sources: SourceRegistry | None = app.bot_data.get("sources")
    if sources:
        try:
            src = sources.get("avito_public_web")
            if hasattr(src, "aclose"):
                await src.aclose()  # type: ignore[func-returns-value]
        except Exception:
            pass
    if engine:
        await engine.dispose()


def main() -> None:
    # On Windows PowerShell, `.env` is often saved as UTF-8 with BOM.
    # `utf-8-sig` safely strips BOM so keys like BOT_TOKEN load correctly.
    bot_dir = Path(__file__).resolve().parents[1]
    load_dotenv(dotenv_path=bot_dir / ".env", encoding="utf-8-sig")

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    log = structlog.get_logger()

    settings = load_settings()
    bot_request = HTTPXRequest(proxy=settings.telegram_proxy_url or None)
    app: Application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .request(bot_request)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["settings"] = settings

    register_handlers(app)

    log.info("bot_start")
    # Python 3.14+ no longer creates an implicit default event loop.
    # python-telegram-bot expects an event loop to already be set.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(close_loop=False)

