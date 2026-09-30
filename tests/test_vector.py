from pathlib import Path
from typing import get_args

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from app.geo import vector


def test_all_layers_have_tags():
    # Every layer except the boundary (the AOI outline itself) is an OpenStreetMap query.
    valid_layers = [lyr for lyr in get_args(vector.Layer) if lyr != "boundary"]
    for lyr in valid_layers:
        assert lyr in vector.LAYER_TAG_MAP
        assert len(vector.LAYER_TAG_MAP[lyr]) > 0


def test_area_guard_trips_above_750_for_non_boundary(tmp_path: Path):
    large_aoi = {
        "name": "Huge State",
        "area_km2": 800.0,
        "bbox": [-100, 30, -90, 40],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-100, 30], [-90, 30], [-90, 40], [-100, 40], [-100, 30]]],
        },
    }

    # Non-boundary layer trips guard
    with pytest.raises(ValueError, match="exceeds the maximum allowed 750"):
        vector.extract_vector_layer(large_aoi, "buildings", tmp_path, "huge")


def test_area_guard_exempts_boundary(tmp_path: Path, monkeypatch):
    large_aoi = {
        "name": "Huge Region",
        "area_km2": 1500.0,
        "bbox": [-100, 30, -90, 40],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-100, 30], [-90, 30], [-90, 40], [-100, 40], [-100, 30]]],
        },
    }

    # The boundary is the AOI's own outline, so OpenStreetMap should never be queried.
    def fail(*args):
        raise AssertionError("boundary should not query OpenStreetMap")

    monkeypatch.setattr(vector.ox, "features_from_polygon", fail)

    artifacts = vector.extract_vector_layer(large_aoi, "boundary", tmp_path, "huge_region")
    assert len(artifacts) == 2  # geojson and gpkg
    assert artifacts[0]["meta"]["feature_count"] == 1

    gdf = gpd.read_file(tmp_path / "huge_region_boundary.geojson")
    assert gdf.iloc[0]["name"] == "Huge Region"
    assert gdf.geometry.iloc[0].equals(Polygon([[-100, 30], [-90, 30], [-90, 40], [-100, 40]]))


def test_object_dtype_stringified_in_gpkg(tmp_path: Path, monkeypatch):
    aoi = {
        "name": "Small Park",
        "area_km2": 2.0,
        "bbox": [0, 0, 1, 1],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    }

    # Dataframe with object dtype containing Python lists/dicts (which causes fiona GPKG issues)
    mock_gdf = gpd.GeoDataFrame(
        [
            {
                "geometry": box(0.1, 0.1, 0.2, 0.2),
                "tags_dict": {"foo": "bar"},
                "list_col": [1, 2, 3],
            }
        ],
        crs="EPSG:4326",
    )
    monkeypatch.setattr(vector.ox, "features_from_polygon", lambda poly, tags: mock_gdf)

    vector.extract_vector_layer(aoi, "buildings", tmp_path, "small_park")

    # Read written GPKG back and verify it succeeded without crash
    gpkg_file = tmp_path / "small_park_buildings.gpkg"
    read_gdf = gpd.read_file(gpkg_file)
    assert len(read_gdf) == 1
    assert isinstance(read_gdf["tags_dict"].iloc[0], str)
