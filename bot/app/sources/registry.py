from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from app.models import Subscription
from app.sources.base import ListingsSource


class SourceRegistry:
    def __init__(self, sources: list[ListingsSource]):
        self._by_key: dict[str, ListingsSource] = {s.key: s for s in sources}
        self._last_status: dict[str, str] = {
            "time": "",
            "source": "",
            "reason": "init",
            "items": "0",
        }

    @property
    def keys(self) -> list[str]:
        return sorted(self._by_key.keys())

    def get(self, key: str) -> ListingsSource:
        if key not in self._by_key:
            raise KeyError(f"Unknown source: {key}")
        return self._by_key[key]

    def as_mapping(self) -> Mapping[str, ListingsSource]:
        return dict(self._by_key)

    def last_status(self) -> Mapping[str, str]:
        return dict(self._last_status)

    def _set_status(self, *, source: str, reason: str, items: int) -> None:
        self._last_status = {
            "time": datetime.now(tz=timezone.utc).isoformat(),
            "source": source,
            "reason": reason,
            "items": str(items),
        }

    async def fetch_latest(self, sub: Subscription, limit: int):
        if sub.source == "avito_public_web":
            cloud = self._by_key.get("avito_cloud_scrape")
            if cloud:
                try:
                    items = await cloud.fetch_latest(sub, limit=limit)
                    if items:
                        self._set_status(source="avito_cloud_scrape", reason="ok", items=len(items))
                        return items
                    self._set_status(source="avito_cloud_scrape", reason="empty", items=0)
                except Exception as e:
                    self._set_status(source="avito_cloud_scrape", reason=f"error:{type(e).__name__}", items=0)

        primary = self.get(sub.source)
        try:
            items = await primary.fetch_latest(sub, limit=limit)
            if items:
                self._set_status(source=sub.source, reason="ok", items=len(items))
                return items
            self._set_status(source=sub.source, reason="empty", items=0)
        except Exception as e:
            self._set_status(source=sub.source, reason=f"error:{type(e).__name__}", items=0)
        fallback = self._by_key.get("mock_fallback")
        if fallback:
            items = await fallback.fetch_latest(sub, limit=limit)
            self._set_status(source="mock_fallback", reason="ok", items=len(items))
            return items
        return []

