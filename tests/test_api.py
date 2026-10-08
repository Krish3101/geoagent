import asyncio

import pytest
from httpx import AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_session_crud_and_messages(client: AsyncClient):
    # 1. Create session
    res = await client.post("/api/sessions")
    assert res.status_code == 201
    s_data = res.json()
    assert "id" in s_data
    assert s_data["aoi"] is None
    sid = s_data["id"]

    # 2. Get session
    get_res = await client.get(f"/api/sessions/{sid}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == sid
    assert get_res.json()["aoi"] is None

    # 3. Post message (returns 202 immediately and persists message)
    post_msg = await client.post(
        f"/api/sessions/{sid}/messages",
        json={"content": "Hello GeoAgent"},
    )
    assert post_msg.status_code == 202
    msg_data = post_msg.json()
    assert msg_data["status"] == "queued"
    assert "task_id" in msg_data
    assert "message_id" in msg_data
    assert msg_data["events_url"] == f"/api/tasks/{msg_data['task_id']}/events"

    # 4. Get transcript in chronological order
    tr_res = await client.get(f"/api/sessions/{sid}/messages")
    assert tr_res.status_code == 200
    transcript = tr_res.json()
    assert len(transcript) >= 1
    assert transcript[0]["content"] == "Hello GeoAgent"
    assert transcript[0]["role"] == "user"
    assert transcript[0]["task_status"] in ("queued", "running", "failed")


@pytest.mark.asyncio
async def test_unknown_resources_return_404(client: AsyncClient):
    res_sess = await client.get("/api/sessions/non-existent-id")
    assert res_sess.status_code == 404

    res_task = await client.get("/api/tasks/non-existent-task")
    assert res_task.status_code == 404

    res_art = await client.get("/api/artifacts/non-existent-art/download")
    assert res_art.status_code == 404


async def _open_stream(path: str) -> None:
    # Drive the ASGI app by hand: httpx's ASGITransport waits for the full body,
    # and an SSE stream on a stuck task never ends.
    started = asyncio.Event()

    async def receive():
        await asyncio.Event().wait()  # the client never disconnects

    async def send(message):
        started.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
