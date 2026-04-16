from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from src.core.database import get_db
from src.api.schemas import ChatRequest, ChatResponse, SessionHistory
from src.services.chat_service import handle_chat
from src.models.session import SessionModel
import uuid

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, db: Session = Depends(get_db)):
    session_id = request.session_id or uuid.uuid4().hex[:8]
    
    try:
        # Business logic goes to service layer
        reply, run_id, pending_interaction = await handle_chat(
            session_id=session_id, 
            message=request.message, 
            db=db
        )
        
        return ChatResponse(
            session_id=session_id,
            reply=reply,
            run_id=run_id,
            status="started",
            pending_interaction=pending_interaction
        )
    except ValueError as e:
        return ChatResponse(
            session_id=session_id,
            reply=f"Need clarification: {str(e)}",
            status="clarifying"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/session/{session_id}/history", response_model=SessionHistory)
async def get_history(session_id: str, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return SessionHistory(messages=[])
        
    return SessionHistory(messages=session.messages)

@router.get("/task/{run_id}/artifacts")
async def get_artifacts(run_id: str):
    import os
    from pathlib import Path
    
    artifacts = {"vector": [], "raster": []}
    run_dir = Path("runs") / run_id / "outputs"
    
    if (run_dir / "vector").exists():
        for file in (run_dir / "vector").iterdir():
            if file.is_file():
                artifacts["vector"].append({
                    "name": file.name,
                    "url": f"/runs/{run_id}/outputs/vector/{file.name}"
                })
                
    if (run_dir / "raster").exists():
        for file in (run_dir / "raster").iterdir():
            if file.is_file():
                artifacts["raster"].append({
                    "name": file.name,
                    "url": f"/runs/{run_id}/outputs/raster/{file.name}"
                })
                
    return artifacts
