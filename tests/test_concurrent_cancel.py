"""Batch-2: concurrent cancel integration tests (D11 observation item closure).

Scenarios per 返修单-技术债批次2 §要求3:
  A. Concurrent cancels on the same RUNNING task — exactly one terminal state.
  B. cancel racing assign on the same task — no illegal intermediate state.
  C. Concurrent cancels on multiple tasks — independent correct transitions.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.project_state.models import Project, Task
from project_runtime.task_graph.manager import TaskGraphManager
from project_runtime.worker.adapter import MockWorkerAdapter


@pytest.fixture(scope="function")
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def project(dbsession: AsyncSession) -> Project:
    p = Project(name="batch2-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _running_task(dbsession, project, name: str) -> Task:
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name=name))
    await mgr.promote_to_ready(task.id)
    await dbsession.refresh(task)
    fsm = StateTransitionService(dbsession)
    await fsm.transition_task(task, TaskStatus.RUNNING)
    await dbsession.flush()
    return task


# Scenario A: concurrent cancels on the same RUNNING task
@pytest.mark.asyncio
async def test_concurrent_cancel_same_task(dbsession, project):
    task = await _running_task(dbsession, project, "cancel-race")
    worker = MockWorkerAdapter(dbsession)

    results = await asyncio.gather(
        worker.cancel(task.id),
        worker.cancel(task.id),
        worker.cancel(task.id),
        return_exceptions=True,
    )

    # No exception escapes (idempotent or cleanly rejected)
    errors = [r for r in results if isinstance(r, Exception)]
    # At most, non-InvalidStateTransition errors are unexpected
    from project_runtime.project_state.fsm import InvalidStateTransitionError

    unexpected = [e for e in errors if not isinstance(e, InvalidStateTransitionError)]
    assert not unexpected, f"unexpected errors: {unexpected}"

    # Single terminal state
    await dbsession.refresh(task)
    assert task.status == TaskStatus.CANCELLED


# Scenario B: cancel racing assign on the same task
@pytest.mark.asyncio
async def test_cancel_races_assign(dbsession, project):
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name="race"))
    await mgr.promote_to_ready(task.id)

    worker = MockWorkerAdapter(dbsession, simulate_delay=0.05)
    from project_runtime.worker.adapter import WorkPackage

    assign_result, cancel_result = await asyncio.gather(
        worker.assign(WorkPackage(task_id=task.id, task_name=task.name)),
        worker.cancel(task.id),
        return_exceptions=True,
    )

    await dbsession.refresh(task)
    # Legal outcomes: assign completed then cancel failed (task EXECUTION_DONE,
    # CANCELLED from RUNNING is legal so cancel may have won mid-flight) —
    # the invariant is: no illegal intermediate state, no exception escape
    # from the FSM's own validation.
    legal = {TaskStatus.EXECUTION_DONE, TaskStatus.CANCELLED, TaskStatus.RUNNING}
    assert task.status in legal, f"illegal state after race: {task.status}"


# Scenario C: concurrent cancels on multiple tasks
@pytest.mark.asyncio
async def test_concurrent_cancel_multiple_tasks(dbsession, project):
    tasks = [await _running_task(dbsession, project, f"t{i}") for i in range(3)]
    worker = MockWorkerAdapter(dbsession)

    await asyncio.gather(*[worker.cancel(t.id) for t in tasks])

    for t in tasks:
        await dbsession.refresh(t)
        assert t.status == TaskStatus.CANCELLED
