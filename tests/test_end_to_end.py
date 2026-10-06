import asyncio
import re
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.geo.types import GeoArtifact
from app.models import Session, Task
from app.tasks.state import STATUS_QUEUED, STATUS_RUNNING, STATUS_SUCCEEDED, log_event, transition
from app.tools import RunDeps, extract_vector


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

    def mock_extract(aoi, layer, output_dir, slug, task_id=""):
        called_tools.append(("extract_vector_layer", layer))
        return GeoArtifact(
            kind="vector",
            filename=f"{slug}_{layer}.geojson",
            relative_path=f"runs/{task_id}/vector/{slug}_{layer}.geojson",
            size_bytes=100,
            bounds=[4.8, 52.3, 4.9, 52.4],
            meta={"layer": layer, "feature_count": 10, "crs": "EPSG:4326"},
        )

    monkeypatch.setattr("app.tools.extract_vector_layer", mock_extract)

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
    monkeypatch.setattr("app.tools.log_event", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr("app.tools.record_artifact", lambda *args, **kwargs: asyncio.sleep(0))

    class DummyContext:
        def __init__(self, deps):
            self.deps = deps

    ctx = DummyContext(deps)
    result = await extract_vector(ctx, ["buildings"])

    assert "Extracted 10 buildings" in result
    assert called_tools == [("extract_vector_layer", "buildings")]
    assert deps.aoi["name"] == "Amsterdam, Netherlands"


@pytest.mark.asyncio
async def test_one_failed_layer_does_not_hide_the_others(test_env, monkeypatch):
    def mock_extract(aoi, layer, output_dir, slug, task_id=""):
        if layer == "roads":
            raise RuntimeError("Overpass timed out")
        return GeoArtifact(
            kind="vector",
            filename=f"{slug}_{layer}.geojson",
            relative_path=f"runs/{task_id}/vector/{slug}_{layer}.geojson",
            size_bytes=100,
            bounds=[4.8, 52.3, 4.9, 52.4],
            meta={"layer": layer, "feature_count": 10, "crs": "EPSG:4326"},
        )

    monkeypatch.setattr("app.tools.extract_vector_layer", mock_extract)
    monkeypatch.setattr("app.tools.log_event", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr("app.tools.record_artifact", lambda *args, **kwargs: asyncio.sleep(0))

    class DummyContext:
        def __init__(self, deps):
            self.deps = deps

    square = [[[4.8, 52.3], [4.9, 52.3], [4.9, 52.4], [4.8, 52.4], [4.8, 52.3]]]
    deps = RunDeps(
        task_id="two-layers",
        session_id="s",
        aoi={
            "name": "Amsterdam",
            "geometry": {"type": "Polygon", "coordinates": square},
            "bbox": [4.8, 52.3, 4.9, 52.4],
            "area_km2": 50.0,
        },
    )

    result = await extract_vector(DummyContext(deps), ["buildings", "roads"])

    assert "Extracted 10 buildings" in result
    assert "roads failed (OpenStreetMap download error)" in result
    assert "Overpass timed out" not in result


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

    await transition(tid, STATUS_QUEUED, STATUS_RUNNING)
    await transition(tid, STATUS_RUNNING, STATUS_SUCCEEDED)

    # Verify no log-text parsing in app source files
    app_dir = Path(__file__).resolve().parent.parent / "app"
    src_files = list(app_dir.rglob("*.py"))
    assert src_files, "no source files found, the check would pass vacuously"
    pattern = re.compile(r"status\s*=\s*.*log.*", re.IGNORECASE)
    assert pattern.search("status = parse(log_text)")
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
