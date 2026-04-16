from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # API Configurations
    API_V1_STR: str = "/api"
    PROJECT_NAME: str = "GeoAgent v3"
    
    # LLM Settings
    OPENROUTER_API_KEY: Optional[str] = None
    LLM_MODEL: str = "google/gemma-3-27b-it"
    
    # Database Settings
    DATABASE_URL: str = "sqlite:///./geoagent.db"
    
    # Execution
    RUNS_DIR: str = "runs"
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
