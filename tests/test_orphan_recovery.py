"""orphan recovery — runtime 重启后 RUNNING 判死停住（方案 B）."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.execution.models import Execution
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AutomationMode, TaskStatus
from project_runtime.project_state.models import Project, Task
from project_runtime.worker.agent_models import (
    AgentDefinition,
    AgentInstance,
    AgentStatus,
)
from project_runtime.worker.registry import seed_builtin_templates


@pytest.fixture
async def eng(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/orphan.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


async def _seed_running_stack(session: AsyncSession) -> tuple[int, int, int]:
    p = Project(name="orphan", automation_mode=AutomationMode.FULL_AUTO)
    session.add(p)
    await session.flush()
    t = Task(
        project_id=p.id,
        name="交付",
        status=TaskStatus.RUNNING,
        task_type="DELIVERY",
    )
    session.add(t)
    await session.flush()
    ex = Execution(
        project_id=p.id,
        task_id=t.id,
        attempt_no=1,
        status="RUNNING",
    )
    session.add(ex)
    await session.flush()
    await seed_builtin_templates(session)
    d = (await session.execute(select(AgentDefinition).limit(1))).scalar_one()
    inst = AgentInstance(
        definition_id=d.id,
        project_id=p.id,
        status=AgentStatus.BUSY,
        current_task_id=t.id,
    )
    session.add(inst)
    await session.flush()
    return ex.id, t.id, inst.id


@pytest.mark.asyncio
async def test_running_orphan_fails_task_and_releases_agent(eng):
    from project_runtime.execution.orphan_recovery import recover_orphan_executions

    async with AsyncSession(eng) as s:
        ex_id, tid, aid = await _seed_running_stack(s)
        await s.commit()

    async with AsyncSession(eng) as s:
        queued = await recover_orphan_executions(s)
        await s.commit()

    assert queued == []

    async with AsyncSession(eng) as s:
        ex = await s.get(Execution, ex_id)
        task = await s.get(Task, tid)
        agent = await s.get(AgentInstance, aid)
        assert str(ex.status) in ("FAILED", "ExecutionStatus.FAILED")
        assert getattr(ex.status, "value", ex.status) == "FAILED"
        assert ex.error_code == "ORPHAN_RECOVERY"
        assert task.status is TaskStatus.FAILED
        assert agent.status is AgentStatus.IDLE
        assert agent.current_task_id is None


@pytest.mark.asyncio
async def test_queued_orphan_returned_for_redispatch(eng):
    from project_runtime.execution.orphan_recovery import recover_orphan_executions

    async with AsyncSession(eng) as s:
        p = Project(name="q", automation_mode=AutomationMode.MANUAL)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id,
            name="t",
            status=TaskStatus.READY,
            task_type="DEV",
        )
        s.add(t)
        await s.flush()
        ex = Execution(
            project_id=p.id,
            task_id=t.id,
            attempt_no=1,
            status="QUEUED",
        )
        s.add(ex)
        await s.flush()
        ex_id = ex.id
        await s.commit()

    async with AsyncSession(eng) as s:
        queued = await recover_orphan_executions(s)
        await s.commit()

    assert queued == [ex_id]
    async with AsyncSession(eng) as s:
        ex = await s.get(Execution, ex_id)
        assert getattr(ex.status, "value", ex.status) == "QUEUED"
