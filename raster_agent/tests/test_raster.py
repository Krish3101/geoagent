import pytest
from fastapi.testclient import TestClient
from src.main import app
from shared import StateManager, verify_token
import os
import asyncio

client = TestClient(app)

def test_auth_missing_token():
    # Chat endpoint
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 401
    assert "Missing authentication token" in response.json()["detail"]
    
    # Upload geometry endpoint
    response = client.post("/api/upload_geometry?session_id=123", files={"file": ("test.geojson", b"{}")})
    assert response.status_code == 401

def test_auth_invalid_token():
    # Chat endpoint
    response = client.post("/api/chat", json={"message": "hello"}, headers={"Authorization": "Bearer invalid_token"})
    assert response.status_code == 403
    assert "Invalid authentication token" in response.json()["detail"]

    # Upload geometry endpoint
    response = client.post("/api/upload_geometry?session_id=123", files={"file": ("test.geojson", b"{}")}, headers={"X-Auth-Token": "invalid_token"})
    assert response.status_code == 403

def test_auth_valid_token_but_invalid_payload():
    # We set environment token first
    os.environ["GEO_AGENT_SECRET_TOKEN"] = "test_secret_123"
    response = client.post("/api/chat", json={"message": "hello"}, headers={"Authorization": "Bearer test_secret_123"})
    # Since LLM is not configured, it will raise 500 error or ValueError, but not 401/403
    assert response.status_code != 401
    assert response.status_code != 403

def test_task_status_endpoint():
    from src.api.websockets import manager
    
    run_id = "test_run_999"
    # Ensure local fallback is tested
    async def set_and_verify():
        await manager.set_task_status(run_id, "running")
        status = await manager.get_task_status(run_id)
        assert status == "running"
        
        await manager.send_log(run_id, "Task Completed")
        status = await manager.get_task_status(run_id)
        assert status == "completed"
        
    asyncio.run(set_and_verify())
    
    # Now query the HTTP endpoint
    response = client.get(f"/api/task/{run_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
