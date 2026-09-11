import asyncio
import time
from typing import Any

import httpx
import shapely.geometry
from pyproj import Geod
from shapely.validation import make_valid

from app.config import settings

_last_nominatim_time: float = 0.0
_nominatim_lock = asyncio.Lock()
_geod = Geod(ellps="WGS84")


def compute_geodesic_area_km2(geom: shapely.geometry.base.BaseGeometry) -> float:
    """Compute true geodesic area in km2 on WGS84 ellipsoid."""
    area_m2, _ = _geod.geometry_area_perimeter(geom)
    return round(abs(area_m2) / 1_000_000.0, 2)


async def geocode(place: str) -> dict[str, Any]:
    """Resolve a place name to an AOI dictionary using OSM Nominatim.

    Throttled to at most 1 request per second.
    Returns:
        {
            "name": str,
            "source": "geocoded",
            "geometry": dict, # GeoJSON geometry
            "bbox": [minx, miny, maxx, maxy],
            "area_km2": float
        }
    """
    global _last_nominatim_time

    async with _nominatim_lock:
        now = time.monotonic()
        elapsed = now - _last_nominatim_time
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _last_nominatim_time = time.monotonic()

        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": place,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "limit": 1,
        }
        headers = {
            "User-Agent": settings.nominatim_user_agent,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Nominatim geocoding failed with status {resp.status_code}")
            data = resp.json()

    if not data or not isinstance(data, list) or len(data) == 0:
        raise ValueError(f"No location found for '{place}'")

    top_result = data[0]
    raw_bbox = top_result.get("boundingbox", [])  # [min_lat, max_lat, min_lon, max_lon]
    if len(raw_bbox) != 4:
        raise ValueError(f"Invalid bounding box returned from geocoder for '{place}'")

    min_lat, max_lat = float(raw_bbox[0]), float(raw_bbox[1])
    min_lon, max_lon = float(raw_bbox[2]), float(raw_bbox[3])
    bbox = [min_lon, min_lat, max_lon, max_lat]

    raw_geojson = top_result.get("geojson")
    geom = None
    if raw_geojson and raw_geojson.get("type") in ("Polygon", "MultiPolygon"):
        try:
            candidate_geom = shapely.geometry.shape(raw_geojson)
            geom = make_valid(candidate_geom)
        except Exception:
            geom = None

    if geom is None or geom.is_empty:
        geom = shapely.geometry.box(*bbox)

    area_km2 = compute_geodesic_area_km2(geom)
    name = top_result.get("display_name", place)

    return {
        "name": name,
        "source": "geocoded",
        "geometry": shapely.geometry.mapping(geom),
        "bbox": bbox,
        "area_km2": area_km2,
    }
