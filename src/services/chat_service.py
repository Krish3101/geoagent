from sqlalchemy.orm import Session
from src.models import SessionModel
from typing import Tuple, Optional
import uuid
import asyncio

async def run_agent_background(session_id: str, message: str, run_id: str):
    from src.agent import agent, Deps
    from src.api.websockets import manager
    from src.database import SessionLocal
    
    await asyncio.sleep(1) # wait for socket
    await manager.send_log(run_id, f"Agent started. Analyzing request: '{message}'")
    
    db = SessionLocal()
    try:
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        
        # Prepare context
        context = "No previous context."
        if session and session.last_task:
            context = f"Previous Task: {session.last_task}"
            
        prompt = f"User Request: {message}\n\nContext: {context}"
        
        result = await agent.run(prompt, deps=Deps(run_id=run_id))
        
        await manager.send_log(run_id, "Task Completed")
        
        # Save output
        if session:
            messages = list(session.messages)
            messages.append({"role": "agent", "content": result.data, "run_id": run_id})
            session.messages = messages
            session.last_task = {"last_output": result.data}
            db.commit()
            
    except Exception as e:
        await manager.send_log(run_id, f"Task Failed: {str(e)}")
    finally:
        db.close()

async def shared_handle_chat(
    session_id: str, 
    message: str, 
    db: Session
) -> Tuple[str, str, bool]:
    
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        session = SessionModel(id=session_id, messages=[])
        db.add(session)
        db.commit()
        db.refresh(session)
        
    messages = list(session.messages)
    messages.append({"role": "user", "content": message, "run_id": None})
    session.messages = messages
    db.commit()
    
    run_id = uuid.uuid4().hex
    
    loop = asyncio.get_running_loop()
    loop.create_task(run_agent_background(session_id, message, run_id))
    
    reply = "I'm working on that for you."
    pending_interaction = False
    
    return reply, run_id, pending_interaction
