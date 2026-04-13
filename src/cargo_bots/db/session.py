from __future__ import annotations

from contextlib import asynccontextmanager

import ssl as _ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cargo_bots.core.config import Settings
from cargo_bots.db.base import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        # Railway internal PostgreSQL не поддерживает SSL
        connect_args: dict = {}
        if ".railway.internal" in settings.database_url:
            connect_args["ssl"] = False

        self.engine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @asynccontextmanager
    async def session(self) -> AsyncSession:
        async with self.session_factory() as session:
            yield session

    async def create_all(self) -> None:
        from sqlalchemy import text
        async with self.engine.begin() as connection:
            if "postgresql" in self.engine.url.drivername:
                try:
                    await connection.execute(text("ALTER TYPE parcelstatus ADD VALUE IF NOT EXISTS 'ISSUED'"))
                except Exception:
                    pass
                try:
                    await connection.execute(text("ALTER TABLE imports ADD COLUMN IF NOT EXISTS delivery_days INTEGER DEFAULT 12 NOT NULL"))
                except Exception:
                    pass
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
