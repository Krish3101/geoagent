import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import SessionLocal, init_db
from app.routes import router
from app.tasks.state import recover_stuck_tasks

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("geoagent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing GeoAgent database...")
    await init_db()
    recovered = await recover_stuck_tasks()
    if recovered:
        logger.info("Recovered %d stuck tasks on startup.", recovered)
    logger.info("GeoAgent ready.")
    yield


app = FastAPI(
    title="GeoAgent",
    description="Conversational assistant that turns plain-language requests into GIS files",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/api/health")
async def health_check():
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "error", "db": "unreachable"})
    return {"status": "ok", "db": "ok", "llm_key": bool(settings.openrouter_api_key)}


web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


@app.get("/", response_class=FileResponse)
async def serve_index():
    index_file = web_dir / "index.html"
    return FileResponse(index_file)
