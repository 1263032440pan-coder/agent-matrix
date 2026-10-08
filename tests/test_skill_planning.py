"""偏离① 兑现: skill 参与项目流程创建.

场景: 用户传需求/功能规格文档（非过程方法）→ 抽取产出功能点节点
（无实质流程）→ 项目绑定 PROJECT_METHOD skill → Playbook 应用
skill stages 骨架 + 功能点归入阶段（RECOMMENDED 只增不改）。

验证:
  [x] 方法不足 + 有 skill → workstreams = skill 九阶段骨架
  [x] 功能节点归入对应阶段（亲和映射）
  [x] 来源可溯（SKILL_METHOD / skill_id+version / RECOMMENDED）
  [x] 方法充分（过程文档）→ 不应用骨架（现行行为不变）
  [x] 无 skill 绑定 → 不应用（回到平铺行为）
  [x] provenance_map 记录 skill_method_skeleton
"""

import hashlib
import json

import pytest
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
from project_runtime.playbook.builder import PlaybookBuilder
from project_runtime.procedure import ProcedureNormalizer
from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    AssetType,
    AutomationMode,
    MethodAssetAuthority,
)
from project_runtime.project_state.models import (
    MethodAsset,
    Project,
    ProjectPlaybook,
)
from project_runtime.readiness.assessor import MethodReadinessAssessor
from project_runtime.skills.enums import SkillSource, SkillStatus, SkillType
from project_runtime.skills.models import SkillBinding, SkillDefinition

# 需求/功能规格文档（无过程结构——功能点列表）
REQ_DOC = """# 公文编辑器接口文档

## 功能清单

### 公文生成
用户选择模板后生成公文初稿。

### 文档保存
编辑器内容以 JSON 保存到后端，带版本号。

### 选区改写
选中一段文字后 AI 改写该选区。

### 撤销
多步撤销编辑操作。
"""

# 过程/方法文档（有明确阶段结构）
METHOD_DOC = """# 项目实施流程

## 阶段：需求分析
- 输入: 用户材料
- 活动: 需求澄清与基线确认
- 输出: 需求基线文档
- 验收: 条目有验收标准

## 阶段：架构设计
- 输入: 需求基线
- 活动: 系统架构设计
- 输出: 架构文档
- 依赖: 需求分析
- 验收: 评审通过
"""

SKILL_STAGES = json.dumps(
    ["项目理解", "需求基线", "解决方案", "原型设计", "架构设计",
     "开发实现", "测试验证", "交付打包", "使用文档"],
    ensure_ascii=False,
)
SKILL_OUTPUTS = json.dumps(
    ["解决方案文档", "需求文档", "系统架构文档", "测试报告", "交付文档"],
    ensure_ascii=False,
)


@pytest.fixture
async def dbengine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sk1.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _seed(dbengine, doc, *, with_skill=False):
    async with AsyncSession(dbengine) as s:
        p = Project(name="sk-plan", goal="建设系统",
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
        if with_skill:
            sk = SkillDefinition(
                skill_id="test-method", name="Test Method", version="1.0.0",
                skill_type=SkillType.PROJECT_METHOD,
                stages=SKILL_STAGES, expected_outputs=SKILL_OUTPUTS,
                entrypoint="SKILL.md", source=SkillSource.USER,
                status=SkillStatus.ENABLED, content_hash="h1",
            )
            s.add(sk)
            await s.flush()
            s.add(SkillBinding(
                project_id=pid, scope_type="PROJECT_METHOD", scope_key="",
                skill_id="test-method", skill_version="1.0.0", pinned=True,
            ))
        await s.commit()
    return pid


async def _chain(dbengine, pid):
    async with AsyncSession(dbengine) as s:
        model = await ProcedureNormalizer(s).normalize(pid)
        mid = model.id
        await s.commit()
    async with AsyncSession(dbengine) as s:
        await MethodReadinessAssessor(s).assess(pid, mid)
        await s.commit()
    async with AsyncSession(dbengine) as s:
        pb = await PlaybookBuilder(s).build(pid)
        pb_id, ws = pb.id, json.loads(pb.workstreams or "[]")
        prov = json.loads(pb.provenance_map or "{}")
        await s.commit()
    return pb_id, ws, prov


@pytest.mark.asyncio
async def test_requirement_doc_with_skill_gets_stage_skeleton(dbengine):
    """功能文档 + skill → 九阶段骨架（非平铺功能模块）。"""
    pid = await _seed(dbengine, REQ_DOC, with_skill=True)
    pb_id, ws, prov = await _chain(dbengine, pid)

    assert len(ws) == 9, f"expected 9 skill stages, got {len(ws)}"
    names = [w["name"] for w in ws]
    assert names[0] == "项目理解"
    assert "开发实现" in names
    # 全部 SKILL_METHOD 来源 + RECOMMENDED 权威
    assert all(w["source"]["type"] == "SKILL_METHOD" for w in ws)
    assert all(w["source"]["authority"] == "RECOMMENDED" for w in ws)
    assert all(w["source"]["skill_id"] == "test-method" for w in ws)
    # provenance 记录
    assert prov.get("skill_method_skeleton") == "applied"


@pytest.mark.asyncio
async def test_function_nodes_mapped_into_stages(dbengine):
    """功能点归入对应阶段（亲和映射）——公文生成/选区改写→开发实现。"""
    pid = await _seed(dbengine, REQ_DOC, with_skill=True)
    pb_id, ws, prov = await _chain(dbengine, pid)

    dev = next(w for w in ws if w["name"] == "开发实现")
    mapped = dev["source"].get("mapped_node_ids") or []
    assert len(mapped) >= 1, "functional nodes must map into 开发实现"
    # activities 含被归入功能点名
    assert "公文生成" in (dev["activities"] or "") or "撤销" in (
        dev["activities"] or ""
    )


@pytest.mark.asyncio
async def test_stage_chain_dependencies(dbengine):
    """阶段链式依赖（后阶段依赖前一阶段）。"""
    pid = await _seed(dbengine, REQ_DOC, with_skill=True)
    pb_id, ws, prov = await _chain(dbengine, pid)
    for i in range(1, len(ws)):
        assert ws[i]["dependencies"] == ws[i - 1]["name"]


@pytest.mark.asyncio
async def test_method_doc_not_overridden(dbengine):
    """过程文档（方法充分）→ 不应用骨架——用户方法为主体（红线）。"""
    pid = await _seed(dbengine, METHOD_DOC, with_skill=True)
    pb_id, ws, prov = await _chain(dbengine, pid)

    # 用户方法节点直接成为 workstreams
    names = [w["name"] for w in ws]
    assert "阶段：需求分析" in names or "需求分析" in str(names)
    assert all(w["source"]["type"] != "SKILL_METHOD" for w in ws)
    assert prov.get("skill_method_skeleton") is None


@pytest.mark.asyncio
async def test_no_skill_no_skeleton(dbengine):
    """功能文档但未绑 skill → 平铺行为不变（无 skill 不编流程）。"""
    pid = await _seed(dbengine, REQ_DOC, with_skill=False)
    pb_id, ws, prov = await _chain(dbengine, pid)
    assert all(w["source"]["type"] != "SKILL_METHOD" for w in ws)
    assert prov.get("skill_method_skeleton") is None


@pytest.mark.asyncio
async def test_planner_consumes_skeleton(dbengine):
    """骨架 workstreams → DynamicPlanner 产出九阶段任务链（端到端）。"""
    from project_runtime.planner.planner import DynamicPlanner

    pid = await _seed(dbengine, REQ_DOC, with_skill=True)
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
    async with AsyncSession(dbengine) as s:
        pb2 = await s.get(ProjectPlaybook, pb_id)
        tasks = await DynamicPlanner(s).plan(pb2)
        task_names = [t.name for t in tasks]  # snapshot before commit
        await s.commit()

    assert len(tasks) == 9
    assert task_names[0] == "项目理解"
    assert task_names[-1] == "使用文档"
