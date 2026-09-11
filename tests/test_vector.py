from pathlib import Path
from typing import get_args

import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from app.geo import vector


def test_all_layers_have_tags():
    valid_layers = get_args(vector.Layer)
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

    # Mock osmnx.features_from_polygon
    mock_gdf = gpd.GeoDataFrame(
        [{"geometry": Polygon([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]), "name": "Boundary Line"}],
        crs="EPSG:4326",
    )
    monkeypatch.setattr(vector.ox, "features_from_polygon", lambda poly, tags: mock_gdf)

    artifacts, refined_aoi = vector.extract_vector_layer(
        large_aoi, "boundary", tmp_path, "huge_region"
    )
    assert len(artifacts) == 2  # geojson and gpkg
    assert (tmp_path / "huge_region_boundary.geojson").exists()
    assert (tmp_path / "huge_region_boundary.gpkg").exists()
    assert refined_aoi is not None
    assert refined_aoi["source"] == "derived"


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

    artifacts, _ = vector.extract_vector_layer(aoi, "buildings", tmp_path, "small_park")

    # Read written GPKG back and verify it succeeded without crash
    gpkg_file = tmp_path / "small_park_buildings.gpkg"
    read_gdf = gpd.read_file(gpkg_file)
    assert len(read_gdf) == 1
    assert isinstance(read_gdf["tags_dict"].iloc[0], str)
