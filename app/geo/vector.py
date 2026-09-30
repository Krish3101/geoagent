from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import osmnx as ox
import shapely.geometry
from shapely.validation import make_valid

from app.config import settings

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


def extract_vector_layer(
    aoi: dict[str, Any],
    layer: str,
    output_dir: Path,
    slug: str,
) -> list[dict[str, Any]]:
    """Extract a single OSM vector layer for an AOI and export GeoJSON + GPKG.

    "boundary" is the outline of the place itself, as geocoded. Every other layer is what
    OpenStreetMap has inside that outline.
    """
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

    polygon = make_valid(shapely.geometry.shape(geom_dict))

    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = output_dir / f"{slug}_{layer}.geojson"
    gpkg_path = output_dir / f"{slug}_{layer}.gpkg"

    if layer == "boundary":
        gdf = gpd.GeoDataFrame({"name": [aoi.get("name")]}, geometry=[polygon], crs="EPSG:4326")
    else:
        try:
            gdf = ox.features_from_polygon(polygon, LAYER_TAG_MAP[layer])
        except Exception as e:
            # If no elements are found, OSMnx may raise or return empty
            if "No data elements in server response" in str(e) or "empty" in str(e).lower():
                gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
            else:
                raise RuntimeError(f"Overpass extraction failed for layer '{layer}': {e}") from e

    if gdf is None or gdf.empty:
        gdf = gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")

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

    # Stringify complex object-dtype columns (lists, dicts) before GeoPackage write
    gpkg_gdf = gdf.copy()
    for col in gpkg_gdf.columns:
        if col != "geometry" and gpkg_gdf[col].dtype == "object":
            gpkg_gdf[col] = gpkg_gdf[col].apply(
                lambda val: str(val) if isinstance(val, (dict, list, set, tuple)) else val
            )

    gpkg_gdf.to_file(gpkg_path, driver="GPKG")

    artifacts = [
        {
            "kind": "vector",
            "filename": geojson_path.name,
            "filepath": geojson_path,
            "size_bytes": geojson_path.stat().st_size,
            "bounds": bounds,
            "meta": {
                "layer": layer,
                "feature_count": feature_count,
                "crs": "EPSG:4326",
                "format": "geojson",
            },
        },
        {
            "kind": "vector",
            "filename": gpkg_path.name,
            "filepath": gpkg_path,
            "size_bytes": gpkg_path.stat().st_size,
            "bounds": bounds,
            "meta": {
                "layer": layer,
                "feature_count": feature_count,
                "crs": "EPSG:4326",
                "format": "gpkg",
            },
        },
    ]

    return artifacts
