from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LLM_BASE_URL = "https://openrouter.ai/api/v1"
NOMINATIM_USER_AGENT = "geoagent/1.0 (+https://github.com/Krish3101/geoagent)"
TASK_TIMEOUT_S = 300
VECTOR_MAX_AREA_KM2 = 750.0
RASTER_MAX_PIXELS = 25_000_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    model: str = Field(default="nvidia/nemotron-3-super-120b-a12b:free", alias="MODEL")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")

    @property
    def database_url(self) -> str:
        db_path = self.data_dir / "geoagent.db"
        return f"sqlite+aiosqlite:///{db_path.resolve()}"

    @property
    def runs_dir(self) -> Path:
        return (self.data_dir / "runs").resolve()


settings = Settings()
