from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # API Configurations
    API_V1_STR: str = "/api"
    PROJECT_NAME: str = "GeoAgent v3"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:8001",
        "http://localhost:8002",
        "http://127.0.0.1:8001",
        "http://127.0.0.1:8002"
    ]
    
    # LLM Settings
    OPENROUTER_API_KEY: Optional[str] = None
    LLM_MODEL: str = "google/gemma-3-27b-it"
    
    # Database Settings
    DATABASE_URL: str = "sqlite:///./geoagent.db"
    
    # Redis Settings
    REDIS_URL: Optional[str] = None
    
    # Auth Settings
    GEO_AGENT_SECRET_TOKEN: str = "default_secret_token_123"
    
    # Execution
    RUNS_DIR: str = "runs"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
