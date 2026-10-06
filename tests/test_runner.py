from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
from openai import APIConnectionError
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UsageLimitExceeded
from sqlalchemy import select

from app.config import settings
from app.models import Message, Session, Task, TaskEvent
from app.tasks.runner import build_prompt_and_deps, classify_failure, run_task
from app.tasks.state import STATUS_FAILED, STATUS_QUEUED, STATUS_SUCCEEDED


async def make_task(factory, prompt: str = "test") -> tuple[str, str]:
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt=prompt)
        session.add(t)
        await session.commit()
        return t.id, s.id


async def stages(factory, task_id: str) -> list[str]:
    async with factory() as session:
        events = await session.execute(
            select(TaskEvent.stage).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
        )
        return list(events.scalars())


async def test_missing_api_key_fails_before_running(test_env, monkeypatch, caplog):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    factory = test_env["session_factory"]
    task_id, _ = await make_task(factory)

    await run_task(task_id)

    async with factory() as session:
        t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        reply = (
            await session.execute(select(Message).where(Message.task_id == task_id))
        ).scalar_one()
    assert t.status == STATUS_FAILED
    assert t.error == "no_api_key"
    assert reply.role == "assistant"
    assert "OPENROUTER_API_KEY is not set" in reply.content
    assert await stages(factory, task_id) == ["error"]
    # an expected failure: a warning, no traceback
    assert all(r.levelname == "WARNING" and r.exc_info is None for r in caplog.records)


def test_rate_limit_reply_is_neutral():
    error = ModelHTTPError(status_code=429, model_name="free-model", body={})
    code, reply = classify_failure(error, "t1")
    assert code == "rate_limited"
    assert "rate limit" in reply


def test_connection_error_reply_says_what_to_do():
    cause = APIConnectionError(request=httpx.Request("POST", "https://openrouter.ai"))
    try:
        raise ModelAPIError("free-model", "Connection error.") from cause
    except ModelAPIError as wrapped:
        code, reply = classify_failure(wrapped, "t1")
    assert code == "connection_error"
    assert "internet connection" in reply


def test_timeout_and_usage_limit_have_their_own_replies():
    assert classify_failure(TimeoutError(), "t1")[0] == "timeout"
    assert classify_failure(UsageLimitExceeded("too many"), "t1")[0] == "usage_limit"


def test_unknown_errors_get_a_generic_reply():
    code, reply = classify_failure(RuntimeError("/home/me/secret.db is locked"), "t42")
    assert code == "internal_error"
    assert reply == "Something went wrong (ref: t42)."


async def test_run_task_success(test_env, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=MagicMock(output="Extracted 15 buildings."))
    monkeypatch.setattr("app.tasks.runner.agent", mock_agent)

    factory = test_env["session_factory"]
    task_id, session_id = await make_task(factory, "Extract buildings")

    await run_task(task_id)

    async with factory() as session:
        t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        msgs = (
            (await session.execute(select(Message).where(Message.session_id == session_id)))
            .scalars()
            .all()
        )
    assert t.status == STATUS_SUCCEEDED
    assert [(m.role, m.content) for m in msgs] == [("assistant", "Extracted 15 buildings.")]
    assert await stages(factory, task_id) == ["starting", "done"]


async def test_history_keeps_newest_text_and_skips_failed_replies(test_env):
    factory = test_env["session_factory"]
    t0 = datetime.now(timezone.utc)
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        old = Task(session_id=s.id, status=STATUS_SUCCEEDED, prompt="x")
        failed = Task(session_id=s.id, status=STATUS_FAILED, prompt="y")
        current = Task(session_id=s.id, status=STATUS_QUEUED, prompt="Now the roads")
        session.add_all([old, failed, current])
        await session.flush()
        rows = [
            (old, "user", "A" * 5000),
            (old, "assistant", "Resolved to Soho"),
            (failed, "user", "NDVI please"),
            (failed, "assistant", "Something went wrong (ref: abc)."),
            (current, "user", "Now the roads"),
        ]
        for i, (task, role, content) in enumerate(rows):
            session.add(
                Message(
                    session_id=s.id,
                    task_id=task.id,
                    role=role,
                    content=content,
                    created_at=t0 + timedelta(seconds=i),
                )
            )
        await session.commit()
        task_id = current.id

    prompt, deps = await build_prompt_and_deps(task_id)

    assert "Assistant: Resolved to Soho" in prompt
    assert "User: NDVI please" in prompt
    assert "Something went wrong" not in prompt
    assert prompt.endswith("User: Now the roads")
    assert prompt.count("Now the roads") == 1
    assert len(prompt) < 4200
    assert deps.aoi is None


async def test_history_cut_drops_partial_first_line(test_env):
    factory = test_env["session_factory"]
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        old = Task(session_id=s.id, status=STATUS_SUCCEEDED, prompt="x")
        current = Task(session_id=s.id, status=STATUS_QUEUED, prompt="next")
        session.add_all([old, current])
        await session.flush()
        t0 = datetime.now(timezone.utc)
        for i, (role, content) in enumerate([("user", "word " * 1000), ("assistant", "tail")]):
            session.add(
                Message(
                    session_id=s.id,
                    task_id=old.id,
                    role=role,
                    content=content,
                    created_at=t0 + timedelta(seconds=i),
                )
            )
        await session.commit()
        task_id = current.id

    prompt, _ = await build_prompt_and_deps(task_id)

    # the long first message is cut mid-text, so only whole lines remain
    assert "Conversation transcript:\nAssistant: tail\n" in prompt
