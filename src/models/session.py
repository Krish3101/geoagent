from sqlalchemy import Column, String, JSON, DateTime
from ..database import Base
from datetime import datetime, timezone

class SessionModel(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    messages = Column(JSON, default=list) # Store list of message dicts
    last_task = Column(JSON, nullable=True) # Store the last parsed Task dict
