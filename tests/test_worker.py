"""D11 Worker Adapter — tests for MockWorker execution and red-line enforcement."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import Project, Task
from project_runtime.task_graph.manager import CreateTaskRequest, TaskGraphManager
from project_runtime.worker.adapter import MockWorkerAdapter, WorkPackage


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
    p = Project(name="d11-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _create_ready_task(dbsession, project, name="test-task"):
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(CreateTaskRequest(project_id=project.id, name=name))
    # Promote to READY (no dependencies)
    await mgr.promote_to_ready(task.id)
    return task


async def _status_of(dbsession, task_id):
    """Scalar status query — immune to ORM expiry after worker short-tx commits."""
    from sqlalchemy import select

    return (
        await dbsession.execute(select(Task.status).where(Task.id == task_id))
    ).scalar_one()


# ════════════════════════════════════════════════════════════════════════════
# Basic execution
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mock_worker_executes_task(dbsession, project):
    task = await _create_ready_task(dbsession, project)
    assert task.status == TaskStatus.READY
    task_id = task.id

    worker = MockWorkerAdapter(dbsession)
    result = await worker.assign(WorkPackage(task_id=task_id, task_name=task.name))

    assert result.success
    # Verify Task is EXECUTION_DONE (NOT ACCEPTED) — scalar read post-commit
    assert await _status_of(dbsession, task_id) == TaskStatus.EXECUTION_DONE


@pytest.mark.asyncio
async def test_mock_worker_pipeline_integration(dbsession, project):
    """D12 rework: assign() → TaskRun + Artifact + artifact_id in result."""
    import os

    from sqlalchemy import select

    from project_runtime.artifact.manager import ArtifactManager
    from project_runtime.project_state.models import Artifact, TaskRun

    task = await _create_ready_task(dbsession, project)
    task_id = task.id
    project_id = project.id
    artifact_mgr = ArtifactManager(dbsession, workspace_root="data/test_pipeline")
    worker = MockWorkerAdapter(dbsession, artifact_mgr=artifact_mgr)

    result = await worker.assign(WorkPackage(task_id=task_id, task_name=task.name))

    # (a) TaskRun exists with expected fields
    stmt = select(TaskRun).where(TaskRun.task_id == task_id)
    run = (await dbsession.execute(stmt)).scalar_one_or_none()
    assert run is not None
    assert run.attempt == 1
    assert run.started_at is not None
    assert run.finished_at is not None
    assert "Mock execution completed" in (run.result or "")

    # (b) ExecutionResult.artifacts non-empty
    assert len(result.artifacts) == 1

    # (c) Artifact record exists, file on disk
    artifact = await dbsession.get(Artifact, result.artifacts[0])
    assert artifact is not None
    assert artifact.task_id == task_id
    assert artifact.project_id == project_id
    assert os.path.exists(artifact.path)


@pytest.mark.asyncio
async def test_mock_worker_cancel(dbsession, project):
    """cancel() transitions RUNNING → CANCELLED via FSM."""
    task = await _create_ready_task(dbsession, project)
    worker = MockWorkerAdapter(dbsession)

    # Manually transition to RUNNING (simulating what assign() does first)
    from project_runtime.project_state.fsm import StateTransitionService
    fsm = StateTransitionService(dbsession)
    await fsm.transition_task(task, TaskStatus.RUNNING)
    await dbsession.flush()

    # Cancel should transition RUNNING → CANCELLED
    await worker.cancel(task.id)
    mgr = TaskGraphManager(dbsession)
    updated = await mgr.get_task(task.id)
    assert updated.status == TaskStatus.CANCELLED


# ════════════════════════════════════════════════════════════════════════════
# Red lines
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_worker_cannot_accept_directly(dbsession, project):
    """Red line 1+2: Worker must NOT set ACCEPTED. Only Gate can."""
    task = await _create_ready_task(dbsession, project)
    task_id = task.id

    # Simulate what would happen if Worker tried to go CREATED→ACCEPTED
    # (FSM prevents this — tested in D03)

    # After execution, task is at EXECUTION_DONE
    worker = MockWorkerAdapter(dbsession)
    await worker.assign(WorkPackage(task_id=task_id, task_name=task.name))

    assert await _status_of(dbsession, task_id) == TaskStatus.EXECUTION_DONE

    # Attempting EXECUTION_DONE → ACCEPTED directly (Gate should do this)
    # This transition IS legal (for Gate), but Worker never calls it.
    # The FSM allows EXECUTION_DONE → ACCEPTED only for Gate.
    # Here we just verify Worker never makes this call — it stops at EXECUTION_DONE.


@pytest.mark.asyncio
async def test_worker_rejects_non_ready(dbsession, project):
    """Red line 3: Worker must reject tasks not in READY status."""
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(CreateTaskRequest(project_id=project.id, name="not-ready"))
    # Task is CREATED, not READY

    worker = MockWorkerAdapter(dbsession)
    with pytest.raises(ValueError, match="Must be READY"):
        await worker.assign(WorkPackage(task_id=task.id, task_name=task.name))


# ════════════════════════════════════════════════════════════════════════════
# Health
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_worker_health():
    from sqlalchemy.ext.asyncio import create_async_engine as _ce
    engine = _ce("sqlite+aiosqlite://", echo=False)
    async with AsyncSession(engine) as s:
        worker = MockWorkerAdapter(s)
        assert await worker.health() == "healthy"
    await engine.dispose()
