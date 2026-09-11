import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import Message, Session, Task, TaskEvent

logger = logging.getLogger("geoagent.runner")

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

LEGAL_TRANSITIONS: dict[str, set[str]] = {
    STATUS_QUEUED: {STATUS_RUNNING},
    STATUS_RUNNING: {STATUS_SUCCEEDED, STATUS_FAILED},
    STATUS_SUCCEEDED: set(),
    STATUS_FAILED: set(),
}


async def transition(task_id: str, new_status: str, error: str | None = None) -> None:
    """Transition a task to a new status following strict state machine rules."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"Task '{task_id}' not found")

        current_status = task.status
        allowed = LEGAL_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Illegal state transition from '{current_status}' to '{new_status}' "
                f"for task '{task_id}'"
            )

        task.status = new_status
        if error:
            task.error = error
        if new_status in (STATUS_SUCCEEDED, STATUS_FAILED):
            task.finished_at = datetime.now(timezone.utc)

        await session.commit()


async def log_event(task_id: str, stage: str, message: str) -> TaskEvent:
    """Append a monotonically sequenced event for a task."""
    async with AsyncSessionLocal() as session:
        # Determine monotonic seq
        stmt = select(func.coalesce(func.max(TaskEvent.seq), 0)).where(TaskEvent.task_id == task_id)
        max_seq = (await session.execute(stmt)).scalar() or 0
        new_seq = max_seq + 1

        event = TaskEvent(
            task_id=task_id,
            seq=new_seq,
            stage=stage,
            message=message,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event


async def build_prompt_and_deps(task_id: str) -> tuple[str, Any]:
    """Retrieve session spatial context, recent conversation turns, and construct RunDeps."""
    from app.agent import RunDeps

    async with AsyncSessionLocal() as session:
        task_res = await session.execute(select(Task).where(Task.id == task_id))
        task = task_res.scalar_one_or_none()
        if not task:
            raise ValueError(f"Task '{task_id}' not found")

        sess_res = await session.execute(select(Session).where(Session.id == task.session_id))
        db_session = sess_res.scalar_one_or_none()
        if not db_session:
            raise ValueError(f"Session '{task.session_id}' not found")

        current_aoi = None
        if db_session.aoi_name and db_session.aoi_geometry:
            current_aoi = {
                "name": db_session.aoi_name,
                "source": db_session.aoi_source,
                "geometry": db_session.aoi_geometry,
                "bbox": db_session.aoi_bbox,
                "area_km2": db_session.aoi_area_km2,
            }

        # Fetch recent transcript for memory
        msg_stmt = (
            select(Message)
            .where(Message.session_id == task.session_id)
            .order_by(Message.created_at.desc())
            .limit(10)
        )
        messages = list((await session.execute(msg_stmt)).scalars().all())
        messages.reverse()

        history_lines = []
        for m in messages:
            # Exclude current task's prompt if it was already inserted as a user message
            if m.task_id == task_id and m.role == "user":
                continue
            history_lines.append(f"{m.role.capitalize()}: {m.content}")

        if history_lines:
            joined = "\n".join(history_lines)
            prompt = f"Conversation transcript:\n{joined}\n\nUser: {task.prompt}"
        else:
            prompt = task.prompt

        deps = RunDeps(
            task_id=task_id,
            session_id=task.session_id,
            aoi=current_aoi,
        )

        return prompt, deps


async def save_assistant_message(task_id: str, content: str) -> None:
    """Save assistant reply message tied to session and task."""
    async with AsyncSessionLocal() as session:
        task_res = await session.execute(select(Task).where(Task.id == task_id))
        task = task_res.scalar_one_or_none()
        if not task:
            return

        msg = Message(
            session_id=task.session_id,
            task_id=task_id,
            role="assistant",
            content=content,
        )
        session.add(msg)
        await session.commit()


async def save_aoi_if_changed(deps: Any) -> None:
    """Update session AOI if tool modified it during run."""
    if not deps.aoi_changed or not deps.aoi:
        return

    async with AsyncSessionLocal() as session:
        sess_res = await session.execute(select(Session).where(Session.id == deps.session_id))
        db_session = sess_res.scalar_one_or_none()
        if db_session:
            db_session.aoi_name = deps.aoi.get("name")
            db_session.aoi_source = deps.aoi.get("source")
            db_session.aoi_geometry = deps.aoi.get("geometry")
            db_session.aoi_bbox = deps.aoi.get("bbox")
            db_session.aoi_area_km2 = deps.aoi.get("area_km2")
            await session.commit()


async def run_task(task_id: str) -> None:
    """Execute task through state machine and Pydantic-AI agent."""
    from app.agent import agent

    await transition(task_id, STATUS_RUNNING)
    await log_event(task_id, "starting", "Agent started")

    try:
        if not settings.openrouter_api_key or settings.openrouter_api_key == "sk-placeholder":
            raise ValueError(
                "OPENROUTER_API_KEY is not configured. "
                "Please set your API key in .env or run ./scripts/start.sh."
            )
        prompt, deps = await build_prompt_and_deps(task_id)
        result = await agent.run(prompt, deps=deps)
        # pydantic-ai result response text: output or data
        output_text = (
            getattr(result, "output", None) or getattr(result, "data", None) or str(result)
        )

        await save_assistant_message(task_id, str(output_text))
        await save_aoi_if_changed(deps)
        await transition(task_id, STATUS_SUCCEEDED)
        await log_event(task_id, "done", "Completed")
    except Exception as e:
        logger.exception("Task %s failed: %s", task_id, e)
        await transition(task_id, STATUS_FAILED, error=str(e))
        await log_event(task_id, "error", str(e))
