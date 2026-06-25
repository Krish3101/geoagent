from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.redis import StateManager

router = APIRouter()
manager = StateManager()

@router.websocket("/ws/task/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str):
    await manager.register_connection(run_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # We don't expect client to send, but keep connection open
            pass
    except WebSocketDisconnect:
        await manager.unregister_connection(run_id)

