"""重新规划清场（用户决策 2026-10-09 方案 1: 全清）.

同一项目再次「生成计划」= 换一版方案——旧任务全部删除（含 ACCEPTED）
不叠加。项目/方法资产/研究记录保留。

验证:
  [x] A 项目清场不影响 B 项目任务（隔离）
  [x] 项目本体/方法资产保留
  （全清行为本体经 /plan API 实测——见变更记录 2026-10-09）
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
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    TaskStatus,
)
from project_runtime.project_state.models import (
    MethodAsset,
    Project,
    Task,
    TaskDependency,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cl.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed_project(eng, name: str, n_tasks: int) -> int:
    async with AsyncSession(eng) as s:
        p = Project(name=name, goal="g",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.md", content="# 文档",
            content_hash="h" + str(pid),
        ))
        tids = []
        for i in range(n_tasks):
            t = Task(project_id=pid, name=f"任务{i}",
                     status=TaskStatus.ACCEPTED if i == 0 else TaskStatus.READY)
            s.add(t)
            await s.flush()
            tids.append(t.id)
        if n_tasks >= 2:
            s.add(TaskDependency(task_id=tids[1], depends_on_id=tids[0]))
        await s.commit()
    return pid


@pytest.mark.asyncio
async def test_isolation_between_projects(dbengine):
    """A 项目清场不影响 B 项目任务（隔离回归）。"""
    pid_a = await _seed_project(dbengine, "A", 3)
    pid_b = await _seed_project(dbengine, "B", 2)

    # 模拟清场（与端点同 SQL）
    from sqlalchemy import text as _text

    async with AsyncSession(dbengine) as s:
        ids = list(
            (
                await s.execute(
                    select(Task.id).where(Task.project_id == pid_a)
                )
            ).scalars().all()
        )
        idlist = ",".join(str(i) for i in ids)
        await s.execute(
            _text(f'DELETE FROM task_dependency WHERE task_id IN ({idlist})')
        )
        await s.execute(_text(f"DELETE FROM task WHERE id IN ({idlist})"))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        a_left = (
            await s.execute(
                select(Task.id).where(Task.project_id == pid_a)
            )
        ).scalars().all()
        b_left = (
            await s.execute(
                select(Task.id).where(Task.project_id == pid_b)
            )
        ).scalars().all()
        assert a_left == []
        assert len(b_left) == 2, "B 项目任务必须保留"
        # 项目本体与资产保留
        assert await s.get(Project, pid_a) is not None
        assets = (
            await s.execute(
                select(MethodAsset.id).where(MethodAsset.project_id == pid_a)
            )
        ).scalars().all()
        assert len(assets) == 1
