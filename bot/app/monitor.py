from __future__ import annotations

import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.ext import Application

from app.config import Settings
from app.repos import get_active_subscriptions, mark_seen
from app.scoring import deal_score
from app.sources.base import Listing
from app.sources.registry import SourceRegistry


log = structlog.get_logger()


def _format_listing(item: Listing) -> str:
    price = f"{item.price} ₽" if item.price is not None else "—"
    city = item.city or "—"
    tag = " [mock]" if item.is_mock else ""
    return f"🆕{tag} {item.title}\nГород: {city}\nЦена: {price}\n{item.url}"


async def run_monitor_once(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    session_factory: async_sessionmaker[AsyncSession] = app.bot_data["session_factory"]
    sources: SourceRegistry = app.bot_data["sources"]

    async with session_factory() as session:
        pairs = await get_active_subscriptions(session)
        await session.commit()

    if not pairs:
        return

    for user, sub in pairs:
        try:
            items = await sources.fetch_latest(sub, limit=settings.max_new_items_per_run)
        except Exception as e:
            log.warning("monitor_fetch_failed", sub_id=sub.id, err=str(e))
            continue

        # Rank by score (desc), but send from lower to higher so chat reads naturally
        ranked = sorted(items, key=lambda it: deal_score(sub, it), reverse=True)
        ranked = ranked[: settings.max_new_items_per_run]

        new_items: list[Listing] = []
        async with session_factory() as session:
            for it in ranked:
                is_new = await mark_seen(
                    session,
                    user_id=user.id,
                    subscription_id=sub.id,
                    source=sub.source,
                    external_id=it.external_id,
                    url=it.url,
                    title=it.title,
                    price=it.price,
                    city=it.city,
                    photo_url=it.photo_url,
                    description=it.description,
                    seller_profile_url=it.seller_profile_url,
                    is_mock=it.is_mock,
                )
                if is_new:
                    new_items.append(it)
            await session.commit()

        if not new_items:
            continue

        # Send oldest-first to reduce “spam feel”
        for it in reversed(new_items):
            try:
                await app.bot.send_message(
                    chat_id=user.chat_id,
                    text=_format_listing(it),
                    disable_web_page_preview=True,
                )
                await asyncio.sleep(0.2)
            except Exception as e:
                log.warning("monitor_notify_failed", user_id=user.id, sub_id=sub.id, err=str(e))
                break

