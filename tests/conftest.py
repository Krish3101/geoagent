from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import agent as app_agent
from app import db as app_db
from app import runner as app_runner
from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Base


@pytest.fixture
async def test_env(tmp_path: Path):
    """Provide an isolated environment with temporary database and run directories."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    old_data_dir = settings.data_dir
    settings.data_dir = data_dir

    db_path = data_dir / "test_geoagent.db"
    test_db_url = f"sqlite+aiosqlite:///{db_path.resolve()}"

    engine = create_async_engine(test_db_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Apply Base metadata
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)

    # Monkeypatch get_db dependency in FastAPI app
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    old_session_maker = app_db.AsyncSessionLocal
    app_db.AsyncSessionLocal = session_factory
    app_runner.AsyncSessionLocal = session_factory
    app_agent.AsyncSessionLocal = session_factory

    yield {
        "data_dir": data_dir,
        "runs_dir": runs_dir,
        "session_factory": session_factory,
        "engine": engine,
    }

    app.dependency_overrides.clear()
    app_db.AsyncSessionLocal = old_session_maker
    app_runner.AsyncSessionLocal = old_session_maker
    app_agent.AsyncSessionLocal = old_session_maker
    settings.data_dir = old_data_dir
    await engine.dispose()


@pytest.fixture
async def client(test_env) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
