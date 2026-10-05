from dataclasses import dataclass, field
from typing import Any


@dataclass
class GeoArtifact:
    kind: str  # "vector" or "raster"
    filename: str
    relative_path: str
    size_bytes: int
    bounds: list[float]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeocodeResult:
    name: str
    source: str
    geometry: dict[str, Any]
    bbox: list[float]
    area_km2: float


@dataclass
class NDVIResult:
    artifact: GeoArtifact
    preview_artifact: GeoArtifact
    resolution: int
    scene_date: str
    cloud_cover: float
