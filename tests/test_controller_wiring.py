"""20号规范批次 2 接线测试: 执行链 Controller 集成（全离线）.

验证:
  [x] Gate PASS → 对账发生（MANIFEST_CHECK 事件落库）
  [x] 全局熔断: 连续 8 FAIL → auto-advance 被抑制 + 项目 PAUSED
  [x] 正常链路: 对账不阻断推进（READY 下游照常提交）
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.evidence.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.traceability.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
import project_runtime.worker.agent_models  # noqa: F401
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AutomationMode,
    ProjectStatus,
    TaskStatus,
)
from project_runtime.project_state.models import Project, Task


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wire.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(eng, *, n_failed: int = 0, n_accepted: int = 1):
    async with AsyncSession(eng) as s:
        p = Project(name="wire", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        for i in range(n_accepted):
            s.add(Task(project_id=pid, name=f"A{i}",
                       status=TaskStatus.ACCEPTED))
        for i in range(n_failed):
            s.add(Task(project_id=pid, name=f"F{i}",
                       status=TaskStatus.FAILED))
        await s.commit()
    return pid


@pytest.mark.asyncio
async def test_fuse_suppresses_advance(dbengine, monkeypatch):
    """五闸④: 熔断态下 _auto_advance 不提交任何任务。"""
    from project_runtime.execution import service as svc

    eng = pid = None
    eng = dbengine
    pid = await _seed(eng, n_failed=8)
    submitted = []

    async def fake_submit(self, project_id, task_id):
        submitted.append(task_id)
        return 1

    monkeypatch.setattr(svc, "engine", eng)
    monkeypatch.setattr(svc.ExecutionService, "submit", fake_submit)
    # Phase D 层先触发熔断（check_global_fuse 置 PAUSED）
    from project_runtime.worker.controller import Controller

    async with AsyncSession(eng) as s2:
        fused = await Controller(s2).check_global_fuse(pid)
        await s2.commit()
    assert fused is True
    # 再调 _auto_advance（Gate PASS 后的推进路径）——PAUSED 抑制提交
    runner = svc.ExecutionRunner.__new__(svc.ExecutionRunner)
    await runner._auto_advance(pid)
    assert submitted == [], "熔断项目不应提交任何任务"
    async with AsyncSession(eng) as s:
        proj = await s.get(Project, pid)
        assert proj.status is ProjectStatus.PAUSED


@pytest.mark.asyncio
async def test_normal_project_advances(dbengine, monkeypatch):
    """正常项目（无熔断）: READY 任务被提交。"""
    from project_runtime.execution import service as svc

    eng = dbengine
    async with AsyncSession(eng) as s:
        p = Project(name="ok", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(Task(project_id=pid, name="done",
                   status=TaskStatus.ACCEPTED))
        s.add(Task(project_id=pid, name="next", status=TaskStatus.READY))
        await s.commit()

    submitted = []

    async def fake_submit(self, project_id, task_id):
        submitted.append(task_id)
        return 1

    monkeypatch.setattr(svc, "engine", eng)
    monkeypatch.setattr(svc.ExecutionService, "submit", fake_submit)
    runner = svc.ExecutionRunner.__new__(svc.ExecutionRunner)
    await runner._auto_advance(pid)
    assert len(submitted) == 1, "READY 任务应被提交"
    async with AsyncSession(eng) as s:
        proj = await s.get(Project, pid)
        assert proj.status is not ProjectStatus.PAUSED


@pytest.mark.asyncio
async def test_manifest_check_event_emitted(dbengine):
    """对账产生 MANIFEST_CHECK 审计事件。"""
    eng = dbengine
    pid = await _seed(eng, n_failed=1)
    from project_runtime.event import EventEngine
    from project_runtime.worker.controller import Controller

    async with AsyncSession(eng) as s:
        await Controller(s, event_engine=EventEngine(eng)).reconcile(pid)
        await s.commit()
    from project_runtime.event.models import RuntimeEvent

    async with AsyncSession(eng) as s:
        rows = (
            await s.execute(
                select(RuntimeEvent).where(
                    RuntimeEvent.event_type == "MANIFEST_CHECK"
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert "✗卡1" in rows[0].message
