from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import db as app_db
from app.config import settings
from app.main import app


@pytest.fixture
async def test_env(tmp_path: Path, monkeypatch):
    """A fresh database and data dir per test, built with the app's own engine setup."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)

    original_engine = app_db.engine
    engine = app_db.make_engine(f"sqlite+aiosqlite:///{data_dir / 'geoagent.db'}")
    monkeypatch.setattr(app_db, "engine", engine)
    app_db.SessionLocal.configure(bind=engine)
    await app_db.init_db()

    yield {
        "data_dir": data_dir,
        "runs_dir": settings.runs_dir,
        "session_factory": app_db.SessionLocal,
        "engine": engine,
    }

    app_db.SessionLocal.configure(bind=original_engine)
    await engine.dispose()


@pytest.fixture
async def client(test_env) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
