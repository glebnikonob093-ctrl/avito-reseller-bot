from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.models import Subscription
from app.sources.base import Listing, ListingsSource


class MockListingsSource(ListingsSource):
    key = "mock_fallback"

    async def fetch_latest(self, sub: Subscription, limit: int) -> list[Listing]:
        now = datetime.now(tz=timezone.utc)
        city = sub.region.replace("-", " ").title()
        cat = sub.category.replace("-", " ").title()
        q = (sub.query or "товар").strip()
        out: list[Listing] = []
        for i in range(max(1, limit)):
            price = random.randint(5000, 120000)
            ext = f"mock-{sub.id}-{int(now.timestamp())}-{i}"
            title = f"{cat}: {q} #{i + 1}"
            out.append(
                Listing(
                    external_id=ext,
                    url=f"https://example.com/mock/{ext}",
                    title=title,
                    price=price,
                    published_at=now - timedelta(minutes=i * 9),
                    city=city,
                    photo_url=f"https://picsum.photos/seed/{ext}/600/400",
                    description=f"Тестовая карточка {cat}. Запрос: {q}.",
                    seller_profile_url=f"https://example.com/seller/{sub.id}",
                    is_mock=True,
                )
            )
        return out

