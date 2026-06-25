from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from src.database import get_db
from src.schemas import ChatRequest, ChatResponse, SessionHistory
from src.services.chat_service import shared_handle_chat
from src.models import SessionModel
import uuid
import json
import shapely.geometry
from src.auth import verify_token

router = APIRouter()

@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_token)])
async def chat_endpoint(request: ChatRequest, db: Session = Depends(get_db)):
    session_id = request.session_id or uuid.uuid4().hex[:8]
    
    try:
        reply, run_id, pending_interaction = await shared_handle_chat(
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload_geometry", dependencies=[Depends(verify_token)])
async def upload_geometry(session_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        content = await file.read()
        geojson_data = json.loads(content.decode("utf-8"))
        
        features = geojson_data.get("features", [])
        if not features:
            raise HTTPException(status_code=400, detail="No features found in GeoJSON")
            
        geom = features[0].get("geometry")
        shape = shapely.geometry.shape(geom)
        bbox = list(shape.bounds)
        
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not session:
            session = SessionModel(id=session_id, messages=[])
            db.add(session)
            db.commit()
            db.refresh(session)
            
        session.last_task = {
            "region_name": "Uploaded Region",
            "bbox": bbox,
            "geometry": geom
        }
        
        messages = list(session.messages)
        messages.append({
            "role": "system",
            "content": f"User uploaded a geometry file describing a region. Bounding box is {bbox}.",
            "run_id": None
        })
        session.messages = messages
        
        db.commit()
        return {"status": "success", "message": "Geometry saved to session context."}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to process file: {str(e)}")

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

@router.get("/task/{run_id}")
async def get_task_status(run_id: str):
    from src.api.websockets import manager
    status = await manager.get_task_status(run_id)
    if not status:
        status = "unknown"
    return {"run_id": run_id, "status": status}
