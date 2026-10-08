"""WP-029 Research Manager & Gap Resolution tests — Gate 四条.

Gate:
  [x] Skill=0 不再自动触发 Method Research
  [x] 用户方法足够时可以完全不联网
  [x] PRIVATE Gap 不会去 Web Search
  [x] Research 不覆盖用户 MANDATORY 流程

Plus: 六类型路由表全路径 + 五查命中即引用 + 同题研究复用（幂等）。
"""

import hashlib
import inspect

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.research.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    GapType,
    MethodAssetAuthority,
)
from project_runtime.project_state.models import (
    MethodAsset,
    MethodGap,
    Project,
)
from project_runtime.research import GapResolver, ResearchRecord
from project_runtime.research.router import ROUTE_TABLE

RICH_DOC = """# 完整流程

## 阶段：需求
- 输入: 用户材料
- 活动: 需求澄清与基线确认
- 输出: 需求基线文档
- 依赖: 无
- 约束: 变更须评审
- 验收: 条目全部有验收标准
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rs.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _project_with_gaps(
    dbengine, doc: str, *, authority=MethodAssetAuthority.MANDATORY,
    gap_type=GapType.DOMAIN,
):
    """Rich doc → project + assessment + one gap of given type."""
    from project_runtime.procedure import ProcedureNormalizer
    from project_runtime.readiness.assessor import MethodReadinessAssessor

    async with AsyncSession(dbengine) as s:
        p = Project(name="rs", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="doc.md", content=doc,
            content_hash=hashlib.sha256(doc.encode()).hexdigest(),
            authority=authority,
        ))
        await s.commit()

    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id
        await s.commit()

    async with AsyncSession(dbengine) as s:
        assessment = await MethodReadinessAssessor(s).assess(pid, mid)
        aid = assessment.id
        await s.commit()

    # plant a gap on the first SUBSTANTIVE node (skip doc-title nodes —
    # they carry no field values and would fake "model covers nothing")
    async with AsyncSession(dbengine) as s:
        from project_runtime.project_state.models import ProcedureNode

        nodes = (await s.execute(
            select(ProcedureNode).where(ProcedureNode.model_id == mid)
        )).scalars().all()
        node = next(
            (n for n in nodes if n.inputs and n.inputs != "UNKNOWN"), nodes[0]
        )
        g = MethodGap(
            assessment_id=aid, gap_type=gap_type,
            scope_ref=f"procedure_node:{node.id}",
            description=f"测试缺口: {gap_type.value}",
        )
        s.add(g)
        await s.flush()
        gid = g.id
        await s.commit()
    return pid, gid


# ── Gate 3: PRIVATE Gap 永不 Web Search ──────────────────────────────


@pytest.mark.asyncio
async def test_private_gap_never_web_search(dbengine):
    pid, gid = await _project_with_gaps(
        dbengine, RICH_DOC, gap_type=GapType.PRIVATE
    )
    async with AsyncSession(dbengine) as s:
        res = await GapResolver(s).resolve_gap(gid)
        await s.commit()

    assert res.route == "USER_CLARIFICATION"
    assert res.action == "USER_CLARIFICATION_REQUESTED"
    # no research record created
    async with AsyncSession(dbengine) as s:
        records = (await s.execute(select(ResearchRecord))).scalars().all()
        assert len(records) == 0


# ── Gate 4: Research 不覆盖 MANDATORY 流程 ───────────────────────────


@pytest.mark.asyncio
async def test_mandatory_flow_not_overwritten(dbengine):
    """MANDATORY 资产上的 DOMAIN gap → USER_CLARIFICATION 而非 Research。"""
    pid, gid = await _project_with_gaps(
        dbengine, RICH_DOC,
        authority=MethodAssetAuthority.MANDATORY, gap_type=GapType.DOMAIN,
    )
    async with AsyncSession(dbengine) as s:
        res = await GapResolver(s).resolve_gap(gid)
        await s.commit()

    # domain would normally route to DOMAIN_RESEARCH — but the gap sits on
    # a MANDATORY asset: research must not rewrite the user's flow
    assert res.route == "USER_CLARIFICATION"
    assert res.source == "mandatory-authority"
    async with AsyncSession(dbengine) as s:
        records = (await s.execute(select(ResearchRecord))).scalars().all()
        assert len(records) == 0


# ── Gate 1: Skill=0 不自动触发 Method Research ───────────────────────


@pytest.mark.asyncio
async def test_zero_skill_no_auto_method_research(dbengine):
    """PROCESS gap + Skill=0 + 有方法资产（MANDATORY doc）→ 不建 Research
    （方法足够时 Skill=0 不是触发条件）。"""
    pid, gid = await _project_with_gaps(
        dbengine, RICH_DOC,
        authority=MethodAssetAuthority.MANDATORY, gap_type=GapType.PROCESS,
    )
    async with AsyncSession(dbengine) as s:
        res = await GapResolver(s).resolve_gap(gid)
        await s.commit()

    # MANDATORY asset present → not research; skills absent didn't trigger
    assert res.action != "RESEARCH_CREATED"
    async with AsyncSession(dbengine) as s:
        records = (await s.execute(select(ResearchRecord))).scalars().all()
        assert len(records) == 0


# ── Gate 2: 方法足够时完全不联网 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_method_sufficient_never_online(dbengine):
    """五查命中（方法资产 RECOMMENDED + procedure model 在）→ 引用不联网。
    五查证据链完整落 notes。"""
    pid, gid = await _project_with_gaps(
        dbengine, RICH_DOC,
        authority=MethodAssetAuthority.RECOMMENDED, gap_type=GapType.DOMAIN,
    )
    async with AsyncSession(dbengine) as s:
        res = await GapResolver(s).resolve_gap(gid)
        await s.commit()

    assert res.action == "RESOLVED_LOCALLY"
    assert res.source in ("method_assets", "procedure_model")
    # evidence chain recorded
    assert any("method_assets=True" in n for n in res.notes)
    assert any("procedure_model=True" in n for n in res.notes)
    async with AsyncSession(dbengine) as s:
        records = (await s.execute(select(ResearchRecord))).scalars().all()
        assert len(records) == 0


# ── 五查全 miss → 建研究 + 同题复用（幂等）─────────────────────────


@pytest.mark.asyncio
async def test_research_created_when_all_checks_miss(dbengine):
    """无 MANDATORY/RECOMMENDED 资产（OPTIONAL）、节点无实质值（thin 文档
    ——模型存在但覆盖不了这个缺口）、memory/skills 空、无既有研究 →
    DOMAIN gap 建新 Research。"""
    thin_doc = """# 薄流程

## 阶段：评审
- 活动: 组织评审
"""
    # OPTIONAL authority → method_assets check misses
    pid, gid = await _project_with_gaps(
        dbengine, thin_doc,
        authority=MethodAssetAuthority.OPTIONAL, gap_type=GapType.DOMAIN,
    )
    async with AsyncSession(dbengine) as s:
        resolver = GapResolver(s, research_executor=False)  # 隔离: 不联网
        res1 = await resolver.resolve_gap(gid)
        await s.commit()

    assert res1.action == "RESEARCH_CREATED"
    assert res1.route == "DOMAIN_RESEARCH"
    assert res1.research_id is not None

    # same question again (new gap, same description) → REUSED not duplicated
    async with AsyncSession(dbengine) as s:
        g2 = MethodGap(
            assessment_id=1, gap_type=GapType.DOMAIN,
            scope_ref="procedure_node:1",
            description="测试缺口: DOMAIN",  # same question text
        )
        s.add(g2)
        await s.flush()
        gid2 = g2.id
        await s.commit()

        res2 = await GapResolver(
            s, research_executor=False
        ).resolve_gap(gid2)
        await s.commit()

    # five-check "existing_research" now hits → reused, not re-created
    assert res2.action in ("RESEARCH_REUSED", "RESOLVED_LOCALLY")
    if res2.research_id is not None:
        assert res2.research_id == res1.research_id
    # exactly ONE research record (dedupe by question+kind)
    async with AsyncSession(dbengine) as s:
        records = (await s.execute(select(ResearchRecord))).scalars().all()
        assert len(records) == 1


# ── 六类型路由表全路径 ───────────────────────────────────────────────


def test_route_table_complete():
    assert ROUTE_TABLE == {
        "PRIVATE": "USER_CLARIFICATION",
        "EXPLICITNESS": "MODEL_PROPOSAL",
        "DOMAIN": "DOMAIN_RESEARCH",
        "TECHNICAL": "TECHNICAL_RESEARCH",
        "PROCESS": "PROJECT_METHOD_RESEARCH",
        "CAPABILITY": "CAPABILITY_RESOLUTION",
    }


@pytest.mark.asyncio
async def test_capability_routes_to_resolution_not_research(dbengine):
    pid, gid = await _project_with_gaps(
        dbengine, RICH_DOC,
        authority=MethodAssetAuthority.OPTIONAL, gap_type=GapType.CAPABILITY,
    )
    async with AsyncSession(dbengine) as s:
        res = await GapResolver(s).resolve_gap(gid)
        await s.commit()
    assert res.route == "CAPABILITY_RESOLUTION"
    assert res.action == "CAPABILITY_RESOLUTION_REQUESTED"
    async with AsyncSession(dbengine) as s:
        records = (await s.execute(select(ResearchRecord))).scalars().all()
        assert len(records) == 0


@pytest.mark.asyncio
async def test_explicitness_routes_to_model_proposal(dbengine):
    pid, gid = await _project_with_gaps(
        dbengine, RICH_DOC,
        authority=MethodAssetAuthority.OPTIONAL, gap_type=GapType.EXPLICITNESS,
    )
    async with AsyncSession(dbengine) as s:
        res = await GapResolver(s).resolve_gap(gid)
        await s.commit()
    assert res.route == "MODEL_PROPOSAL"
    assert res.action == "RESOLVED_LOCALLY"  # model can fill, no network


# ── 结构性断言: router 无 Research 执行体（只路由不执行）────────────


def test_router_routes_but_never_executes():
    """router 只创建/引用研究记录——无 web 调用执行路径（执行属
    research.providers 扩展/后续 WP）。"""
    import project_runtime.research.router as r_mod

    src = inspect.getsource(r_mod)
    for forbidden in ("httpx", "aiohttp", "requests.get", "litellm",
                      "web_search"):
        assert forbidden not in src, f"router must not execute research: {forbidden}"


# ── 偏离②③: 路由后立即执行研究（stub executor 验证流转）───────────


@pytest.mark.asyncio
async def test_research_executed_inline_to_answered(dbengine):
    """新建研究 → 注入的 executor 立即执行 → RESEARCH_ANSWERED +
    ResearchRecord.status=ANSWERED（真实链路用 WebResearchExecutor 联网）。"""

    class StubExecutor:
        def __init__(self, session):
            self._session = session

        async def execute_research(self, research_id):
            from project_runtime.research.models import ResearchRecord

            rec = await self._session.get(ResearchRecord, research_id)
            rec.status = "ANSWERED"
            rec.summary = "stub 研究结论"
            await self._session.flush()
            return rec

    thin_doc = "# 薄流程\n\n## 阶段：评审\n- 活动: 组织评审\n"
    await _project_with_gaps(
        dbengine, thin_doc,
        authority=MethodAssetAuthority.OPTIONAL, gap_type=GapType.DOMAIN,
    )
    async with AsyncSession(dbengine) as s:
        resolver = GapResolver(s, research_executor=StubExecutor)
        res = await resolver.resolve_gap(await _last_gap_id(s))
        await s.commit()

    assert res.action == "RESEARCH_ANSWERED"
    assert res.research_id is not None
    from project_runtime.research.models import ResearchRecord

    async with AsyncSession(dbengine) as s:
        rec = await s.get(ResearchRecord, res.research_id)
        assert rec.status == "ANSWERED"
        assert rec.summary == "stub 研究结论"


async def _last_gap_id(session):
    from project_runtime.project_state.models import MethodGap

    stmt = select(MethodGap).order_by(MethodGap.id.desc()).limit(1)
    return (await session.execute(stmt)).scalar_one().id
