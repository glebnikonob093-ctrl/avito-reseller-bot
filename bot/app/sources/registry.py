from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

import httpx

from app.models import Subscription
from app.sources.base import ListingsSource


def _classify_exception(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in {401, 403}:
            return f"auth:{code}"
        if code == 429:
            return "rate_limited"
        if 500 <= code < 600:
            return f"server_error:{code}"
        return f"http:{code}"
    name = type(e).__name__
    if "ConnectError" in name or "RemoteProtocol" in name or "ReadError" in name:
        return f"network:{name}"
    return f"error:{name}"


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
        # 1. Try a push-style source first (e.g. Duff89 webhook ingest) if available.
        #    This is preferred because it bypasses Avito's anti-bot measures entirely.
        push = self._by_key.get("duff_push")
        if push is not None:
            try:
                items = await push.fetch_latest(sub, limit=limit)
                if items:
                    self._set_status(source="duff_push", reason="ok", items=len(items))
                    return items
            except Exception:
                # If the push buffer fails, fall through to scraping sources.
                pass

        cloud_attempted = False
        cloud_reason = ""
        if sub.source == "avito_public_web":
            cloud = self._by_key.get("avito_cloud_scrape")
            if cloud is not None:
                cloud_attempted = True
                has_cloud_key = bool(getattr(cloud, "_api_key", "").strip())
                if not has_cloud_key:
                    cloud_reason = "missing_api_key"
                else:
                    try:
                        items = await cloud.fetch_latest(sub, limit=limit)
                        if items:
                            self._set_status(source="avito_cloud_scrape", reason="ok", items=len(items))
                            return items
                        cloud_reason = "empty"
                    except Exception as e:
                        cloud_reason = _classify_exception(e)

        primary = self.get(sub.source)
        primary_reason = ""
        try:
            items = await primary.fetch_latest(sub, limit=limit)
            if items:
                self._set_status(source=sub.source, reason="ok", items=len(items))
                return items
            primary_reason = "empty"
        except Exception as e:
            primary_reason = _classify_exception(e)

        fallback = self._by_key.get("mock_fallback")
        if fallback is not None and self._enable_mock_fallback:
            items = await fallback.fetch_latest(sub, limit=limit)
            self._set_status(source="mock_fallback", reason="ok", items=len(items))
            return items
        if cloud_attempted:
            merged_reason = f"cloud:{cloud_reason or 'empty'}; public:{primary_reason or 'no_data'}"
            self._set_status(source="avito_cloud_scrape", reason=merged_reason, items=0)
        else:
            self._set_status(source=sub.source, reason=f"public:{primary_reason or 'no_real_data'}", items=0)
        return []

