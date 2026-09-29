"""WP-037 Method Asset Discovery & Procedure Normalization tests.

Gate:
  [x] 能从多个文档中发现流程资产
  [x] 能建立 Procedure Model
  [x] 每个节点可追溯 source_ref
  [x] 未明确 Acceptance 时保持 UNKNOWN
  [x] 冲突流程可标记 CONFLICTED

Extension (CR-METHOD-POINTS):
  [x] method.assets.providers / processors / procedure.normalizers 三点可注册
  [x] 未登记点仍拒绝（稳定点纪律不破）
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
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.extension.runtime.contracts import (
    Permission,
    RuntimeExtensionKind,
)
from project_runtime.extension.runtime.executor import (
    STABLE_POINTS,
    RuntimeHookExecutor,
)
from project_runtime.procedure import MethodAssetDiscovery, ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    ReadinessStatus,
)
from project_runtime.project_state.models import (
    MethodAsset,
    ProcedureNode,
    Project,
)
from project_runtime.understanding import UnderstandingService

SOP_DOC = """# 实施流程

## 阶段：需求
- 输入: 用户材料
- 活动: 需求澄清
- 输出: 需求基线
- 验收: 条目有验收标准

## 阶段：开发
- 步骤: 按模块实施
- 输出: 代码
- 验收: 测试通过
"""

TEMPLATE_DOC = """# 测试用例模板

## 模板结构
- 用例编号 / 前置条件 / 步骤 / 预期结果
"""

HISTORICAL_DOC = """# 上次项目回顾

- 历史项目复盘要点
- 交付节奏参考
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/disc.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(dbengine, docs: list[tuple[str, str, str]], goal="建设系统"):
    """[(name, content, asset_type)] → project_id（含 understanding 前置）"""
    async with AsyncSession(dbengine) as s:
        p = Project(name="disc-test", goal=goal,
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        for name, content, atype in docs:
            s.add(MethodAsset(
                project_id=pid,
                asset_type=AssetType(atype),
                source_path=name,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ))
        await s.commit()
    return pid


# ── Gate 1: 多文档发现流程资产 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_from_multiple_docs(dbengine):
    pid = await _seed(dbengine, [
        ("sop.md", SOP_DOC, "PROCESS"),
        ("tpl.md", TEMPLATE_DOC, "TEMPLATE"),
        ("hist.md", HISTORICAL_DOC, "PROCESS"),
    ])
    async with AsyncSession(dbengine) as s:
        # 先跑 understanding 产 hints（WP-026 先验链）
        await UnderstandingService(s).analyze_project(pid)
        await s.commit()

        disc = MethodAssetDiscovery(s)
        results = await disc.discover(pid)
        await s.commit()

        assert len(results) == 3
        by_name = {}
        assets = (
            (await s.execute(select(MethodAsset).where(
                MethodAsset.project_id == pid))).scalars().all()
        )
        for r in results:
            meta = json.loads(
                next(a for a in assets if a.id == r.asset_id).meta_json or "{}"
            )
            assert meta["discovery"]["matched_signal"]  # enrichment 落库
            by_name[r.asset_id] = r

        # SOP 文档被识别为流程资产（hint 或内容复核二选一）
        sop_id = next(a.id for a in assets if a.source_path == "sop.md")
        assert by_name[sop_id].asset_type in (
            AssetType.PROCESS, AssetType.SOP,
        )
        assert by_name[sop_id].asset_type != AssetType.TEMPLATE

        # 模板文档识别为 TEMPLATE
        tpl_id = next(a.id for a in assets if a.source_path == "tpl.md")
        assert by_name[tpl_id].asset_type == AssetType.TEMPLATE

        # 历史参考识别为 HISTORICAL_REFERENCE
        hist_id = next(a.id for a in assets if a.source_path == "hist.md")
        assert by_name[hist_id].asset_type == AssetType.HISTORICAL_REFERENCE


@pytest.mark.asyncio
async def test_discovery_without_understanding_still_works(dbengine):
    """无 understanding 快照（hint 缺失）时内容复核兜底——不依赖先验。"""
    pid = await _seed(dbengine, [
        ("sop.md", SOP_DOC, "PROCESS"),
    ])
    async with AsyncSession(dbengine) as s:
        disc = MethodAssetDiscovery(s)
        results = await disc.discover(pid)
        assert len(results) == 1
        assert results[0].asset_type in (AssetType.PROCESS, AssetType.SOP)
        assert results[0].from_hint is False  # 内容复核路径


# ── Gate 2+3: 建立 Procedure Model + 节点 source_ref 可追溯 ───────────


@pytest.mark.asyncio
async def test_procedure_model_with_source_refs(dbengine):
    pid = await _seed(dbengine, [
        ("sop.md", SOP_DOC, "PROCESS"),
        ("tpl.md", TEMPLATE_DOC, "TEMPLATE"),
    ])
    async with AsyncSession(dbengine) as s:
        norm = ProcedureNormalizer(s)
        model = await norm.normalize(pid)
        mid = model.id  # snapshot before commit (ORM expiry)
        await s.commit()

    # fresh session: nodes carry source_ref + provenance
    async with AsyncSession(dbengine) as s:
        nodes = (
            (await s.execute(select(ProcedureNode).where(
                ProcedureNode.model_id == mid))).scalars().all()
        )
        assert len(nodes) >= 2  # 阶段：需求 / 阶段：开发
        for n in nodes:
            assert n.source_ref, f"node {n.name} missing source_ref"
            prov = json.loads(n.provenance or "{}")
            assert "asset_id" in prov  # provenance 可追溯到资产
        names = {n.name for n in nodes}
        assert any("需求" in x for x in names)


# ── Gate 4: 未明确 Acceptance 保持 UNKNOWN ────────────────────────────


@pytest.mark.asyncio
async def test_unspecified_acceptance_stays_unknown(dbengine):
    no_acc_doc = """# 流程

## 阶段：设计
- 输入: 需求
- 活动: 概要设计
- 输出: 设计文档
"""
    pid = await _seed(dbengine, [("flow.md", no_acc_doc, "PROCESS")])
    async with AsyncSession(dbengine) as s:
        norm = ProcedureNormalizer(s)
        model = await norm.normalize(pid)
        mid = model.id  # snapshot before commit (ORM expiry)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        nodes = (
            (await s.execute(select(ProcedureNode).where(
                ProcedureNode.model_id == mid))).scalars().all()
        )
        assert nodes
        for n in nodes:
            # no 验收/标准 markers in this doc → must be UNKNOWN, never
            # fabricated acceptance criteria
            assert n.acceptance_criteria == "UNKNOWN", (
                f"fabricated acceptance on {n.name}: {n.acceptance_criteria}"
            )


# ── Gate 5: 冲突流程可标记 CONFLICTED ────────────────────────────────


@pytest.mark.asyncio
async def test_conflicting_docs_mark_conflicted(dbengine):
    doc_a = """# 流程甲

## 阶段：需求
- 输入: 用户材料
- 输出: 需求文档A
- 验收: 标准A
"""
    doc_b = """# 流程乙

## 阶段：需求
- 输入: 用户材料
- 输出: 需求文档B
- 验收: 标准B
"""
    pid = await _seed(dbengine, [
        ("a.md", doc_a, "PROCESS"), ("b.md", doc_b, "PROCESS"),
    ])
    async with AsyncSession(dbengine) as s:
        norm = ProcedureNormalizer(s)
        model = await norm.normalize(pid)
        mid = model.id  # snapshot before commit (ORM expiry)
        await s.commit()

    async with AsyncSession(dbengine) as s:
        nodes = (
            (await s.execute(select(ProcedureNode).where(
                ProcedureNode.model_id == mid))).scalars().all()
        )
        req_nodes = [n for n in nodes if "需求" in n.name]
        assert len(req_nodes) >= 2
        assert any(
            n.readiness_status == ReadinessStatus.CONFLICTED
            for n in req_nodes
        ), "same-name nodes with differing outputs must be CONFLICTED"


# ── Extension: 三点可注册 + 稳定点纪律 ────────────────────────────────


def test_method_points_registered_in_stable_points():
    for pt in ("method.assets.providers", "method.assets.processors",
               "procedure.normalizers"):
        assert pt in STABLE_POINTS, f"{pt} missing from STABLE_POINTS"
    assert len(STABLE_POINTS) == 24  # 21 + 3


@pytest.mark.asyncio
async def test_discovery_fires_extension_hooks(dbengine):
    """providers/processors 两点在 discovery 链上真实 fire（advisory）。"""
    from project_runtime.extension.registry import ExtensionRegistry

    pid = await _seed(dbengine, [("sop.md", SOP_DOC, "PROCESS")])

    fired: list[str] = []

    class Probe:
        permission = Permission.ENRICH

        async def invoke(self, payload):
            fired.append(payload.get("point_marker", "unknown"))
            return {"result": "ACK"}

    registry = ExtensionRegistry()
    executor = RuntimeHookExecutor(registry)
    executor.register_runtime(
        "method.assets.providers", "probe-provider",
        RuntimeExtensionKind.MIDDLEWARE, Probe(),
    )
    executor.register_runtime(
        "method.assets.processors", "probe-processor",
        RuntimeExtensionKind.MIDDLEWARE, Probe(),
    )

    async with AsyncSession(dbengine) as s:
        disc = MethodAssetDiscovery(s)
        await disc.discover(pid, executor=executor)
        await s.commit()

    assert len(fired) == 2  # both hooks really fired


def test_unknown_point_still_rejected():
    """稳定点纪律不破——未登记点注册仍拒绝。"""
    from project_runtime.extension.registry import ExtensionRegistry
    from project_runtime.extension.runtime.executor import (
        RuntimeExtensionError,
    )

    executor = RuntimeHookExecutor(ExtensionRegistry())

    class Probe:
        async def invoke(self, payload):
            return {"result": "ACK"}

    with pytest.raises(RuntimeExtensionError):
        executor.register_runtime(
            "method.assets.bogus", "x",
            RuntimeExtensionKind.MIDDLEWARE, Probe(),
        )


# ── 零脑补红线（结构性）──────────────────────────────────────────────


def test_discovery_module_no_fabrication_paths():
    import project_runtime.procedure.discovery as d_mod

    src = inspect.getsource(d_mod)
    for forbidden in (
        "SkillDefinition(",     # 不当 Skill（WP-026 红线延续）
        "ACCEPTANCE_RULE if",   # 无内容信号不得猜验收类
    ):
        assert forbidden not in src
