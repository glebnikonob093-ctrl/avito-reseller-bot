from __future__ import annotations

from urllib.parse import urlencode

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.models import Subscription
from app.sources.avito_public_web import AvitoPublicWebSource
from app.sources.base import Listing, ListingsSource


class AvitoCloudScrapeSource(ListingsSource):
    key = "avito_cloud_scrape"

    def __init__(self, *, provider: str, api_key: str) -> None:
        self._provider = (provider or "").strip().lower()
        self._api_key = (api_key or "").strip()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(45.0))
        self._parser = AvitoPublicWebSource(max_requests_per_minute=20, proxy_url="")

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._parser.aclose()

    def _build_avito_urls(self, sub: Subscription) -> list[str]:
        # Mobile MFE state is the most reliable shape for our parser; desktop is a backup.
        return [self._parser._build_mobile_url(sub), self._parser._build_url(sub)]

    def _build_endpoint(self, avito_url: str) -> str:
        if self._provider != "scraperapi":
            raise RuntimeError(f"Unsupported scraper provider: {self._provider}")
        params = {
            "api_key": self._api_key,
            "url": avito_url,
            "country_code": "ru",
            "keep_headers": "true",
            # render=true asks the provider to wait for hydration so the
            # mfe-state JSON we rely on is present in the response HTML.
            "render": "true",
            "device_type": "mobile",
        }
        return f"https://api.scraperapi.com/?{urlencode(params)}"

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.0, min=1.0, max=8.0),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError)),
    )
    async def _fetch_html(self, avito_url: str) -> str:
        endpoint = self._build_endpoint(avito_url)
        r = await self._client.get(endpoint, follow_redirects=True)
        r.raise_for_status()
        return r.text

    def _classify_error(self, exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "provider_timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in {401, 403}:
                return f"provider_auth:{code}"
            if code == 429:
                return "provider_rate_limited"
            if 500 <= code < 600:
                return f"provider_5xx:{code}"
            return f"provider_http:{code}"
        return f"provider_error:{type(exc).__name__}"

    def _extract(self, html: str, *, limit: int) -> list[Listing]:
        items = self._parser._extract_from_mfe_state(html, limit=limit)
        if items:
            return items
        items = self._parser._extract_from_jsonld(html, limit=limit)
        if items:
            return items
        return self._parser._extract_listings(html, limit=limit)

    async def fetch_latest(self, sub: Subscription, limit: int) -> list[Listing]:
        if not self._api_key:
            return []
        last_error: Exception | None = None
        for url in self._build_avito_urls(sub):
            try:
                html = await self._fetch_html(url)
            except Exception as e:
                last_error = e
                continue
            items = self._extract(html, limit=limit)
            if items:
                return items
        if last_error is not None:
            raise last_error
        return []

    async def fetch_latest_with_debug(self, sub: Subscription, limit: int) -> tuple[list[Listing], dict]:
        if not self._api_key:
            return [], {"provider": self._provider, "reason": "missing_api_key"}
        attempts: list[dict] = []
        for url in self._build_avito_urls(sub):
            attempt: dict = {"url": url}
            try:
                html = await self._fetch_html(url)
            except Exception as e:
                attempt["reason"] = self._classify_error(e)
                attempts.append(attempt)
                continue
            attempt["blocked"] = "captcha" in html.lower()
            items = self._extract(html, limit=limit)
            if items:
                attempt["reason"] = "ok"
                return items, {"provider": self._provider, "reason": "ok", "attempts": attempts + [attempt]}
            attempt["reason"] = "captcha" if attempt["blocked"] else "empty"
            attempts.append(attempt)
        # Pick the most informative reason for callers.
        reason = "empty"
        for a in attempts:
            r = a.get("reason", "")
            if r and r != "empty":
                reason = r
                break
        return [], {"provider": self._provider, "reason": reason, "attempts": attempts}
