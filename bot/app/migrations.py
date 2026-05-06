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
            if "city" not in existing:
                await conn.exec_driver_sql("ALTER TABLE seen_items ADD COLUMN city VARCHAR(120)")
            if "photo_url" not in existing:
                await conn.exec_driver_sql("ALTER TABLE seen_items ADD COLUMN photo_url VARCHAR(800)")
            if "description" not in existing:
                await conn.exec_driver_sql("ALTER TABLE seen_items ADD COLUMN description VARCHAR(800)")
            if "seller_profile_url" not in existing:
                await conn.exec_driver_sql("ALTER TABLE seen_items ADD COLUMN seller_profile_url VARCHAR(800)")
            if "is_mock" not in existing:
                await conn.exec_driver_sql("ALTER TABLE seen_items ADD COLUMN is_mock BOOLEAN DEFAULT 0")

            sub_cols = await conn.execute(text("PRAGMA table_info('subscriptions')"))
            sub_existing = {row[1] for row in sub_cols.fetchall()}
            if "display_name" not in sub_existing:
                await conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN display_name VARCHAR(120)")
                await conn.exec_driver_sql("UPDATE subscriptions SET display_name = '' WHERE display_name IS NULL")
            if "is_selected" not in sub_existing:
                await conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN is_selected BOOLEAN DEFAULT 1")
                await conn.exec_driver_sql("UPDATE subscriptions SET is_selected = 1 WHERE is_selected IS NULL")

            user_cols = await conn.execute(text("PRAGMA table_info('users')"))
            user_existing = {row[1] for row in user_cols.fetchall()}
            if "role" not in user_existing:
                await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'")
                await conn.exec_driver_sql("UPDATE users SET role = 'user' WHERE role IS NULL")

            await conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS work_items (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    source VARCHAR(50) NOT NULL,
                    external_id VARCHAR(200) NOT NULL,
                    status VARCHAR(30) DEFAULT 'new',
                    updated_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    CONSTRAINT uq_work_item_user_source_ext UNIQUE (user_id, source, external_id)
                )
                """
            )

