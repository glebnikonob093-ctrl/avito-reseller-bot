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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repos import get_user_by_tg_user_id, list_feed_items


@dataclass(frozen=True)
class TgWebAppUser:
    tg_user_id: int


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
        user: TgWebAppUser = Depends(get_current_user),
    ) -> dict[str, Any]:
        safe_limit = max(1, min(200, int(limit)))
        async with session_factory() as session:
            db_user = await get_user_by_tg_user_id(session, tg_user_id=user.tg_user_id)
            if not db_user:
                # user not known to bot yet: tell UI to ask user to /start
                raise HTTPException(status_code=404, detail="User not found. Open bot and run /start first.")
            items = await list_feed_items(session, user_id=db_user.id, limit=safe_limit)
            await session.commit()
        return {"items": items}

    return app

