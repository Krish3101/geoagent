from pathlib import Path
from typing import get_args

import geopandas as gpd
import pytest
from shapely.geometry import GeometryCollection, LineString, Polygon

from app.geo import vector
from app.geo.validity import valid_polygonal


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

    artifact = vector.extract_vector_layer(large_aoi, "boundary", tmp_path, "huge_region")
    assert artifact.meta["feature_count"] == 1
    assert artifact.filename.endswith(".geojson")

    gdf = gpd.read_file(tmp_path / "huge_region_boundary.geojson")
    assert gdf.iloc[0]["name"] == "Huge Region"
    assert gdf.geometry.iloc[0].equals(Polygon([[-100, 30], [-90, 30], [-90, 40], [-100, 40]]))


def test_feature_count_limit_trips_above_200k(tmp_path: Path, monkeypatch):
    aoi = {
        "name": "Dense City",
        "area_km2": 50.0,
        "bbox": [0, 0, 1, 1],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    }

    class FakeHugeGDF(list):
        def __init__(self):
            super().__init__([None] * 200_001)
            self.empty = False
            self.crs = "EPSG:4326"

        def __len__(self):
            return 200_001

    monkeypatch.setattr(vector.ox, "features_from_polygon", lambda *args, **kwargs: FakeHugeGDF())

    with pytest.raises(ValueError, match="exceeding the limit of 200,000"):
        vector.extract_vector_layer(aoi, "buildings", tmp_path, "dense")


def test_overpass_url_setting_reaches_osmnx(monkeypatch):
    monkeypatch.setattr(vector.ox.settings, "overpass_url", vector.ox.settings.overpass_url)
    monkeypatch.setattr(vector.settings, "overpass_url", "https://overpass.example/api")
    vector._configure_osmnx()
    assert vector.ox.settings.overpass_url == "https://overpass.example/api"


def test_valid_polygonal_repairs_bowtie():
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    fixed = valid_polygonal(bowtie)
    assert fixed.geom_type in ("Polygon", "MultiPolygon")
    assert fixed.is_valid and fixed.area > 0


def test_valid_polygonal_drops_lines_from_collection():
    # a ring touching itself along an edge makes make_valid return polygon + line pieces
    geom = GeometryCollection(
        [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]), LineString([(2, 2), (3, 3)])]
    )
    fixed = valid_polygonal(geom)
    assert fixed.geom_type == "Polygon"
    assert fixed.area == 1


def test_valid_polygonal_pure_line_is_empty():
    assert valid_polygonal(LineString([(0, 0), (1, 1)])).is_empty


def test_extract_boundary_falls_back_to_bbox_for_degenerate_geometry(tmp_path: Path):
    aoi = {
        "name": "Sliver",
        "area_km2": 1.0,
        "bbox": [0, 0, 1, 1],
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
    }
    art = vector.extract_vector_layer(aoi, "boundary", tmp_path, "sliver")
    assert art.bounds == [0, 0, 1, 1]


def test_repeated_extraction_does_not_overwrite_earlier_file(tmp_path: Path):
    aoi = {
        "name": "Twice",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 0.01], [0.01, 0.01], [0, 0]]]},
        "bbox": [0, 0, 0.01, 0.01],
        "area_km2": 1.0,
    }
    first = vector.extract_vector_layer(
        aoi=aoi, layer="boundary", output_dir=tmp_path, slug="twice"
    )
    second = vector.extract_vector_layer(
        aoi=aoi, layer="boundary", output_dir=tmp_path, slug="twice"
    )
    assert first.filename == "twice_boundary.geojson"
    assert second.filename == "twice_boundary_2.geojson"
    assert (tmp_path / first.filename).is_file() and (tmp_path / second.filename).is_file()
