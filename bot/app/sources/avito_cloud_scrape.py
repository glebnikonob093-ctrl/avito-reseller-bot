from __future__ import annotations

from urllib.parse import quote_plus

import httpx

from app.models import Subscription
from app.sources.avito_public_web import AvitoPublicWebSource
from app.sources.base import Listing, ListingsSource


class AvitoCloudScrapeSource(ListingsSource):
    key = "avito_cloud_scrape"

    def __init__(self, *, provider: str, api_key: str) -> None:
        self._provider = (provider or "").strip().lower()
        self._api_key = (api_key or "").strip()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._parser = AvitoPublicWebSource(max_requests_per_minute=20, proxy_url="")

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._parser.aclose()

    def _build_avito_url(self, sub: Subscription) -> str:
        return self._parser._build_mobile_url(sub)

    async def _fetch_html(self, avito_url: str) -> str:
        if self._provider != "scraperapi":
            raise RuntimeError(f"Unsupported scraper provider: {self._provider}")
        endpoint = (
            "https://api.scraperapi.com/"
            f"?api_key={quote_plus(self._api_key)}"
            f"&url={quote_plus(avito_url)}"
            "&country_code=ru"
            "&keep_headers=true"
        )
        r = await self._client.get(endpoint, follow_redirects=True)
        r.raise_for_status()
        return r.text

    async def fetch_latest(self, sub: Subscription, limit: int) -> list[Listing]:
        if not self._api_key:
            return []
        html = await self._fetch_html(self._build_avito_url(sub))
        items = self._parser._extract_from_jsonld(html, limit=limit)
        if items:
            return items
        return self._parser._extract_listings(html, limit=limit)

    async def fetch_latest_with_debug(self, sub: Subscription, limit: int) -> tuple[list[Listing], dict]:
        if not self._api_key:
            return [], {"provider": self._provider, "reason": "missing_api_key"}
        try:
            html = await self._fetch_html(self._build_avito_url(sub))
            blocked = "captcha" in html.lower()
            items = self._parser._extract_from_jsonld(html, limit=limit)
            if not items:
                items = self._parser._extract_listings(html, limit=limit)
            if items:
                return items, {"provider": self._provider, "blocked": blocked, "reason": "ok"}
            return [], {"provider": self._provider, "blocked": blocked, "reason": "empty"}
        except Exception as e:
            return [], {"provider": self._provider, "reason": f"provider_error:{type(e).__name__}"}
