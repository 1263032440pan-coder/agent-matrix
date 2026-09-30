"""WP-038 Readiness 双轴 + Gap Classification tests — Gate 四条.

Gate:
  [x] 有大量文档不等于自动判 READY（Method Readiness 按维度实质评估）
  [x] 能识别人类可理解但 AI 不可执行的流程缺口
  [x] Method / Execution Readiness 可分别为 READY / MISSING
  [x] Skill=0 本身不触发 Research（不判 MISSING、不产 CAPABILITY gap）

Extension: method.readiness.evaluators / gap.classifiers / gap.resolvers 登记
"""

import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.extension.runtime.executor import STABLE_POINTS
from project_runtime.gap.classifier import GapClassifier
from project_runtime.procedure import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    GapType,
)
from project_runtime.project_state.models import MethodAsset, Project
from project_runtime.readiness.assessor import MethodReadinessAssessor

RICH_DOC = """# 完整流程

## 阶段：需求
- 输入: 用户材料
- 活动: 需求澄清与基线确认
- 输出: 需求基线文档
- 依赖: 无
- 约束: 变更须评审
- 验收: 条目全部有验收标准
"""

# 大量文字但维度缺失: 无输入/输出/验收/约束/依赖标记
VERBOSE_THIN_DOC = """# 项目概述

本项目旨在建设一个领先行业的智能化平台。平台将深度融合前沿技术，
打造卓越的用户体验。我们将秉持精益求精的态度，持续推进项目进展。
团队将全力以赴，确保交付高质量成果。

## 背景阐述

随着数字化转型的深入，企业面临着前所未有的机遇与挑战。本项目的
构想源于对市场趋势的深刻洞察。我们相信，通过不懈努力，项目必将
取得圆满成功。

## 愿景展望

展望未来，平台将成为行业标杆，引领技术发展方向的灯塔式工程。
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rdy.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed_and_normalize(dbengine, doc: str, name="doc.md"):
    async with AsyncSession(dbengine) as s:
        p = Project(name="rdy", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path=name, content=doc,
            content_hash=hashlib.sha256(doc.encode()).hexdigest(),
        ))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id  # snapshot before commit
        await s.commit()
    return pid, mid


async def _assess(dbengine, pid, mid):
    async with AsyncSession(dbengine) as s:
        assessment = await MethodReadinessAssessor(s).assess(pid, mid)
        # snapshot everything before commit
        method = json.loads(assessment.method_readiness)
        ecr = json.loads(assessment.execution_capability_readiness)
        gaps = json.loads(assessment.gaps)
        coverage = json.loads(assessment.coverage_summary or "{}")
        aid = assessment.id
        await s.commit()
    return aid, method, ecr, gaps, coverage


# ── Gate 1: 大量文档 ≠ 自动 READY ────────────────────────────────────


@pytest.mark.asyncio
async def test_verbose_thin_doc_not_ready(dbengine):
    """文字量大但六维度缺失 → 不得判 READY（实质评估，非字数）。"""
    pid, mid = await _seed_and_normalize(dbengine, VERBOSE_THIN_DOC)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    assert coverage["overall_status"] != "READY"
    # thin sections produce nodes with missing dimensions → gaps generated
    assert gaps["method_gaps"], "thin doc must produce method gaps"
    # dimension statuses actually reflect missing fields (not READY-by-default)
    assert any(
        d["status"] in ("MISSING", "PARTIAL")
        for n in method for d in n["dimensions"].values()
    )


@pytest.mark.asyncio
async def test_rich_doc_scores_higher_than_thin(dbengine):
    """对照组: 六维度齐全的文档 coverage 显著更高。"""
    pid, mid = await _seed_and_normalize(dbengine, RICH_DOC)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    pid2, mid2 = await _seed_and_normalize(dbengine, VERBOSE_THIN_DOC)
    aid2, method2, ecr2, gaps2, coverage2 = await _assess(dbengine, pid2, mid2)

    assert (
        coverage["avg_dimension_coverage"]
        > coverage2["avg_dimension_coverage"]
    )


# ── Gate 2: 人类可理解但 AI 不可执行的缺口 ───────────────────────────


@pytest.mark.asyncio
async def test_ai_non_executable_gaps_identified(dbengine):
    """「需求澄清」人类可做但材料未定义输入/产物/验收 → EXPLICITNESS/
    PRIVATE gap 指出缺什么（而不是笼统「无法执行」）。"""
    semi_doc = """# 流程

## 阶段：评审
- 活动: 组织评审并形成结论
"""
    pid, mid = await _seed_and_normalize(dbengine, semi_doc)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    assert coverage["overall_status"] in ("PARTIAL", "MISSING")
    gap_dims = {g["dimension"] for g in gaps["method_gaps"]}
    # what's missing is NAMED: scope (输入/输出) and acceptance
    assert "scope_coverage" in gap_dims
    assert "acceptance_definition" in gap_dims
    # every gap carries a traceable source_ref
    assert all(
        g["source_ref"].startswith("procedure_node:") for g in gaps["method_gaps"]
    )


# ── Gate 3: Method / Execution 独立双轴 ──────────────────────────────


@pytest.mark.asyncio
async def test_dual_axis_independent(dbengine):
    """方法烂（thin doc）+ 能力全（无特殊要求）→ Method MISSING/PARTIAL
    且 Execution READY——双轴互不裹挟。"""
    pid, mid = await _seed_and_normalize(dbengine, VERBOSE_THIN_DOC)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    # Method axis: thin → NOT READY
    assert coverage["overall_status"] in ("PARTIAL", "MISSING", "AMBIGUOUS")
    # Execution axis: nodes declare no special capability → READY
    assert ecr["overall"] == "READY"
    assert gaps["execution_gaps"] == []


@pytest.mark.asyncio
async def test_capability_gap_axis(dbengine):
    """节点声明「浏览器」能力但运行时无 browser provider/skill →
    Execution PARTIAL + CAPABILITY gap（Gate 2 的能力侧）。"""
    doc = """# 流程

## 阶段：抓取
- 输入: 站点清单
- 活动: 使用浏览器 自动抓取
- 输出: 抓取结果
- 验收: 结果完整
"""
    pid, mid = await _seed_and_normalize(dbengine, doc)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    # browser declared but no provider registered → capability missing
    assert ecr["overall"] in ("PARTIAL", "MISSING")
    assert any(
        g["gap_type"] == "CAPABILITY" and "browser" in g["reason"]
        for g in gaps["execution_gaps"]
    )


# ── Gate 4: Skill=0 不触发 Research / 不判 MISSING ───────────────────


@pytest.mark.asyncio
async def test_zero_skill_not_missing(dbengine):
    """无任何 skill 注册绑定时，普通节点（无特殊能力声明）Execution 仍
    READY——Skill=0 不是缺口（10-规范规则 5）。"""
    pid, mid = await _seed_and_normalize(dbengine, RICH_DOC)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    # verify no skill bindings exist (Skill=0 for this project)
    async with AsyncSession(dbengine) as s:
        from project_runtime.skills.models import SkillBinding

        bindings = (
            await s.execute(
                select(SkillBinding).where(SkillBinding.project_id == pid)
            )
        ).scalars().all()
        assert len(list(bindings)) == 0

    # execution axis still READY — zero skills alone is NOT a gap
    assert ecr["overall"] == "READY"
    assert gaps["execution_gaps"] == []
    # and no CAPABILITY-type gap fabrications
    assert not any(
        g.get("gap_type") == GapType.CAPABILITY.value
        for g in gaps["method_gaps"]
    )


def test_no_research_triggered_by_zero_skills():
    """结构性断言: readiness/gap 模块无 Research 触发路径（WP-029 域）。"""
    import inspect

    import project_runtime.readiness.assessor as a_mod
    import project_runtime.readiness.execution_capability as e_mod

    for mod in (a_mod, e_mod):
        src = inspect.getsource(mod)
        for forbidden in ("ResearchManager", "web_research", "start_research",
                          "trigger_research"):
            assert forbidden not in src, (
                f"readiness must not trigger research: {forbidden}"
            )


# ── Gap 分类链: 六类型 + extension 点 ────────────────────────────────


@pytest.mark.asyncio
async def test_gap_types_rich_classification(dbengine):
    """六类型映射: PRIVATE（产物/验收私有知识）/ TECHNICAL（约束）/
    EXPLICITNESS / PROCESS——分类不再是单一 EXPLICITNESS。"""
    pid, mid = await _seed_and_normalize(dbengine, VERBOSE_THIN_DOC)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    types = {g["gap_type"] for g in gaps["method_gaps"]}
    assert "PRIVATE" in types or "TECHNICAL" in types, (
        f"classification must use the full type set, got {types}"
    )


@pytest.mark.asyncio
async def test_gap_classifier_persists(dbengine):
    """GapClassifier 从 assessment 产 MethodGap 记录（六类型枚举）。"""
    pid, mid = await _seed_and_normalize(dbengine, VERBOSE_THIN_DOC)
    aid, method, ecr, gaps, coverage = await _assess(dbengine, pid, mid)

    async with AsyncSession(dbengine) as s:
        clf = GapClassifier(s)
        mgaps = await clf.classify(aid)
        # snapshot gap types BEFORE commit (ORM expiry)
        types = [g.gap_type for g in mgaps]
        await s.commit()
        assert len(mgaps) >= 1
        for t in types:
            assert t in list(GapType)


def test_extension_points_registered():
    for pt in ("method.readiness.evaluators", "gap.classifiers",
               "gap.resolvers"):
        assert pt in STABLE_POINTS, f"{pt} missing"
    # existence, not exact count (hardcoded counts rot on every addition)


@pytest.mark.asyncio
async def test_readiness_evaluators_hook_fires(dbengine):
    """method.readiness.evaluators 在评估链上真实 fire（advisory）。"""
    from project_runtime.extension.registry import ExtensionRegistry
    from project_runtime.extension.runtime.contracts import (
        Permission,
        RuntimeExtensionKind,
    )
    from project_runtime.extension.runtime.executor import RuntimeHookExecutor
    from project_runtime.readiness.execution_capability import (
        ExecutionCapabilityReadiness,
    )

    fired: list[dict] = []

    class Probe:
        permission = Permission.ENRICH

        async def invoke(self, payload):
            fired.append(payload)
            return {"result": "ACK"}

    registry = ExtensionRegistry()
    executor = RuntimeHookExecutor(registry)
    executor.register_runtime(
        "method.readiness.evaluators", "probe",
        RuntimeExtensionKind.MIDDLEWARE, Probe(),
    )

    pid, mid = await _seed_and_normalize(dbengine, RICH_DOC)
    async with AsyncSession(dbengine) as s:
        await ExecutionCapabilityReadiness(s).assess(
            pid, mid, executor=executor
        )
    assert len(fired) == 1
    assert fired[0]["project_id"] == pid
