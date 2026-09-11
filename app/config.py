from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    llm_model: str = Field(default="anthropic/claude-sonnet-4.5", alias="LLM_MODEL")
    llm_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="LLM_BASE_URL")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    nominatim_user_agent: str = Field(
        default="geoagent/1.0 (dev@localhost)", alias="NOMINATIM_USER_AGENT"
    )
    vector_max_area_km2: float = Field(default=750.0, alias="VECTOR_MAX_AREA_KM2")
    raster_max_pixels: int = Field(default=40_000_000, alias="RASTER_MAX_PIXELS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def database_url(self) -> str:
        db_path = self.data_dir / "geoagent.db"
        return f"sqlite+aiosqlite:///{db_path.resolve()}"

    @property
    def runs_dir(self) -> Path:
        return (self.data_dir / "runs").resolve()


settings = Settings()
