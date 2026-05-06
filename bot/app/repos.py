from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SeenItem, Subscription, User


async def upsert_user(session: AsyncSession, tg_user_id: int, chat_id: int) -> User:
    q = select(User).where(User.tg_user_id == tg_user_id)
    user = (await session.execute(q)).scalar_one_or_none()
    if user:
        if user.chat_id != chat_id:
            user.chat_id = chat_id
        return user
    user = User(tg_user_id=tg_user_id, chat_id=chat_id)
    session.add(user)
    await session.flush()
    return user


async def list_subscriptions(session: AsyncSession, user_id: int) -> list[Subscription]:
    q = select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.id.asc())
    return list((await session.execute(q)).scalars().all())


async def get_subscription(session: AsyncSession, *, user_id: int, subscription_id: int) -> Subscription | None:
    q = (
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .where(Subscription.id == subscription_id)
    )
    return (await session.execute(q)).scalar_one_or_none()


async def create_subscription(
    session: AsyncSession,
    *,
    user_id: int,
    source: str,
    category: str,
    region: str,
    query: str = "",
    price_min: int | None = None,
    price_max: int | None = None,
) -> Subscription:
    sub = Subscription(
        user_id=user_id,
        source=source,
        category=category,
        region=region,
        query=query,
        price_min=price_min,
        price_max=price_max,
    )
    session.add(sub)
    await session.flush()
    return sub


async def set_subscription_paused(
    session: AsyncSession, *, subscription_id: int, is_paused: bool
) -> None:
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(is_paused=is_paused)
    )


async def delete_subscription(session: AsyncSession, *, subscription_id: int) -> None:
    # seen items will remain unless we delete them; for MVP we remove both
    await session.execute(delete(SeenItem).where(SeenItem.subscription_id == subscription_id))
    await session.execute(delete(Subscription).where(Subscription.id == subscription_id))


async def mark_seen(
    session: AsyncSession,
    *,
    user_id: int,
    subscription_id: int,
    source: str,
    external_id: str,
    url: str,
    title: str | None = None,
    price: int | None = None,
) -> bool:
    """
    Returns True if item was newly inserted, False if it already existed.
    """
    seen = SeenItem(
        user_id=user_id,
        subscription_id=subscription_id,
        source=source,
        external_id=external_id,
        url=url,
        title=title,
        price=price,
    )
    try:
        async with session.begin_nested():
            session.add(seen)
            await session.flush()
        return True
    except IntegrityError:
        return False


async def get_user_by_tg_user_id(session: AsyncSession, *, tg_user_id: int) -> User | None:
    q = select(User).where(User.tg_user_id == tg_user_id)
    return (await session.execute(q)).scalar_one_or_none()


async def list_feed_items(session: AsyncSession, *, user_id: int, limit: int) -> list[dict]:
    q = (
        select(SeenItem)
        .where(SeenItem.user_id == user_id)
        .order_by(SeenItem.first_seen_at.desc())
        .limit(limit)
    )
    rows = list((await session.execute(q)).scalars().all())
    out: list[dict] = []
    for it in rows:
        out.append(
            {
                "title": it.title,
                "price": it.price,
                "url": it.url,
                "first_seen_at": it.first_seen_at.isoformat() if it.first_seen_at else None,
                "subscription_id": it.subscription_id,
                "external_id": it.external_id,
                "source": it.source,
            }
        )
    return out


async def get_active_subscriptions(session: AsyncSession) -> list[tuple[User, Subscription]]:
    q = (
        select(User, Subscription)
        .join(Subscription, Subscription.user_id == User.id)
        .where(Subscription.is_paused.is_(False))
        .order_by(Subscription.id.asc())
    )
    rows = (await session.execute(q)).all()
    return [(row[0], row[1]) for row in rows]

