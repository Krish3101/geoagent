"""Runs a task through the agent, records its events, and streams them to the browser.

A task goes queued -> running -> succeeded | failed.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Request
from pydantic_ai.usage import UsageLimits
from sqlalchemy import insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TASK_TIMEOUT_S, settings
from app.db import SessionLocal
from app.models import ChatSession, Message, Task, TaskEvent

if TYPE_CHECKING:
    from app.tools import RunDeps

logger = logging.getLogger("geoagent.tasks")

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

ACTIVE = (STATUS_QUEUED, STATUS_RUNNING)


async def _add_event(session: AsyncSession, task_id: str, stage: str, message: str) -> int:
    stmt = (
        insert(TaskEvent)
        .values(task_id=task_id, stage=stage, message=message)
        .returning(TaskEvent.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def log_event(task_id: str, stage: str, message: str) -> int:
    async with SessionLocal() as session:
        event_id = await _add_event(session, task_id, stage, message)
        await session.commit()
        return event_id


async def start_task(task_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == STATUS_QUEUED)
            .values(status=STATUS_RUNNING)
        )
        await session.commit()


async def finish_task(
    task_id: str,
    status: str,
    reply: str,
    stage: str,
    message: str,
    error: str | None = None,
) -> bool:
    """Write the final status, the last event and the assistant reply in one transaction.

    A task that has already finished is left as it is.
    """
    async with SessionLocal() as session:
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
        session.add(
            Message(session_id=session_id, task_id=task_id, role="assistant", content=reply)
        )
        await session.commit()
        return True


async def recover_stuck_tasks() -> int:
    """On startup, fail tasks a previous server process left queued or running."""
    async with SessionLocal() as session:
        stuck = list(
            (await session.execute(select(Task.id).where(Task.status.in_(ACTIVE)))).scalars()
        )
    recovered = 0
    for task_id in stuck:
        recovered += await finish_task(
            task_id,
            STATUS_FAILED,
            "That request was interrupted by a server restart. Please try again.",
            "error",
            "Interrupted by a server restart",
            "interrupted",
        )
    return recovered


async def build_prompt_and_deps(task_id: str) -> tuple[str, "RunDeps"]:
    """Build the model prompt (recent history + this message) and the run's deps."""
    from app.tools import RunDeps

    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        db_session = await session.get(ChatSession, task.session_id)

        current_aoi = None
        if db_session.aoi_name and db_session.aoi_geometry:
            current_aoi = {
                "name": db_session.aoi_name,
                "source": db_session.aoi_source,
                "geometry": db_session.aoi_geometry,
                "bbox": db_session.aoi_bbox,
                "area_km2": db_session.aoi_area_km2,
            }

        # Recent turns give the model some memory. Replies of failed tasks are left out so
        # the model doesn't repeat an error message back.
        msg_stmt = (
            select(Message.role, Message.content)
            .outerjoin(Task, Message.task_id == Task.id)
            .where(
                Message.session_id == task.session_id,
                Message.task_id.is_distinct_from(task_id),
                Message.role != "system",
                or_(Message.role != "assistant", Task.status.is_distinct_from(STATUS_FAILED)),
            )
            .order_by(Message.created_at.desc())
            .limit(10)
        )
        rows = list((await session.execute(msg_stmt)).all())
        rows.reverse()
        history = "\n".join(f"{role.capitalize()}: {content}" for role, content in rows)

        # keep the newest text if the history is long
        joined = history[-4000:]
        if len(history) > 4000:
            # the cut may land mid-line; drop the partial first line (keep it if it's the only one)
            joined = joined.partition("\n")[2] or joined
        if joined:
            prompt = f"Conversation transcript:\n{joined}\n\nUser: {task.prompt}"
        else:
            prompt = task.prompt

        return prompt, RunDeps(task_id=task_id, session_id=task.session_id, aoi=current_aoi)


NO_KEY_REPLY = (
    "OPENROUTER_API_KEY is not set, so the assistant can't answer yet. "
    "Add a key to .env and restart the server."
)


def classify_failure(error: Exception, task_id: str) -> tuple[str, str]:
    """Map an exception to (short code for tasks.error, reply shown in the chat).

    Anything but a timeout gets a generic reply, so no exception text (SQL, paths) reaches the user.
    """
    if isinstance(error, TimeoutError):
        return "timeout", "That request timed out. Please try a smaller area or simpler request."
    return "internal_error", f"Something went wrong (ref: {task_id})."


async def run_task(task_id: str) -> None:
    """Run one chat request through the agent and record the result."""
    # imported here: agent -> tools -> tasks.log_event would otherwise be a circular import
    from app.agent import agent, build_model

    if not settings.openrouter_api_key:
        logger.warning("task=%s failed: OPENROUTER_API_KEY is not set", task_id)
        await finish_task(task_id, STATUS_FAILED, NO_KEY_REPLY, "error", NO_KEY_REPLY, "no_api_key")
        return

    try:
        await start_task(task_id)
        await log_event(task_id, "starting", "Agent started")
        prompt, deps = await build_prompt_and_deps(task_id)

        async with asyncio.timeout(TASK_TIMEOUT_S):
            result = await agent.run(
                prompt,
                model=build_model(),
                deps=deps,
                usage_limits=UsageLimits(
                    request_limit=6,
                    tool_calls_limit=8,
                    total_tokens_limit=40_000,
                ),
                model_settings={"max_tokens": 800, "temperature": 0.0},
            )

        await finish_task(task_id, STATUS_SUCCEEDED, result.output, "done", "Completed")
    except Exception as e:
        code, reply = classify_failure(e, task_id)
        if code == "timeout":
            logger.warning("task=%s timed out", task_id)
        else:
            logger.exception("task=%s failed", task_id)
        await finish_task(task_id, STATUS_FAILED, reply, "error", reply, code)


async def stream_task_events(task_id: str, request: Request) -> AsyncGenerator[str, None]:
    """Stream all of a task's events. Each poll opens and closes its own short DB session."""
    last_id = 0
    terminal_stages = {"done", "error"}
    # a task can't run longer than its timeout, so stop streaming a bit after that
    deadline = time.monotonic() + TASK_TIMEOUT_S + 30

    while time.monotonic() < deadline:
        if await request.is_disconnected():
            break

        # read, then close the session before yielding, so a slow client never holds it
        async with SessionLocal() as session:
            # status first: the final event is written with the final status, so a finished
            # task's events are all visible to the query below
            task_chk = await session.execute(select(Task.status).where(Task.id == task_id))
            curr_status = task_chk.scalar_one_or_none()
            stmt = (
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.id > last_id)
                .order_by(TaskEvent.id.asc())
            )
            events = (await session.execute(stmt)).scalars().all()

        reached_terminal = False
        for ev in events:
            last_id = ev.id
            data = {
                "id": ev.id,
                "stage": ev.stage,
                "message": ev.message,
                "created_at": ev.created_at.isoformat(),
            }
            yield f"data: {json.dumps(data)}\n\n"
            if ev.stage in terminal_stages:
                reached_terminal = True

        if reached_terminal:
            break
        if curr_status in (STATUS_SUCCEEDED, STATUS_FAILED) and not events:
            break

        await asyncio.sleep(0.5)
