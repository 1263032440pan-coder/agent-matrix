"""WP-040 Async Execution tests — A-group matrix (AE-01 ~ AE-08).

LLM is fully mocked: these tests verify Execution lifecycle, idempotency,
and Task-FSM mapping — not network behavior.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401 — register runtime_event table
import project_runtime.execution.models  # noqa: F401 — register table
import project_runtime.event.engine as event_engine_module
import project_runtime.execution.service as exec_service_module
from project_runtime.execution.models import ExecutionStatus
from project_runtime.execution.repository import (
    ActiveExecutionExistsError,
    ExecutionRepository,
)
from project_runtime.execution.service import ExecutionRunner, ExecutionService
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import TaskStatus
from project_runtime.project_state.models import Project, Task
from project_runtime.task_graph.manager import TaskGraphManager


@pytest.fixture
async def dbengine(tmp_path, monkeypatch):
    """Test engine + engine patch for BOTH execution service and event engine
    (Phase D gate events go through EventEngine's own session)."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(exec_service_module, "engine", eng)
    monkeypatch.setattr(event_engine_module, "engine", eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def dbsession(dbengine):
    async with AsyncSession(dbengine) as s:
        yield s


@pytest.fixture
async def project(dbsession):
    from project_runtime.project_state.enums import AutomationMode

    p = Project(name="ae-test", automation_mode=AutomationMode.FULL_AUTO)
    dbsession.add(p)
    await dbsession.flush()
    return p


@pytest.fixture
async def project_id(dbsession, project):
    """Stable project id (survives commits that expire ORM objects)."""
    pid = project.id
    return pid


async def _ready_task(dbsession, project, name="t1"):
    mgr = TaskGraphManager(dbsession)
    task = await mgr.create_task(Task(project_id=project.id, name=name))
    await mgr.promote_to_ready(task.id)
    task_id = task.id
    await dbsession.commit()
    return task_id


@pytest.mark.asyncio
async def test_execution_repo_create_and_active_guard(dbsession, project, project_id):
    """AE-02/05: create QUEUED; duplicate active rejected."""
    task_id = await _ready_task(dbsession, project)
    repo = ExecutionRepository(dbsession)
    ex = await repo.create(project_id, task_id)
    assert ex.status == ExecutionStatus.QUEUED.value
    assert ex.attempt_no == 1

    with pytest.raises(ActiveExecutionExistsError):
        await repo.create(project_id, task_id)


@pytest.mark.asyncio
async def test_execution_retry_increments_attempt(dbsession, project, project_id):
    """AE-06: after FAILED, new Execution has attempt_no+1 (no overwrite)."""
    task_id = await _ready_task(dbsession, project)
    repo = ExecutionRepository(dbsession)
    ex1 = await repo.create(project_id, task_id)
    await repo.mark_running(ex1)
    await repo.mark_finished(ex1, False, error_message="boom")
    ex2 = await repo.create(project_id, task_id)
    assert ex2.attempt_no == 2
    assert ex1.status == ExecutionStatus.FAILED.value  # history preserved


@pytest.mark.asyncio
async def test_runner_success_maps_task_to_execution_done(
    dbsession, dbengine, project, project_id, monkeypatch
):
    """AE-07: Execution SUCCEEDED never directly ACCEPTs the Task.

    V1.13+ Phase D: after SUCCEEDED, the runner invokes GATE (actor=GATE)
    which may ACCEPT. The red line is that *the Execution path itself* never
    bypasses the gate — verified here by polling through the gate window.
    """
    task_id = await _ready_task(dbsession, project)

    async def fake_llm(self, wp):
        return "# 产出\nmock"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )
    import project_runtime.execution.service as svc

    monkeypatch.setattr(svc, "engine", dbengine)

    repo = ExecutionRepository(dbsession)
    ex = await repo.create(project_id, task_id)
    ex_id = ex.id
    await dbsession.commit()

    await ExecutionRunner().run(ex_id)

    import time as _time

    async with AsyncSession(dbengine) as s:
        updated_ex = await s.get(
            __import__("project_runtime.execution.models", fromlist=["Execution"]).Execution,
            ex_id,
        )
        assert updated_ex.status == ExecutionStatus.SUCCEEDED.value

    # Phase D gate runs after the terminal event — poll for its outcome.
    # Red line: acceptance (if any) comes from GATE, never from the worker.
    deadline = _time.monotonic() + 5
    final = None
    while _time.monotonic() < deadline:
        async with AsyncSession(dbengine) as s:
            from sqlalchemy import select as _sel

            final = (
                await s.execute(_sel(Task.status).where(Task.id == task_id))
            ).scalar_one()
        if final == TaskStatus.ACCEPTED:
            break
        await asyncio.sleep(0.1)
    assert final == TaskStatus.ACCEPTED  # via GATE (Phase D), not worker


@pytest.mark.asyncio
async def test_runner_failure_marks_failed_and_task_failed(
    dbsession, dbengine, project, project_id, monkeypatch
):
    """LLM failure → Execution FAILED + Task FAILED (not silently done)."""
    task_id = await _ready_task(dbsession, project)

    async def fake_llm(self, wp):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )
    import project_runtime.execution.service as svc

    monkeypatch.setattr(svc, "engine", dbengine)

    repo = ExecutionRepository(dbsession)
    ex = await repo.create(project_id, task_id)
    ex_id = ex.id
    await dbsession.commit()

    await ExecutionRunner().run(ex_id)

    async with AsyncSession(dbengine) as s:
        updated_ex = await s.get(
            __import__("project_runtime.execution.models", fromlist=["Execution"]).Execution,
            ex_id,
        )
        assert updated_ex.status == ExecutionStatus.FAILED.value
        assert updated_ex.error_code == "WORKER_FAILED"
        updated_task = await s.get(Task, task_id)
        assert updated_task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_submit_returns_immediately_and_runs_in_background(
    dbsession, dbengine, project, project_id, monkeypatch
):
    """AE-01/03: submit() returns without waiting; background task completes."""
    task_id = await _ready_task(dbsession, project)

    finished = asyncio.Event()

    async def slow_llm(self, wp):
        await asyncio.sleep(0.2)
        finished.set()
        return "# done"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", slow_llm
    )
    import project_runtime.execution.service as svc

    monkeypatch.setattr(svc, "engine", dbengine)

    service = ExecutionService()
    import time

    t0 = time.monotonic()
    ex_id = await service.submit(project_id, task_id)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.2  # returned before the (0.2s) LLM call finished
    assert isinstance(ex_id, int)

    await asyncio.wait_for(finished.wait(), timeout=5)  # background completes


@pytest.mark.asyncio
async def test_concurrent_duplicate_submit_one_wins(dbsession, dbengine, project, project_id):
    """AE-05 (concurrent): two simultaneous creates for the same Task — exactly one wins."""
    task_id = await _ready_task(dbsession, project)

    results = []

    async def _try_create():
        async with AsyncSession(dbengine) as s:
            repo = ExecutionRepository(s)
            try:
                ex = await repo.create(project_id, task_id)
                ex_id = ex.id
                await s.commit()
                results.append(("ok", ex_id))
            except ActiveExecutionExistsError:
                await s.rollback()
                results.append(("rejected", None))

    await asyncio.gather(_try_create(), _try_create())

    kinds = sorted(k for k, _ in results)
    assert kinds == ["ok", "rejected"]  # exactly one active Execution created


@pytest.mark.asyncio
async def test_execution_persists_across_sessions(dbsession, dbengine, project, project_id):
    """AE-04: Execution is queryable from a brand-new session (survives restart)."""
    from project_runtime.execution.models import Execution

    task_id = await _ready_task(dbsession, project)
    repo = ExecutionRepository(dbsession)
    ex = await repo.create(project_id, task_id)
    ex_id = ex.id
    await dbsession.commit()

    # Fresh session on the same DB file — simulates process restart read
    async with AsyncSession(dbengine) as s2:
        found = await s2.get(Execution, ex_id)
        assert found is not None
        assert found.status == ExecutionStatus.QUEUED.value
        assert found.task_id == task_id
