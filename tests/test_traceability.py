"""WP-022 Traceability Graph tests — 验收五条.

- [x] 可建立 Requirement 到 Delivery 的追踪链
- [x] 可检查未实现 Requirement
- [x] 可检查无测试 Artifact
- [x] 可检查无 Gate 的 Task
- [x] 可输出 Traceability Gap
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.checkpoint.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
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
    ArtifactType,
    AutomationMode,
    DeliveryItemStatus,
    GateStatus,
    TaskStatus,
)
from project_runtime.project_state.models import (
    Artifact,
    DeliveryContract,
    DeliveryItem,
    GateResult,
    Project,
    Task,
)
from project_runtime.traceability import (
    TraceabilityGraph,
    TraceKind,
)
from project_runtime.understanding import UnderstandingService


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/tr.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(dbengine, *, with_req=True, with_test=False,
                with_gate=False, with_delivery=False):
    """基础链: understanding（需求）→ 2 Task → 1 普通产物。"""
    async with AsyncSession(dbengine) as s:
        p = Project(name="tr", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        t1 = Task(project_id=pid, name="实现任务搜索",
                  status=TaskStatus.ACCEPTED)
        t2 = Task(project_id=pid, name="实现数据持久化",
                  status=TaskStatus.EXECUTION_DONE)
        s.add_all([t1, t2])
        await s.flush()
        t1_id, t2_id = t1.id, t2.id
        s.add(Artifact(
            logical_artifact_id="doc", project_id=pid, task_id=t1_id,
            name="搜索设计文档", version=1, path="data/d.md",
            sha256="d" * 64, artifact_type=ArtifactType.DOCUMENT,
        ))
        if with_test:
            s.add(Artifact(
                logical_artifact_id="tst", project_id=pid, task_id=t1_id,
                name="搜索测试报告", version=1, path="data/t.md",
                sha256="t" * 64, artifact_type=ArtifactType.TEST_RESULT,
            ))
        if with_gate:
            s.add(GateResult(
                task_id=t1_id, gate_name="hard-check",
                status=GateStatus.PASS, details="ok",
            ))
        if with_delivery:
            contract = DeliveryContract(project_id=pid, version=1,
                                        items=json.dumps(["搜索功能"]))
            s.add(contract)
            await s.flush()
            s.add(DeliveryItem(
                contract_id=contract.id, requirement_ref="搜索功能交付物",
                status=DeliveryItemStatus.SATISFIED,
            ))
        await s.commit()

    if with_req:
        # understanding 快照带 known_requirements（含任务名关键词——V0.1
        # 名称关联可命中）
        class _Analyzer:
            async def analyze(self, goal, assets):
                from project_runtime.understanding.analyzer import (
                    UnderstandingResult,
                )

                return UnderstandingResult(
                    goal=goal,
                    known_requirements=[
                        "系统必须支持实现任务搜索",
                        "需要实现数据持久化",
                    ],
                )

        async with AsyncSession(dbengine) as s:
            await UnderstandingService(s).analyze_project(
                pid, analyzer=_Analyzer()
            )
            await s.commit()
    return pid, t1_id, t2_id


# ── Gate 1: Requirement → Delivery 全链 ─────────────────────────────


@pytest.mark.asyncio
async def test_full_chain_requirement_to_delivery(dbengine):
    pid, t1_id, _ = await _seed(
        dbengine, with_req=True, with_test=True, with_gate=True,
        with_delivery=True,
    )
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        stats = await g.rebuild_scope(pid)
        await s.commit()

        assert stats["nodes"] > 0
        kinds = {n.kind for n in (await s.execute(
            select(__import__(
                "project_runtime.traceability.models",
                fromlist=["TraceNode"]).TraceNode).where(
                __import__(
                    "project_runtime.traceability.models",
                    fromlist=["TraceNode"]).TraceNode.project_id == pid)
        )).scalars().all()}
        assert kinds >= {TraceKind.REQUIREMENT, TraceKind.TASK,
                         TraceKind.ARTIFACT, TraceKind.TEST, TraceKind.GATE,
                         TraceKind.DELIVERY}

        # upstream of delivery reaches requirement（间接 BFS）
        dnodes = (await s.execute(
            select(__import__(
                "project_runtime.traceability.models",
                fromlist=["TraceNode"]).TraceNode).where(
                __import__(
                    "project_runtime.traceability.models",
                    fromlist=["TraceNode"]).TraceNode.kind == "DELIVERY")
        )).scalars().all()
        assert dnodes
        upstream = await g.get_upstream(dnodes[0].id)
        up_kinds = {n.kind for n in upstream}
        assert up_kinds & {TraceKind.REQUIREMENT, TraceKind.TASK}


# ── Gate 2+5: 未实现 Requirement + Gap 输出 ────────────────────────


@pytest.mark.asyncio
async def test_unimplemented_requirement_detected(dbengine):
    """需求「实现数据持久化」有 Task 关联；「实现任务搜索」也有——改用
    无 Task 的独立需求验证未实现检测。"""
    pid, _, _ = await _seed(dbengine, with_req=True)

    # 追加一条没有对应任务的需求（重新分析覆盖 requirements）
    class _Analyzer2:
        async def analyze(self, goal, assets):
            from project_runtime.understanding.analyzer import (
                UnderstandingResult,
            )

            return UnderstandingResult(
                goal=goal,
                known_requirements=[
                    "系统必须支持实现任务搜索",
                    "需要实现数据持久化",
                    "系统必须支持消息通知",  # 无对应 Task
                ],
            )

    async with AsyncSession(dbengine) as s:
        await UnderstandingService(s).analyze_project(
            pid, analyzer=_Analyzer2()
        )
        await s.commit()

    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        await g.rebuild_scope(pid)
        gaps = await g.find_gaps(pid)
        await s.commit()

    unimplemented = [
        x for x in gaps if x.gap_type == "UNIMPLEMENTED_REQUIREMENT"
    ]
    assert any("消息通知" in x.label for x in unimplemented), (
        "requirement without task must be flagged"
    )
    # implemented ones not flagged
    assert not any("任务搜索" in x.label for x in unimplemented)


# ── Gate 3: 无测试 Artifact 的 Task ─────────────────────────────────


@pytest.mark.asyncio
async def test_no_test_artifact_detected(dbengine):
    pid, t1_id, t2_id = await _seed(dbengine, with_req=True)  # no tests
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        await g.rebuild_scope(pid)
        gaps = await g.find_gaps(pid)
        await s.commit()

    no_test = [x for x in gaps if x.gap_type == "NO_TEST_ARTIFACT"]
    assert len(no_test) == 2  # both tasks lack test artifacts
    refs = {x.ref for x in no_test}
    assert f"task:{t1_id}" in refs


@pytest.mark.asyncio
async def test_test_artifact_clears_gap(dbengine):
    pid, t1_id, _ = await _seed(dbengine, with_req=True, with_test=True)
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        await g.rebuild_scope(pid)
        gaps = await g.find_gaps(pid)
        await s.commit()
    no_test_refs = {
        x.ref for x in gaps if x.gap_type == "NO_TEST_ARTIFACT"
    }
    assert f"task:{t1_id}" not in no_test_refs  # t1 has a test artifact


# ── Gate 4: 无 Gate 的 Task ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_gate_detected_and_cleared(dbengine):
    pid, t1_id, _ = await _seed(dbengine, with_req=True)  # no gates
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        await g.rebuild_scope(pid)
        gaps = await g.find_gaps(pid)
        await s.commit()
    assert any(
        x.gap_type == "NO_GATE" and x.ref == f"task:{t1_id}" for x in gaps
    )

    # 对照：有 gate 的项目
    pid2, t1b, _ = await _seed(dbengine, with_req=True, with_gate=True)
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        await g.rebuild_scope(pid2)
        gaps2 = await g.find_gaps(pid2)
        await s.commit()
    assert not any(
        x.gap_type == "NO_GATE" and x.ref == f"task:{t1b}" for x in gaps2
    )


# ── coverage 报告 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_coverage_report(dbengine):
    pid, t1_id, _ = await _seed(
        dbengine, with_req=True, with_test=True, with_gate=True
    )
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        await g.rebuild_scope(pid)
        cov = await g.get_coverage(pid)
        await s.commit()

    d = cov.to_dict()
    assert d["requirements"] == 2
    assert d["requirements_implemented"] == 2  # both matched by name
    assert d["tasks"] == 2
    assert d["tasks_with_tests"] == 1
    assert d["tasks_with_gate"] == 1
    assert 0 < d["requirement_coverage"] <= 1


# ── rebuild 幂等（重复重建不堆积）─────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_idempotent(dbengine):
    pid, _, _ = await _seed(dbengine, with_req=True)
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        s1 = await g.rebuild_scope(pid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        g = TraceabilityGraph(s)
        s2 = await g.rebuild_scope(pid)
        await s.commit()
    assert s1["nodes"] == s2["nodes"]
    assert s1["edges"] == s2["edges"]
