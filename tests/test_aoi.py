import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_aoi_bare_geometry(client: AsyncClient):
    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    bare_geom = {
        "type": "Polygon",
        "coordinates": [
            [
                [-73.98, 40.76],
                [-73.95, 40.76],
                [-73.95, 40.79],
                [-73.98, 40.79],
                [-73.98, 40.76],
            ]
        ],
    }

    put_res = await client.put(f"/api/sessions/{sid}/aoi", json=bare_geom)
    assert put_res.status_code == 200
    data = put_res.json()
    assert data["name"] == "Uploaded area"
    assert data["source"] == "uploaded"
    assert data["bbox"] == [-73.98, 40.76, -73.95, 40.79]
    assert data["area_km2"] > 0
    # True area of ~3km x ~3.3km is ~8-10 km², not 0.0009 deg^2!
    assert 5.0 < data["area_km2"] < 15.0


@pytest.mark.asyncio
async def test_aoi_feature_and_feature_collection(client: AsyncClient):
    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    # FeatureCollection with 2 features (union)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [10.0, 50.0],
                            [10.1, 50.0],
                            [10.1, 50.1],
                            [10.0, 50.1],
                            [10.0, 50.0],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [10.1, 50.0],
                            [10.2, 50.0],
                            [10.2, 50.1],
                            [10.1, 50.1],
                            [10.1, 50.0],
                        ]
                    ],
                },
            },
        ],
    }

    put_res = await client.put(f"/api/sessions/{sid}/aoi", json=fc)
    assert put_res.status_code == 200
    data = put_res.json()
    assert data["bbox"] == [10.0, 50.0, 10.2, 50.1]
    assert data["area_km2"] > 0


@pytest.mark.asyncio
async def test_aoi_self_intersecting_polygon_repaired(client: AsyncClient):
    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    # Bowtie self-intersecting polygon
    bowtie = {
        "type": "Polygon",
        "coordinates": [
            [
                [0.0, 0.0],
                [1.0, 1.0],
                [0.0, 1.0],
                [1.0, 0.0],
                [0.0, 0.0],
            ]
        ],
    }

    put_res = await client.put(f"/api/sessions/{sid}/aoi", json=bowtie)
    assert put_res.status_code == 200
    data = put_res.json()
    assert data["area_km2"] > 0


@pytest.mark.asyncio
async def test_aoi_out_of_range_coords_fails(client: AsyncClient):
    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    bad_coords = {
        "type": "Polygon",
        "coordinates": [
            [
                [200.0, 40.0],  # Lon > 180!
                [201.0, 40.0],
                [201.0, 41.0],
                [200.0, 41.0],
                [200.0, 40.0],
            ]
        ],
    }

    put_res = await client.put(f"/api/sessions/{sid}/aoi", json=bad_coords)
    assert put_res.status_code == 400
    assert "out of WGS84 range" in put_res.json()["detail"]


@pytest.mark.asyncio
async def test_aoi_empty_collection_fails(client: AsyncClient):
    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    empty_fc = {
        "type": "FeatureCollection",
        "features": [],
    }

    put_res = await client.put(f"/api/sessions/{sid}/aoi", json=empty_fc)
    assert put_res.status_code == 400
