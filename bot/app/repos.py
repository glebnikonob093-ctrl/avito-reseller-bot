from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SeenItem, Subscription, User, WorkItem

ADMIN_TG_IDS = {1200247714}


async def upsert_user(session: AsyncSession, tg_user_id: int, chat_id: int) -> User:
    q = select(User).where(User.tg_user_id == tg_user_id)
    user = (await session.execute(q)).scalar_one_or_none()
    if user:
        if user.chat_id != chat_id:
            user.chat_id = chat_id
        if not user.subscription_tier:
            user.subscription_tier = "free"
        if tg_user_id in ADMIN_TG_IDS and user.role != "admin":
            user.role = "admin"
        return user
    user = User(tg_user_id=tg_user_id, chat_id=chat_id)
    if tg_user_id in ADMIN_TG_IDS:
        user.role = "admin"
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
    display_name: str = "",
    is_selected: bool = True,
) -> Subscription:
    sub = Subscription(
        user_id=user_id,
        source=source,
        category=category,
        region=region,
        query=query,
        price_min=price_min,
        price_max=price_max,
        display_name=display_name,
        is_selected=is_selected,
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
    city: str | None = None,
    photo_url: str | None = None,
    description: str | None = None,
    seller_profile_url: str | None = None,
    is_mock: bool = False,
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
        city=city,
        photo_url=photo_url,
        description=description,
        seller_profile_url=seller_profile_url,
        is_mock=is_mock,
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
        .where(SeenItem.is_mock.is_(False))
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
                "city": it.city,
                "photo_url": it.photo_url,
                "description": it.description,
                "seller_profile_url": it.seller_profile_url,
                "is_mock": bool(it.is_mock),
                "deal_score": 0,
                "work_status": "new",
            }
        )
    return out


def _deal_score_for_seen(item: SeenItem, catalog: Subscription) -> float:
    score = 0.0
    if item.price is not None:
        if catalog.price_max is not None and item.price <= catalog.price_max:
            score += 25
        if catalog.price_min is not None and item.price >= catalog.price_min:
            score += 10
        if catalog.price_max is not None and catalog.price_min is not None:
            mid = (catalog.price_max + catalog.price_min) / 2
            score += max(0, 20 - abs(item.price - mid) / max(1, mid) * 20)
    if catalog.query and item.title:
        q = catalog.query.lower().strip()
        t = item.title.lower()
        if q and q in t:
            score += 30
    if item.is_mock:
        score -= 2
    return max(0.0, min(100.0, round(score, 2)))


async def list_feed_items_for_catalog(
    session: AsyncSession,
    *,
    user_id: int,
    catalog_id: int | None,
    sort_by: str,
    limit: int,
    min_deal_score: float | None = None,
    max_price: int | None = None,
    only_with_photo: bool = False,
    work_status: str | None = None,
) -> list[dict]:
    sub_q = select(Subscription).where(Subscription.user_id == user_id)
    if catalog_id is not None:
        sub_q = sub_q.where(Subscription.id == catalog_id)
    else:
        sub_q = sub_q.where(Subscription.is_selected.is_(True))
    catalogs = list((await session.execute(sub_q)).scalars().all())
    if not catalogs:
        return []

    catalog_ids = [c.id for c in catalogs]
    q = select(SeenItem).where(SeenItem.user_id == user_id).where(SeenItem.subscription_id.in_(catalog_ids))
    q = q.where(SeenItem.is_mock.is_(False))
    rows = list((await session.execute(q)).scalars().all())
    catalogs_by_id = {c.id: c for c in catalogs}
    raw_scores: dict[tuple[str, str], float] = {}
    for it in rows:
        raw_scores[(it.source, it.external_id)] = _deal_score_for_seen(it, catalogs_by_id[it.subscription_id])

    work_q = select(WorkItem).where(WorkItem.user_id == user_id)
    work_rows = list((await session.execute(work_q)).scalars().all())
    work_status_map = {(w.source, w.external_id): w.status for w in work_rows}

    filtered: list[SeenItem] = []
    for it in rows:
        key = (it.source, it.external_id)
        score = raw_scores[key]
        if min_deal_score is not None and score < float(min_deal_score):
            continue
        if max_price is not None and it.price is not None and it.price > int(max_price):
            continue
        if only_with_photo and not it.photo_url:
            continue
        if work_status and work_status_map.get(key, "new") != work_status:
            continue
        filtered.append(it)

    rows = filtered
    if sort_by == "best_deals":
        rows.sort(key=lambda it: raw_scores[(it.source, it.external_id)], reverse=True)
    else:
        rows.sort(key=lambda it: it.first_seen_at, reverse=True)
    rows = rows[:limit]

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
                "city": it.city,
                "photo_url": it.photo_url,
                "description": it.description,
                "seller_profile_url": it.seller_profile_url,
                "is_mock": bool(it.is_mock),
                "deal_score": raw_scores[(it.source, it.external_id)],
                "work_status": work_status_map.get((it.source, it.external_id), "new"),
            }
        )
    return out


async def set_work_item_status(
    session: AsyncSession, *, user_id: int, source: str, external_id: str, status: str
) -> dict:
    q = (
        select(WorkItem)
        .where(WorkItem.user_id == user_id)
        .where(WorkItem.source == source)
        .where(WorkItem.external_id == external_id)
    )
    existing = (await session.execute(q)).scalar_one_or_none()
    if existing:
        existing.status = status
        existing.updated_at = datetime.utcnow()
        await session.flush()
        return {"source": existing.source, "external_id": existing.external_id, "status": existing.status}
    obj = WorkItem(
        user_id=user_id,
        source=source,
        external_id=external_id,
        status=status,
        updated_at=datetime.utcnow(),
    )
    session.add(obj)
    await session.flush()
    return {"source": obj.source, "external_id": obj.external_id, "status": obj.status}


async def get_active_subscriptions(session: AsyncSession) -> list[tuple[User, Subscription]]:
    q = (
        select(User, Subscription)
        .join(Subscription, Subscription.user_id == User.id)
        .where(Subscription.is_paused.is_(False))
        .where(Subscription.is_selected.is_(True))
        .order_by(Subscription.id.asc())
    )
    rows = (await session.execute(q)).all()
    return [(row[0], row[1]) for row in rows]


async def list_catalogs(session: AsyncSession, *, user_id: int) -> list[Subscription]:
    q = select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.id.asc())
    return list((await session.execute(q)).scalars().all())


async def create_catalog(
    session: AsyncSession,
    *,
    user_id: int,
    source: str,
    display_name: str,
    category: str,
    region: str,
    query: str = "",
    price_min: int | None = None,
    price_max: int | None = None,
    select_now: bool = True,
) -> Subscription:
    if select_now:
        await session.execute(
            update(Subscription).where(Subscription.user_id == user_id).values(is_selected=False)
        )
    return await create_subscription(
        session,
        user_id=user_id,
        source=source,
        category=category,
        region=region,
        query=query,
        price_min=price_min,
        price_max=price_max,
        display_name=display_name.strip(),
        is_selected=select_now,
    )


async def update_catalog(
    session: AsyncSession,
    *,
    user_id: int,
    catalog_id: int,
    display_name: str | None = None,
    category: str | None = None,
    region: str | None = None,
    query: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    is_paused: bool | None = None,
) -> Subscription | None:
    sub = await get_subscription(session, user_id=user_id, subscription_id=catalog_id)
    if not sub:
        return None
    if display_name is not None:
        sub.display_name = display_name.strip()
    if category is not None:
        sub.category = category
    if region is not None:
        sub.region = region
    if query is not None:
        sub.query = query
    if price_min is not None:
        sub.price_min = price_min
    if price_max is not None:
        sub.price_max = price_max
    if is_paused is not None:
        sub.is_paused = is_paused
    await session.flush()
    return sub


async def select_catalog(session: AsyncSession, *, user_id: int, catalog_id: int) -> Subscription | None:
    sub = await get_subscription(session, user_id=user_id, subscription_id=catalog_id)
    if not sub:
        return None
    await session.execute(update(Subscription).where(Subscription.user_id == user_id).values(is_selected=False))
    await session.execute(
        update(Subscription).where(Subscription.id == catalog_id).where(Subscription.user_id == user_id).values(is_selected=True)
    )
    await session.flush()
    return await get_subscription(session, user_id=user_id, subscription_id=catalog_id)

