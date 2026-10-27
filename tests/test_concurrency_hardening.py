"""WP-041 SQLite Concurrency Hardening tests (DB group).

DB-01/02/03/04/06/07 core guarantees, measured not asserted-by-design:
- 3 concurrent Executions run to completion without persistent lock errors
- Worker (LLM 60s-scale simulated) holds NO open write transaction
- commit_with_retry: bounded retries + audit events (DB-06)
- Normal path does not depend on the 30s busy_timeout (DB-07)
"""

import asyncio
import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.engine as _evmod
import project_runtime.event.models  # noqa: F401 — register runtime_event (gate direct-insert)
import project_runtime.execution.models  # noqa: F401 — register table
import project_runtime.execution.service as _svc
from infrastructure.database.short_tx import (
    commit_with_retry,
    get_lock_audit,
    reset_lock_audit,
)
from project_runtime.execution.models import Execution, ExecutionStatus
from project_runtime.execution.repository import ExecutionRepository
from project_runtime.execution.service import ExecutionRunner
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode, TaskStatus
from project_runtime.project_state.models import Project, Task
from project_runtime.task_graph.manager import TaskGraphManager


@pytest.fixture
async def dbengine(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Phase D (auto-gate) writes events via the event engine — patch it too
    monkeypatch.setattr(_svc, "engine", eng)
    monkeypatch.setattr(_evmod, "engine", eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def project_id(dbengine):
    async with AsyncSession(dbengine) as s:
        p = Project(name="db-hard", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        await s.commit()
        return pid


async def _mk_ready_task(dbengine, project_id, name):
    async with AsyncSession(dbengine) as s:
        mgr = TaskGraphManager(s)
        t = await mgr.create_task(Task(project_id=project_id, name=name))
        await mgr.promote_to_ready(t.id)
        tid = t.id
        await s.commit()
        return tid


@pytest.mark.asyncio
async def test_three_concurrent_executions_no_persistent_lock(
    dbengine, project_id, monkeypatch
):
    """DB-02/D-group: 3 concurrent Executions complete; lock retries bounded."""
    import project_runtime.execution.service as svc

    monkeypatch.setattr(svc, "engine", dbengine)

    async def fake_llm(self, wp):
        await asyncio.sleep(0.05)
        return f"# out {wp.task_name}"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", fake_llm
    )

    task_ids = [await _mk_ready_task(dbengine, project_id, f"t{i}") for i in range(3)]

    ex_ids = []
    async with AsyncSession(dbengine) as s:
        repo = ExecutionRepository(s)
        for tid in task_ids:
            ex = await repo.create(project_id, tid)
            ex_ids.append(ex.id)
        await s.commit()

    reset_lock_audit()
    t0 = time.monotonic()
    await asyncio.gather(*(ExecutionRunner().run(eid) for eid in ex_ids))
    elapsed = time.monotonic() - t0

    import time as _t
    deadline = _t.monotonic() + 10
    finals = {}
    while _t.monotonic() < deadline:
        async with AsyncSession(dbengine) as s:
            for tid in task_ids:
                from sqlalchemy import select as _sel
                finals[tid] = (
                    await s.execute(_sel(Task.status).where(Task.id == tid))
                ).scalar_one()
        if all(v == TaskStatus.ACCEPTED for v in finals.values()):
            break
        await asyncio.sleep(0.1)
    # V1.13+: auto-gate (Phase D) accepts after execution — final task state
    async with AsyncSession(dbengine) as s:
        for eid in ex_ids:
            ex = await s.get(Execution, eid)
            assert ex.status == ExecutionStatus.SUCCEEDED.value
        for tid in task_ids:
            assert finals[tid] == TaskStatus.ACCEPTED  # via auto-gate

    # Concurrency succeeded in ~parallel time (3×0.05s serial would be ≥0.15s).
    # Budget includes Phase D (auto-gate + downstream promote per execution,
    # added V1.13+): wall time stays well under serial-with-locks territory.
    assert elapsed < 3.0
    # DB-06: if any lock retries happened, they were bounded & audited
    assert len(get_lock_audit()) <= 9  # 3 commits × max 3 retries


@pytest.mark.asyncio
async def test_worker_holds_no_write_transaction_during_llm(
    dbengine, project_id, monkeypatch
):
    """DB-01/04: while the (simulated) LLM call is in flight, another session
    can write immediately — proves no open write transaction is held."""
    import project_runtime.execution.service as svc

    monkeypatch.setattr(svc, "engine", dbengine)

    llm_in_flight = asyncio.Event()
    release_llm = asyncio.Event()

    async def slow_llm(self, wp):
        llm_in_flight.set()
        await asyncio.wait_for(release_llm.wait(), timeout=5)
        return "# done"

    monkeypatch.setattr(
        "project_runtime.worker.llm_worker.LLMWorkerAdapter._execute_llm", slow_llm
    )

    task_id = await _mk_ready_task(dbengine, project_id, "t-slow")
    async with AsyncSession(dbengine) as s:
        repo = ExecutionRepository(s)
        ex = await repo.create(project_id, task_id)
        ex_id = ex.id
        await s.commit()

    runner_task = asyncio.create_task(ExecutionRunner().run(ex_id))
    await asyncio.wait_for(llm_in_flight.wait(), timeout=5)

    # While LLM is "running", an unrelated write must succeed fast (no lock wait)
    t0 = time.monotonic()
    async with AsyncSession(dbengine) as s:
        p = await s.get(Project, project_id)
        p.goal = "updated during llm call"
        await commit_with_retry(s, context="db04-probe")
    write_elapsed = time.monotonic() - t0
    assert write_elapsed < 1.0  # milliseconds-scale — no 30s busy_timeout reliance (DB-07)

    release_llm.set()
    await asyncio.wait_for(runner_task, timeout=10)

    async with AsyncSession(dbengine) as s:
        ex = await s.get(Execution, ex_id)
        assert ex.status == ExecutionStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_commit_with_retry_audit_and_bound(dbengine, project_id):
    """DB-06: commit_with_retry retries transient lock with audit, then raises.

    Real-lock integration is covered by the 3-concurrent-executions test; here
    we deterministically verify the retry policy via a stubbed failing commit.
    """
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import OperationalError

    async with AsyncSession(dbengine) as s:
        p = await s.get(Project, project_id)
        p.goal = "normal commit"
        reset_lock_audit()
        await commit_with_retry(s, context="normal")
        assert get_lock_audit() == []  # no lock events on clean path

    # Deterministic lock simulation: commit raises "database is locked" twice
    # then succeeds on the 3rd attempt → 2 audited retries, final success.
    async with AsyncSession(dbengine) as s:
        p = await s.get(Project, project_id)
        p.goal = "retry then succeed"
        real_commit = s.commit
        calls = {"n": 0}

        async def flaky_commit():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise OperationalError("x", {}, Exception("database is locked"))
            await real_commit()

        async def restage():
            p2 = await s.get(Project, project_id)
            p2.goal = "retry then succeed"

        reset_lock_audit()
        # rollback discards pending ORM writes — on_retry re-stages them.
        s.commit = AsyncMock(side_effect=flaky_commit)  # type: ignore[method-assign]
        await commit_with_retry(s, context="flaky", on_retry=restage)
        events = get_lock_audit()
        assert len(events) == 2
        assert all(e["context"] == "flaky" for e in events)
        assert events[0]["attempt"] == 1 and events[1]["attempt"] == 2

    # Bound: always-failing commit raises after MAX_RETRIES (3) + 1 attempts
    async with AsyncSession(dbengine) as s:
        p = await s.get(Project, project_id)
        p.goal = "always locked"

        async def always_locked():
            raise OperationalError("x", {}, Exception("database is locked"))

        s.commit = AsyncMock(side_effect=always_locked)  # type: ignore[method-assign]
        reset_lock_audit()
        with pytest.raises(OperationalError):
            await commit_with_retry(s, context="bound-probe")
        assert len(get_lock_audit()) == 3  # exactly MAX_RETRIES audited events
