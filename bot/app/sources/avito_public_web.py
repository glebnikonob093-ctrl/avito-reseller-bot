from __future__ import annotations

import json
import re
from html import unescape
from random import randint
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

    _USER_AGENTS = (
        # Modern iOS Safari
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        # Modern Android Chrome
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36",
        # Modern desktop Chrome
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    )

    def __init__(self, *, max_requests_per_minute: int = 20, proxy_url: str = "") -> None:
        self._limiter = SimpleRateLimiter(max_per_minute=max_requests_per_minute)
        raw = (proxy_url or "").replace(";", ",")
        self._proxies = [p.strip() for p in raw.split(",") if p.strip()]
        # Always include a direct-connection fallback so the source still tries
        # without a proxy when none is configured (or all configured ones fail).
        self._proxies.append("")
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._ua_index = 0

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()

    def _next_user_agent(self) -> str:
        ua = self._USER_AGENTS[self._ua_index % len(self._USER_AGENTS)]
        self._ua_index += 1
        return ua

    def _get_client(self, proxy: str) -> httpx.AsyncClient:
        key = proxy.strip()
        if key in self._clients:
            return self._clients[key]
        self._clients[key] = httpx.AsyncClient(
            proxy=(key or None),
            timeout=httpx.Timeout(25.0),
            headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        return self._clients[key]

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

    def _build_mobile_url(self, sub: Subscription) -> str:
        base = f"https://m.avito.ru/{sub.region}/{sub.category}"
        params: dict[str, str] = {"s": "104"}
        if sub.query:
            params["q"] = sub.query
        if sub.price_min is not None:
            params["pmin"] = str(sub.price_min)
        if sub.price_max is not None:
            params["pmax"] = str(sub.price_max)
        return f"{base}?{urlencode(params)}"

    def _looks_blocked(self, html: str) -> bool:
        low = html.lower()
        return ("captcha" in low) or ("доступ ограничен" in low) or ("access denied" in low)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=6))
    async def _get_html(self, url: str) -> str:
        await self._limiter.wait()
        if not self._proxies:
            raise RuntimeError("No proxy routes configured")
        start = randint(0, max(0, len(self._proxies) - 1))
        last_error: Exception | None = None
        for i in range(len(self._proxies)):
            proxy = self._proxies[(start + i) % len(self._proxies)]
            try:
                client = self._get_client(proxy)
                r = await client.get(
                    url,
                    follow_redirects=True,
                    headers={"User-Agent": self._next_user_agent()},
                )
                r.raise_for_status()
                if self._looks_blocked(r.text):
                    last_error = RuntimeError("blocked_or_captcha")
                    continue
                return r.text
            except Exception as e:
                last_error = e
                continue
        if last_error:
            raise last_error
        raise RuntimeError("All proxy routes failed")

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

    def _extract_from_jsonld(self, html: str, limit: int) -> list[Listing]:
        out: list[Listing] = []
        seen_urls: set[str] = set()
        scripts = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            flags=re.S,
        )
        for raw in scripts:
            text = raw.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            entries = obj.get("itemListElement")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item")
                if isinstance(item, dict):
                    url = str(item.get("url") or "").strip()
                    title = str(item.get("name") or "").strip()
                    offers = item.get("offers")
                else:
                    url = str(entry.get("url") or "").strip()
                    title = str(entry.get("name") or "").strip()
                    offers = entry.get("offers")
                if not url:
                    continue
                if not url.startswith("http"):
                    if url.startswith("/"):
                        url = "https://www.avito.ru" + url
                    else:
                        url = "https://www.avito.ru/" + url
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                price = None
                if isinstance(offers, dict):
                    raw_price = offers.get("price")
                    if raw_price is not None:
                        try:
                            price = int(float(str(raw_price)))
                        except Exception:
                            price = None
                m = re.search(r"/(\d+)(?:\?|$)", url)
                external_id = m.group(1) if m else url
                if not title:
                    title = "Без названия"
                out.append(Listing(external_id=external_id, url=url, title=title, price=price))
                if len(out) >= limit:
                    return out
        return out

    def _extract_from_mfe_state(self, html: str, limit: int) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        state_data: dict | None = None
        for script in soup.select("script"):
            if script.get("type") == "mime/invalid" and script.get("data-mfe-state") == "true":
                text = (script.get_text() or "").strip()
                if not text or "sandbox" in text:
                    continue
                try:
                    payload = json.loads(unescape(text))
                except Exception:
                    continue
                maybe_data = payload.get("state", {}).get("data", {})
                if isinstance(maybe_data, dict):
                    state_data = maybe_data
                    break
        if not state_data:
            return []

        catalog = state_data.get("catalog") if isinstance(state_data, dict) else None
        items = catalog.get("items") if isinstance(catalog, dict) else None
        if not isinstance(items, list):
            return []

        out: list[Listing] = []
        seen: set[str] = set()
        for row in items:
            if not isinstance(row, dict):
                continue
            raw_id = row.get("id")
            external_id = str(raw_id).strip() if raw_id is not None else ""
            if not external_id:
                continue
            if external_id in seen:
                continue
            seen.add(external_id)

            url_path = str(row.get("urlPath") or "").strip()
            if not url_path:
                continue
            if not url_path.startswith("http"):
                if not url_path.startswith("/"):
                    url_path = "/" + url_path
                url = f"https://www.avito.ru{url_path}"
            else:
                url = url_path

            title = str(row.get("title") or "").strip() or "Без названия"
            price = None
            pd = row.get("priceDetailed")
            if isinstance(pd, dict):
                pval = pd.get("value")
                if pval is not None:
                    try:
                        price = int(pval)
                    except Exception:
                        price = None
            if price is None:
                p = row.get("price")
                if p is not None:
                    try:
                        price = int(p)
                    except Exception:
                        price = self._parse_price(str(p))

            out.append(Listing(external_id=external_id, url=url, title=title, price=price))
            if len(out) >= limit:
                return out
        return out

    async def fetch_latest(self, sub: Subscription, limit: int) -> list[Listing]:
        mobile_url = self._build_mobile_url(sub)
        html_mobile = await self._get_html(mobile_url)
        mobile_mfe = self._extract_from_mfe_state(html_mobile, limit=limit)
        if mobile_mfe:
            return mobile_mfe
        mobile_items = self._extract_from_jsonld(html_mobile, limit=limit)
        if mobile_items:
            return mobile_items

        url = self._build_url(sub)
        html = await self._get_html(url)
        desktop_mfe = self._extract_from_mfe_state(html, limit=limit)
        if desktop_mfe:
            return desktop_mfe
        parsed = self._extract_listings(html, limit=limit)
        if parsed:
            return parsed
        return self._extract_from_jsonld(html, limit=limit)

    def _classify_request_error(self, exc: Exception) -> str:
        msg = str(exc).strip()
        name = type(exc).__name__
        if "blocked_or_captcha" in msg:
            return "blocked_or_captcha"
        if "Timeout" in name:
            return "timeout"
        if "ConnectError" in name or "RemoteProtocol" in name or "ReadError" in name:
            return f"network:{name}"
        return f"error:{name}"

    async def fetch_latest_with_debug(self, sub: Subscription, limit: int) -> tuple[list[Listing], dict]:
        debug: dict = {
            "mobile_captcha": False,
            "desktop_captcha": False,
            "mobile_jsonld_items": 0,
            "desktop_selector_items": 0,
            "desktop_jsonld_items": 0,
            "reason": "",
        }
        try:
            mobile_url = self._build_mobile_url(sub)
            html_mobile = await self._get_html(mobile_url)
            debug["mobile_captcha"] = "captcha" in html_mobile.lower()
            mobile_mfe = self._extract_from_mfe_state(html_mobile, limit=limit)
            if mobile_mfe:
                debug["reason"] = "ok_mobile_mfe_state"
                return mobile_mfe, debug
            mobile_items = self._extract_from_jsonld(html_mobile, limit=limit)
            debug["mobile_jsonld_items"] = len(mobile_items)
            if mobile_items:
                debug["reason"] = "ok_mobile_jsonld"
                return mobile_items, debug
        except Exception as e:
            debug["reason"] = f"mobile_request_failed:{self._classify_request_error(e)}"

        try:
            url = self._build_url(sub)
            html = await self._get_html(url)
            debug["desktop_captcha"] = "captcha" in html.lower()
            desktop_mfe = self._extract_from_mfe_state(html, limit=limit)
            if desktop_mfe:
                debug["reason"] = "ok_desktop_mfe_state"
                return desktop_mfe, debug
            parsed = self._extract_listings(html, limit=limit)
            debug["desktop_selector_items"] = len(parsed)
            if parsed:
                debug["reason"] = "ok_desktop_selectors"
                return parsed, debug
            jsonld_items = self._extract_from_jsonld(html, limit=limit)
            debug["desktop_jsonld_items"] = len(jsonld_items)
            if jsonld_items:
                debug["reason"] = "ok_desktop_jsonld"
                return jsonld_items, debug
        except Exception as e:
            classified = f"desktop_request_failed:{self._classify_request_error(e)}"
            if not debug["reason"] or debug["reason"].startswith("mobile_request_failed"):
                debug["reason"] = classified

        if not debug["reason"]:
            debug["reason"] = "no_items_found"
        return [], debug

