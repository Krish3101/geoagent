import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.config import settings
import os
import io
import json
import uuid
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

client = TestClient(app)
AUTH_HEADER = {"Authorization": f"Bearer {settings.GEO_AGENT_SECRET_TOKEN}"}
X_AUTH_HEADER = {"X-Auth-Token": settings.GEO_AGENT_SECRET_TOKEN}

# --- FR-19: Access Control ---

def test_auth_missing_token():
    """Unauthenticated requests are rejected with 401."""
    res = client.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 401
    assert "Missing authentication token" in res.json()["detail"]

def test_auth_invalid_token():
    """Requests with an invalid token are rejected with 403."""
    res = client.post("/api/chat", json={"message": "hello"}, headers={"Authorization": "Bearer bad_token"})
    assert res.status_code == 403
    assert "Invalid authentication token" in res.json()["detail"]

def test_auth_bearer_token_accepted():
    """Bearer token authentication is accepted."""
    res = client.post("/api/chat", json={"message": "ping"}, headers=AUTH_HEADER)
    assert res.status_code == 200
    data = res.json()
    assert "run_id" in data
    assert "session_id" in data
    assert data["status"] == "started"

def test_auth_x_auth_token_header_accepted():
    """X-Auth-Token header authentication is accepted."""
    res = client.post("/api/chat", json={"message": "ping"}, headers=X_AUTH_HEADER)
    assert res.status_code == 200
    assert "run_id" in res.json()

def test_upload_geometry_requires_auth():
    """Upload geometry endpoint requires authentication."""
    res_no_auth = client.post("/api/upload_geometry?session_id=test_s", files={"file": ("test.geojson", b"{}")})
    assert res_no_auth.status_code == 401

    res_bad_auth = client.post(
        "/api/upload_geometry?session_id=test_s", 
        files={"file": ("test.geojson", b"{}")},
        headers={"Authorization": "Bearer wrong"}
    )
    assert res_bad_auth.status_code == 403

def test_public_endpoints_do_not_require_auth():
    """Read-only endpoints (history, task status, artifacts) are open."""
    assert client.get("/api/session/nonexistent_session/history").status_code == 200
    assert client.get("/api/task/nonexistent_run").status_code == 200
    assert client.get("/api/task/nonexistent_run/artifacts").status_code == 200


# --- FR-4 & FR-16: Task Status Tracking ---

def test_task_status_unknown_id():
    """Unknown task returns 'unknown' status rather than an error."""
    res = client.get("/api/task/stale_or_unknown_task_123")
    assert res.status_code == 200
    assert res.json() == {"run_id": "stale_or_unknown_task_123", "status": "unknown"}

def test_task_status_lifecycle():
    """Task status progresses from running to completed or failed based on log signals."""
    from src.api.websockets import manager
    run_id = "lifecycle_test_run"

    async def run_lifecycle():
        await manager.send_log(run_id, "Agent started. Analyzing request...")
        status = await manager.get_task_status(run_id)
        assert status == "running"

        await manager.send_log(run_id, "Executing tool...")
        status = await manager.get_task_status(run_id)
        assert status == "running"

        await manager.send_log(run_id, "Task Completed")
        status = await manager.get_task_status(run_id)
        assert status == "completed"

        # Another run for failure
        fail_run = "fail_test_run"
        await manager.send_log(fail_run, "Agent started")
        await manager.send_log(fail_run, "Task Failed: Network error")
        fail_status = await manager.get_task_status(fail_run)
        assert fail_status == "failed"

    asyncio.run(run_lifecycle())

    res_completed = client.get(f"/api/task/{run_id}")
    assert res_completed.status_code == 200
    assert res_completed.json()["status"] == "completed"

    res_failed = client.get("/api/task/fail_test_run")
    assert res_failed.status_code == 200
    assert res_failed.json()["status"] == "failed"


# --- FR-1, FR-2, FR-3: Conversation & History ---

def test_history_unknown_session_returns_empty():
    """Requesting history for an unknown conversation returns empty history rather than an error."""
    res = client.get("/api/session/nonexistent_session_9999/history")
    assert res.status_code == 200
    assert res.json() == {"messages": []}

def test_chat_creates_session_and_records_history():
    """Chat creates session, acknowledges immediately, and records user message."""
    res = client.post("/api/chat", json={"message": "Show boundaries"}, headers=AUTH_HEADER)
    assert res.status_code == 200
    data = res.json()
    session_id = data["session_id"]
    run_id = data["run_id"]
    assert data["reply"] == "I'm working on that for you."

    # Fetch history
    h_res = client.get(f"/api/session/{session_id}/history")
    assert h_res.status_code == 200
    messages = h_res.json()["messages"]
    assert len(messages) >= 1
    user_msg = messages[0]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "Show boundaries"
    assert user_msg["run_id"] == run_id


# --- FR-15: Custom Area of Interest Upload ---

def test_upload_geometry_invalid_json():
    """Invalid non-JSON file is rejected with 400."""
    res = client.post(
        "/api/upload_geometry?session_id=upload_session",
        files={"file": ("test.txt", b"not valid json content")},
        headers=AUTH_HEADER
    )
    assert res.status_code == 400
    assert "not valid JSON" in res.json()["detail"]

def test_upload_geometry_empty_features():
    """GeoJSON file with no features is rejected with 400."""
    empty_geojson = json.dumps({"type": "FeatureCollection", "features": []}).encode("utf-8")
    res = client.post(
        "/api/upload_geometry?session_id=upload_session",
        files={"file": ("empty.geojson", empty_geojson)},
        headers=AUTH_HEADER
    )
    assert res.status_code == 400
    assert "No features" in res.json()["detail"]

def test_upload_geometry_valid_geojson():
    """Valid GeoJSON defines active area and adds system message with bbox."""
    valid_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-73.97, 40.76], [-73.95, 40.76], [-73.95, 40.78], [-73.97, 40.78], [-73.97, 40.76]]
                    ]
                },
                "properties": {}
            }
        ]
    }
    content = json.dumps(valid_geojson).encode("utf-8")
    test_session_id = f"geo_session_{uuid.uuid4().hex[:6]}"
    res = client.post(
        f"/api/upload_geometry?session_id={test_session_id}",
        files={"file": ("central_park.geojson", content)},
        headers=AUTH_HEADER
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "bbox" in data
    assert len(data["bbox"]) == 4

    # Verify session has the system message
    h_res = client.get(f"/api/session/{test_session_id}/history")
    messages = h_res.json()["messages"]
    system_msgs = [m for m in messages if m["role"] == "system"]
    assert len(system_msgs) == 1
    assert "defining the active area of interest" in system_msgs[0]["content"]


# --- FR-17: Artifact Listing & Delivery ---

def test_artifacts_listing_empty():
    """Listing a task that produced nothing returns empty result sets rather than failing."""
    res = client.get("/api/task/empty_run_123/artifacts")
    assert res.status_code == 200
    assert res.json() == {"vector": [], "raster": []}

def test_artifacts_listing_populated(tmp_path):
    """Artifacts are discovered and categorized into vector and raster outputs."""
    run_id = "test_artifact_run"
    vector_dir = Path(settings.RUNS_DIR) / run_id / "outputs" / "vector"
    raster_dir = Path(settings.RUNS_DIR) / run_id / "outputs" / "raster"
    vector_dir.mkdir(parents=True, exist_ok=True)
    raster_dir.mkdir(parents=True, exist_ok=True)

    geojson_file = vector_dir / "central_park_boundary.geojson"
    gpkg_file = vector_dir / "central_park_boundary.gpkg"
    tif_file = raster_dir / "central_park_true_color.tif"

    geojson_file.write_text('{"type": "FeatureCollection", "features": []}')
    gpkg_file.write_text("binary data")
    tif_file.write_text("tiff data")

    try:
        res = client.get(f"/api/task/{run_id}/artifacts")
        assert res.status_code == 200
        data = res.json()
        vector_names = [f["name"] for f in data["vector"]]
        raster_names = [f["name"] for f in data["raster"]]

        assert "central_park_boundary.geojson" in vector_names
        assert "central_park_boundary.gpkg" in vector_names
        assert "central_park_true_color.tif" in raster_names
    finally:
        # Cleanup test artifact files
        import shutil
        shutil.rmtree(Path(settings.RUNS_DIR) / run_id, ignore_errors=True)


# --- FR-5 & NFR-4: StateManager Log Replay & Fault Tolerance ---

def test_state_manager_log_replay():
    """Logs emitted before a subscriber attaches are buffered and replayed."""
    from src.api.websockets import manager
    run_id = "replay_test_run"

    async def test_replay():
        # Emit logs before any subscriber attaches
        await manager.send_log(run_id, "Agent started. Analyzing request: 'Central Park'")
        await manager.send_log(run_id, "Executing geocode for Central Park...")
        await manager.send_log(run_id, "geocode success.")

        # Mock a WebSocket subscriber connecting later
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        await manager.register_connection(run_id, mock_ws)

        # Verify all 3 logs were sent during replay
        sent_messages = [call.args[0] for call in mock_ws.send_text.call_args_list]
        assert "Agent started. Analyzing request: 'Central Park'" in sent_messages
        assert "Executing geocode for Central Park..." in sent_messages
        assert "geocode success." in sent_messages

        await manager.unregister_connection(run_id)

    asyncio.run(test_replay())

def test_state_manager_reconnection_evaluation():
    """StateManager re-evaluates Redis availability on connection checks (NFR-4)."""
    from src.redis import StateManager
    sm = StateManager()
    
    mock_client = AsyncMock()
    sm.redis_client = mock_client

    async def verify_recovery():
        # When ping fails, degrades to local
        mock_client.ping.side_effect = Exception("Connection refused")
        ok1 = await sm.check_connection()
        assert ok1 is False
        assert sm.use_redis is False

        # When ping succeeds again, re-enables Redis (re-evaluates availability)
        mock_client.ping.side_effect = None
        ok2 = await sm.check_connection()
        assert ok2 is True
        assert sm.use_redis is True

    asyncio.run(verify_recovery())
