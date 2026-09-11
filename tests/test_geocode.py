import time

import httpx
import pytest

from app.geo import geocode


@pytest.mark.asyncio
async def test_geocode_hit_and_bbox_conversion(monkeypatch):
    mock_response = [
        {
            "display_name": "Central Park, Manhattan, New York, USA",
            "boundingbox": ["40.7648", "40.7968", "-73.9819", "-73.9498"],
            "geojson": {
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
    ]

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None, headers=None):
            assert "nominatim.openstreetmap.org" in url
            assert "User-Agent" in headers
            return httpx.Response(200, json=mock_response)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    res = await geocode.geocode("Central Park")
    assert res["name"] == "Central Park, Manhattan, New York, USA"
    assert res["source"] == "geocoded"
    # minx, miny, maxx, maxy
    assert res["bbox"] == [-73.9819, 40.7648, -73.9498, 40.7968]
    assert res["area_km2"] > 0
    assert res["geometry"]["type"] in ("Polygon", "MultiPolygon")


@pytest.mark.asyncio
async def test_geocode_no_match_raises(monkeypatch):
    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None, headers=None):
            return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    with pytest.raises(ValueError, match="No location found for 'Nowhere Land'"):
        await geocode.geocode("Nowhere Land")


@pytest.mark.asyncio
async def test_geocode_throttle_enforces_one_second(monkeypatch):
    call_times = []

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None, headers=None):
            call_times.append(time.monotonic())
            return httpx.Response(
                200,
                json=[
                    {
                        "display_name": "Test Place",
                        "boundingbox": ["10", "11", "20", "21"],
                    }
                ],
            )

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    # Force last time to now
    geocode._last_nominatim_time = time.monotonic()

    t0 = time.monotonic()
    await geocode.geocode("Place 1")
    t1 = time.monotonic()

    assert (t1 - t0) >= 0.95  # Throttled by ~1.0s
