"""WP-019 E2E 场景验收 — 12 场景（A-L）覆盖映射 + 四块缺口集成.

已有测试底座（场景 → 测试文件映射）:
  A 零 Skill 启动      → test_e2e（主链）/ test_project_understanding
  B Capability Plugin  → test_capability_extension + 本文件场景 B 集成
  C Runtime Validator  → test_runtime_extension::validator_*
  D Runtime Hook       → test_runtime_extension::hook/runner_fires/before_pause
  E Observer           → test_runtime_extension::observer_cannot_block
  F 多 Extension 顺序  → test_pipeline_engine（priority/before/after/cycle）
  G 插件崩溃           → test_plugin_manager::crash / capability::crashed
  H 二态自动化         → 本文件场景 H 对照（scheduler/execution 散测收拢）
  I 低资源 Multi-Agent → 本文件场景 I（LOW profile 串行）
  J 三档资料完整度     → test_readiness_dual_axis（verbose-thin/丰富对照）
                          + 本文件 J3 zero-method
  K 架构演进           → 结构性（method 六扩展点/Protocol 换入——既有断言）
  L Skill Package      → test_e2e::skill_branch/version_pin + test_skill_*

本文件补四块: 场景 B 端到端 / 场景 H 二态对照 / 场景 I LOW 串行 /
Demo Final（含 checkpoint 段——WP017-TD02 部分兑付）。
"""

import hashlib
import json

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
from project_runtime.extension.registry import ExtensionRegistry
from project_runtime.extension.runtime.contracts import (
    Permission,
)
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    ArtifactType,
    AssetType,
    AutomationMode,
    MethodAssetAuthority,
    TaskStatus,
)
from project_runtime.project_state.models import (
    MethodAsset,
    Project,
    Task,
    TaskDependency,
)
from project_runtime.resource_governor import (
    ResourceDecision,
    ResourceProfile,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/e19.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


DEFAULT_DOC = (
    "# 流程\n\n## 阶段：需求\n- 输入: A\n- 活动: B\n- 输出: C\n- 验收: D\n"
)


async def _seed_project(dbengine, n_tasks=3, mode=AutomationMode.FULL_AUTO,
                        doc=DEFAULT_DOC):
    async with AsyncSession(dbengine) as s:
        p = Project(name="e19", goal="建设系统", automation_mode=mode)
        s.add(p)
        await s.flush()
        pid = p.id
        if doc:
            s.add(MethodAsset(
                project_id=pid, asset_type=AssetType.PROCESS,
                source_path="m.md", content=doc,
                content_hash=hashlib.sha256(doc.encode()).hexdigest(),
                authority=MethodAssetAuthority.MANDATORY,
            ))
        await s.commit()
    return pid


# ── 场景 B: Plugin → retrieval.providers → Retrieval 参与 ──────────


@pytest.mark.asyncio
async def test_scenario_b_plugin_participates_retrieval(dbengine):
    """插件 Provider 经统一 registry 参与 Memory retrieve；禁用后退回
    Core FTS5（无 provider 时空结果——Core 不改）。"""
    pid = await _seed_project(dbengine)

    # 注册一个 stub retrieval provider（capability 层）
    from project_runtime.extension.registry import CapabilityType
    from project_runtime.retrieval.providers import RetrievalOrchestrator

    class StubSemanticProvider:
        name = "stub-semantic"
        capability = CapabilityType.RETRIEVAL
        permission = Permission.READ_ONLY
        priority = 50
        enabled = True

        async def invoke(self, request):
            # Provider contract: 返回 list[dict]（orchestrator 合并约定）
            return [
                {"source": "semantic", "source_id": "x1",
                 "content": "语义命中: 需求阶段", "score": 0.9}
            ]

    registry = ExtensionRegistry()
    registry.register(StubSemanticProvider())

    async with AsyncSession(dbengine) as s:
        # 有 provider: 参与检索（stub 命中）
        orch = RetrievalOrchestrator(s, registry)
        result = await orch.search("需求", project_id=pid, limit=5)
        assert result["total"] >= 1
        assert any("语义命中" in str(r.get("content", "")) for r in result["results"])

        # 禁用 → 不参与（Core FTS5 无索引时空）
        registry.set_enabled("stub-semantic", False)
        result2 = await orch.search("需求", project_id=pid, limit=5)
        assert not any(
            "语义命中" in str(r.get("content", "")) for r in result2["results"]
        )
        # 再启用 → 恢复
        registry.set_enabled("stub-semantic", True)
        result3 = await orch.search("需求", project_id=pid, limit=5)
        assert any("语义命中" in str(r.get("content", "")) for r in result3["results"])

    # Core 代码零改动（结构性: Memory 层只经 registry）
    import inspect

    import project_runtime.memory.manager as mm

    assert "registry" in inspect.getsource(mm)


# ── 场景 H: 二态自动化对照 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_h_dual_mode_contract(dbengine):
    """FULL_AUTO: READY 即调度；MANUAL: ManualAction 等确认（未确认不
    Dispatch）；API 拒绝第三模式。"""
    from project_runtime.project_state.enums import ManualActionStatus
    from project_runtime.project_state.models import ManualAction
    from project_runtime.scheduler.scheduler import Scheduler
    from project_runtime.worker.adapter import WorkerAdapter, WorkPackage

    class NoWorker(WorkerAdapter):
        dispatched: list[int] = []  # class-level ok for test

        async def assign(self, wp: WorkPackage) -> None:
            NoWorker.dispatched.append(wp.task_id)

        async def cancel(self, task_id: int) -> None:
            pass

        async def health(self) -> str:
            return "ok"

    # FULL_AUTO
    pid = await _seed_project(dbengine, mode=AutomationMode.FULL_AUTO)
    async with AsyncSession(dbengine) as s:
        s.add(Task(project_id=pid, name="T1", status=TaskStatus.READY))
        await s.commit()
    NoWorker.dispatched = []
    async with AsyncSession(dbengine) as s:
        sched = Scheduler(s, worker=NoWorker())
        decisions = await sched.dispatch_once(pid)
        await s.commit()
    assert any(d.action == "DISPATCHED" for d in decisions)

    # MANUAL
    pid2 = await _seed_project(dbengine, mode=AutomationMode.MANUAL)
    async with AsyncSession(dbengine) as s:
        s.add(Task(project_id=pid2, name="T2", status=TaskStatus.READY))
        await s.commit()
    NoWorker.dispatched = []
    async with AsyncSession(dbengine) as s:
        sched = Scheduler(s, worker=NoWorker())
        decisions2 = await sched.dispatch_once(pid2)
        await s.commit()
        actions = (await s.execute(
            select(ManualAction).where(
                ManualAction.project_id == pid2))).scalars().all()
    # ManualAction created, nothing dispatched
    assert actions and actions[0].status == ManualActionStatus.PENDING
    assert not any(d.action == "DISPATCHED" for d in decisions2)
    assert NoWorker.dispatched == []

    # Schema: API 拒绝第三模式（create_project 400 路径）
    import inspect as _i

    from apps.runtime.api.projects import create_project

    src = _i.getsource(create_project)
    assert "FULL_AUTO or MANUAL" in src or "must be FULL_AUTO or MANUAL" in src


# ── 场景 I: LOW 资源 8GB Multi-Agent ────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_i_low_profile_serializes(dbengine):
    """A→B/C/D 扇出图: LOW(1) 下 B/C/D 依次执行（同一时刻 ≤1 Worker）、
    无 ManualAction、Task Graph 不被改写、DEFER 语义可观察。"""
    pid = await _seed_project(dbengine, mode=AutomationMode.FULL_AUTO)
    async with AsyncSession(dbengine) as s:
        a = Task(project_id=pid, name="A", status=TaskStatus.RUNNING)
        b = Task(project_id=pid, name="B", status=TaskStatus.READY)
        c = Task(project_id=pid, name="C", status=TaskStatus.READY)
        d = Task(project_id=pid, name="D", status=TaskStatus.READY)
        s.add_all([a, b, c, d])
        await s.flush()
        a_id, b_id = a.id, b.id
        c_id, d_id = c.id, d.id
        # A → B/C/D 扇出（HARD）
        for dep_on, t in ((a_id, b_id), (a_id, c_id), (a_id, d_id)):
            s.add(TaskDependency(task_id=t, depends_on_id=dep_on))
        await s.commit()

    # LOW profile + 1 RUNNING（A 在跑）→ B/C/D 全 DEFER
    async with AsyncSession(dbengine) as s:
        from project_runtime.resource_governor import ResourceGovernor

        gov = ResourceGovernor(s, profile=ResourceProfile.LOW)
        decisions = [await gov.evaluate(pid) for _ in range(3)]
    assert all(d is ResourceDecision.DEFER_RESOURCE for d in decisions)

    # 释放（A 完成）→ 下一个可跑（依次执行语义）
    async with AsyncSession(dbengine) as s:
        a_row = await s.get(Task, a_id)
        a_row.status = TaskStatus.ACCEPTED
        await s.commit()
    async with AsyncSession(dbengine) as s:
        from project_runtime.resource_governor import ResourceGovernor

        gov = ResourceGovernor(s, profile=ResourceProfile.LOW)
        first = await gov.evaluate(pid)
    assert first is ResourceDecision.ALLOW_DISPATCH  # exactly one slot

    # Task Graph 未被改写成假依赖（B/C/D 间零依赖边）
    async with AsyncSession(dbengine) as s:
        edges = (await s.execute(
            select(TaskDependency).where(
                TaskDependency.task_id.in_([b_id, c_id, d_id])
            ))).scalars().all()
        assert all(e.depends_on_id == a_id for e in edges)

    # 无 ManualAction（FULL_AUTO + LOW 不产生人工节点）
    from project_runtime.project_state.models import ManualAction

    async with AsyncSession(dbengine) as s:
        acts = (await s.execute(
            select(ManualAction).where(
                ManualAction.project_id == pid))).scalars().all()
    assert acts == []


# ── 场景 J3: Zero-Method Fallback ───────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_j3_zero_method(dbengine):
    """零方法资产 → knowledge gap 指名 + 仍可走 understanding→plan。"""
    pid = await _seed_project(dbengine, doc=None)  # zero method assets

    from project_runtime.understanding import UnderstandingService

    async with AsyncSession(dbengine) as s:
        row = await UnderstandingService(s).analyze_project(pid)
        gaps = json.loads(row.knowledge_gaps_json or "[]")
        await s.commit()
    assert any("无任何项目材料" in g for g in gaps)


# ── Demo Final E2E: 全链 + checkpoint 段（WP017-TD02 兑付）──────────


@pytest.mark.asyncio
async def test_demo_final_full_chain_with_checkpoint(dbengine, tmp_path):
    """Method Asset → … → ACCEPTED → Checkpoint 快照/恢复 → 断点续跑语义
    （执行中途快照 → 恢复 → 任务状态对比）。"""
    from project_runtime.artifact.manager import ArtifactManager
    from project_runtime.checkpoint import CheckpointManager
    from project_runtime.planner.planner import DynamicPlanner
    from project_runtime.playbook.builder import PlaybookBuilder
    from project_runtime.procedure import ProcedureNormalizer
    from project_runtime.readiness.assessor import MethodReadinessAssessor
    from project_runtime.reviewer.gate import GateCore
    from project_runtime.understanding import UnderstandingService

    doc = """# 项目流程

## 阶段：实现
- 输入: 需求
- 活动: 编码实现
- 输出: 代码
- 依赖: 无
- 约束: 轻量
- 验收: 通过测试
"""
    pid = await _seed_project(dbengine, doc=doc)

    # 认知链
    async with AsyncSession(dbengine) as s:
        await UnderstandingService(s).analyze_project(pid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id
        await s.commit()
    async with AsyncSession(dbengine) as s:
        await MethodReadinessAssessor(s).assess(pid, mid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        pb = await PlaybookBuilder(s).build(pid)
        pb_id = pb.id
        await s.commit()

    # 规划
    async with AsyncSession(dbengine) as s:
        pb = await s.get(
            __import__("project_runtime.project_state.models",
                       fromlist=["ProjectPlaybook"]).ProjectPlaybook, pb_id)
        tasks = await DynamicPlanner(s).plan(pb)
        task_ids = [t.id for t in tasks]
        await s.commit()

    # 执行到 EXECUTION_DONE
    async with AsyncSession(dbengine) as s:
        t = await s.get(Task, task_ids[0])
        t.status = TaskStatus.EXECUTION_DONE
        await s.commit()

    # ★ 中途 checkpoint（WP017-TD02: 执行中途快照→恢复→对比）
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        rec = await mgr.create(pid, trigger="AUTO_MILESTONE")
        cp_id = rec.id
        await s.commit()

    # 恢复 → 新副本保留中途状态
    async with AsyncSession(dbengine) as s:
        mgr = CheckpointManager(s, root=tmp_path / "cps")
        new_pid = await mgr.restore(cp_id)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        orig = (await s.execute(
            select(Task).where(Task.project_id == pid))).scalars().all()
        restored = (await s.execute(
            select(Task).where(Task.project_id == new_pid))).scalars().all()
        assert len(orig) == len(restored)
        assert {t.status for t in restored} == {t.status for t in orig}

    # 原项目继续走 Gate → ACCEPTED
    async with AsyncSession(dbengine) as s:
        artifact_mgr = ArtifactManager(
            s, workspace_root=f"data/tasks/{pid}")
        await artifact_mgr.create_artifact(
            project_id=pid, task_id=task_ids[0],
            logical_id=f"out-{task_ids[0]}",
            name="实现产物.md", content="# 实现\n完成",
            artifact_type=ArtifactType.DOCUMENT,
        )
        await s.commit()
    async with AsyncSession(dbengine) as s:
        gate = GateCore(s)
        result = await gate.evaluate(task_ids[0])
        gate_status = result.status.value
        await s.commit()
    assert gate_status == "PASS"
    async with AsyncSession(dbengine) as s:
        t = await s.get(Task, task_ids[0])
        assert t.status == TaskStatus.ACCEPTED
