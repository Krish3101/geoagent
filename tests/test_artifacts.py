import pytest
from httpx import AsyncClient

from app.models import Artifact, Session, Task


@pytest.mark.asyncio
async def test_artifact_row_and_download(client: AsyncClient, test_env):
    factory = test_env["session_factory"]
    runs_dir = test_env["runs_dir"]

    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status="succeeded", prompt="test")
        session.add(t)
        await session.flush()

        task_dir = runs_dir / t.id / "vector"
        task_dir.mkdir(parents=True, exist_ok=True)
        file_path = task_dir / "test_data.geojson"
        file_path.write_text('{"type": "FeatureCollection", "features": []}')

        art = Artifact(
            task_id=t.id,
            kind="vector",
            filename="test_data.geojson",
            relative_path=f"runs/{t.id}/vector/test_data.geojson",
            size_bytes=file_path.stat().st_size,
            bounds=[-73.98, 40.76, -73.94, 40.79],
            meta={"layer": "boundary", "feature_count": 0, "crs": "EPSG:4326"},
        )
        session.add(art)
        await session.commit()
        artifact_id = art.id
        task_id = t.id

    # List artifacts
    list_res = await client.get(f"/api/tasks/{task_id}/artifacts")
    assert list_res.status_code == 200
    data = list_res.json()
    assert len(data["vector"]) == 1
    assert data["vector"][0]["id"] == artifact_id
    assert data["vector"][0]["filename"] == "test_data.geojson"

    # Download artifact
    dl_res = await client.get(f"/api/artifacts/{artifact_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.content == b'{"type": "FeatureCollection", "features": []}'


@pytest.mark.asyncio
async def test_artifact_path_traversal_rejected(client: AsyncClient, test_env):
    factory = test_env["session_factory"]

    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status="succeeded", prompt="traversal test")
        session.add(t)
        await session.flush()

        # Malicious artifact pointing outside runs_dir
        art = Artifact(
            task_id=t.id,
            kind="vector",
            filename="passwd",
            relative_path="runs/../../etc/passwd",
            size_bytes=100,
            bounds=[0, 0, 0, 0],
            meta={},
        )
        session.add(art)
        await session.commit()
        artifact_id = art.id

    res = await client.get(f"/api/artifacts/{artifact_id}/download")
    assert res.status_code in (403, 404)
