from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, run_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[run_id] = websocket

    def disconnect(self, run_id: str):
        if run_id in self.active_connections:
            del self.active_connections[run_id]

    async def send_log(self, run_id: str, message: str):
        if run_id in self.active_connections:
            await self.active_connections[run_id].send_text(message)

manager = ConnectionManager()

@router.websocket("/ws/task/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: str):
    await manager.connect(run_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # We don't expect client to send, but keep connection open
            pass
    except WebSocketDisconnect:
        manager.disconnect(run_id)
