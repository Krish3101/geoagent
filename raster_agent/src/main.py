from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.core.config import settings
from shared import setup_logging

# Setup centralized logging
setup_logging()

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        description="GeoAgent v4 Raster Agent API",
    )

    # Set all CORS enabled origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from src.api.routes import router as chat_router
    from src.api.websockets import router as ws_router
    from src.core.database import Base, engine
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    
    # Create tables
    Base.metadata.create_all(bind=engine)

    app.include_router(chat_router, prefix=settings.API_V1_STR)
    app.include_router(ws_router)
    
    app.mount("/static", StaticFiles(directory="src/static"), name="static")
    
    import os
    os.makedirs("runs", exist_ok=True)
    app.mount("/runs", StaticFiles(directory="runs"), name="runs")
    
    @app.get("/")
    async def serve_index():
        return FileResponse("src/static/index.html")

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8002, reload=True)
