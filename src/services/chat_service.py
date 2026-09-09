import logging
from sqlalchemy.orm import Session
from src.models import SessionModel
from typing import Tuple, Optional
import uuid
import asyncio

logger = logging.getLogger(__name__)

async def run_agent_background(session_id: str, message: str, run_id: str):
    from src.agent import agent, Deps
    from src.api.websockets import manager
    from src.database import SessionLocal
    
    await manager.send_log(run_id, f"Agent started. Analyzing request: '{message}'")
    
    db = SessionLocal()
    try:
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        
        # Prepare context and pre-populate spatial tracking from last_task (FR-2)
        initial_spatial_context = {}
        context_parts = []
        if session and session.last_task:
            for k in ("region_name", "bbox", "geometry"):
                if k in session.last_task:
                    initial_spatial_context[k] = session.last_task[k]
                    context_parts.append(f"{k}: {session.last_task[k]}")
            if "last_output" in session.last_task:
                context_parts.append(f"Previous Output: {session.last_task['last_output']}")
                
        context_str = "\n".join(context_parts) if context_parts else "No previous context."
        prompt = f"User Request: {message}\n\nContext:\n{context_str}"
        
        deps = Deps(run_id=run_id, spatial_context=initial_spatial_context)
        result = await agent.run(prompt, deps=deps)
        
        await manager.send_log(run_id, "Task Completed")
        
        # Save output and updated spatial context
        if session:
            messages = list(session.messages)
            messages.append({"role": "agent", "content": result.data, "run_id": run_id})
            session.messages = messages
            session.last_task = {
                "last_output": result.data,
                **deps.spatial_context
            }
            db.commit()
            
    except Exception as e:
        logger.error(f"Task {run_id} failed: {e}", exc_info=True)
        await manager.send_log(run_id, f"Task Failed: {str(e)}")
    finally:
        db.close()

async def shared_handle_chat(
    session_id: str, 
    message: str, 
    db: Session
) -> Tuple[str, str, bool]:
    
    run_id = uuid.uuid4().hex
    
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        session = SessionModel(id=session_id, messages=[])
        db.add(session)
        db.commit()
        db.refresh(session)
        
    messages = list(session.messages)
    messages.append({"role": "user", "content": message, "run_id": run_id})
    session.messages = messages
    db.commit()
    
    loop = asyncio.get_running_loop()
    loop.create_task(run_agent_background(session_id, message, run_id))
    
    reply = "I'm working on that for you."
    pending_interaction = False
    
    return reply, run_id, pending_interaction
