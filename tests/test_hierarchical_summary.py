"""WP-021 Hierarchical Summary tests — acceptance checklist.

Acceptance:
  [x] Summary 有来源引用
  [x] Source 变化会使 Summary STALE
  [x] 只更新受影响 Scope
  [x] Summary 不作为事实源
Plus: WP-020 get_summary_memory 接线 + version 水位。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
from project_runtime.memory_summary import (
    SummaryGenerator,
    SummaryScope,
    SummaryService,
    SummaryStatus,
)
from project_runtime.project_state.base import Base


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/summary.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(dbengine, pid: int = 1):
    from project_runtime.project_state.enums import AutomationMode, TaskStatus
    from project_runtime.project_state.models import Artifact, Project, Task

    async with AsyncSession(dbengine) as s:
        p = Project(name="sum-test", automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        t1 = Task(project_id=pid, name="task-A", status=TaskStatus.ACCEPTED)
        t2 = Task(project_id=pid, name="task-B", status=TaskStatus.READY)
        s.add_all([t1, t2])
        await s.flush()
        t1_id, t2_id = t1.id, t2.id
        s.add(Artifact(
            logical_artifact_id="art-a", project_id=pid, task_id=t1_id,
            name="a.md", version=1, path="data/a.md", sha256="h1",
        ))
        await s.commit()
        return t1_id, t2_id


# ── §1 Summary 有来源引用 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_has_source_refs(dbengine):
    t1_id, _ = await _seed(dbengine)
    import json

    async with AsyncSession(dbengine) as s:
        gen = SummaryGenerator()
        proj = await gen.generate_project_summary(s, 1, source_version=10, engine=dbengine)
        task = await gen.generate_task_summary(s, 1, t1_id, source_version=10, engine=dbengine)
        # snapshots BEFORE commit (ORM expiry)
        proj_refs = (proj.task_refs_json, proj.artifact_refs_json, proj.source_version)
        task_refs = (task.task_refs_json, task.artifact_refs_json)
        await s.commit()

        # PROJECT summary references its tasks and artifacts
        assert json.loads(proj_refs[0]) == [f"task:{t1_id}", f"task:{_}"]
        assert len(json.loads(proj_refs[1])) == 1
        assert proj_refs[2] == 10

        # TASK summary references the task and its artifacts
        assert json.loads(task_refs[0]) == [f"task:{t1_id}"]
        assert len(json.loads(task_refs[1])) == 1


# ── §2 Source 变化会使 Summary STALE ──────────────────────────────────


@pytest.mark.asyncio
async def test_source_change_marks_stale(dbengine):
    t1_id, t2_id = await _seed(dbengine)
    async with AsyncSession(dbengine) as s:
        gen = SummaryGenerator()
        await gen.generate_project_summary(s, 1, source_version=10, engine=dbengine)
        await gen.generate_task_summary(s, 1, t1_id, source_version=10, engine=dbengine)
        await gen.generate_task_summary(s, 1, t2_id, source_version=10, engine=dbengine)
        await s.commit()

    # Artifact update / task state change on t1 → its summary + ancestors STALE
    async with AsyncSession(dbengine) as s:
        svc = SummaryService(s)
        n = await svc.mark_stale(1, task_id=t1_id, reason="artifact update")
        await s.commit()
        assert n == 2  # task:t1 + PROJECT ancestor; t2 summary untouched

        stale = {r.scope_key or r.scope_type for r in await svc.stale_scopes(1)}
        assert "task:%d" % t1_id in stale
        assert "PROJECT" in stale
        assert "task:%d" % t2_id not in stale

    # Refresh restores FRESH with the advanced source version
    async with AsyncSession(dbengine) as s:
        gen = SummaryGenerator()
        await gen.generate_task_summary(s, 1, t1_id, source_version=11, engine=dbengine)
        await s.commit()
        svc = SummaryService(s)
        row = await svc.get_scope(1, SummaryScope.TASK, f"task:{t1_id}")
        assert row.status == SummaryStatus.FRESH.value
        assert row.source_version == 11


# ── §3 只更新受影响 Scope ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_only_affected_scope(dbengine):
    t1_id, t2_id = await _seed(dbengine)
    async with AsyncSession(dbengine) as s:
        gen = SummaryGenerator()
        await gen.generate_project_summary(s, 1, source_version=10, engine=dbengine)
        await gen.generate_task_summary(s, 1, t1_id, source_version=10, engine=dbengine)
        await gen.generate_task_summary(s, 1, t2_id, source_version=10, engine=dbengine)
        await s.commit()

        svc = SummaryService(s)
        t2_row = await svc.get_scope(1, SummaryScope.TASK, f"task:{t2_id}")
        t2_summary_before = t2_row.summary

    # t1 source change → refresh ONLY t1 + project; t2 must stay untouched
    async with AsyncSession(dbengine) as s:
        svc = SummaryService(s)
        await svc.mark_stale(1, task_id=t1_id)
        gen = SummaryGenerator()
        await gen.generate_task_summary(s, 1, t1_id, source_version=12, engine=dbengine)
        await gen.generate_project_summary(s, 1, source_version=12, engine=dbengine)
        await s.commit()

        t2_row2 = await svc.get_scope(1, SummaryScope.TASK, f"task:{t2_id}")
        assert t2_row2.summary == t2_summary_before
        assert t2_row2.status == SummaryStatus.FRESH.value  # never went stale
        assert t2_row2.source_version == 10  # unchanged


# ── §4 Summary 不作为事实源 ───────────────────────────────────────────


def test_summary_module_never_writes_state():
    """结构性断言: summary 模块不 import 任何状态写入路径。"""
    import inspect

    import project_runtime.memory_summary.service as svc_mod

    src = inspect.getsource(svc_mod)
    forbidden = [
        "transition_task",      # FSM 写
        "StateTransitionService",
        "fsm.transition",
        "update(Project",       # 状态 UPDATE
        "add(Project",          # 状态 INSERT
        "session.delete(",      # 状态 DELETE
    ]
    for f in forbidden:
        assert f not in src, f"summary module must not write state: {f!r}"
    # 唯一写路径是自己的 ProjectSummary 表
    assert "ProjectSummary(" in src


# ── WP-020 接线：get_summary_memory 现在返回真实数据 ──────────────────


@pytest.mark.asyncio
async def test_wp20_summary_memory_wiring(dbengine):
    t1_id, _ = await _seed(dbengine)
    async with AsyncSession(dbengine) as s:
        await SummaryGenerator().generate_project_summary(s, 1, source_version=5, engine=dbengine)
        await s.commit()

    from project_runtime.memory.manager import ProjectMemoryManager

    mgr = ProjectMemoryManager(engine=dbengine)
    summaries = await mgr.get_summary_memory(1)
    assert len(summaries) == 1
    assert summaries[0]["scope_type"] == "PROJECT"
    assert "sum-test" in summaries[0]["summary"]
