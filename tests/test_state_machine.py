import pytest
from sqlalchemy import select

from app.models import Message, Session, Task, TaskEvent
from app.runner import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    log_event,
    run_task,
    transition,
)


@pytest.mark.asyncio
async def test_legal_transitions(test_env):
    factory = test_env["session_factory"]
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="Test prompt")
        session.add(t)
        await session.commit()
        task_id = t.id

    # queued -> running
    await transition(task_id, STATUS_RUNNING)
    async with factory() as session:
        t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.status == STATUS_RUNNING
        assert t.finished_at is None

    # running -> succeeded
    await transition(task_id, STATUS_SUCCEEDED)
    async with factory() as session:
        t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.status == STATUS_SUCCEEDED
        assert t.finished_at is not None


@pytest.mark.asyncio
async def test_failed_transition(test_env):
    factory = test_env["session_factory"]
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="Fail test")
        session.add(t)
        await session.commit()
        task_id = t.id

    await transition(task_id, STATUS_RUNNING)
    await transition(task_id, STATUS_FAILED, error="Some failure")
    async with factory() as session:
        t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.status == STATUS_FAILED
        assert t.error == "Some failure"
        assert t.finished_at is not None


@pytest.mark.asyncio
async def test_illegal_transitions_raise(test_env):
    factory = test_env["session_factory"]
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="Illegal test")
        session.add(t)
        await session.commit()
        task_id = t.id

    # Directly jumping from queued to succeeded must raise
    with pytest.raises(ValueError, match="Illegal state transition"):
        await transition(task_id, STATUS_SUCCEEDED)

    # Transition to running
    await transition(task_id, STATUS_RUNNING)

    # Transition to succeeded
    await transition(task_id, STATUS_SUCCEEDED)

    # Transitioning from terminal state succeeded to running must raise
    with pytest.raises(ValueError, match="Illegal state transition"):
        await transition(task_id, STATUS_RUNNING)


@pytest.mark.asyncio
async def test_monotonic_gapless_events(test_env):
    factory = test_env["session_factory"]
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="Events test")
        session.add(t)
        await session.commit()
        task_id = t.id

    ev1 = await log_event(task_id, "starting", "Agent started")
    ev2 = await log_event(task_id, "geocoding", "Geocoding location")
    ev3 = await log_event(task_id, "vector", "Extracting vector")
    ev4 = await log_event(task_id, "done", "Completed")

    assert ev1.seq == 1
    assert ev2.seq == 2
    assert ev3.seq == 3
    assert ev4.seq == 4

    async with factory() as session:
        events = list(
            (
                await session.execute(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id)
                    .order_by(TaskEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        assert [e.seq for e in events] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_run_task_missing_api_key_fails_gracefully(test_env, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "")

    factory = test_env["session_factory"]
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="Missing key test")
        session.add(t)
        await session.commit()
        task_id = t.id

    await run_task(task_id)

    async with factory() as session:
        t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.status == STATUS_FAILED
        assert "OPENROUTER_API_KEY is not configured" in (t.error or "")

        events = list(
            (
                await session.execute(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id)
                    .order_by(TaskEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        stages = [e.stage for e in events]
        assert "starting" in stages
        assert "error" in stages


@pytest.mark.asyncio
async def test_run_task_success(test_env, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app import agent as app_agent
    from app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test-key")

    mock_run_result = MagicMock()
    mock_run_result.output = "Extracted 15 buildings successfully."
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=mock_run_result)
    monkeypatch.setattr(app_agent, "agent", mock_agent)

    factory = test_env["session_factory"]
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=STATUS_QUEUED, prompt="Extract buildings")
        session.add(t)
        await session.commit()
        task_id = t.id
        session_id = s.id

    await run_task(task_id)

    async with factory() as session:
        t = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        assert t.status == STATUS_SUCCEEDED
        assert t.finished_at is not None

        messages = list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(messages) == 1
        assert messages[0].role == "assistant"
        assert messages[0].content == "Extracted 15 buildings successfully."

        events = list(
            (
                await session.execute(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id)
                    .order_by(TaskEvent.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        stages = [e.stage for e in events]
        assert stages == ["starting", "done"]
