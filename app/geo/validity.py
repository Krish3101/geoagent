from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid


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
