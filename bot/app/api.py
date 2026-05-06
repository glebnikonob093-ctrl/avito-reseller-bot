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
from app.sources.avito_public_web import AvitoPublicWebSource
from app.sources.avito_cloud_scrape import AvitoCloudScrapeSource


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
            return {"items": [], "source": "avito_public_web", "live": True}

        selected = None
        if catalog_id is not None:
            for c in catalogs_rows:
                if c.id == catalog_id:
                    selected = c
                    break
        if selected is None:
            selected = next((c for c in catalogs_rows if bool(c.is_selected)), catalogs_rows[0])

        listings: list[Any] = []
        debug: dict[str, Any] = {}
        if scraper_api_key:
            listings, debug = await cloud_source.fetch_latest_with_debug(selected, limit=safe_limit)
        if not listings:
            try:
                fallback_items, fallback_debug = await live_source.fetch_latest_with_debug(selected, limit=safe_limit)
                listings = fallback_items
                debug = {"primary": debug, "fallback": fallback_debug}
            except Exception:
                debug = {"primary": debug, "fallback": {"reason": "live_source_failed"}}

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
                    "source": "avito_public_web",
                    "city": it.city,
                    "photo_url": it.photo_url,
                    "description": it.description,
                    "seller_profile_url": it.seller_profile_url,
                    "is_mock": bool(it.is_mock),
                    "deal_score": 0,
                    "work_status": "new",
                }
            )
        return {"items": items, "source": "avito_public_web", "live": True, "debug": debug}

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

