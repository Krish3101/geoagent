import asyncio
import re
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.agent import RunDeps, extract_vector, fetch_imagery
from app.models import Session, Task
from app.runner import (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    log_event,
    transition,
)


@pytest.mark.asyncio
async def test_session_reuses_stored_aoi(test_env, monkeypatch):
    """Turn 1 resolves AOI, Turn 2 reuses stored AOI without geocoding."""
    factory = test_env["session_factory"]

    async with factory() as session:
        coords = [[[4.8, 52.3], [4.9, 52.3], [4.9, 52.4], [4.8, 52.4], [4.8, 52.3]]]
        s = Session(
            aoi_name="Amsterdam, Netherlands",
            aoi_source="geocoded",
            aoi_geometry={"type": "Polygon", "coordinates": coords},
            aoi_bbox=[4.8, 52.3, 4.9, 52.4],
            aoi_area_km2=50.0,
        )
        session.add(s)
        await session.commit()
        session_id = s.id

    called_tools = []

    def mock_extract(aoi, layer, output_dir, slug):
        called_tools.append(("extract_vector_layer", layer))
        return [
            {
                "kind": "vector",
                "filename": f"{slug}_{layer}.geojson",
                "size_bytes": 100,
                "bounds": [4.8, 52.3, 4.9, 52.4],
                "meta": {"layer": layer, "feature_count": 10, "crs": "EPSG:4326"},
            }
        ], None

    monkeypatch.setattr("app.agent.extract_vector_layer", mock_extract)

    # Turn 2: RunDeps has current AOI
    deps = RunDeps(
        task_id="turn-2-task",
        session_id=session_id,
        aoi={
            "name": "Amsterdam, Netherlands",
            "source": "geocoded",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[4.8, 52.3], [4.9, 52.3], [4.9, 52.4], [4.8, 52.4], [4.8, 52.3]]],
            },
            "bbox": [4.8, 52.3, 4.9, 52.4],
            "area_km2": 50.0,
        },
    )

    # Mock log_event and record_artifact to avoid DB FK in pure tool test
    monkeypatch.setattr("app.agent.log_event", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr("app.agent.record_artifact", lambda *args, **kwargs: asyncio.sleep(0))

    # Context with RunDeps
    class DummyContext:
        def __init__(self, deps):
            self.deps = deps

    ctx = DummyContext(deps)
    result = await extract_vector(ctx, ["buildings"])

    assert "Extracted 10 buildings" in result
    assert called_tools == [("extract_vector_layer", "buildings")]
    assert deps.aoi["name"] == "Amsterdam, Netherlands"


@pytest.mark.asyncio
async def test_uploaded_aoi_beats_geocoding(test_env, client: AsyncClient, monkeypatch):
    """Uploaded AOI is used directly without calling Nominatim."""
    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    polygon_geojson = {
        "type": "Polygon",
        "coordinates": [[[12.4, 41.8], [12.5, 41.8], [12.5, 41.9], [12.4, 41.9], [12.4, 41.8]]],
    }

    # Upload AOI
    put_res = await client.put(f"/api/sessions/{sid}/aoi", json=polygon_geojson)
    assert put_res.status_code == 200
    aoi_data = put_res.json()
    assert aoi_data["source"] == "uploaded"

    geocode_called = False

    async def mock_geocode(place):
        nonlocal geocode_called
        geocode_called = True
        return {}

    monkeypatch.setattr("app.agent.geocode", mock_geocode)

    # Tool invocation for imagery
    deps = RunDeps(
        task_id="task-img",
        session_id=sid,
        aoi=aoi_data,
    )

    def mock_fetch_raster(aoi, start_date, end_date, product, output_dir, slug, max_cloud_cover):
        assert aoi["source"] == "uploaded"
        assert aoi["bbox"] == [12.4, 41.8, 12.5, 41.9]
        return (
            {
                "kind": "raster",
                "filename": f"{slug}_{product}.tif",
                "size_bytes": 500,
                "bounds": [12.4, 41.8, 12.5, 41.9],
                "meta": {
                    "product": product,
                    "crs": "EPSG:32633",
                    "resolution_m": 10,
                    "cloud_cover": 2.0,
                },
            },
            10,
            "2024-07-15",
            2.0,
        )

    monkeypatch.setattr("app.agent.fetch_raster_product", mock_fetch_raster)
    monkeypatch.setattr("app.agent.log_event", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr("app.agent.record_artifact", lambda *args, **kwargs: asyncio.sleep(0))

    class DummyContext:
        def __init__(self, deps):
            self.deps = deps

    res = await fetch_imagery(DummyContext(deps), "2024-07-01", "2024-07-31", ["ndvi"], 10)
    assert not geocode_called
    assert "Composited scenes" in res


@pytest.mark.asyncio
async def test_task_state_never_derives_from_log_text(test_env):
    """queued -> running -> succeeded, no status change derives from log text."""
    factory = test_env["session_factory"]

    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="state machine test")
        session.add(t)
        await session.commit()
        tid = t.id

    await transition(tid, STATUS_RUNNING)
    await transition(tid, STATUS_SUCCEEDED)

    # Verify no log-text parsing in app source files
    src_files = list(Path("app").rglob("*.py"))
    pattern = re.compile(r"status\s*=\s*.*log.*", re.IGNORECASE)
    for p in src_files:
        content = p.read_text()
        for line in content.splitlines():
            assert not pattern.search(line), f"Suspicious status-from-log line in {p}: {line}"


@pytest.mark.asyncio
async def test_late_sse_subscriber_gets_every_event(client: AsyncClient, test_env):
    """Connect to SSE stream after events emitted and receive
    every event from seq=1 in order.
    """
    factory = test_env["session_factory"]

    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="SSE test")
        session.add(t)
        await session.commit()
        tid = t.id

    # Emit events before subscriber connects
    await log_event(tid, "starting", "Agent started")
    await log_event(tid, "geocoding", "Resolved to New York")
    await log_event(tid, "vector", "Extracted 50 buildings")
    await log_event(tid, "done", "Completed")

    # Connect to SSE
    resp = await client.get(f"/api/tasks/{tid}/events?after_seq=0")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    lines = [line.strip() for line in resp.text.split("\n") if line.startswith("data:")]
    assert len(lines) == 4
    assert '"seq": 1' in lines[0]
    assert '"seq": 2' in lines[1]
    assert '"seq": 3' in lines[2]
    assert '"seq": 4' in lines[3]
    assert "Completed" in lines[3]


@pytest.mark.asyncio
async def test_oversized_aoi_is_refused_before_any_request():
    """5,000 km² AOI asking for buildings returns clear message and makes no Overpass call."""
    giant_aoi = {
        "name": "Huge State",
        "area_km2": 5000.0,
        "bbox": [-100, 30, -90, 40],
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-100, 30], [-90, 30], [-90, 40], [-100, 40], [-100, 30]]],
        },
    }

    deps = RunDeps(task_id="t-giant", session_id="s-giant", aoi=giant_aoi)

    class DummyContext:
        def __init__(self, deps):
            self.deps = deps

    msg = await extract_vector(DummyContext(deps), ["buildings"])
    assert "exceeds the maximum allowed" in msg
    assert "narrow your area of interest" in msg


# Network tests (deselected by default)
@pytest.mark.network
@pytest.mark.asyncio
async def test_vector_export_is_valid_geojson_and_gpkg(tmp_path: Path):
    """Real OSMnx query produces valid GeoJSON and GPKG in EPSG:4326."""
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

    artifacts, _ = extract_vector_layer(central_park_aoi, "boundary", tmp_path, "cp")
    geojson_art = next(a for a in artifacts if a["filename"].endswith(".geojson"))
    gpkg_art = next(a for a in artifacts if a["filename"].endswith(".gpkg"))

    gdf_json = gpd.read_file(geojson_art["filepath"])
    gdf_gpkg = gpd.read_file(gpkg_art["filepath"])

    assert gdf_json.crs.to_epsg() == 4326
    assert gdf_gpkg.crs.to_epsg() == 4326


@pytest.mark.network
@pytest.mark.asyncio
async def test_ndvi_export_is_a_valid_geotiff(tmp_path: Path):
    """Real Planetary Computer STAC search & NDVI GeoTIFF generation."""
    import rioxarray

    from app.geo.raster import fetch_raster_product

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

    art, res, day, cloud = fetch_raster_product(
        small_aoi, "2024-06-01", "2024-06-30", "ndvi", tmp_path, "farm", max_cloud_cover=20
    )
    rds = rioxarray.open_rasterio(art["filepath"])
    assert rds.rio.crs is not None
    assert str(rds.dtype) == "float32"
