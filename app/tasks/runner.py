import asyncio
import logging

from openai import APIConnectionError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import UsageLimits
from sqlalchemy import or_, select

from app.agent import agent, build_model
from app.config import settings
from app.db import SessionLocal
from app.models import Message, Session, Task
from app.tasks.state import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    finish_task,
    log_event,
    transition,
)
from app.tools import RunDeps

logger = logging.getLogger("geoagent.tasks.runner")

# at most 2 agent runs at once; others wait here in the queued state
_task_semaphore = asyncio.Semaphore(2)


async def build_prompt_and_deps(task_id: str) -> tuple[str, RunDeps]:
    """Build the model prompt (recent history + this message) and the run's deps."""
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        db_session = await session.get(Session, task.session_id)

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

    Unknown errors get a generic reply so no exception text (SQL, paths) reaches the user.
    """
    if isinstance(error, TimeoutError):
        return "timeout", "That request timed out. Please try a smaller area or simpler request."
    if getattr(error, "status_code", None) == 429:
        return (
            "rate_limited",
            "The model provider's rate limit was reached. Please try again later.",
        )
    if isinstance(error, APIConnectionError) or isinstance(error.__cause__, APIConnectionError):
        return (
            "connection_error",
            "Couldn't reach OpenRouter. Check your internet connection and try again.",
        )
    if isinstance(error, UsageLimitExceeded):
        return (
            "usage_limit",
            "That request needed too many steps. Please ask for less in one message.",
        )
    return "internal_error", f"Something went wrong (ref: {task_id})."


EXPECTED_ERRORS = {"timeout", "rate_limited", "connection_error", "usage_limit"}


async def run_task(task_id: str) -> None:
    """Run one chat request through the agent and record the result."""
    if not settings.openrouter_api_key:
        logger.warning("task=%s failed: OPENROUTER_API_KEY is not set", task_id)
        await finish_task(task_id, STATUS_FAILED, NO_KEY_REPLY, "error", NO_KEY_REPLY, "no_api_key")
        return

    async with _task_semaphore:
        try:
            await transition(task_id, STATUS_QUEUED, STATUS_RUNNING)
            await log_event(task_id, "starting", "Agent started")
            prompt, deps = await build_prompt_and_deps(task_id)

            async with asyncio.timeout(settings.task_timeout_s):
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
            if code in EXPECTED_ERRORS:
                logger.warning("task=%s failed: %s", task_id, code)
            else:
                logger.exception("task=%s failed", task_id)
            await finish_task(task_id, STATUS_FAILED, reply, "error", reply, code)
