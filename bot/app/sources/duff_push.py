from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models import PushedListing, Subscription
from app.sources.base import Listing, ListingsSource


class DuffPushSource(ListingsSource):
    """Read-only source backed by the `pushed_listings` buffer.

    Items are written into the buffer by the external Duff89/parser_avito worker
    via `POST /api/duff89/webhook`. At read time we filter by the subscription's
    region/category/query/price range so a single push can serve many users.
    """

    key = "duff_push"

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _matches(item: PushedListing, sub: Subscription) -> bool:
        if sub.category and item.category and item.category != sub.category:
            return False
        if sub.region and item.region and item.region != sub.region:
            return False
        if sub.query:
            q = sub.query.lower().strip()
            if q:
                title = (item.title or "").lower()
                desc = (item.description or "").lower()
                if q not in title and q not in desc:
                    return False
        if sub.price_min is not None and item.price is not None and item.price < sub.price_min:
            return False
        if sub.price_max is not None and item.price is not None and item.price > sub.price_max:
            return False
        return True

    async def fetch_latest(self, sub: Subscription, limit: int) -> list[Listing]:
        async with self._session_factory() as session:
            stmt = (
                select(PushedListing)
                .order_by(PushedListing.received_at.desc())
                .limit(max(1, min(200, int(limit) * 20)))
            )
            rows = list((await session.execute(stmt)).scalars().all())
        out: list[Listing] = []
        for it in rows:
            if not self._matches(it, sub):
                continue
            out.append(
                Listing(
                    external_id=it.external_id,
                    url=it.url,
                    title=it.title or "",
                    price=it.price,
                    published_at=it.published_at or it.received_at,
                    city=it.city,
                    photo_url=it.photo_url,
                    description=it.description,
                    seller_profile_url=it.seller_profile_url,
                    is_mock=False,
                )
            )
            if len(out) >= limit:
                break
        return out
