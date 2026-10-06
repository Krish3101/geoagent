from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def unique_stem(output_dir: Path, stem: str, suffixes: tuple[str, ...]) -> str:
    """Return stem, or stem_2, stem_3... so no file with these suffixes exists yet.

    A tool can run twice in one task; without this the second run would overwrite the first
    file while both artifact rows point at it.
    """
    candidate, n = stem, 1
    while any((output_dir / f"{candidate}{s}").exists() for s in suffixes):
        n += 1
        candidate = f"{stem}_{n}"
    return candidate


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
