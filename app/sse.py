import asyncio
import json
import time
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Task, TaskEvent
from app.tasks.state import STATUS_FAILED, STATUS_SUCCEEDED


async def event_generator(
    tid: str,
    request: Request,
    after_seq: int = 0,
) -> AsyncGenerator[str, None]:
    """Stream task events. Each poll opens and closes its own short DB session."""
    last_seq = after_seq
    terminal_stages = {"done", "error"}
    last_ping = time.monotonic()
    # a task can't run longer than its timeout, so stop streaming a bit after that
    deadline = time.monotonic() + settings.task_timeout_s + 30

    while time.monotonic() < deadline:
        if await request.is_disconnected():
            break

        # read, then close the session before yielding, so a slow client never holds it
        async with SessionLocal() as session:
            # status first: the final event is written with the final status, so a finished
            # task's events are all visible to the query below
            task_chk = await session.execute(select(Task.status).where(Task.id == tid))
            curr_status = task_chk.scalar_one_or_none()
            stmt = (
                select(TaskEvent)
                .where(TaskEvent.task_id == tid, TaskEvent.seq > last_seq)
                .order_by(TaskEvent.seq.asc())
            )
            events = (await session.execute(stmt)).scalars().all()

        reached_terminal = False
        for ev in events:
            last_seq = ev.seq
            data = {
                "seq": ev.seq,
                "stage": ev.stage,
                "message": ev.message,
                "created_at": ev.created_at.isoformat(),
            }
            yield f"id: {ev.seq}\ndata: {json.dumps(data)}\n\n"
            if ev.stage in terminal_stages:
                reached_terminal = True

        if reached_terminal:
            break
        if curr_status in (STATUS_SUCCEEDED, STATUS_FAILED) and not events:
            break

        # Emit SSE heartbeat comment every 15 seconds
        now = time.monotonic()
        if now - last_ping >= 15.0:
            yield ": ping\n\n"
            last_ping = now

        await asyncio.sleep(0.5)
