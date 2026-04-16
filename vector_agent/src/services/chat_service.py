from sqlalchemy.orm import Session
from src.models.session import SessionModel
from typing import Tuple, Optional
import uuid

async def handle_chat(session_id: str, message: str, db: Session) -> Tuple[str, str, bool]:
    """
    Handles a chat request, persisting to DB.
    Returns: (reply_text, run_id, pending_interaction)
    """
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        session = SessionModel(id=session_id, messages=[])
        db.add(session)
        db.commit()
        db.refresh(session)
        
    # Append User Message
    messages = list(session.messages)
    messages.append({"role": "user", "content": message, "run_id": None})
    session.messages = messages
    db.commit()
    
    # 1. Parse Task from LLM
    from src.agents.parser import ParsingAgent
    from src.agents.planner import PlannerAgent
    from src.agents.executor import ExecutionManager
    from src.tools.registry import registry
    from src.tools.geocoding import GeocodeTool
    from src.tools.vector import VectorTool
    from src.agents.schema import TaskValidationError
    import asyncio
    
    # Register core tools in V3
    try:
        registry.register(GeocodeTool())
        registry.register(VectorTool())
    except ValueError:
        pass # Already registered
        
    parser = ParsingAgent()
    planner = PlannerAgent()
    
    try:
        context = {"last_task": session.last_task}
        task = await parser.act(message, context=context)
        
        # Save validated task
        session.last_task = task.model_dump()
        db.commit()
        
        # 2. Plan Execution
        plan = await planner.act("", context={"task": task})
        
        # 3. Start Background Execution
        run_id = uuid.uuid4().hex
        
        # Create an asyncio background task to mimic threading in v2 but non-blocking in v3 FastAPI
        loop = asyncio.get_running_loop()
        loop.create_task(ExecutionManager.execute_plan(task, plan, run_id))
        
        reply = f"Successfully parsed task. Region: {task.region_name}, Data: {task.data_types}. Execution planned: {plan}"
        pending_interaction = False
        
    except TaskValidationError as e:
        reply = f"Need clarification: {str(e)}"
        pending_interaction = False
        run_id = None
        
    except Exception as e:
        reply = f"Error processing task: {str(e)}"
        pending_interaction = False
        run_id = None
    
    # Append Agent Message
    messages.append({"role": "agent", "content": reply, "run_id": run_id})
    session.messages = messages
    db.commit()
    
    return reply, run_id, pending_interaction
