"""WP-042 Event Engine tests — EV-group core (EV-01~03, 05, 07~09 essential).

LLM mocked; verifies event trail, ordering, persistence, and that event
recording never breaks the execution path.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Point the event engine + execution service at the test DB
import project_runtime.event.engine as event_engine_mod
import project_runtime.event.models  # noqa: F401 — register table
import project_runtime.execution.service as exec_service_mod
from infrastructure.database import short_tx
from project_runtime.event import EventEngine, RuntimeEventType
from project_runtime.execution.models import ExecutionStatus
from project_runtime.execution.service import ExecutionService
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode, TaskStatus
from project_runtime.project_state.models import Project, Task
from project_runtime.task_graph.manager import TaskGraphManager


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def patched_engines(dbengine, monkeypatch):
    monkeypatch.setattr(event_engine_mod, "engine", dbengine)
    monkeypatch.setattr(exec_service_mod, "engine", dbengine)
    return dbengine


@pytest.fixture
async def project_id(patched_engines):
    async with AsyncSession(patched_engines) as s:
        p = Project(name="ev-test", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
        return pid


async def _mk_ready_task(dbengine, project_id, name="t1"):
    async with AsyncSession(dbengine) as s:
        mgr = TaskGraphManager(s)
        t = await mgr.create_task(Task(project_id=project_id, name=name))
        await mgr.promote_to_ready(t.id)
        tid = t.id
        await s.commit()
        return tid


@pytest.mark.asyncio
async def test_event_engine_record_and_order(patched_engines, project_id):
    """EV-01/03: events persist with execution_id, id order == time order."""
    ev = EventEngine()
    e1 = await ev.record(
        RuntimeEventType.EXECUTION_QUEUED,
        project_id=project_id, execution_id=99, phase="QUEUED", message="q",
    )
    e2 = await ev.record(
        RuntimeEventType.EXECUTION_STARTED,
        project_id=project_id, execution_id=99, phase="RUNNING", message="s",
    )
    assert e1 is not None and e2 is not None and e2 > e1

    events = await ev.list_for_execution(99)
    assert [e.event_type for e in events] == [
        "EXECUTION_QUEUED", "EXECUTION_STARTED",
    ]
    assert all(e.execution_id == 99 for e in events)


@pytest.mark.asyncio
async def test_after_id_reconnection_recovery(patched_engines, project_id):
    """EV-09 / spec §8: after_id returns only later events."""
    ev = EventEngine()
    for i in range(5):
        await ev.record(
            RuntimeEventType.WORKER_COMPLETED,
            project_id=project_id, execution_id=7, message=f"m{i}",
        )
    all_events = await ev.list_for_execution(7)
    third_id = all_events[2].id
    tail = await ev.list_for_execution(7, after_id=third_id)
    assert len(tail) == 2
    assert tail[0].message == "m3"


@pytest.mark.asyncio
async def test_runner_emits_full_event_trail(patched_engines, project_id, monkeypatch):
    """EV-01~03: submit → runner produces ordered QUEUED→…→COMPLETED trail."""
    async def fake_llm(self, wp):
        return "# done"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )

    task_id = await _mk_ready_task(patched_engines, project_id)
    service = ExecutionService()
    ex_id = await service.submit(project_id, task_id)
    # wait for background runner
    for _ in range(50):
        async with AsyncSession(patched_engines) as s:
            ex = await s.get(
                __import__("project_runtime.execution.models", fromlist=["Execution"]).Execution,
                ex_id,
            )
            if ex and ex.status not in (
                ExecutionStatus.QUEUED.value, ExecutionStatus.RUNNING.value,
            ):
                break
        await asyncio.sleep(0.1)

    events = await EventEngine().list_for_execution(ex_id)
    types = [e.event_type for e in events]
    assert "EXECUTION_QUEUED" in types
    assert "EXECUTION_STARTED" in types
    assert "WORKER_STARTED" in types
    assert "WORKER_COMPLETED" in types
    assert types[-1] == "EXECUTION_COMPLETED"
    # ordering: QUEUED first, terminal last
    assert types.index("EXECUTION_QUEUED") == 0
    # every event carries the execution_id (acceptance §2)
    assert all(e.execution_id == ex_id for e in events)


@pytest.mark.asyncio
async def test_event_failure_never_breaks_execution(patched_engines, project_id, monkeypatch):
    """EV-10-ish / spec §9: event persistence failing does not fail the run."""
    async def fake_llm(self, wp):
        return "# ok"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )

    async def broken_record(self, *a, **k):
        raise RuntimeError("event store down")

    monkeypatch.setattr(EventEngine, "record", broken_record)

    task_id = await _mk_ready_task(patched_engines, project_id)
    service = ExecutionService()
    ex_id = await service.submit(project_id, task_id)
    for _ in range(50):
        async with AsyncSession(patched_engines) as s:
            ex = await s.get(
                __import__("project_runtime.execution.models", fromlist=["Execution"]).Execution,
                ex_id,
            )
            if ex and ex.status == ExecutionStatus.SUCCEEDED.value:
                break
        await asyncio.sleep(0.1)

    async with AsyncSession(patched_engines) as s:
        ex = await s.get(
            __import__("project_runtime.execution.models", fromlist=["Execution"]).Execution,
            ex_id,
        )
        assert ex.status == ExecutionStatus.SUCCEEDED.value  # execution unaffected
        t = await s.get(Task, task_id)
        assert t.status == TaskStatus.EXECUTION_DONE


@pytest.mark.asyncio
async def test_event_write_is_short_tx(patched_engines, project_id, monkeypatch):
    """RH-02 衔接: event inserts go through commit_with_retry (bounded, audited)."""
    calls = []
    real_retry = short_tx.commit_with_retry

    async def spy_retry(session, *, context=""):
        calls.append(context)
        return await real_retry(session, context=context)

    monkeypatch.setattr(event_engine_mod.short_tx, "commit_with_retry", spy_retry)

    await EventEngine().record(
        RuntimeEventType.ARTIFACT_CREATED,
        project_id=project_id, execution_id=5, message="x",
    )
    assert calls == ["event.ARTIFACT_CREATED"]  # one short tx per event
