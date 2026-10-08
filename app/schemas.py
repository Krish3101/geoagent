from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AOISchema(BaseModel):
    name: str
    source: str  # geocoded | derived
    area_km2: float
    bbox: list[float]  # [minx, miny, maxx, maxy]
    geometry: dict[str, Any]


class SessionResponse(BaseModel):
    id: str
    aoi: AOISchema | None = None
    created_at: datetime


class MessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class MessageCreateResponse(BaseModel):
    task_id: str
    message_id: str
    status: str
    events_url: str


class MessageResponse(BaseModel):
    id: str
    session_id: str
    task_id: str | None = None
    task_status: str | None = None
    role: str
    content: str
    created_at: datetime


class ArtifactItem(BaseModel):
    id: str
    kind: str
    filename: str
    size_bytes: int
    bounds: list[float]
    meta: dict[str, Any]
    download_url: str
