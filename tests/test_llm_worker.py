"""LLMWorker — red lines + LLM execution path (mocked litellm)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.artifact.manager import ArtifactManager
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import Project, Task
from project_runtime.task_graph.manager import TaskGraphManager
from project_runtime.worker.llm_worker import LLMWorkerAdapter


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
    p = Project(name="llm-worker-test")
    dbsession.add(p)
    await dbsession.flush()
    return p


async def _ready_task(dbsession, project, name="llm-task") -> Task:
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name=name))
    await mgr.promote_to_ready(task.id)
    return task


@pytest.mark.asyncio
async def test_llm_worker_executes_and_produces_artifact(dbsession, project, monkeypatch):
    """LLMWorker: READY → RUNNING → EXECUTION_DONE with LLM output artifact."""
    task = await _ready_task(dbsession, project)
    artifact_mgr = ArtifactManager(dbsession, workspace_root="data/test_llm_worker")
    worker = LLMWorkerAdapter(dbsession, artifact_mgr=artifact_mgr)

    # Mock the LLM call (don't hit real API in tests)
    async def fake_llm(wp):
        return f"# {wp.task_name} 产出\n\n这是 LLM 生成的内容。"

    monkeypatch.setattr(worker, "_execute_llm", fake_llm)

    from project_runtime.worker.adapter import WorkPackage

    result = await worker.assign(WorkPackage(task_id=task.id, task_name=task.name))

    assert result.success
    assert "产出" in result.output
    assert len(result.artifacts) == 1

    mgr = TaskGraphManager(dbsession)
    updated = await mgr.get_task(task.id)
    assert updated.status == TaskStatus.EXECUTION_DONE  # not ACCEPTED


@pytest.mark.asyncio
async def test_llm_worker_failure_marks_failed(dbsession, project, monkeypatch):
    """LLM failure → FAILED (not silently EXECUTION_DONE)."""
    task = await _ready_task(dbsession, project)
    worker = LLMWorkerAdapter(dbsession)

    async def fail_llm(wp):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(worker, "_execute_llm", fail_llm)

    from project_runtime.worker.adapter import WorkPackage

    result = await worker.assign(WorkPackage(task_id=task.id, task_name=task.name))
    assert not result.success

    mgr = TaskGraphManager(dbsession)
    updated = await mgr.get_task(task.id)
    assert updated.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_llm_worker_not_ready_rejected(dbsession, project):
    """Red line: worker cannot start from non-READY."""
    task = await TaskGraphManager(dbsession).create_task(
        Task(project_id=project.id, name="not-ready")
    )
    worker = LLMWorkerAdapter(dbsession)

    from project_runtime.worker.adapter import WorkPackage

    with pytest.raises(ValueError, match="READY"):
        await worker.assign(WorkPackage(task_id=task.id, task_name=task.name))


@pytest.mark.asyncio
async def test_llm_worker_health(dbsession):
    worker = LLMWorkerAdapter(dbsession)
    # Without LLM config, health reports not_configured
    status = await worker.health()
    assert status in ("healthy", "llm_not_configured")
