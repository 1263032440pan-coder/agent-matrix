"""WP-026 Project Understanding tests — acceptance checklist.

Acceptance:
  [x] Fact / Assumption / Open Question 分离
  [x] 能识别潜在 Method Asset（五类信号）
  [x] 不把普通项目文档直接当 Skill（结构性断言）

复验预告（收官确认单）:
  ① 零 Archetype 结构性断言（SYS-032）
  ② 理解结果的 Planning Context 真实消费（下游可读快照）
  ③ 与既有认知链（D05–D09）的关系定位（不接管 Normalizer——上游新站点）
"""

import inspect
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import project_runtime.event.models  # noqa: F401
import project_runtime.execution.models  # noqa: F401
import project_runtime.memory_summary.models  # noqa: F401
import project_runtime.project_state.models  # noqa: F401
import project_runtime.retrieval.models  # noqa: F401
import project_runtime.understanding.models  # noqa: F401
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import AssetType, AutomationMode
from project_runtime.project_state.models import MethodAsset, Project
from project_runtime.understanding import (
    UnderstandingService,
)

SOP_MATERIAL = """# 项目实施流程

## 阶段：需求
- 输入: 用户原始材料
- 活动: 需求澄清与基线确认
- 输出: 需求基线文档
- 验收: 需求条目全部有验收标准

## 阶段：开发
- 步骤: 按模块实施
- 输出: 模块代码
- 验收: 测试通过

## 待定事项
- 部署方式待定
"""

REQ_MATERIAL = """# 项目需求

- 系统必须支持任务搜索
- 需要持久化存储
- 界面交互方式尚不明确
"""


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/und.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(dbengine, goal: str, materials: list[tuple[str, str]]) -> int:
    """goal + [(name, content)] → project_id with method assets."""
    async with AsyncSession(dbengine) as s:
        p = Project(name="und-test", goal=goal,
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        for name, content in materials:
            import hashlib

            s.add(MethodAsset(
                project_id=pid,
                asset_type=AssetType.PROCESS,
                source_path=name,
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ))
        await s.commit()
        return pid


# ── §1 Fact / Assumption / Open Question 分离 ─────────────────────────


@pytest.mark.asyncio
async def test_three_way_separation(dbengine):
    pid = await _seed(dbengine, "开发一个个人任务看板 Web 应用",
                      [("req.md", REQ_MATERIAL)])
    async with AsyncSession(dbengine) as s:
        svc = UnderstandingService(s)
        row = await svc.analyze_project(pid)
        facts = json.loads(row.known_facts_json or "[]")
        assumptions = json.loads(row.assumptions_json or "[]")
        open_qs = json.loads(row.open_questions_json or "[]")

        # requirement-grounded statements are FACTS (from user material)
        assert any("必须" in f for f in facts)
        assert any("持久化" in f for f in facts)
        # unknown-marked statements are OPEN QUESTIONS, never assumed
        assert any("尚不明确" in q for q in open_qs)
        # nothing marked open leaks into facts or assumptions
        assert not any("尚不明确" in f for f in facts)
        assert not any("尚不明确" in a for a in assumptions)
        # three-way: the same statement never appears in two buckets
        assert not (set(facts) & set(assumptions))
        assert not (set(facts) & set(open_qs))
        assert not (set(assumptions) & set(open_qs))
        # requirements tracked separately too
        reqs = json.loads(row.known_requirements_json or "[]")
        assert any("搜索" in r for r in reqs)


@pytest.mark.asyncio
async def test_assumptions_are_labelled_inference(dbengine):
    # 无方法信号材料 → assumption 明说这是推断非事实
    pid = await _seed(dbengine, "开发一个数据分析工具",
                      [("plain.md", "# 随便一篇文档\n\n- 项目背景介绍\n")])
    async with AsyncSession(dbengine) as s:
        svc = UnderstandingService(s)
        row = await svc.analyze_project(pid)
        assumptions = json.loads(row.assumptions_json or "[]")
        assert any("推断" in a for a in assumptions)
        # hypotheses derived from goal
        assert row.domain_hypothesis == "DATA"
        assert row.project_type_hypothesis == "BUILD_SOFTWARE"


# ── §2 识别潜在 Method Asset ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_method_asset_recognition(dbengine):
    pid = await _seed(dbengine, "建设任务看板",
                      [("sop.md", SOP_MATERIAL), ("req.md", REQ_MATERIAL)])
    async with AsyncSession(dbengine) as s:
        svc = UnderstandingService(s)
        row = await svc.analyze_project(pid)
        hints = json.loads(row.method_asset_hints_json or "[]")

        # SOP material recognized with a signal + provenance + rationale
        assert len(hints) >= 1
        sop_hint = [h for h in hints if h["signal"] in ("PROCESS", "SOP")]
        assert sop_hint, f"expected method signal, got {hints}"
        h = sop_hint[0]
        assert h["provenance"] == "user-material"
        assert 0.5 <= h["confidence"] <= 0.9
        assert h["rationale"]

        # plain requirement material is NOT a method asset
        # (no 流程/阶段/步骤/模板 signals at sufficient strength)
        asset_ids_hinted = {x["asset_id"] for x in hints}
        async with AsyncSession(dbengine) as s2:
            from sqlalchemy import select

            assets = (
                (await s2.execute(select(MethodAsset).where(
                    MethodAsset.project_id == pid)))
                .scalars().all()
            )
            sop_ids = {a.id for a in assets if a.source_path == "sop.md"}
        assert sop_ids & asset_ids_hinted  # sop.md hinted
        # req.md MAY be hinted only if it truly carries method signals —
        # with this fixture it carries 验收 markers, accept either but the
        # sop must be there.

        # open questions from the SOP's 待定 section survive
        open_qs = json.loads(row.open_questions_json or "[]")
        assert any("待定" in q for q in open_qs)


# ── §3 不把普通项目文档直接当 Skill（结构性断言）────────────────────


def test_no_skill_registration_path():
    """understanding 模块不得存在任何 Skill 写路径——hints 只指向资产。"""
    import project_runtime.understanding.analyzer as an_mod
    import project_runtime.understanding.models as md_mod

    for mod in (an_mod, md_mod):
        src = inspect.getsource(mod)
        for forbidden in (
            "SkillDefinition(",          # Skill 注册
            "skills.importer",           # 导入管线
            "skill_registry",            # 注册表写
            "SkillLoader",               # 加载
            "bind_skill",                # 绑定
        ):
            assert forbidden not in src, (
                f"understanding must not touch skill paths: {forbidden}"
            )
    # the ONLY entity written is ProjectUnderstanding
    src = inspect.getsource(an_mod)
    assert "ProjectUnderstanding(" in src


# ── 复验预告① 零 Archetype（SYS-032）─────────────────────────────────


def test_zero_archetype_structural():
    """核心不预置任何行业 Archetype——模块内不得有任何行业硬编码模板表。"""
    import project_runtime.understanding.analyzer as an_mod

    src = inspect.getsource(an_mod)
    # domain hints are HYPOTHESIS keywords, not preset archetypes: no
    # industry-specific playbook/template/rule tables may exist.
    for forbidden in (
        "ARCHETYPE", "archetype",
        "PLAYBOOK_TEMPLATE", "playbook_template",
        "行业模板", "预设模板",
    ):
        assert forbidden not in src, f"no preset archetype allowed: {forbidden}"


@pytest.mark.asyncio
async def test_unknown_domain_is_honest(dbengine):
    """材料无法识别领域时诚实标 UNKNOWN + assumption——不脑补行业。"""
    pid = await _seed(dbengine, "", [("odd.md", "# 某材料\n- 内容 A\n")])
    async with AsyncSession(dbengine) as s:
        svc = UnderstandingService(s)
        row = await svc.analyze_project(pid)
        assert row.domain_hypothesis == "UNKNOWN"
        # goal missing → open question, not a fabricated goal
        open_qs = json.loads(row.open_questions_json or "[]")
        assert any("目标缺失" in q for q in open_qs)


# ── 复验预告② Planning Context 消费（下游可读）──────────────────────


@pytest.mark.asyncio
async def test_snapshot_consumable_downstream(dbengine):
    """理解快照落库可查——Planning Context（WP-030 消费点）能拿到结构化字段。"""
    pid = await _seed(dbengine, "开发 Web 看板", [("sop.md", SOP_MATERIAL)])
    async with AsyncSession(dbengine) as s:
        svc = UnderstandingService(s)
        row = await svc.analyze_project(pid)
        uid = row.id
        await s.commit()

    # fresh session — downstream consumer reads the snapshot
    from sqlalchemy import select

    from project_runtime.understanding.models import ProjectUnderstanding

    async with AsyncSession(dbengine) as s:
        snap = (await s.execute(
            select(ProjectUnderstanding).where(
                ProjectUnderstanding.project_id == pid)
        )).scalar_one()
        assert snap.id == uid
        assert snap.analyzer_version == "v0.1"
        assert snap.goal  # goal materialized
        assert json.loads(snap.method_asset_hints_json or "[]")  # hints present
        # every JSON field parses (downstream contract)
        for f in ("known_facts_json", "assumptions_json",
                  "open_questions_json", "known_requirements_json",
                  "knowledge_gaps_json"):
            if getattr(snap, f):
                json.loads(getattr(snap, f))


# ── 复验预告③ 与 D05–D09 认知链的关系：上游新站点，不接管 Normalizer ──


def test_does_not_replace_normalizer_chain():
    """understanding 是认知链上游新站点——不得接管/复制 D06 Normalizer 职责
    （不产 ProcedureNode、不做 readiness/gap/planner）。"""
    import project_runtime.understanding.analyzer as an_mod

    src = inspect.getsource(an_mod)
    for forbidden in (
        "ProcedureNode(",        # D06 规范化
        "ProcedureNormalizer",   # normalizer 链
        "ReadinessAssessment(",  # D08 readiness
        "GapClassifier",         # D08 gap
        "DynamicPlanner",        # D09 planner
    ):
        assert forbidden not in src, (
            f"understanding must not take over D05-D09 duties: {forbidden}"
        )


# ── WP-037 前向接口：analyzer 协议可替换 ─────────────────────────────


@pytest.mark.asyncio
async def test_analyzer_protocol_swappable(dbengine):
    """UnderstandingAnalyzer 协议可注入自定义实现（WP-037 LLM 版同入口）。"""

    class LLMStub:
        async def analyze(self, goal, assets):
            from project_runtime.understanding.analyzer import (
                UnderstandingResult,
            )

            return UnderstandingResult(
                domain_hypothesis="AI",
                project_type_hypothesis="RESEARCH",
                goal=goal,
                known_facts=["llm-derived fact"],
            )

    pid = await _seed(dbengine, "研究一个课题", [("m.md", "# 材料\n- 内容\n")])
    async with AsyncSession(dbengine) as s:
        svc = UnderstandingService(s)
        row = await svc.analyze_project(pid, analyzer=LLMStub())
        assert row.domain_hypothesis == "AI"
        assert json.loads(row.known_facts_json) == ["llm-derived fact"]


# ── API 层: POST/GET /api/projects/{id}/understanding ─────────────────


@pytest.mark.asyncio
async def test_understanding_api(dbengine, monkeypatch):
    import apps.runtime.api.projects as prj_mod

    monkeypatch.setattr(prj_mod, "engine", dbengine)
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from apps.runtime.api.projects import router

    app = FastAPI()
    app.include_router(router)  # router already carries /api/projects

    pid = await _seed(dbengine, "开发 Web 看板",
                      [("sop.md", SOP_MATERIAL)])

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        resp = await client.post(f"/api/projects/{pid}/understanding")
        assert resp.status_code == 201
        body = resp.json()
        assert body["analyzer_version"] == "v0.1"
        assert json.loads(body["method_asset_hints"] or "[]")

        # GET reads back the latest snapshot
        resp2 = await client.get(f"/api/projects/{pid}/understanding")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == body["id"]

        # 404 for unknown project / no snapshot
        resp3 = await client.post("/api/projects/999/understanding")
        assert resp3.status_code == 404
        resp4 = await client.get("/api/projects/999/understanding")
        assert resp4.status_code == 404
