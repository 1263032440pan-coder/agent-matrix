"""SYS-106: plan_status 生命周期门——非 READY 拒绝执行。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from project_runtime.execution.service import (
    PlanNotReadyError,
)
from project_runtime.project_state.enums import AutomationMode, ProjectStatus, TaskStatus
from project_runtime.project_state.models import Base, Project, Task


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_submit_rejects_when_assigning(session, monkeypatch):
    proj = Project(
        name="p",
        automation_mode=AutomationMode.FULL_AUTO,
        status=ProjectStatus.CREATED,
        plan_status="ASSIGNING",
    )
    session.add(proj)
    await session.flush()
    task = Task(
        project_id=proj.id,
        name="t1",
        task_type="generic",
        status=TaskStatus.READY,
    )
    session.add(task)
    await session.commit()

    # 把 ExecutionService 用的 engine 指到本测试库较难；直接测守卫函数
    from project_runtime.execution.service import assert_plan_ready_for_execution

    with pytest.raises(PlanNotReadyError) as ei:
        await assert_plan_ready_for_execution(session, proj.id)
    assert ei.value.plan_status == "ASSIGNING"


@pytest.mark.asyncio
async def test_submit_allows_ready(session):
    from project_runtime.execution.service import assert_plan_ready_for_execution

    proj = Project(
        name="p2",
        automation_mode=AutomationMode.FULL_AUTO,
        status=ProjectStatus.CREATED,
        plan_status="READY",
    )
    session.add(proj)
    await session.commit()
    await assert_plan_ready_for_execution(session, proj.id)


@pytest.mark.asyncio
async def test_submit_rejects_invalid(session):
    from project_runtime.execution.service import assert_plan_ready_for_execution

    proj = Project(
        name="p3",
        automation_mode=AutomationMode.FULL_AUTO,
        status=ProjectStatus.CREATED,
        plan_status="INVALID",
    )
    session.add(proj)
    await session.commit()
    with pytest.raises(PlanNotReadyError):
        await assert_plan_ready_for_execution(session, proj.id)
