from __future__ import annotations

import re
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models import Subscription
from app.sources.base import Listing, ListingsSource
from app.sources.rate_limit import SimpleRateLimiter


class AvitoPublicWebSource(ListingsSource):
    """
    MVP implementation that reads public search pages.

    NOTE: Public pages and markup can change; access can be rate-limited/blocked.
    Keep this behind an adapter to allow swapping to a legal API/integration later.
    """

    key = "avito_public_web"

    def __init__(self, *, max_requests_per_minute: int = 20, proxy_url: str = "") -> None:
        self._limiter = SimpleRateLimiter(max_per_minute=max_requests_per_minute)
        proxy = proxy_url.strip() or None
        self._client = httpx.AsyncClient(
            proxy=proxy,
            timeout=httpx.Timeout(20.0),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AvitoResellerBot/0.1; +https://example.invalid)"
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _build_url(self, sub: Subscription) -> str:
        # Avito URL scheme varies; we use a conservative pattern:
        # https://www.avito.ru/{region}/{category}?q=...&pmin=...&pmax=...&s=104 (sort by date)
        base = f"https://www.avito.ru/{sub.region}/{sub.category}"
        params: dict[str, str] = {"s": "104"}  # 104: by date (newest)
        if sub.query:
            params["q"] = sub.query
        if sub.price_min is not None:
            params["pmin"] = str(sub.price_min)
        if sub.price_max is not None:
            params["pmax"] = str(sub.price_max)
        return f"{base}?{urlencode(params)}"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=6))
    async def _get_html(self, url: str) -> str:
        await self._limiter.wait()
        r = await self._client.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.text

    def _parse_price(self, text: str) -> int | None:
        digits = re.sub(r"[^\d]", "", text or "")
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    def _extract_listings(self, html: str, limit: int) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")

        # Avito changes markup; we attempt a few heuristics:
        anchors = soup.select('a[href*="/items/"]') or soup.select('a[data-marker="item-title"]')
        seen_urls: set[str] = set()
        out: list[Listing] = []
        for a in anchors:
            href = a.get("href") or ""
            if not href:
                continue
            if not href.startswith("http"):
                href = "https://www.avito.ru" + href
            if href in seen_urls:
                continue
            seen_urls.add(href)

            title = (a.get_text(" ", strip=True) or "").strip()
            if not title:
                continue

            # external_id heuristic: try to find numeric id in URL, else use URL
            m = re.search(r"/(\d+)(?:\?|$)", href)
            external_id = m.group(1) if m else href

            # try to locate a price nearby
            price = None
            parent = a.parent
            if parent:
                price_el = parent.select_one('[data-marker="item-price"]') or parent.select_one(
                    '[data-marker="item-price"] span'
                )
                if price_el:
                    price = self._parse_price(price_el.get_text(" ", strip=True))

            out.append(Listing(external_id=external_id, url=href, title=title, price=price))
            if len(out) >= limit:
                break
        return out

    async def fetch_latest(self, sub: Subscription, limit: int) -> list[Listing]:
        url = self._build_url(sub)
        html = await self._get_html(url)
        return self._extract_listings(html, limit=limit)

