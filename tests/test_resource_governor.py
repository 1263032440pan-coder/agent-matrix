"""D15 ResourceGovernor Lite — ALLOW/DEFER_RESOURCE decisions, never FAIL."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.fsm import StateTransitionService
from project_runtime.project_state.models import Project, Task
from project_runtime.resource_governor.governor import (
    ResourceDecision,
    ResourceGovernor,
)
from project_runtime.task_graph.manager import TaskGraphManager


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
    p = Project(name="d15-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


# ════════════════════════════════════════════════════════════════════════════
# ALLOW within capacity
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_allow_within_capacity(dbsession, project):
    gov = ResourceGovernor(dbsession, max_workers=1)
    assert await gov.evaluate(project.id) == ResourceDecision.ALLOW_DISPATCH


# ════════════════════════════════════════════════════════════════════════════
# DEFER_RESOURCE at capacity — task stays READY, never FAILED
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_defer_at_capacity_task_stays_ready(dbsession, project):
    mgr = TaskGraphManager(dbsession)
    fsm = StateTransitionService(dbsession)

    running = await mgr.create_task(Task(project_id=project.id, name="running"))
    await mgr.promote_to_ready(running.id)
    await dbsession.refresh(running)
    await fsm.transition_task(running, TaskStatus.RUNNING)

    waiting = await mgr.create_task(Task(project_id=project.id, name="waiting"))
    await mgr.promote_to_ready(waiting.id)

    gov = ResourceGovernor(dbsession, max_workers=1)
    decision = await gov.evaluate(project.id)

    assert decision == ResourceDecision.DEFER_RESOURCE
    await dbsession.refresh(waiting)
    assert waiting.status == TaskStatus.READY  # not lost, not FAILED


# ════════════════════════════════════════════════════════════════════════════
# After release → ALLOW again
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_allow_after_release(dbsession, project):
    mgr = TaskGraphManager(dbsession)
    fsm = StateTransitionService(dbsession)
    gov = ResourceGovernor(dbsession, max_workers=1)

    t = await mgr.create_task(Task(project_id=project.id, name="t"))
    await mgr.promote_to_ready(t.id)
    await dbsession.refresh(t)
    await fsm.transition_task(t, TaskStatus.RUNNING)
    assert await gov.evaluate(project.id) == ResourceDecision.DEFER_RESOURCE

    # Release the slot (task completes via gate path)
    await fsm.transition_task(t, TaskStatus.EXECUTION_DONE)
    await fsm.transition_task(t, TaskStatus.ACCEPTED, actor="GATE")

    assert await gov.evaluate(project.id) == ResourceDecision.ALLOW_DISPATCH


# ════════════════════════════════════════════════════════════════════════════
# Governor never FAILs a task (rule 3)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_governor_never_fails_task(dbsession, project):
    """DEFER_RESOURCE decision set does not contain FAIL semantics; task status
    is untouched by evaluate()."""
    mgr = TaskGraphManager(dbsession)
    fsm = StateTransitionService(dbsession)

    t = await mgr.create_task(Task(project_id=project.id, name="t"))
    await mgr.promote_to_ready(t.id)
    await dbsession.refresh(t)
    await fsm.transition_task(t, TaskStatus.RUNNING)

    gov = ResourceGovernor(dbsession, max_workers=1)
    decision = await gov.evaluate(project.id)
    assert decision == ResourceDecision.DEFER_RESOURCE
    # evaluate() is read-only: task status unchanged
    await dbsession.refresh(t)
    assert t.status == TaskStatus.RUNNING
