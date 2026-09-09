from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.config import settings
from src.logging import setup_logging
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Setup centralized logging
setup_logging(settings.LOG_LEVEL)

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        description="Unified GeoAgent API",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from src.api.routes import router as chat_router
    from src.api.websockets import router as ws_router
    from src.database import Base, engine
    
    Base.metadata.create_all(bind=engine)

    app.include_router(chat_router, prefix=settings.API_V1_STR)
    app.include_router(ws_router)
    
    frontend_dir = "frontend" if os.path.exists("frontend") else "/app/frontend"
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    
    os.makedirs(settings.RUNS_DIR, exist_ok=True)
    app.mount(f"/{settings.RUNS_DIR}", StaticFiles(directory=settings.RUNS_DIR), name="runs")
    
    @app.get("/")
    async def serve_index():
        return FileResponse(f"{frontend_dir}/index.html")

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
