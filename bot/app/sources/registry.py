from __future__ import annotations

from collections.abc import Mapping

from app.models import Subscription
from app.sources.base import ListingsSource


class SourceRegistry:
    def __init__(self, sources: list[ListingsSource]):
        self._by_key: dict[str, ListingsSource] = {s.key: s for s in sources}

    @property
    def keys(self) -> list[str]:
        return sorted(self._by_key.keys())

    def get(self, key: str) -> ListingsSource:
        if key not in self._by_key:
            raise KeyError(f"Unknown source: {key}")
        return self._by_key[key]

    def as_mapping(self) -> Mapping[str, ListingsSource]:
        return dict(self._by_key)

    async def fetch_latest(self, sub: Subscription, limit: int):
        primary = self.get(sub.source)
        try:
            items = await primary.fetch_latest(sub, limit=limit)
            if items:
                return items
        except Exception:
            pass
        fallback = self._by_key.get("mock_fallback")
        if fallback:
            return await fallback.fetch_latest(sub, limit=limit)
        return []

