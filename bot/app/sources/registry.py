from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from app.models import Subscription
from app.sources.base import ListingsSource


class SourceRegistry:
    def __init__(self, sources: list[ListingsSource], *, enable_mock_fallback: bool = True):
        self._by_key: dict[str, ListingsSource] = {s.key: s for s in sources}
        self._enable_mock_fallback = enable_mock_fallback
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
        cloud_attempted = False
        cloud_reason = ""
        if sub.source == "avito_public_web":
            cloud = self._by_key.get("avito_cloud_scrape")
            if cloud:
                cloud_attempted = True
                has_cloud_key = bool(getattr(cloud, "_api_key", "").strip())
                if not has_cloud_key:
                    cloud_reason = "missing_api_key"
                try:
                    items = await cloud.fetch_latest(sub, limit=limit)
                    if items:
                        self._set_status(source="avito_cloud_scrape", reason="ok", items=len(items))
                        return items
                    if not cloud_reason:
                        cloud_reason = "empty"
                except Exception as e:
                    cloud_reason = f"error:{type(e).__name__}"

        primary = self.get(sub.source)
        primary_reason = ""
        try:
            items = await primary.fetch_latest(sub, limit=limit)
            if items:
                self._set_status(source=sub.source, reason="ok", items=len(items))
                return items
            primary_reason = "empty"
        except Exception as e:
            primary_reason = f"error:{type(e).__name__}"
        fallback = self._by_key.get("mock_fallback")
        if fallback and self._enable_mock_fallback:
            items = await fallback.fetch_latest(sub, limit=limit)
            self._set_status(source="mock_fallback", reason="ok", items=len(items))
            return items
        if cloud_attempted:
            merged_reason = f"cloud_{cloud_reason or 'empty'}; public_{primary_reason or 'no_data'}"
            self._set_status(source="avito_cloud_scrape", reason=merged_reason, items=0)
        else:
            self._set_status(source=sub.source, reason="no_real_data", items=0)
        return []

