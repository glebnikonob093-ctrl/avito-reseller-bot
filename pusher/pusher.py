"""Avito → Duff89 webhook pusher.

Runs locally on the user's machine (e.g. Windows + Task Scheduler), opens each
configured Avito search URL in a headless Chromium (Playwright), parses the
listings, and POSTs them to the bot's `/api/duff89/webhook` endpoint with an
HMAC-SHA256 signature.

Why local + a real browser:
- Avito heavily fingerprints datacenter IPs and headless-`requests`-style
  clients. A residential IP + real browser bypass nearly all of that.
- The bot already has the receiver, dedup, and filter-at-fetch pipeline; this
  script is intentionally thin and just feeds the buffer.

Configuration: see `.env.example` and `targets.example.json`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


HERE = Path(__file__).resolve().parent
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Caps the size of fields we pass to the receiver — the API enforces these too,
# so trimming here gives us nicer errors and keeps the payload small.
FIELD_LIMITS = {
    "external_id": 200,
    "url": 500,
    "title": 300,
    "city": 120,
    "category": 100,
    "region": 100,
    "photo_url": 800,
    "description": 800,
    "seller_profile_url": 800,
}


@dataclass
class Target:
    """One catalog the pusher should scrape."""

    url: str
    category: str
    region: str
    # Optional human label, only for logs.
    label: str = ""
    # Maximum items to push from this target per run. Webhook caps at 200 across
    # the whole batch, so per-target should stay well under that.
    max_items: int = 30


@dataclass
class Listing:
    external_id: str
    url: str
    title: str
    price: int | None = None
    city: str | None = None
    photo_url: str | None = None
    description: str | None = None
    seller_profile_url: str | None = None
    category: str = ""
    region: str = ""
    published_at: str | None = None  # ISO 8601


@dataclass
class RunReport:
    targets: int = 0
    items_scraped: int = 0
    items_pushed: int = 0
    inserted: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)


def _setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("pusher")


def _truncate(value: str | None, key: str) -> str | None:
    if value is None:
        return None
    limit = FIELD_LIMITS.get(key)
    if limit is None:
        return value
    return value[:limit]


def _parse_price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _id_from_url(url: str) -> str:
    """Extract numeric Avito ad ID from a URL like /.../iphone_15_512_gb_7670684650."""
    m = re.search(r"_(\d+)(?:\?|$)", url) or re.search(r"/(\d+)(?:\?|$)", url)
    return m.group(1) if m else url


def _absolute_url(href: str) -> str:
    if not href:
        return href
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.avito.ru" + href
    return "https://www.avito.ru/" + href


def load_targets(path: Path) -> list[Target]:
    if not path.exists():
        raise FileNotFoundError(
            f"Targets file not found: {path}. Copy `targets.example.json` to `targets.json` and edit."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("targets.json must be a JSON array")
    out: list[Target] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"targets.json[{i}] must be an object")
        url = str(row.get("url") or "").strip()
        category = str(row.get("category") or "").strip()
        region = str(row.get("region") or "").strip()
        if not url or not category or not region:
            raise ValueError(
                f"targets.json[{i}] requires url, category, region (got: {row})"
            )
        if not url.startswith("http"):
            raise ValueError(f"targets.json[{i}] url must be absolute: {url}")
        out.append(
            Target(
                url=url,
                category=category,
                region=region,
                label=str(row.get("label") or "").strip(),
                max_items=int(row.get("max_items") or 30),
            )
        )
    return out


def _make_browser_context(p: Any, *, profile_dir: Path, headless: bool) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={"width": 1366, "height": 768},
        user_agent=DEFAULT_USER_AGENT,
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )


def _stealth_init(page: Page) -> None:
    """Light fingerprint patches so headless Chromium looks more like real Chrome."""
    page.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        if (!window.chrome) { window.chrome = { runtime: {} }; }
        Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """
    )


def _looks_blocked(html: str) -> bool:
    low = html.lower()
    return ("captcha" in low) or ("доступ ограничен" in low) or ("access denied" in low)


def _extract_from_mfe_state(html: str, limit: int) -> list[Listing]:
    """Extract listings from Avito's `<script data-mfe-state>` JSON payload.

    Mirrors the parser in `bot/app/sources/avito_public_web.py` so the local
    pusher and the in-bot fallback agree on what counts as an item.
    """
    out: list[Listing] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<script[^>]*data-mfe-state="true"[^>]*>(.*?)</script>',
        html,
        flags=re.S,
    ):
        text = m.group(1).strip()
        if not text or "sandbox" in text.lower()[:50]:
            continue
        try:
            payload = json.loads(unescape(text))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        data = payload.get("state", {}).get("data", {})
        if not isinstance(data, dict):
            continue
        catalog = data.get("catalog")
        items = catalog.get("items") if isinstance(catalog, dict) else None
        if not isinstance(items, list):
            continue
        for row in items:
            if not isinstance(row, dict):
                continue
            ext_id = str(row.get("id") or "").strip()
            if not ext_id or ext_id in seen:
                continue
            url_path = str(row.get("urlPath") or "").strip()
            if not url_path:
                continue
            url = url_path if url_path.startswith("http") else (
                "https://www.avito.ru" + (url_path if url_path.startswith("/") else "/" + url_path)
            )
            title = str(row.get("title") or "").strip() or "Без названия"
            price: int | None = None
            pd = row.get("priceDetailed")
            if isinstance(pd, dict):
                pval = pd.get("value")
                if pval is not None:
                    try:
                        price = int(pval)
                    except Exception:
                        price = None
            if price is None and row.get("price") is not None:
                try:
                    price = int(row["price"])
                except Exception:
                    price = _parse_price(str(row["price"]))
            seen.add(ext_id)
            out.append(Listing(external_id=ext_id, url=url, title=title, price=price))
            if len(out) >= limit:
                return out
    return out


def _extract_from_dom(page: Page, limit: int) -> list[Listing]:
    """Walk `div[data-marker="item"]` containers and pull title/price/photo/city.

    Runs entirely in the page so we don't have to ship the HTML over.
    """
    raw = page.evaluate(
        """(limit) => {
            const out = [];
            const seen = new Set();
            const containers = document.querySelectorAll('div[data-marker="item"]');
            for (const c of containers) {
                const a = c.querySelector('a[data-marker="item-title"]') || c.querySelector('a[itemprop="url"]');
                if (!a) continue;
                let href = a.getAttribute('href') || '';
                if (!href) continue;
                if (!href.startsWith('http')) {
                    href = 'https://www.avito.ru' + (href.startsWith('/') ? href : '/' + href);
                }
                if (seen.has(href)) continue;
                seen.add(href);
                const title = (a.innerText || '').trim();
                if (!title) continue;
                const containerId = (c.getAttribute('data-item-id') || c.id || '').replace(/^i/, '');
                const priceMeta = c.querySelector('meta[itemprop="price"]');
                const priceTxt = c.querySelector('[data-marker="item-price"]');
                const img = c.querySelector('img[itemprop="image"]') || c.querySelector('[data-marker="item-photo"] img');
                const geo = c.querySelector('[data-marker="item-address"]') || c.querySelector('[class*="geo-georeferences"]');
                const desc = c.querySelector('meta[itemprop="description"]');
                out.push({
                    external_id: containerId,
                    url: href,
                    title: title,
                    price_meta: priceMeta ? (priceMeta.getAttribute('content') || '') : '',
                    price_text: priceTxt ? (priceTxt.innerText || '') : '',
                    photo_url: img ? (img.getAttribute('src') || '') : '',
                    city: geo ? (geo.innerText || '').trim() : '',
                    description: desc ? (desc.getAttribute('content') || '') : '',
                });
                if (out.length >= limit) break;
            }
            return out;
        }""",
        limit,
    )
    items: list[Listing] = []
    for row in raw or []:
        url = _absolute_url(row.get("url") or "")
        ext_id = (row.get("external_id") or "").strip() or _id_from_url(url)
        title = (row.get("title") or "").strip()
        if not url or not title:
            continue
        price = _parse_price(row.get("price_meta") or "") or _parse_price(row.get("price_text") or "")
        photo = (row.get("photo_url") or "").strip() or None
        city = (row.get("city") or "").strip() or None
        desc = (row.get("description") or "").strip() or None
        items.append(
            Listing(
                external_id=ext_id,
                url=url,
                title=title,
                price=price,
                photo_url=photo,
                city=city,
                description=desc,
            )
        )
    return items


def scrape_target(context: BrowserContext, target: Target, log: logging.Logger) -> list[Listing]:
    log.info("scraping target: %s (%s, %s)", target.label or target.url, target.category, target.region)
    page = context.new_page()
    _stealth_init(page)
    try:
        try:
            page.goto(target.url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            log.warning("goto timeout: %s", target.url)
            return []
        # Give the page a moment to render any hydration-driven content,
        # but not so long that captcha pages waste time.
        try:
            page.wait_for_selector('div[data-marker="item"]', timeout=8_000)
        except PlaywrightTimeoutError:
            pass
        html = page.content()
        if _looks_blocked(html):
            log.warning("blocked/captcha on %s — skipping", target.url)
            return []
        items = _extract_from_dom(page, limit=target.max_items)
        if not items:
            items = _extract_from_mfe_state(html, limit=target.max_items)
        for it in items:
            it.category = target.category
            it.region = target.region
        log.info("  parsed %d items from %s", len(items), urlparse(target.url).netloc)
        return items
    finally:
        try:
            page.close()
        except Exception:
            pass


def to_payload_dict(items: list[Listing], source: str) -> dict[str, Any]:
    out_items: list[dict[str, Any]] = []
    for it in items:
        out_items.append(
            {
                "external_id": _truncate(it.external_id, "external_id") or "",
                "url": _truncate(it.url, "url") or "",
                "title": _truncate(it.title, "title"),
                "price": it.price,
                "city": _truncate(it.city, "city"),
                "category": _truncate(it.category, "category"),
                "region": _truncate(it.region, "region"),
                "photo_url": _truncate(it.photo_url, "photo_url"),
                "description": _truncate(it.description, "description"),
                "seller_profile_url": _truncate(it.seller_profile_url, "seller_profile_url"),
                "published_at": it.published_at,
            }
        )
    return {"source": source, "items": out_items}


def post_payload(
    *,
    webhook_url: str,
    secret: str,
    payload: dict[str, Any],
    timeout: float,
    log: logging.Logger,
) -> dict[str, Any]:
    """POST the payload with HMAC-SHA256 signature.

    The receiver re-serializes the parsed Pydantic model and computes its own
    HMAC over that. We must serialize identically here, otherwise the signature
    doesn't match. `json.dumps` with `separators=(',', ':')` matches Pydantic's
    `model_dump_json()` byte-for-byte for our schema (no datetime / unicode-only
    fields that need extra handling).
    """
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    log.debug("POST %s body=%dB items=%d", webhook_url, len(body), len(payload["items"]))
    r = requests.post(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": f"sha256={digest}",
            "User-Agent": "avito-pusher/1.0",
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"webhook HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def run() -> int:
    load_dotenv(HERE / ".env", override=False)
    log = _setup_logging(os.getenv("PUSHER_VERBOSE", "").lower() in {"1", "true", "yes"})

    webhook_url = (os.getenv("WEBHOOK_URL") or "").strip()
    secret = (os.getenv("DUFF_WEBHOOK_SECRET") or "").strip()
    source = (os.getenv("SOURCE") or "avito").strip()
    headless_env = (os.getenv("HEADLESS") or "true").lower()
    headless = headless_env not in {"0", "false", "no"}
    profile_dir = Path(os.getenv("PROFILE_DIR") or str(HERE / ".browser-profile")).expanduser()
    targets_path = Path(os.getenv("TARGETS_FILE") or str(HERE / "targets.json")).expanduser()
    timeout = float(os.getenv("WEBHOOK_TIMEOUT") or "30")

    if not webhook_url or not secret:
        log.error(
            "Both WEBHOOK_URL and DUFF_WEBHOOK_SECRET are required. Copy .env.example to .env and edit."
        )
        return 2

    try:
        targets = load_targets(targets_path)
    except (FileNotFoundError, ValueError) as e:
        log.error(str(e))
        return 2
    if not targets:
        log.error("targets.json is empty — add at least one target")
        return 2

    report = RunReport(targets=len(targets))
    all_items: list[Listing] = []
    with sync_playwright() as p:
        try:
            ctx = _make_browser_context(p, profile_dir=profile_dir, headless=headless)
        except Exception as e:
            log.error("Failed to launch Chromium: %s", e)
            log.error("Run `python -m playwright install chromium` (or setup.bat) first.")
            return 3
        try:
            for t in targets:
                try:
                    items = scrape_target(ctx, t, log)
                    all_items.extend(items)
                    report.items_scraped += len(items)
                except Exception as e:
                    msg = f"{t.label or t.url}: {type(e).__name__}: {e}"
                    log.warning("scrape failed: %s", msg)
                    report.failures.append(msg)
                # gentle pacing between targets to avoid burst patterns
                time.sleep(1.5)
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    # Deduplicate by external_id (a single item may match multiple targets).
    by_ext: dict[str, Listing] = {}
    for it in all_items:
        if not it.external_id or not it.url:
            continue
        by_ext.setdefault(it.external_id, it)
    deduped = list(by_ext.values())
    # API caps batch at 200 items per POST.
    deduped = deduped[:200]

    if not deduped:
        log.warning("no items scraped — nothing to push")
        log.info(json.dumps(_report_dict(report), ensure_ascii=False))
        return 1 if report.failures else 0

    payload = to_payload_dict(deduped, source=source)
    try:
        resp = post_payload(
            webhook_url=webhook_url,
            secret=secret,
            payload=payload,
            timeout=timeout,
            log=log,
        )
    except Exception as e:
        log.error("webhook POST failed: %s", e)
        return 4
    report.items_pushed = len(deduped)
    report.inserted = int(resp.get("inserted") or 0)
    report.skipped = int(resp.get("skipped") or 0)
    log.info(
        "pushed %d items → inserted=%d skipped=%d",
        report.items_pushed,
        report.inserted,
        report.skipped,
    )
    log.info(json.dumps(_report_dict(report), ensure_ascii=False))
    return 0


def _report_dict(r: RunReport) -> dict[str, Any]:
    return {
        "targets": r.targets,
        "items_scraped": r.items_scraped,
        "items_pushed": r.items_pushed,
        "inserted": r.inserted,
        "skipped": r.skipped,
        "failures": r.failures,
    }


if __name__ == "__main__":
    sys.exit(run())
