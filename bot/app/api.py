from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
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
    select_catalog,
    update_catalog,
)


@dataclass(frozen=True)
class TgWebAppUser:
    tg_user_id: int


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


def _parse_init_data(init_data: str) -> dict[str, str]:
    # initData is querystring-like: "query_id=...&user=...&auth_date=...&hash=..."
    return {k: v for k, v in parse_qsl(init_data, keep_blank_values=True)}


def _verify_init_data(*, init_data: str, bot_token: str) -> dict[str, Any]:
    data = _parse_init_data(init_data)
    given_hash = data.get("hash", "")
    if not given_hash:
        raise HTTPException(status_code=401, detail="Missing initData hash")

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
        raise HTTPException(status_code=401, detail="Invalid initData signature")

    # Telegram sends `user` as a JSON string
    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="Missing user in initData")
    try:
        user_obj = json.loads(user_raw)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid user JSON in initData")

    tg_user_id = user_obj.get("id")
    if not isinstance(tg_user_id, int):
        raise HTTPException(status_code=401, detail="Invalid user id in initData")

    return {"tg_user_id": tg_user_id, "user": user_obj, "raw": data}


def create_api_app(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bot_token: str,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    app = FastAPI(title="AvitoResellerBot API", version="0.1")

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    async def get_current_user(
        x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
    ) -> TgWebAppUser:
        if not x_telegram_init_data:
            raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data header")
        verified = _verify_init_data(init_data=x_telegram_init_data, bot_token=bot_token)
        return TgWebAppUser(tg_user_id=int(verified["tg_user_id"]))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": _utc_now_iso()}

    @app.get("/api/feed")
    async def feed(
        limit: int = 50,
        catalog_id: int | None = None,
        sort: str = "newest",
        user: TgWebAppUser = Depends(get_current_user),
    ) -> dict[str, Any]:
        safe_limit = max(1, min(200, int(limit)))
        sort_by = sort if sort in {"newest", "best_deals"} else "newest"
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                # user not known to bot yet: tell UI to ask user to /start
                raise HTTPException(status_code=404, detail="User not found. Open bot and run /start first.")
            items = await list_feed_items_for_catalog(
                session,
                user_id=db_user.id,
                catalog_id=catalog_id,
                sort_by=sort_by,
                limit=safe_limit,
            )
            await session.commit()
        return {"items": items, "sort": sort_by}

    @app.get("/api/catalogs")
    async def catalogs(user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="User not found. Open bot and run /start first.")
            rows = await list_catalogs(session, user_id=db_user.id)
            await session.commit()
        return {"items": [_catalog_to_dict(s) for s in rows]}

    @app.post("/api/catalogs")
    async def create_catalog_endpoint(payload: CatalogCreate, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="User not found. Open bot and run /start first.")
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
                raise HTTPException(status_code=404, detail="User not found. Open bot and run /start first.")
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
                raise HTTPException(status_code=404, detail="Catalog not found")
            await session.commit()
        return {"item": _catalog_to_dict(updated)}

    @app.post("/api/catalogs/{catalog_id}/select")
    async def select_catalog_endpoint(catalog_id: int, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="User not found. Open bot and run /start first.")
            selected = await select_catalog(session, user_id=db_user.id, catalog_id=catalog_id)
            if not selected:
                raise HTTPException(status_code=404, detail="Catalog not found")
            await session.commit()
        return {"item": _catalog_to_dict(selected)}

    @app.delete("/api/catalogs/{catalog_id}")
    async def delete_catalog_endpoint(catalog_id: int, user: TgWebAppUser = Depends(get_current_user)) -> dict[str, Any]:
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                raise HTTPException(status_code=404, detail="User not found. Open bot and run /start first.")
            existing = await get_subscription(session, user_id=db_user.id, subscription_id=catalog_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Catalog not found")
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

    return app

