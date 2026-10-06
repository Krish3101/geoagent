from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import osmnx as ox
import requests
import shapely.geometry
from shapely.validation import make_valid

from app.config import settings
from app.geo.types import GeoArtifact
from app.geo.validity import valid_polygonal

Layer = Literal[
    "boundary",
    "buildings",
    "roads",
    "waterways",
    "landuse",
    "amenities",
    "natural",
]

LAYER_TAG_MAP: dict[str, dict[str, Any]] = {
    "buildings": {"building": True},
    "roads": {"highway": True},
    "waterways": {"waterway": True},
    "landuse": {"landuse": True},
    "amenities": {"amenity": True},
    "natural": {"natural": True},
}


def _configure_osmnx() -> None:
    ox.settings.http_user_agent = settings.nominatim_user_agent
    ox.settings.cache_folder = settings.data_dir / "osm_cache"
    ox.settings.requests_timeout = 60
    if settings.overpass_url:
        ox.settings.overpass_url = settings.overpass_url


def extract_vector_layer(
    aoi: dict[str, Any],
    layer: str,
    output_dir: Path,
    slug: str,
    task_id: str = "",
) -> GeoArtifact:
    """Extract a single OSM vector layer for an AOI and export GeoJSON only.

    "boundary" is the outline of the place itself, as geocoded. Every other layer is what
    OpenStreetMap has inside that outline.
    """
    _configure_osmnx()

    if layer != "boundary" and layer not in LAYER_TAG_MAP:
        valid = ["boundary", *LAYER_TAG_MAP]
        raise ValueError(f"Unknown layer '{layer}'. Must be one of {valid}")

    area_km2 = aoi.get("area_km2", 0.0)
    if area_km2 > settings.vector_max_area_km2 and layer != "boundary":
        raise ValueError(
            f"Area is {area_km2:.1f} km², which exceeds the maximum allowed "
            f"{settings.vector_max_area_km2} km² for detailed vector extraction. "
            "Please narrow your area of interest or request only the boundary."
        )

    geom_dict = aoi.get("geometry")
    if not geom_dict:
        raise ValueError("AOI has no geometry")

    polygon = valid_polygonal(shapely.geometry.shape(geom_dict))
    if polygon.is_empty:
        # Degenerate outline (a line or point after repair): the bbox still bounds the place.
        bbox = aoi.get("bbox")
        if not bbox:
            raise ValueError("AOI geometry has no area")
        polygon = shapely.geometry.box(*bbox)

    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = output_dir / f"{slug}_{layer}.geojson"

    if layer == "boundary":
        gdf = gpd.GeoDataFrame({"name": [aoi.get("name")]}, geometry=[polygon], crs="EPSG:4326")
    else:
        try:
            gdf = ox.features_from_polygon(polygon, LAYER_TAG_MAP[layer])
        except (
            ox._errors.InsufficientResponseError,
            ox._errors.ResponseStatusCodeError,
            requests.exceptions.RequestException,
            ValueError,
            RuntimeError,
        ) as e:
            # If no elements are found, OSMnx may raise or return empty
            err_msg = str(e).lower()
            if "no data elements" in err_msg or "empty" in err_msg:
                gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
            else:
                raise RuntimeError(f"Overpass extraction failed for layer '{layer}': {e}") from e

    if gdf is None or gdf.empty:
        gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")

    # Feature count limit to avoid out-of-memory and browser freeze
    if len(gdf) > 200_000:
        raise ValueError(
            f"Layer '{layer}' returned {len(gdf):,} features, exceeding the limit of 200,000. "
            "Please narrow your area of interest."
        )

    # Ensure CRS is EPSG:4326
    if gdf.crs is None:
        gdf.set_crs(epsg=4326, inplace=True)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    gdf["geometry"] = gdf["geometry"].apply(lambda g: make_valid(g) if g is not None else None)
    gdf = gdf[gdf["geometry"].notnull() & ~gdf["geometry"].is_empty]

    if not gdf.empty:
        bounds = [round(b, 6) for b in gdf.total_bounds.tolist()]
    else:
        bounds = aoi.get("bbox", [-180.0, -90.0, 180.0, 90.0])

    feature_count = len(gdf)

    gdf.to_file(geojson_path, driver="GeoJSON")

    rel_path = f"runs/{task_id}/vector/{geojson_path.name}" if task_id else geojson_path.name

    return GeoArtifact(
        kind="vector",
        filename=geojson_path.name,
        relative_path=rel_path,
        size_bytes=geojson_path.stat().st_size,
        bounds=bounds,
        meta={
            "layer": layer,
            "feature_count": feature_count,
            "crs": "EPSG:4326",
            "format": "geojson",
        },
    )
