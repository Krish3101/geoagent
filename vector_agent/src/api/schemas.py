from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    run_id: Optional[str] = None
    status: str
    pending_interaction: bool = False

class Message(BaseModel):
    role: str
    content: str
    run_id: Optional[str] = None

class SessionHistory(BaseModel):
    messages: List[Message]
