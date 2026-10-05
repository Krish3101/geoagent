"""Tests that call Nominatim, Overpass and Planetary Computer. Run with: pytest -m network"""

from pathlib import Path

import pytest


@pytest.mark.network
@pytest.mark.asyncio
async def test_geocode_finds_central_park():
    """Real Nominatim lookup returns a polygon of a sensible size."""
    from app.geo.geocode import geocode

    aoi = await geocode("Central Park, New York")

    assert "Central Park" in aoi.name
    assert aoi.geometry["type"] in ("Polygon", "MultiPolygon")
    assert 2 < aoi.area_km2 < 5


@pytest.mark.network
@pytest.mark.asyncio
async def test_vector_export_is_valid_geojson(tmp_path: Path):
    """Real OSMnx query produces valid GeoJSON in EPSG:4326."""
    import geopandas as gpd

    from app.geo.vector import extract_vector_layer

    central_park_aoi = {
        "name": "Central Park",
        "area_km2": 3.41,
        "bbox": [-73.9819, 40.7648, -73.9498, 40.7968],
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-73.9819, 40.7648],
                    [-73.9498, 40.7648],
                    [-73.9498, 40.7968],
                    [-73.9819, 40.7968],
                    [-73.9819, 40.7648],
                ]
            ],
        },
    }

    artifact = extract_vector_layer(central_park_aoi, "landuse", tmp_path, "cp")

    gdf_json = gpd.read_file(tmp_path / artifact.filename)
    assert gdf_json.crs.to_epsg() == 4326
    assert len(gdf_json) > 0


@pytest.mark.network
@pytest.mark.asyncio
async def test_ndvi_export_is_a_valid_geotiff(tmp_path: Path):
    """Real Planetary Computer STAC search & NDVI GeoTIFF generation."""
    import rioxarray

    from app.geo.ndvi import fetch_ndvi_product

    small_aoi = {
        "name": "Test Farm",
        "bbox": [-120.1, 36.1, -120.08, 36.12],
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-120.1, 36.1],
                    [-120.08, 36.1],
                    [-120.08, 36.12],
                    [-120.1, 36.12],
                    [-120.1, 36.1],
                ]
            ],
        },
    }

    res = fetch_ndvi_product(
        small_aoi, "2024-06-01", "2024-06-30", tmp_path, "farm", max_cloud_cover=20
    )
    rds = rioxarray.open_rasterio(tmp_path / res.artifact.filename)
    assert rds.rio.crs is not None
    assert str(rds.dtype) == "float32"
