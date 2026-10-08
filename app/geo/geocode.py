import asyncio
import time

import httpx
import shapely.geometry
from pyproj import Geod
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.config import NOMINATIM_USER_AGENT
from app.geo.types import GeocodeResult

_last_nominatim_time: float = 0.0
_nominatim_lock = asyncio.Lock()
_geod = Geod(ellps="WGS84")


def compute_geodesic_area_km2(geom: shapely.geometry.base.BaseGeometry) -> float:
    """Compute true geodesic area in km2 on WGS84 ellipsoid."""
    # Geod returns signed area by ring winding, so a MultiPolygon with mixed orientations
    # would cancel out. Orient each part counter-clockwise (holes clockwise) before summing.
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    area_m2 = sum(
        _geod.geometry_area_perimeter(orient(p, sign=1.0))[0]
        for p in parts
        if p.geom_type == "Polygon"
    )
    return round(abs(area_m2) / 1_000_000.0, 2)


async def geocode(place: str) -> GeocodeResult:
    """Resolve a place name to an AOI using OSM Nominatim.

    Throttled to at most 1 request per second.
    """
    global _last_nominatim_time

    place_clean = place.strip().replace("\n", " ")[:200]

    async with _nominatim_lock:
        now = time.monotonic()
        elapsed = now - _last_nominatim_time
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _last_nominatim_time = time.monotonic()

        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": place_clean,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "polygon_threshold": 0.0005,
            "limit": 1,
        }
        headers = {
            "User-Agent": NOMINATIM_USER_AGENT,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 429:
                    raise RuntimeError("Nominatim rate limit reached. Please wait a moment.")
                if resp.status_code != 200:
                    raise RuntimeError(f"Nominatim geocoding failed with status {resp.status_code}")
                data = resp.json()
        except httpx.TimeoutException as e:
            raise RuntimeError(f"Geocoding service timed out for '{place_clean}'") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"Geocoding network error: {e}") from e

    if not data or not isinstance(data, list) or len(data) == 0:
        raise ValueError(f"No location found for '{place_clean}'")

    top_result = data[0]
    raw_bbox = top_result.get("boundingbox", [])  # [min_lat, max_lat, min_lon, max_lon]
    if len(raw_bbox) != 4:
        raise ValueError(f"Invalid bounding box returned from geocoder for '{place_clean}'")

    min_lat, max_lat = float(raw_bbox[0]), float(raw_bbox[1])
    min_lon, max_lon = float(raw_bbox[2]), float(raw_bbox[3])
    bbox = [min_lon, min_lat, max_lon, max_lat]

    raw_geojson = top_result.get("geojson")
    geom = None
    if raw_geojson and raw_geojson.get("type") in ("Polygon", "MultiPolygon"):
        try:
            candidate_geom = shapely.geometry.shape(raw_geojson)
            geom = valid_polygonal(candidate_geom)
        except (ValueError, TypeError, shapely.errors.GEOSException):
            geom = None

    if geom is None or geom.is_empty:
        geom = shapely.geometry.box(*bbox)

    area_km2 = compute_geodesic_area_km2(geom)
    raw_name = top_result.get("display_name", place_clean)
    name = raw_name.replace("\n", " ").strip()[:120]

    return GeocodeResult(
        name=name,
        source="geocoded",
        geometry=shapely.geometry.mapping(geom),
        bbox=bbox,
        area_km2=area_km2,
    )


def valid_polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Repair a geometry and keep only its polygonal parts.

    make_valid can return a GeometryCollection (polygon plus stray lines) or a bare
    LineString/Point for degenerate input. OSMnx only accepts Polygon/MultiPolygon, so
    anything else is dropped. Returns an empty Polygon when no area is left; callers
    decide the fallback.
    """
    fixed = make_valid(geom)
    if fixed.geom_type in ("Polygon", "MultiPolygon"):
        return fixed
    parts = [g for g in getattr(fixed, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    if not parts:
        return Polygon()
    return unary_union(parts)
