import asyncio

import pytest
from sqlalchemy import select

from app import db as app_db
from app.models import Message, Session, Task, TaskEvent
from app.tasks.state import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    finish_task,
    log_event,
    recover_stuck_tasks,
    transition,
)


async def make_task(factory, status: str = STATUS_QUEUED) -> str:
    async with factory() as session:
        s = Session()
        session.add(s)
        await session.flush()
        t = Task(session_id=s.id, status=status, prompt="test")
        session.add(t)
        await session.commit()
        return t.id


async def get_task(factory, task_id: str) -> Task:
    async with factory() as session:
        return (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()


async def test_legal_transitions(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory)

    await transition(task_id, STATUS_QUEUED, STATUS_RUNNING)
    t = await get_task(factory, task_id)
    assert t.status == STATUS_RUNNING
    assert t.finished_at is None

    await transition(task_id, STATUS_RUNNING, STATUS_SUCCEEDED)
    t = await get_task(factory, task_id)
    assert t.status == STATUS_SUCCEEDED
    assert t.finished_at is not None


async def test_queued_to_failed_is_legal(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory)
    await transition(task_id, STATUS_QUEUED, STATUS_FAILED)
    assert (await get_task(factory, task_id)).status == STATUS_FAILED


async def test_illegal_transitions_raise(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory)

    with pytest.raises(ValueError, match="Illegal state transition"):
        await transition(task_id, STATUS_QUEUED, STATUS_SUCCEEDED)
    with pytest.raises(ValueError, match="Illegal state transition"):
        await transition(task_id, STATUS_SUCCEEDED, STATUS_RUNNING)


async def test_transition_is_compare_and_set(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory)

    # two runners try to start the same task: exactly one wins
    results = await asyncio.gather(
        transition(task_id, STATUS_QUEUED, STATUS_RUNNING),
        transition(task_id, STATUS_QUEUED, STATUS_RUNNING),
        return_exceptions=True,
    )
    assert sum(r is None for r in results) == 1
    assert sum(isinstance(r, ValueError) for r in results) == 1


async def test_events_are_gapless_under_concurrency(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory, STATUS_RUNNING)

    seqs = await asyncio.gather(*[log_event(task_id, "vector", f"step {i}") for i in range(10)])
    assert sorted(seqs) == list(range(1, 11))

    async with factory() as session:
        stored = (
            await session.execute(select(TaskEvent.seq).where(TaskEvent.task_id == task_id))
        ).scalars()
        assert sorted(stored) == list(range(1, 11))


async def test_finish_task_is_one_transaction(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory, STATUS_RUNNING)
    await log_event(task_id, "starting", "Agent started")

    await finish_task(task_id, STATUS_SUCCEEDED, "Success reply", "done", "All done")

    t = await get_task(factory, task_id)
    assert t.status == STATUS_SUCCEEDED
    assert t.finished_at is not None
    async with factory() as session:
        events = (
            await session.execute(
                select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
            )
        ).scalars()
        assert [(e.seq, e.stage) for e in events] == [(1, "starting"), (2, "done")]
        msgs = (
            (await session.execute(select(Message).where(Message.task_id == task_id)))
            .scalars()
            .all()
        )
        assert [m.content for m in msgs] == ["Success reply"]


async def test_finish_task_does_not_overwrite_a_finished_task(test_env):
    factory = test_env["session_factory"]
    task_id = await make_task(factory, STATUS_SUCCEEDED)

    await finish_task(task_id, STATUS_FAILED, "late failure", "error", "late", "internal_error")

    t = await get_task(factory, task_id)
    assert t.status == STATUS_SUCCEEDED
    async with factory() as session:
        msgs = (await session.execute(select(Message).where(Message.task_id == task_id))).all()
        assert msgs == []


async def test_startup_recovery_sweeps_queued_and_running(test_env):
    factory = test_env["session_factory"]
    q_id = await make_task(factory, STATUS_QUEUED)
    r_id = await make_task(factory, STATUS_RUNNING)
    s_id = await make_task(factory, STATUS_SUCCEEDED)

    assert await recover_stuck_tasks() == 2

    for task_id in (q_id, r_id):
        t = await get_task(factory, task_id)
        assert t.status == STATUS_FAILED
        assert t.finished_at is not None
        assert t.error == "interrupted"
    assert (await get_task(factory, s_id)).status == STATUS_SUCCEEDED

    async with factory() as session:
        reply = (
            await session.execute(
                select(Message).where(Message.task_id == q_id, Message.role == "assistant")
            )
        ).scalar_one()
        assert "interrupted by a server restart" in reply.content


async def test_foreign_keys_on_every_connection(test_env):
    # app.db.engine is the app's own engine setup, pointed at the test database
    connections = [await app_db.engine.connect() for _ in range(4)]
    try:
        values = [(await c.exec_driver_sql("PRAGMA foreign_keys")).scalar() for c in connections]
    finally:
        for c in connections:
            await c.close()
    assert values == [1, 1, 1, 1]


async def test_old_database_is_refused(test_env):
    async with app_db.engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA user_version=0")

    with pytest.raises(SystemExit, match="older version: delete"):
        await app_db.init_db()


async def test_init_db_accepts_its_own_database(test_env):
    await app_db.init_db()  # second start on the same data dir
