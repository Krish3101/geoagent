"""Task state machine and the per-task event log.

queued -> running -> succeeded | failed, plus queued -> failed. Every status change is a
compare-and-set, so two writers can never both move the same task.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Message, Task, TaskEvent

logger = logging.getLogger("geoagent.tasks.state")

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

ACTIVE = (STATUS_QUEUED, STATUS_RUNNING)
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    STATUS_QUEUED: {STATUS_RUNNING, STATUS_FAILED},
    STATUS_RUNNING: {STATUS_SUCCEEDED, STATUS_FAILED},
    STATUS_SUCCEEDED: set(),
    STATUS_FAILED: set(),
}


async def _add_event(session: AsyncSession, task_id: str, stage: str, message: str) -> int:
    # One INSERT that reads MAX(seq) itself. SQLite runs it under the write lock, so two
    # writers can't get the same seq and there are no gaps.
    next_seq = (
        select(func.coalesce(func.max(TaskEvent.seq), 0) + 1)
        .where(TaskEvent.task_id == task_id)
        .scalar_subquery()
    )
    stmt = (
        insert(TaskEvent)
        .values(task_id=task_id, seq=next_seq, stage=stage, message=message)
        .returning(TaskEvent.seq)
    )
    return (await session.execute(stmt)).scalar_one()


async def log_event(task_id: str, stage: str, message: str) -> int:
    async with SessionLocal() as session:
        seq = await _add_event(session, task_id, stage, message)
        await session.commit()
        return seq


async def transition(task_id: str, from_status: str, to_status: str) -> None:
    if to_status not in LEGAL_TRANSITIONS[from_status]:
        raise ValueError(f"Illegal state transition from '{from_status}' to '{to_status}'")

    values: dict = {"status": to_status}
    if to_status in (STATUS_SUCCEEDED, STATUS_FAILED):
        values["finished_at"] = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        result = await session.execute(
            update(Task).where(Task.id == task_id, Task.status == from_status).values(**values)
        )
        if result.rowcount != 1:
            raise ValueError(f"Task '{task_id}' is not '{from_status}'")
        await session.commit()


async def _finish(
    session: AsyncSession,
    task_id: str,
    status: str,
    reply: str,
    stage: str,
    message: str,
    error: str | None,
) -> bool:
    session_id = (
        await session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.in_(ACTIVE))
            .values(status=status, error=error, finished_at=datetime.now(timezone.utc))
            .returning(Task.session_id)
        )
    ).scalar_one_or_none()
    if session_id is None:
        logger.warning("task=%s was already finished", task_id)
        return False

    await _add_event(session, task_id, stage, message)
    session.add(Message(session_id=session_id, task_id=task_id, role="assistant", content=reply))
    return True


async def finish_task(
    task_id: str,
    status: str,
    reply: str,
    stage: str,
    message: str,
    error: str | None = None,
) -> None:
    """Write the final status, the last event and the assistant reply in one transaction."""
    async with SessionLocal() as session:
        await _finish(session, task_id, status, reply, stage, message, error)
        await session.commit()


async def recover_stuck_tasks() -> int:
    """On startup, fail tasks a previous server process left queued or running."""
    async with SessionLocal() as session:
        stuck = (await session.execute(select(Task.id).where(Task.status.in_(ACTIVE)))).scalars()
        recovered = 0
        for task_id in list(stuck):
            recovered += await _finish(
                session,
                task_id,
                STATUS_FAILED,
                "That request was interrupted by a server restart. Please try again.",
                "error",
                "Interrupted by a server restart",
                "interrupted",
            )
        await session.commit()
    return recovered
