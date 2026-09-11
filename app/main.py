import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import init_db
from app.routes import router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("geoagent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing GeoAgent database...")
    await init_db()
    logger.info("GeoAgent ready.")
    yield


app = FastAPI(
    title="GeoAgent",
    description="Conversational assistant that turns plain-language requests into GIS files",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)

web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


@app.get("/", response_class=FileResponse)
async def serve_index():
    index_file = web_dir / "index.html"
    return FileResponse(index_file)
