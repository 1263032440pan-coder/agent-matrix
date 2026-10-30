"""SYS-143/144/146: 单节点用户 Rework Core。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from project_runtime.execution.user_rework import (
    UserReworkConflictError,
    UserReworkNotFoundError,
    preview_user_rework,
    release_cascaded_after_accept,
    run_user_rework,
)
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AutomationMode,
    DependencyType,
    TaskStatus,
)
from project_runtime.project_state.models import Project, Task, TaskDependency


@pytest.fixture
async def dbsession():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def _proj_tasks(session, *, cascade_ready: bool = False):
    p = Project(
        name="ur",
        automation_mode=AutomationMode.FULL_AUTO,
    )
    session.add(p)
    await session.flush()
    up = Task(project_id=p.id, name="upstream", status=TaskStatus.ACCEPTED)
    mid = Task(project_id=p.id, name="mid", status=TaskStatus.ACCEPTED)
    down = Task(
        project_id=p.id,
        name="down",
        status=TaskStatus.ACCEPTED if not cascade_ready else TaskStatus.READY,
    )
    session.add_all([up, mid, down])
    await session.flush()
    session.add(
        TaskDependency(
            task_id=mid.id,
            depends_on_id=up.id,
            dependency_type=DependencyType.HARD,
        )
    )
    session.add(
        TaskDependency(
            task_id=down.id,
            depends_on_id=mid.id,
            dependency_type=DependencyType.HARD,
        )
    )
    await session.flush()
    return p, up, mid, down


@pytest.mark.asyncio
async def test_preview_no_cascade(dbsession):
    p, _up, mid, down = await _proj_tasks(dbsession)
    prev = await preview_user_rework(
        dbsession, p.id, mid.id, cascade_hard_downstream=False,
    )
    assert prev["task_id"] == mid.id
    assert prev["to_status"] == "READY"
    assert prev["released"] is True
    assert prev["cascaded_task_ids"] == []
    await dbsession.refresh(mid)
    assert mid.status == TaskStatus.ACCEPTED  # dry 不写


@pytest.mark.asyncio
async def test_run_reopens_target_only(dbsession):
    p, _up, mid, down = await _proj_tasks(dbsession)
    out = await run_user_rework(
        dbsession,
        p.id,
        mid.id,
        cascade_hard_downstream=False,
        submit=False,
    )
    assert out["to_status"] == "READY"
    await dbsession.refresh(mid)
    await dbsession.refresh(down)
    assert mid.status == TaskStatus.READY
    assert down.status == TaskStatus.ACCEPTED


@pytest.mark.asyncio
async def test_cascade_parks_downstream_in_rework(dbsession):
    p, _up, mid, down = await _proj_tasks(dbsession)
    out = await run_user_rework(
        dbsession,
        p.id,
        mid.id,
        cascade_hard_downstream=True,
        submit=False,
    )
    assert down.id in out["cascaded_task_ids"]
    await dbsession.refresh(mid)
    await dbsession.refresh(down)
    assert mid.status == TaskStatus.READY
    assert down.status == TaskStatus.REWORK


@pytest.mark.asyncio
async def test_release_cascaded_after_accept(dbsession):
    p, _up, mid, down = await _proj_tasks(dbsession)
    await run_user_rework(
        dbsession,
        p.id,
        mid.id,
        cascade_hard_downstream=True,
        submit=False,
    )
    # simulate mid accepted again
    mid.status = TaskStatus.ACCEPTED
    await dbsession.flush()
    released = await release_cascaded_after_accept(
        dbsession, p.id, mid.id, submit=False,
    )
    assert down.id in released
    await dbsession.refresh(down)
    assert down.status == TaskStatus.READY


@pytest.mark.asyncio
async def test_running_target_409(dbsession):
    p, _up, mid, _down = await _proj_tasks(dbsession)
    mid.status = TaskStatus.RUNNING
    await dbsession.flush()
    with pytest.raises(UserReworkConflictError):
        await run_user_rework(
            dbsession, p.id, mid.id, cascade_hard_downstream=False,
        )


@pytest.mark.asyncio
async def test_missing_task_404(dbsession):
    p, *_ = await _proj_tasks(dbsession)
    with pytest.raises(UserReworkNotFoundError):
        await run_user_rework(
            dbsession, p.id, 99999, cascade_hard_downstream=False,
        )


@pytest.mark.asyncio
async def test_event_payload_has_cascaded(dbsession):
    from sqlalchemy import select

    from project_runtime.event.models import RuntimeEvent, RuntimeEventType

    p, _up, mid, down = await _proj_tasks(dbsession)
    await run_user_rework(
        dbsession,
        p.id,
        mid.id,
        cascade_hard_downstream=True,
        submit=False,
    )
    rows = (
        await dbsession.execute(
            select(RuntimeEvent).where(
                RuntimeEvent.event_type
                == RuntimeEventType.USER_TASK_REWORK.value,
                RuntimeEvent.task_id == mid.id,
            )
        )
    ).scalars().all()
    assert rows
    payload = json.loads(rows[0].payload or "{}")
    assert down.id in payload.get("cascaded_task_ids", [])
    assert payload.get("bypass_gate_fuse") is True
