"""D14 Scheduler — dispatch READY tasks, concurrency, automation modes."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AutomationMode,
    ManualActionStatus,
    TaskStatus,
)
from project_runtime.project_state.models import ManualAction, Project, Task
from project_runtime.scheduler.scheduler import Scheduler
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
    p = Project(name="d14-test", automation_mode=AutomationMode.FULL_AUTO)
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _ready_task(dbsession, project, name: str) -> Task:
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name=name))
    await mgr.promote_to_ready(task.id)
    return task


# ════════════════════════════════════════════════════════════════════════════
# FULL_AUTO dispatch
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_auto_dispatches_ready_task(dbsession, project):
    task = await _ready_task(dbsession, project, "t1")
    task_id = task.id
    worker = MockWorkerAdapter(dbsession)
    sched = Scheduler(dbsession, worker=worker)

    decisions = await sched.dispatch_once(project.id)

    assert len(decisions) == 1
    assert decisions[0].action == "DISPATCHED"
    # MockWorker commits short tx per task (WP-041) — scalar status read
    from sqlalchemy import select

    status = (
        await dbsession.execute(select(Task.status).where(Task.id == task_id))
    ).scalar_one()
    assert status == TaskStatus.EXECUTION_DONE  # MockWorker completes instantly


@pytest.mark.asyncio
async def test_only_ready_tasks_dispatched(dbsession, project):
    """CREATED tasks are not dispatched."""
    mgr = TaskGraphManager(dbsession)
    await mgr.create_task(Task(project_id=project.id, name="not-ready"))
    worker = MockWorkerAdapter(dbsession)
    sched = Scheduler(dbsession, worker=worker)

    decisions = await sched.dispatch_once(project.id)
    assert decisions == []


# ════════════════════════════════════════════════════════════════════════════
# Concurrency limit
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrency_limit(dbsession, project):
    """max_workers=1: second READY task gets SKIP_NO_CAPACITY."""
    t1 = await _ready_task(dbsession, project, "t1")
    t2 = await _ready_task(dbsession, project, "t2")

    # Simulate a running task occupying the slot
    from project_runtime.project_state.fsm import StateTransitionService

    fsm = StateTransitionService(dbsession)
    await fsm.transition_task(t1, TaskStatus.RUNNING)
    await dbsession.flush()

    worker = MockWorkerAdapter(dbsession)
    sched = Scheduler(dbsession, worker=worker, max_workers=1)
    decisions = await sched.dispatch_once(project.id)

    assert len(decisions) == 1
    assert decisions[0].task_id == t2.id
    assert decisions[0].action == "SKIP_NO_CAPACITY"
    await dbsession.refresh(t2)
    assert t2.status == TaskStatus.READY  # not lost


# ════════════════════════════════════════════════════════════════════════════
# No worker → task not lost
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_worker_task_stays_ready(dbsession, project):
    task = await _ready_task(dbsession, project, "t1")
    sched = Scheduler(dbsession, worker=None)

    decisions = await sched.dispatch_once(project.id)

    assert decisions[0].action == "SKIP_NO_WORKER"
    await dbsession.refresh(task)
    assert task.status == TaskStatus.READY


# ════════════════════════════════════════════════════════════════════════════
# MANUAL mode
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_manual_mode_creates_manual_action(dbsession, project):
    project.automation_mode = AutomationMode.MANUAL
    await dbsession.flush()

    task = await _ready_task(dbsession, project, "t1")
    worker = MockWorkerAdapter(dbsession)
    sched = Scheduler(dbsession, worker=worker)

    decisions = await sched.dispatch_once(project.id)

    assert decisions[0].action == "MANUAL_PENDING"
    await dbsession.refresh(task)
    assert task.status == TaskStatus.READY  # not dispatched

    # ManualAction persisted
    from sqlalchemy import select

    stmt = select(ManualAction).where(ManualAction.task_id == task.id)
    action = (await dbsession.execute(stmt)).scalar_one()
    assert action.status == ManualActionStatus.PENDING


@pytest.mark.asyncio
async def test_confirm_manual_action_dispatches(dbsession, project):
    project.automation_mode = AutomationMode.MANUAL
    await dbsession.flush()

    task = await _ready_task(dbsession, project, "t1")
    task_id = task.id
    worker = MockWorkerAdapter(dbsession)
    sched = Scheduler(dbsession, worker=worker)

    decisions = await sched.dispatch_once(project.id)
    assert decisions[0].action == "MANUAL_PENDING"

    from sqlalchemy import select

    stmt = select(ManualAction).where(ManualAction.task_id == task_id)
    action = (await dbsession.execute(stmt)).scalar_one()
    action_id = action.id

    result = await sched.confirm_manual_action(action_id)
    assert result.action == "DISPATCHED"
    # Worker committed short tx (WP-041) — scalar reads post-commit
    status = (
        await dbsession.execute(select(Task.status).where(Task.id == task_id))
    ).scalar_one()
    assert status == TaskStatus.EXECUTION_DONE
    a_status = (
        await dbsession.execute(
            select(ManualAction.status).where(ManualAction.id == action_id)
        )
    ).scalar_one()
    assert a_status == ManualActionStatus.CONFIRMED


# ════════════════════════════════════════════════════════════════════════════
# Scheduler does not touch task business state directly
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scheduler_never_sets_task_status_directly(dbsession, project):
    """Scheduler only calls worker.assign(); RUNNING→EXECUTION_DONE is the
    Worker's FSM path. Verified by: with no worker, nothing changes."""
    task = await _ready_task(dbsession, project, "t1")
    sched = Scheduler(dbsession, worker=None)
    await sched.dispatch_once(project.id)
    await dbsession.refresh(task)
    assert task.status == TaskStatus.READY
