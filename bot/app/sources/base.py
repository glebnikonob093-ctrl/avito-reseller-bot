from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models import Subscription


@dataclass(frozen=True)
class Listing:
    external_id: str
    url: str
    title: str
    price: int | None
    published_at: datetime | None = None


class ListingsSource:
    key: str

    async def fetch_latest(self, sub: Subscription, limit: int) -> list[Listing]:
        raise NotImplementedError

