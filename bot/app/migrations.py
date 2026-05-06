from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import Base


async def create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight migrations for SQLite (create_all doesn't add columns).
        # Add optional fields for Mini App feed rendering.
        dialect = conn.dialect.name
        if dialect == "sqlite":
            cols = await conn.execute(text("PRAGMA table_info('seen_items')"))
            existing = {row[1] for row in cols.fetchall()}  # row[1] = name
            if "title" not in existing:
                await conn.exec_driver_sql("ALTER TABLE seen_items ADD COLUMN title VARCHAR(300)")
            if "price" not in existing:
                await conn.exec_driver_sql("ALTER TABLE seen_items ADD COLUMN price INTEGER")

