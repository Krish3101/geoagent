import pytest
from httpx import AsyncClient

from app.models import Session, Task


@pytest.mark.asyncio
async def test_session_aoi_retrieval(client: AsyncClient, test_env):
    """Session stores and returns AOI info accurately."""
    factory = test_env["session_factory"]

    async with factory() as session:
        s = Session(
            aoi_name="Central Park",
            aoi_source="geocoded",
            aoi_geometry={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            aoi_bbox=[-73.98, 40.76, -73.95, 40.79],
            aoi_area_km2=3.41,
        )
        session.add(s)
        await session.commit()
        sid = s.id

    res = await client.get(f"/api/sessions/{sid}")
    assert res.status_code == 200
    data = res.json()
    assert data["aoi"]["name"] == "Central Park"
    assert data["aoi"]["area_km2"] == 3.41
    assert data["aoi"]["source"] == "geocoded"


@pytest.mark.asyncio
async def test_upload_endpoint_is_removed(client: AsyncClient):
    """PUT /sessions/{sid}/aoi was cut and should not be available."""
    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    put_res = await client.put(
        f"/api/sessions/{sid}/aoi",
        json={"type": "Polygon", "coordinates": []},
    )
    # 405 Method Not Allowed
    assert put_res.status_code in (404, 405)


@pytest.mark.asyncio
async def test_admission_control_rejects_second_active_task(client: AsyncClient, test_env):
    """Submitting a message while a task is already queued or running returns 409."""
    factory = test_env["session_factory"]

    sess_res = await client.post("/api/sessions")
    sid = sess_res.json()["id"]

    # Seed an active task in 'running' state
    async with factory() as session:
        t = Task(
            session_id=sid,
            status="running",
            prompt="First long running task",
        )
        session.add(t)
        await session.commit()

    # Try to post another message to the same session
    post_res = await client.post(
        f"/api/sessions/{sid}/messages",
        json={"content": "Second task"},
    )
    assert post_res.status_code == 409
    assert "already running" in post_res.json()["detail"].lower()
