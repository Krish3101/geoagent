import sys
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# Bump this when the tables change. Old local databases are refused, not migrated.
SCHEMA_VERSION = 1


def make_engine(url: str) -> AsyncEngine:
    new_engine = create_async_engine(url)

    # SQLite settings are per connection, so set them on every new pooled connection
    @event.listens_for(new_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return new_engine


engine = make_engine(settings.database_url)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_db() -> None:
    from app.models import Base

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        version = (await conn.exec_driver_sql("PRAGMA user_version")).scalar()
        has_tables = (
            await conn.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type='table'")
        ).scalar()
        if has_tables and version != SCHEMA_VERSION:
            sys.exit(
                f"{settings.data_dir}/geoagent.db is from an older version: "
                f"delete {settings.data_dir}/ and restart."
            )
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
