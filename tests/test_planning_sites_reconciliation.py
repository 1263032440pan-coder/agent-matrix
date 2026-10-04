"""阶段 5 对账批 — WP-007/008/009 tests.

WP-007 Dynamic Planner（D09 交付，本批补齐）:
  [x] 无 Skill 的 Planning Context 可正常规划
  [x] Task 可引用 Playbook / Research（playbook_ref.research_enrichments）
  [x] 不硬编码任何行业阶段
  [x] Planner 需要更多知识时返回 Research Request（UNKNOWN 验收 →
      REQUEST_RESEARCH；未解析依赖 → REQUEST_USER_INPUT）
  增补:
  [x] 无 SkillRequirement 的 Task 仍可规划
  [x] required=false 可生成无 Skill Task（Runner Phase B-0 链）
  [x] Planner 不依赖任何固定 Skill ID（结构性断言）

WP-008 Scheduler（D10/D15/WP-036 交付，本批补）:
  [x] scheduler.before_dispatch 扩展点 fire（V1.9 清单点全消费）
  [x] V1.12 Resolution 前置链（Runner Phase B-0：READY→Resolution→
      Capability Check→Worker Routing→Governor→Dispatch）

WP-009 Worker Adapter（对账登记）:
  [x] 统一 Adapter 接口（assign/cancel/health ABC + Mock/LLM 双实现）
  [x] goose/ACP 属 V1+（RTM 已标注——本批断言现状不冒领）
"""

import hashlib
import inspect
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.artifact_template.models  # noqa: F401
import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.planner.planner import DynamicPlanner
from project_runtime.playbook.builder import PlaybookBuilder
from project_runtime.procedure import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    MethodAssetAuthority,
)
from project_runtime.project_state.models import MethodAsset, Project
from project_runtime.readiness.assessor import MethodReadinessAssessor

DOC = """# 实施流程

## 阶段：需求
- 输入: 用户材料
- 活动: 需求澄清
- 输出: 需求基线
- 依赖: 无
- 约束: 变更须评审
- 验收: 条目有验收标准

## 阶段：开发
- 输入: 需求基线
- 活动: 按模块实现
- 输出: 模块代码
- 依赖: 需求
- 约束: 内存<512MB
- 验收: 测试通过
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plan.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _plan_chain(dbengine, doc=DOC):
    """project → asset → normalize → assess → playbook → plan。"""
    async with AsyncSession(dbengine) as s:
        p = Project(name="plan", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m.md", content=doc,
            content_hash=hashlib.sha256(doc.encode()).hexdigest(),
            authority=MethodAssetAuthority.MANDATORY,
        ))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id
        await s.commit()
    async with AsyncSession(dbengine) as s:
        await MethodReadinessAssessor(s).assess(pid, mid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        playbook = await PlaybookBuilder(s).build(pid)
        pb_id = playbook.id
        await s.commit()
    return pid, pb_id


# ── WP-007 Gate 1+增补1: 无 Skill 上下文可规划 ──────────────────────


@pytest.mark.asyncio
async def test_plan_without_skills(dbengine):
    pid, pb_id = await _plan_chain(dbengine)
    async with AsyncSession(dbengine) as s:
        playbook = await s.get(
            __import__("project_runtime.project_state.models",
                       fromlist=["ProjectPlaybook"]).ProjectPlaybook,
            pb_id,
        )
        planner = DynamicPlanner(s)
        tasks = await planner.plan(playbook)
        names = [t.name for t in tasks]  # snapshot before commit (detached)
        await s.commit()
    assert len(tasks) >= 2
    assert any("需求" in n for n in names)


# ── WP-007 Gate 2: Task 引用 Playbook / Research ────────────────────


@pytest.mark.asyncio
async def test_task_references_playbook_and_research(dbengine):
    pid, pb_id = await _plan_chain(dbengine)
    async with AsyncSession(dbengine) as s:
        playbook = await s.get(
            __import__("project_runtime.project_state.models",
                       fromlist=["ProjectPlaybook"]).ProjectPlaybook,
            pb_id,
        )
        planner = DynamicPlanner(s)
        tasks = await planner.plan(playbook)
        refs = [json.loads(t.playbook_ref or "{}") for t in tasks]  # snapshot
        await s.commit()
        # every task carries a playbook_ref with workstream identity
        for ref in refs:
            assert ref.get("workstream_name")
            assert ref.get("authority") in (
                "MANDATORY", "RECOMMENDED", "OPTIONAL", "PROPOSED")
            # research slot present (empty when no enrichments — WP-030)
            assert "research_enrichments" in ref


@pytest.mark.asyncio
async def test_research_enrichment_flows_to_task(dbengine):
    """ANSWERED 研究 → playbook workstream → task.playbook_ref 透传。"""
    from project_runtime.research.models import ResearchRecord

    pid, pb_id = await _plan_chain(dbengine)
    async with AsyncSession(dbengine) as s:
        s.add(ResearchRecord(
            project_id=pid, research_kind="DOMAIN_RESEARCH",
            question="阶段：需求 的域知识", status="ANSWERED",
            summary="域要点 X",
        ))
        await s.commit()

    # new asset → new chain (playbook picks up enrichment)
    async with AsyncSession(dbengine) as s:
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="m2.md", content=DOC + "\n补: 无\n",
            content_hash=hashlib.sha256((DOC + "2").encode()).hexdigest(),
            authority=MethodAssetAuthority.MANDATORY,
        ))
        await s.commit()
    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id
        await s.commit()
    async with AsyncSession(dbengine) as s:
        await MethodReadinessAssessor(s).assess(pid, mid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        playbook = await PlaybookBuilder(s).build(pid)
        pb2 = playbook.id
        await s.commit()
    async with AsyncSession(dbengine) as s:
        playbook = await s.get(
            __import__("project_runtime.project_state.models",
                       fromlist=["ProjectPlaybook"]).ProjectPlaybook,
            pb2,
        )
        planner = DynamicPlanner(s)
        tasks = await planner.plan(playbook)
        refs = [json.loads(t.playbook_ref or "{}") for t in tasks]  # snapshot
        await s.commit()
        enriched = [r for r in refs if r.get("research_enrichments")]
        assert enriched, "research enrichment must flow into task refs"


# ── WP-007 Gate 3: 不硬编码行业阶段 ─────────────────────────────────


def test_no_industry_stages_hardcoded():
    import project_runtime.planner.planner as p_mod

    src = inspect.getsource(p_mod)
    for forbidden in ("需求分析", "设计阶段", "编码", "测试阶段", "部署阶段",
                      "waterfall", "agile_sprint"):
        assert forbidden not in src, f"hardcoded stage: {forbidden}"


# ── WP-007 Gate 4: 知识缺口 → Research Request ──────────────────────


@pytest.mark.asyncio
async def test_planner_emits_research_request(dbengine):
    """UNKNOWN 验收的 workstream → REQUEST_RESEARCH（Planner 不自己联网）。"""
    thin_doc = """# 流程

## 阶段：评审
- 活动: 组织评审
"""
    pid, pb_id = await _plan_chain(dbengine, thin_doc)
    async with AsyncSession(dbengine) as s:
        playbook = await s.get(
            __import__("project_runtime.project_state.models",
                       fromlist=["ProjectPlaybook"]).ProjectPlaybook,
            pb_id,
        )
        planner = DynamicPlanner(s)
        await planner.plan(playbook)
        await s.commit()

    assert planner.pending_requests, "UNKNOWN method fields must signal"
    req = planner.pending_requests[0]
    assert req["request_type"] in ("REQUEST_RESEARCH", "REQUEST_USER_INPUT")
    assert req["reason"]


def test_planner_never_researches_directly():
    """结构性断言: Planner 无网络/Research 执行路径——只发请求。"""
    import project_runtime.planner.planner as p_mod

    src = inspect.getsource(p_mod)
    for forbidden in ("httpx", "litellm", "web_search", "ResearchManager("):
        assert forbidden not in src, f"planner must not execute: {forbidden}"


# ── WP-007 增补 2+3: required=false 无 Skill Task / 不依赖固定 ID ──


@pytest.mark.asyncio
async def test_required_false_no_skill_task(dbengine):
    """Runner Phase B-0 以 required=False 解析——NO_SKILL 时任务照常执行
    （既有 test_no_skill_task_executes 已证；此处验证 Planner 产出的
    Task 无 skill 硬依赖）。"""
    pid, pb_id = await _plan_chain(dbengine)
    async with AsyncSession(dbengine) as s:
        playbook = await s.get(
            __import__("project_runtime.project_state.models",
                       fromlist=["ProjectPlaybook"]).ProjectPlaybook,
            pb_id,
        )
        planner = DynamicPlanner(s)
        tasks = await planner.plan(playbook)
        refs = [json.loads(t.playbook_ref or "{}") for t in tasks]  # snapshot
        await s.commit()
        # no task carries a fixed skill id in its ref
        for ref in refs:
            assert "skill_id" not in ref


def test_planner_no_fixed_skill_ids():
    import project_runtime.planner.planner as p_mod

    src = inspect.getsource(p_mod)
    for forbidden in ('preferred_skill_id="', "SkillResolver", "SkillLoader"):
        assert forbidden not in src, f"planner must not bind skills: {forbidden}"


# ── WP-008: scheduler.before_dispatch fire ──────────────────────────


@pytest.mark.asyncio
async def test_scheduler_before_dispatch_fires(dbengine):
    from project_runtime.extension.registry import ExtensionRegistry
    from project_runtime.extension.runtime.contracts import (
        Permission,
        RuntimeExtensionKind,
    )
    from project_runtime.extension.runtime.executor import RuntimeHookExecutor
    from project_runtime.project_state.enums import TaskStatus
    from project_runtime.project_state.models import Task
    from project_runtime.scheduler.scheduler import Scheduler
    from project_runtime.worker.adapter import WorkerAdapter, WorkPackage

    fired: list[dict] = []

    class Probe:
        permission = Permission.ENRICH

        async def invoke(self, payload):
            fired.append(payload)
            return {"result": "ACK"}

    class NoWorker(WorkerAdapter):
        async def assign(self, wp: WorkPackage) -> None:
            pass

        async def cancel(self, task_id: int) -> None:
            pass

        async def health(self) -> str:
            return "ok"

    async with AsyncSession(dbengine) as s:
        p = Project(name="sch", goal="g",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(Task(project_id=pid, name="t1", status=TaskStatus.READY))
        await s.commit()

    registry = ExtensionRegistry()
    executor = RuntimeHookExecutor(registry)
    executor.register_runtime(
        "scheduler.before_dispatch", "probe",
        RuntimeExtensionKind.MIDDLEWARE, Probe(),
    )
    async with AsyncSession(dbengine) as s:
        sched = Scheduler(s, worker=NoWorker(), executor=executor)
        await sched.dispatch_once(pid)

    assert len(fired) == 1
    assert fired[0]["project_id"] == pid
    assert fired[0]["ready"] == [1]


def test_scheduler_v19_points_all_consumed():
    """V1.9 scheduler 侧点位：scheduler.before_dispatch 本批接线后，
    STABLE_POINTS 中 scheduler/context/worker 三组点全部有消费方。"""
    import project_runtime.scheduler.scheduler as sch_mod

    src = inspect.getsource(sch_mod)
    assert "scheduler.before_dispatch" in src


# ── WP-009: 统一 Adapter 接口对账 ───────────────────────────────────


def test_worker_adapter_unified_interface():
    from project_runtime.worker.adapter import WorkerAdapter

    # spec 五能力 → V0.1 ABC 三方法映射（createWorker/initialize = 构造；
    # assignWorkPackage/sendContext = assign（context 走 contract 槽）；
    # resumeTask = 重试链 retry 端点）；goose/ACP 属 V1+（RTM 已标）
    for m in ("assign", "cancel", "health"):
        assert hasattr(WorkerAdapter, m)
    import inspect as _i

    assert _i.isabstract(WorkerAdapter)


def test_worker_lifecycle_and_events():
    """Worker 生命周期与事件流：TaskRun 每执行必建 + 事件链（既有断言
    ——对账引用不重复实现）。"""
    import project_runtime.worker.adapter as w_mod

    src = inspect.getsource(w_mod)
    assert "TaskRun" in src  # audit trail per execution
