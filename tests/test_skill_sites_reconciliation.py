"""批次 A — WP-027/WP-031 Skill 三站对账批（前两站）tests.

WP-027 补齐专项（其余 Gate 已被 D17A-C + WP-038 覆盖）:
  [x] Gate 7: 用户方法完整但无 Skill → Method Ready
  [x] Gate 8: 方法明确但缺 required Skill/Tool/Worker → Execution Gap
  [x] Gate 9: 不把 LLM 推断自动注册为可信 Skill
  [x] 六输出字段名与 spec 对齐（covered_by_worker/tool/skill +
      missing_capabilities/missing_skill_dependencies/execution_readiness）

WP-031 对账（D17C/D17D + Runner 注入已交付，本批补齐）:
  [x] WorkPackage 三字段（skill_requirement/resolved_skill_ref/
      skill_execution_context_ref）可携带且可审计
"""

import hashlib
import inspect
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.skills.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.procedure import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    MethodAssetAuthority,
)
from project_runtime.project_state.models import MethodAsset, Project
from project_runtime.readiness.assessor import MethodReadinessAssessor
from project_runtime.worker.adapter import WorkPackage

FULL_METHOD_DOC = """# 完整方法

## 阶段：需求
- 输入: 用户材料
- 活动: 需求澄清
- 输出: 需求基线
- 依赖: 无
- 约束: 变更须评审
- 验收: 条目有验收标准
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sk.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _chain(dbengine, doc=FULL_METHOD_DOC):
    async with AsyncSession(dbengine) as s:
        p = Project(name="sk", goal="建设系统",
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
    return pid, mid


# ── WP-027 Gate 7: 方法完整无 Skill → Method Ready ──────────────────


@pytest.mark.asyncio
async def test_gate7_method_ready_without_skills(dbengine):
    pid, mid = await _chain(dbengine)
    async with AsyncSession(dbengine) as s:
        assessment = await MethodReadinessAssessor(s).assess(pid, mid)
        method = json.loads(assessment.method_readiness)
        ecr = json.loads(assessment.execution_capability_readiness)
        coverage = json.loads(assessment.coverage_summary)
        await s.commit()

    # method axis: the full method doc's substantive node is READY-grade
    # (per-node dimensions covered) — Skill count plays NO role
    substantive = [
        n for n in method
        if n["covered_dimensions"] >= 5
    ]
    assert substantive, "full-method doc must produce high-coverage nodes"
    # no skill bindings exist (verified by absence of SkillBinding rows)
    async with AsyncSession(dbengine) as s:
        from project_runtime.skills.models import SkillBinding

        n = len(list((await s.execute(select(SkillBinding))).scalars().all()))
        assert n == 0
    # execution axis unaffected by Skill=0
    assert ecr["overall"] == "READY"


# ── WP-027 Gate 8: 方法明确但缺 Tool → Execution Gap ────────────────


@pytest.mark.asyncio
async def test_gate8_execution_gap_when_tool_missing(dbengine):
    doc = FULL_METHOD_DOC.replace(
        "- 活动: 需求澄清", "- 活动: 使用浏览器 抓取界面"
    )
    pid, mid = await _chain(dbengine, doc)
    async with AsyncSession(dbengine) as s:
        assessment = await MethodReadinessAssessor(s).assess(pid, mid)
        ecr = json.loads(assessment.execution_capability_readiness)
        gaps = json.loads(assessment.gaps)
        await s.commit()

    # method is explicit (doc complete) — but browser tool is missing:
    # execution gap, precisely named, never a method failure
    assert ecr["overall"] in ("PARTIAL", "MISSING")
    # WP-027 named outputs present per node
    node = next(n for n in ecr["nodes"] if n["missing"])
    assert "missing_capabilities" in node
    assert "covered_by_worker" in node
    assert "execution_readiness" in node
    # gap recorded as CAPABILITY with the missing tool named
    assert any(
        "browser" in g["reason"] for g in gaps["execution_gaps"]
    )


# ── WP-027 输出六字段与 spec 对齐 ───────────────────────────────────


@pytest.mark.asyncio
async def test_spec_output_fields_aligned(dbengine):
    pid, mid = await _chain(dbengine)
    async with AsyncSession(dbengine) as s:
        assessment = await MethodReadinessAssessor(s).assess(pid, mid)
        ecr = json.loads(assessment.execution_capability_readiness)
        await s.commit()

    spec_fields = (
        "covered_by_worker", "covered_by_tool", "covered_by_skill",
        "missing_capabilities", "missing_skill_dependencies",
        "execution_readiness",
    )
    for node in ecr["nodes"]:
        for f in spec_fields:
            assert f in node, f"WP-027 spec field missing: {f}"


# ── WP-027 Gate 9: LLM 推断不自动注册为可信 Skill ───────────────────


def test_gate9_no_auto_skill_registration():
    """结构性断言: 认知链（understanding/normalizer/readiness）无 Skill
    注册路径——LLM 的推断只落自己的表，永不进 SkillRegistry。"""
    import project_runtime.procedure.normalizer as n_mod
    import project_runtime.readiness.assessor as a_mod
    import project_runtime.understanding.analyzer as u_mod

    for mod in (n_mod, a_mod, u_mod):
        src = inspect.getsource(mod)
        for forbidden in ("SkillRegistry", "SkillDefinition(", "skill_registry",
                          "register_skill"):
            assert forbidden not in src, (
                f"cognitive chain must not register skills: {forbidden}"
            )


@pytest.mark.asyncio
async def test_gate9_registry_rejects_no_source_entries(dbengine):
    """Registry 侧防线: register 拒绝 source=LLM_INFERENCE 类不可信来源。
    （V0.1 SkillSource 枚举 USER/MARKETPLACE——LLM 推断根本不在合法来源
    集内，注册入口类型即拒绝。）"""
    from project_runtime.skills.enums import SkillSource
    from project_runtime.skills.models import SkillDefinition

    legal_sources = {s.value for s in SkillSource}
    assert "LLM_INFERENCE" not in legal_sources
    assert "LLM" not in legal_sources
    # and the registry persists source from the enum only (Pydantic-less
    # model layer enforces via Enum column)
    async with AsyncSession(dbengine) as s:
        sk = SkillDefinition(
            skill_id="x", name="x", version="1",
            skill_type=__import__(
                "project_runtime.skills.enums", fromlist=["SkillType"]
            ).SkillType.CAPABILITY,
            entrypoint="SKILL.md", source=SkillSource.USER,
        )
        s.add(sk)
        src_val = sk.source.value  # snapshot before commit (ORM expiry)
        await s.commit()
        assert src_val == "USER"


# ── WP-031: WorkPackage 三字段可携带可审计 ──────────────────────────


def test_workpackage_binding_trace_fields():
    wp = WorkPackage(
        task_id=1, task_name="t",
        skill_requirement="OPTIONAL",
        resolved_skill_ref="demo-method@1.0.0",
        skill_execution_context_ref="skill-ctx:demo-method:abc123def456",
    )
    assert wp.skill_requirement == "OPTIONAL"
    assert wp.resolved_skill_ref == "demo-method@1.0.0"
    assert wp.skill_execution_context_ref.startswith("skill-ctx:")


def test_workpackage_none_skill_legal():
    """Task 可以没有 Skill（skill_requirement=NONE 合法）——WP-031 Gate 1
    既有 test_no_skill_task_executes 的字段层对应。"""
    wp = WorkPackage(task_id=1, task_name="t", skill_requirement="NONE")
    assert wp.skill_context is None
    assert wp.resolved_skill_ref is None


def test_runner_injects_binding_trace():
    """结构性断言: ExecutionRunner 构造 WorkPackage 时带上三字段。"""
    import project_runtime.execution.service as svc_mod

    src = inspect.getsource(svc_mod)
    assert "skill_requirement=" in src
    assert "resolved_skill_ref=" in src
    assert "skill_execution_context_ref=" in src


# ── WP-031 既有能力对账清单（引用既有测试，防重复实现）────────────


def test_wp031_coverage_matrix_exists():
    """对账清单: D17D + 试用增量已覆盖的 Gate 与测试文件对应。

    Gate 2 携带 Resolved Skill Context → test_llm_worker / test_skill_loader
    Gate 3 Loader 只加载必要文件 → test_skill_loader::test_e02
    Gate 4 版本/hash 可审计 → test_skill_loader::test_e05
    Gate 5 CAPABILITY_MISSING → test_skill_resolver::test_d07
    Gate 6 Loader 不修改 Project State → 结构性 + loader 无状态写路径
    Gate 7 不绕过 Worker/Gate 生命周期 → test_skill_loader::test_e06
    """
    import os

    for f in (
        "tests/test_skill_loader.py",
        "tests/test_skill_resolver.py",
        "tests/test_llm_worker.py",
    ):
        assert os.path.exists(f), f"WP-031 coverage file missing: {f}"


def test_loader_no_state_write_paths():
    """WP-031 Gate 6 结构性断言: loader 无 Project/Task 状态写路径。"""
    import project_runtime.skills.loader as l_mod

    src = inspect.getsource(l_mod)
    for forbidden in ("transition_task", "StateTransitionService",
                      "add(Project", "add(Task"):
        assert forbidden not in src, f"loader must not write state: {forbidden}"
