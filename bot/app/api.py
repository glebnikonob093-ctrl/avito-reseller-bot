from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repos import (
    create_catalog,
    delete_subscription,
    get_subscription,
    get_user_by_tg_user_id,
    list_catalogs,
    list_feed_items_for_catalog,
    set_work_item_status,
    select_catalog,
    upsert_user,
    update_catalog,
)
from app.models import PushedListing
from app.sources.avito_public_web import AvitoPublicWebSource
from app.sources.avito_cloud_scrape import AvitoCloudScrapeSource
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError


@dataclass(frozen=True)
class TgWebAppUser:
    tg_user_id: int
    username: str
    first_name: str


class CatalogCreate(BaseModel):
    display_name: str = Field(default="", max_length=120)
    category: str
    region: str
    query: str = ""
    price_min: int | None = None
    price_max: int | None = None
    select_now: bool = True


class CatalogUpdate(BaseModel):
    display_name: str | None = None
    category: str | None = None
    region: str | None = None
    query: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    is_paused: bool | None = None


class WorkStatusUpdate(BaseModel):
    source: str
    external_id: str
    status: str = Field(pattern="^(new|contacted|negotiating|bought|sold)$")


def _catalog_to_dict(sub: Any) -> dict[str, Any]:
    return {
        "id": sub.id,
        "display_name": sub.display_name or f"Каталог #{sub.id}",
        "category": sub.category,
        "region": sub.region,
        "query": sub.query,
        "price_min": sub.price_min,
        "price_max": sub.price_max,
        "is_paused": bool(sub.is_paused),
        "is_selected": bool(sub.is_selected),
        "source": sub.source,
    }


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _resolve_account_status(*, role: str, subscription_tier: str) -> str:
    if role == "admin":
        return "Admin"
    if subscription_tier == "pro":
        return "Pro"
    return "Free"


def _parse_init_data(init_data: str) -> dict[str, str]:
    # initData is querystring-like: "query_id=...&user=...&auth_date=...&hash=..."
    return {k: v for k, v in parse_qsl(init_data, keep_blank_values=True)}


def _verify_init_data(*, init_data: str, bot_token: str) -> dict[str, Any]:
    data = _parse_init_data(init_data)
    given_hash = data.get("hash", "")
    if not given_hash:
        raise HTTPException(status_code=401, detail="Отсутствует hash в initData")

    check_pairs: list[str] = []
    for k in sorted(data.keys()):
        if k == "hash":
            continue
        check_pairs.append(f"{k}={data[k]}")
    data_check_string = "\n".join(check_pairs)

    # Telegram WebApp signature:
    # secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, given_hash):
        raise HTTPException(status_code=401, detail="Неверная подпись initData")

    # Telegram sends `user` as a JSON string
    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="Отсутствует пользователь в initData")
    try:
        user_obj = json.loads(user_raw)
    except Exception:
        raise HTTPException(status_code=401, detail="Некорректный JSON пользователя в initData")

    tg_user_id = user_obj.get("id")
    if not isinstance(tg_user_id, int):
        raise HTTPException(status_code=401, detail="Некорректный id пользователя в initData")

    return {"tg_user_id": tg_user_id, "user": user_obj, "raw": data}


def create_api_app(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bot_token: str,
    allowed_origins: list[str] | None = None,
    source_proxy_url: str = "",
    max_requests_per_minute: int = 20,
    scraper_provider: str = "scraperapi",
    scraper_api_key: str = "",
    duff_webhook_secret: str = "",
) -> FastAPI:
    app = FastAPI(title="AvitoResellerBot API", version="0.1")
    live_source = AvitoPublicWebSource(
        max_requests_per_minute=max_requests_per_minute,
        proxy_url=source_proxy_url,
    )
    cloud_source = AvitoCloudScrapeSource(provider=scraper_provider, api_key=scraper_api_key)

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    async def get_current_user(
        x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
    ) -> TgWebAppUser:
        if not x_telegram_init_data:
            raise HTTPException(status_code=401, detail="Отсутствует заголовок X-Telegram-Init-Data")
        verified = _verify_init_data(init_data=x_telegram_init_data, bot_token=bot_token)
        user_obj = verified.get("user", {})
        return TgWebAppUser(
            tg_user_id=int(verified["tg_user_id"]),
            username=str(user_obj.get("username") or ""),
            first_name=str(user_obj.get("first_name") or ""),
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": _utc_now_iso()}

    @app.get("/api/feed")
    async def feed(
        limit: int = 50,
        catalog_id: int | None = None,
        sort: str = "newest",
        min_deal_score: float | None = Query(default=None, ge=0, le=100),
        max_price: int | None = Query(default=None, ge=0),
        only_with_photo: bool = False,
        work_status: str | None = Query(default=None),
        user: TgWebAppUser = Depends(get_current_user),
    ) -> dict[str, Any]:
        safe_limit = max(1, min(200, int(limit)))
        sort_by = sort if sort in {"newest", "best_deals"} else "newest"
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                db_user = await upsert_user(session, tg_user_id=user.tg_user_id, chat_id=user.tg_user_id)
            items = await list_feed_items_for_catalog(
                session,
                user_id=db_user.id,
                catalog_id=catalog_id,
                sort_by=sort_by,
                limit=safe_limit,
                min_deal_score=min_deal_score,
                max_price=max_price,
                only_with_photo=only_with_photo,
                work_status=work_status,
            )
            await session.commit()
        return {"items": items, "sort": sort_by}

    def _reason_user_message(reason: str) -> str:
        if not reason:
            return "Источник вернул пусто. Попробуй ещё раз через минуту."
        if reason == "ok":
            return ""
        if reason == "missing_api_key":
            return "Облачный парсер не настроен (нет ключа). Используется прямой парсинг."
        if reason.startswith("provider_timeout") or "timeout" in reason:
            return "Источник долго не отвечает. Сеть тормозит — попробуй ещё раз."
        if "captcha" in reason or "blocked" in reason:
            return "Avito временно блокирует автоматические запросы. Попробуй позже или подключи облачный парсер."
        if reason.startswith("provider_rate_limited"):
            return "Превышен лимит облачного парсера. Подожди немного."
        if reason.startswith("provider_auth"):
            return "Облачный парсер: ключ некорректен или отозван."
        if reason.startswith("provider_5xx"):
            return "Облачный парсер временно недоступен. Это лечится повторной попыткой."
        if reason == "empty" or reason == "no_items_found":
            return "По текущим фильтрам ничего нет. Попробуй другой каталог или запрос."
        return f"Источник вернул статус: {reason}"

    def _summarize_reason(primary: dict[str, Any], fallback: dict[str, Any]) -> str:
        # Prefer the "ok" branch; otherwise pick the more informative non-ok reason.
        for d in (primary, fallback):
            if (d or {}).get("reason") == "ok":
                return "ok"
        primary_reason = (primary or {}).get("reason") or ""
        fallback_reason = (fallback or {}).get("reason") or ""
        # If both are missing/empty, default to "no_data".
        if not primary_reason and not fallback_reason:
            return "no_data"
        # Combine to keep cause traceable in admin diagnostics.
        if primary_reason and fallback_reason:
            return f"cloud:{primary_reason}; public:{fallback_reason}"
        return primary_reason or fallback_reason

    @app.get("/api/feed/live")
    async def feed_live(limit: int = 5, catalog_id: int | None = None, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        safe_limit = max(1, min(5, int(limit)))
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                db_user = await upsert_user(session, tg_user_id=user.tg_user_id, chat_id=user.tg_user_id)
            catalogs_rows = await list_catalogs(session, user_id=db_user.id)
            await session.commit()

        if not catalogs_rows:
            return {
                "items": [],
                "source": "avito_public_web",
                "live": True,
                "ok": False,
                "reason": "no_catalogs",
                "used_source": "",
                "debug": {"reason": "no_catalogs"},
                "user_message": "Сначала создай каталог во вкладке «Каталоги».",
            }

        selected = None
        if catalog_id is not None:
            for c in catalogs_rows:
                if c.id == catalog_id:
                    selected = c
                    break
        if selected is None:
            selected = next((c for c in catalogs_rows if bool(c.is_selected)), catalogs_rows[0])

        listings: list[Any] = []
        primary_debug: dict[str, Any] = {}
        fallback_debug: dict[str, Any] = {}
        used_source = "avito_public_web"
        if scraper_api_key:
            listings, primary_debug = await cloud_source.fetch_latest_with_debug(selected, limit=safe_limit)
            if listings:
                used_source = "avito_cloud_scrape"
        if not listings:
            try:
                fallback_items, fallback_debug = await live_source.fetch_latest_with_debug(selected, limit=safe_limit)
                if fallback_items:
                    listings = fallback_items
                    used_source = "avito_public_web"
            except Exception as e:
                fallback_debug = {"reason": f"live_source_failed:{type(e).__name__}"}

        summary_reason = _summarize_reason(primary_debug, fallback_debug)
        debug = {
            "reason": summary_reason,
            "primary": primary_debug,
            "fallback": fallback_debug,
            "used_source": used_source,
        }

        items: list[dict[str, Any]] = []
        for it in listings:
            items.append(
                {
                    "title": it.title,
                    "price": it.price,
                    "url": it.url,
                    "first_seen_at": it.published_at.isoformat() if it.published_at else None,
                    "subscription_id": selected.id,
                    "external_id": it.external_id,
                    "source": used_source,
                    "city": it.city,
                    "photo_url": it.photo_url,
                    "description": it.description,
                    "seller_profile_url": it.seller_profile_url,
                    "is_mock": bool(it.is_mock),
                    "deal_score": 0,
                    "work_status": "new",
                }
            )
        user_message = "" if items else _reason_user_message(summary_reason)
        return {
            "items": items,
            "source": used_source,
            "live": True,
            "debug": debug,
            "user_message": user_message,
        }

    @app.get("/api/source-status")
    async def source_status(user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                db_user = await upsert_user(session, tg_user_id=user.tg_user_id, chat_id=user.tg_user_id)
            pushed_count = 0
            if duff_webhook_secret:
                pushed_count = int(
                    (
                        await session.execute(select(func.count()).select_from(PushedListing))
                    ).scalar_one()
                )
            await session.commit()
        is_admin = (db_user.role or "user") == "admin"
        return {
            "cloud_provider": scraper_provider,
            "cloud_configured": bool(scraper_api_key),
            "public_proxy_configured": bool(source_proxy_url),
            "duff_webhook_enabled": bool(duff_webhook_secret),
            "duff_buffer_size": pushed_count,
            "is_admin": is_admin,
        }

    class DuffListing(BaseModel):
        external_id: str = Field(min_length=1, max_length=200)
        url: str = Field(min_length=1, max_length=500)
        title: str | None = Field(default=None, max_length=300)
        price: int | None = None
        city: str | None = Field(default=None, max_length=120)
        category: str | None = Field(default=None, max_length=100)
        region: str | None = Field(default=None, max_length=100)
        photo_url: str | None = Field(default=None, max_length=800)
        description: str | None = Field(default=None, max_length=800)
        seller_profile_url: str | None = Field(default=None, max_length=800)
        published_at: str | None = None  # ISO 8601 timestamp

    class DuffWebhookPayload(BaseModel):
        source: str = Field(default="avito", max_length=50)
        items: list[DuffListing] = Field(default_factory=list)

    def _verify_duff_signature(*, body: bytes, given: str) -> bool:
        if not duff_webhook_secret or not given:
            return False
        # Header may be in form "sha256=...", strip the prefix.
        token = given.strip()
        if token.startswith("sha256="):
            token = token.split("=", 1)[1].strip()
        digest = hmac.new(
            duff_webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, token)

    def _parse_iso_dt(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None

    @app.post("/api/duff89/webhook")
    async def duff_webhook(
        payload: DuffWebhookPayload,
        x_signature: str | None = Header(default=None, alias="X-Signature"),
    ) -> dict[str, Any]:
        if not duff_webhook_secret:
            raise HTTPException(status_code=404, detail="Duff89 webhook is disabled")
        # Recompute signature on the canonical JSON of the payload Pydantic
        # parsed for us. Clients must use the same JSON serialization.
        raw_body = payload.model_dump_json().encode("utf-8")
        if not _verify_duff_signature(body=raw_body, given=x_signature or ""):
            raise HTTPException(status_code=401, detail="Bad signature")

        inserted = 0
        skipped = 0
        async with session_factory() as session:
            # Prune very old buffer entries (older than ~24h) to keep buffer small.
            cutoff = datetime.utcnow().timestamp() - 24 * 3600
            try:
                await session.execute(
                    delete(PushedListing).where(
                        PushedListing.received_at
                        < datetime.fromtimestamp(cutoff)
                    )
                )
            except Exception:
                # Pruning is best-effort; ignore failures.
                pass
            for item in payload.items:
                row = PushedListing(
                    source=payload.source,
                    external_id=item.external_id,
                    url=item.url,
                    title=item.title,
                    price=item.price,
                    city=item.city,
                    category=item.category,
                    region=item.region,
                    photo_url=item.photo_url,
                    description=item.description,
                    seller_profile_url=item.seller_profile_url,
                    published_at=_parse_iso_dt(item.published_at),
                )
                try:
                    async with session.begin_nested():
                        session.add(row)
                        await session.flush()
                    inserted += 1
                except IntegrityError:
                    skipped += 1
            await session.commit()
        return {"ok": True, "inserted": inserted, "skipped": skipped}

    @app.post("/api/work-status")
    async def update_work_status(payload: WorkStatusUpdate, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                db_user = await upsert_user(session, tg_user_id=user.tg_user_id, chat_id=user.tg_user_id)
            item = await set_work_item_status(
                session,
                user_id=db_user.id,
                source=payload.source,
                external_id=payload.external_id,
                status=payload.status,
            )
            await session.commit()
        return {"item": item}

    @app.get("/api/notifications")
    async def notifications(limit: int = 20, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        safe_limit = max(1, min(100, int(limit)))
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                db_user = await upsert_user(session, tg_user_id=user.tg_user_id, chat_id=user.tg_user_id)
            items = await list_feed_items_for_catalog(
                session,
                user_id=db_user.id,
                catalog_id=None,
                sort_by="newest",
                limit=safe_limit,
            )
            await session.commit()
        return {"items": items}

    @app.get("/api/me")
    async def me(user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                db_user = await upsert_user(session, tg_user_id=user.tg_user_id, chat_id=user.tg_user_id)
            await session.commit()
        return {
            "id": db_user.id,
            "tg_user_id": db_user.tg_user_id,
            "first_name": user.first_name,
            "username": user.username,
            "role": db_user.role or "user",
            "subscription_tier": db_user.subscription_tier or "free",
            "account_status": _resolve_account_status(
                role=(db_user.role or "user"),
                subscription_tier=(db_user.subscription_tier or "free"),
            ),
            "is_admin": (db_user.role or "user") == "admin",
        }

    @app.get("/api/catalogs")
    async def catalogs(user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                db_user = await upsert_user(session, tg_user_id=user.tg_user_id, chat_id=user.tg_user_id)
            rows = await list_catalogs(session, user_id=db_user.id)
            await session.commit()
        return {"items": [_catalog_to_dict(s) for s in rows]}

    @app.post("/api/catalogs")
    async def create_catalog_endpoint(payload: CatalogCreate, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="Пользователь не найден. Сначала отправь /start в боте.")
            created = await create_catalog(
                session,
                user_id=db_user.id,
                source="avito_public_web",
                display_name=payload.display_name,
                category=payload.category,
                region=payload.region,
                query=payload.query,
                price_min=payload.price_min,
                price_max=payload.price_max,
                select_now=payload.select_now,
            )
            await session.commit()
        return {"item": _catalog_to_dict(created)}

    @app.patch("/api/catalogs/{catalog_id}")
    async def update_catalog_endpoint(
        catalog_id: int,
        payload: CatalogUpdate,
        user: TgWebAppUser = Depends(get_current_user),
    ) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="Пользователь не найден. Сначала отправь /start в боте.")
            updated = await update_catalog(
                session,
                user_id=db_user.id,
                catalog_id=catalog_id,
                display_name=payload.display_name,
                category=payload.category,
                region=payload.region,
                query=payload.query,
                price_min=payload.price_min,
                price_max=payload.price_max,
                is_paused=payload.is_paused,
            )
            if not updated:
                raise HTTPException(status_code=404, detail="Каталог не найден")
            await session.commit()
        return {"item": _catalog_to_dict(updated)}

    @app.post("/api/catalogs/{catalog_id}/select")
    async def select_catalog_endpoint(catalog_id: int, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="Пользователь не найден. Сначала отправь /start в боте.")
            selected = await select_catalog(session, user_id=db_user.id, catalog_id=catalog_id)
            if not selected:
                raise HTTPException(status_code=404, detail="Каталог не найден")
            await session.commit()
        return {"item": _catalog_to_dict(selected)}

    @app.delete("/api/catalogs/{catalog_id}")
    async def delete_catalog_endpoint(catalog_id: int, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="Пользователь не найден. Сначала отправь /start в боте.")
            existing = await get_subscription(session, user_id=db_user.id, subscription_id=catalog_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Каталог не найден")
            await delete_subscription(session, subscription_id=catalog_id)
            await session.commit()
        return {"ok": True}

    @app.get("/api/categories")
    async def categories() -> dict[str, Any]:
        return {
            "items": [
                {"slug": "telefony", "title": "Телефоны"},
                {"slug": "noutbuki", "title": "Ноутбуки"},
                {"slug": "televizory", "title": "Телевизоры"},
                {"slug": "odezhda", "title": "Одежда"},
                {"slug": "detskie_tovary", "title": "Детские товары"},
                {"slug": "bytovaya_tehnika", "title": "Бытовая техника"},
            ]
        }

    @app.get("/api/cities")
    async def cities(q: str = Query(default="", max_length=100), limit: int = Query(default=20, ge=1, le=50)) -> dict[str, Any]:
        query = q.strip()
        if len(query) < 2:
            return {"items": []}
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "avito-reseller-miniapp/1.0"}) as client:
                r = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "format": "jsonv2",
                        "countrycodes": "ru",
                        "q": query,
                        "limit": max(limit * 3, 60),
                        "addressdetails": 1,
                        "accept-language": "ru",
                        "featuretype": "settlement",
                    },
                )
                r.raise_for_status()
                rows = r.json()
            items: list[dict[str, str]] = []
            used: set[str] = set()
            for row in rows:
                address = row.get("address") or {}
                if not isinstance(address, dict):
                    address = {}
                name = (
                    str(address.get("city") or "")
                    or str(address.get("town") or "")
                    or str(address.get("village") or "")
                    or str(address.get("hamlet") or "")
                    or str(address.get("municipality") or "")
                    or str(address.get("county") or "")
                    or str(row.get("name") or "")
                    or str(row.get("display_name") or "").split(",")[0].strip()
                )
                name = name.strip()
                slug = str(row.get("name") or name).strip().lower().replace(" ", "-")
                if not name or slug in used:
                    continue
                used.add(slug)
                items.append({"slug": slug, "title": name})
                if len(items) >= limit:
                    break
            return {"items": items}
        except Exception:
            return {"items": []}

    @app.on_event("shutdown")
    async def _close_live_source() -> None:
        await live_source.aclose()
        await cloud_source.aclose()

    return app

