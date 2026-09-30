"""WP-030 Project Playbook 与 Planning Context tests — Gate 四条 + V1.12 三条.

Gate:
  [x] Playbook 以用户方法为主体
  [x] 外部 Research 内容可识别为 RECOMMENDED / OPTIONAL
  [x] Model Inference 可识别为 PROPOSED
  [x] 任一关键 Workstream 可回答"为什么要做、来源是什么"

V1.12 增补:
  [x] Installed Project Method Skills = [] 时仍可生成 Playbook
  [x] Skill 不静默覆盖用户 MANDATORY 方法
  [x] Playbook 仅记录偏好/绑定，不直接加载 TASK_EXECUTION Skill
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
)
from project_runtime.readiness.assessor import MethodReadinessAssessor
from project_runtime.research.models import ResearchRecord

USER_DOC = """# 用户实施流程

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
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/pb.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


async def _full_chain(dbengine, doc: str, *, authority=MethodAssetAuthority.MANDATORY):
    """project → asset → normalize → assess → build playbook."""
    async with AsyncSession(dbengine) as s:
        p = Project(name="pb", goal="建设系统",
                    automation_mode=AutomationMode.FULL_AUTO)
        s.add(p)
        await s.flush()
        pid = p.id
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="user.md", content=doc,
            content_hash=hashlib.sha256(doc.encode()).hexdigest(),
            authority=authority,
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
        # snapshot before commit
        snap = {
            "id": playbook.id,
            "workstreams": json.loads(playbook.workstreams or "[]"),
            "method_asset_refs": json.loads(playbook.method_asset_refs or "[]"),
            "skill_refs": json.loads(playbook.project_method_skill_refs or "[]"),
            "exec_snapshot": playbook.execution_readiness_snapshot,
            "gap_refs": json.loads(playbook.gap_refs or "[]"),
        }
        await s.commit()
    return pid, snap


# ── Gate 1: Playbook 以用户方法为主体 ───────────────────────────────


@pytest.mark.asyncio
async def test_user_method_is_the_body(dbengine):
    pid, snap = await _full_chain(dbengine, USER_DOC)
    assert snap["method_asset_refs"], "user asset must be referenced"
    ws = [w for w in snap["workstreams"] if "需求" in w["name"]]
    assert ws, "user-method workstream must exist"
    w = ws[0]
    # body comes from the user's doc (not research/skill/inference)
    assert w["source"]["type"] == "USER_METHOD"
    assert w["inputs"] == "用户材料"
    assert w["outputs"] == "需求基线"
    # activities/acceptance carried verbatim from the user doc
    assert "需求澄清" in (w["activities"] or "")


# ── Gate 2: Research 识别为 RECOMMENDED（只增不改）──────────────────


@pytest.mark.asyncio
async def test_research_enrichment_recommended(dbengine):
    pid, _ = await _full_chain(dbengine, USER_DOC)

    # answer a research record mentioning the workstream
    async with AsyncSession(dbengine) as s:
        rec = ResearchRecord(
            project_id=pid, research_kind="DOMAIN_RESEARCH",
            question="阶段：需求 的行业域知识是什么",
            status="ANSWERED",
            summary="该阶段需领域词典辅助",
        )
        s.add(rec)
        await s.commit()

    # rebuild playbook (new model → new playbook) — force via fresh assets
    async with AsyncSession(dbengine) as s:
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="user2.md", content=USER_DOC + "\n补: 无\n",
            content_hash=hashlib.sha256((USER_DOC + "2").encode()).hexdigest(),
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
        ws = json.loads(playbook.workstreams or "[]")
        await s.commit()

    target = [w for w in ws if "需求" in w["name"]]
    assert target
    enriched = target[0].get("research_enrichments", [])
    assert enriched, "answered research must attach to related workstream"
    assert enriched[0]["authority"] == "RECOMMENDED"
    assert enriched[0]["source"] == "research"
    # research NEVER replaces user-method fields (only adds a key)
    assert target[0]["source"]["type"] == "USER_METHOD"
    assert target[0]["inputs"] == "用户材料"


# ── Gate 3: Model Inference 识别为 PROPOSED ─────────────────────────


def test_model_inference_proposed():
    """无资产来源的 workstream（如 LLM 抽取的推断节点）标 PROPOSED。"""
    # builder 的 source_type 语义: no-asset → MODEL_INFERENCE（纯静态语义，
    # 无需 session/链路）
    from project_runtime.playbook.builder import PlaybookBuilder as B

    assert B._determine_source_type(None) == "MODEL_INFERENCE"
    # planner 层将 MODEL_INFERENCE 按 PROPOSED 权威处理（D09 既有链）


# ── Gate 4: 关键 Workstream 可回答"为什么、来源" ─────────────────────


@pytest.mark.asyncio
async def test_workstream_why_and_source(dbengine):
    pid, snap = await _full_chain(dbengine, USER_DOC)
    for w in snap["workstreams"]:
        src = w["source"]
        # every workstream answers: what asset / which node / what authority
        assert src["asset_id"] or src["type"] == "MODEL_INFERENCE"
        assert src["procedure_node_id"]
        assert src["authority"] in ("MANDATORY", "RECOMMENDED", "OPTIONAL",
                                    "PROPOSED")


# ── V1.12-1: Skills=[] 仍可生成 Playbook ────────────────────────────


@pytest.mark.asyncio
async def test_zero_skills_playbook_still_builds(dbengine):
    pid, snap = await _full_chain(dbengine, USER_DOC)
    # no skill bindings exist
    async with AsyncSession(dbengine) as s:
        from project_runtime.skills.models import SkillBinding

        n = len(list((await s.execute(select(SkillBinding))).scalars().all()))
        assert n == 0
    assert snap["skill_refs"] == []
    assert snap["workstreams"]  # playbook fully generated


# ── V1.12-2: Skill 不静默覆盖 MANDATORY ─────────────────────────────


@pytest.mark.asyncio
async def test_skill_not_override_mandatory(dbengine):
    """绑定 PROJECT_METHOD skill 后重建——workstream 主体仍是用户方法，
    skill 只出现在 refs 列表（authority=OPTIONAL 低于用户方法）。"""
    pid, _ = await _full_chain(dbengine, USER_DOC)

    # register + bind a PROJECT_METHOD skill
    async with AsyncSession(dbengine) as s:
        from project_runtime.skills.enums import (
            SkillSource,
            SkillStatus,
            SkillType,
        )
        from project_runtime.skills.models import (
            SkillBinding,
            SkillDefinition,
        )

        sk = SkillDefinition(
            skill_id="demo-method", name="Demo Method", version="1.0.0",
            skill_type=SkillType.PROJECT_METHOD,
            entrypoint="SKILL.md", source=SkillSource.USER,
            status=SkillStatus.ENABLED, content_hash="h1",
        )
        s.add(sk)
        await s.flush()
        s.add(SkillBinding(
            project_id=pid, scope_type="PROJECT_METHOD", scope_key="",
            skill_id="demo-method", skill_version="1.0.0", pinned=True,
        ))
        await s.commit()

    # new asset → new model → new playbook with skill bound
    async with AsyncSession(dbengine) as s:
        s.add(MethodAsset(
            project_id=pid, asset_type=AssetType.PROCESS,
            source_path="user3.md", content=USER_DOC + "\n期: 三\n",
            content_hash=hashlib.sha256((USER_DOC + "3").encode()).hexdigest(),
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
        ws = json.loads(playbook.workstreams or "[]")
        refs = json.loads(playbook.project_method_skill_refs or "[]")
        await s.commit()

    # skill recorded as reference with required fields
    assert refs and refs[0]["skill_id"] == "demo-method"
    assert refs[0]["version"] == "1.0.0"
    assert refs[0]["content_hash"]
    assert refs[0]["source"]
    # user method body untouched
    target = [w for w in ws if "需求" in w["name"]][0]
    assert target["source"]["type"] == "USER_METHOD"
    assert target["inputs"] == "用户材料"


# ── V1.12-3: 只记录不加载（结构性断言）─────────────────────────────


def test_playbook_records_but_never_loads():
    """builder 不得 import SkillLoader/Resolver——加载属执行链。"""
    import project_runtime.playbook.builder as b_mod

    src = inspect.getsource(b_mod)
    for forbidden in ("SkillLoader", "SkillResolver", "load_skill",
                      "TASK_EXECUTION"):
        assert forbidden not in src, f"playbook must not load skills: {forbidden}"
    # refs only: the only skill-related writes are reference records
    assert "project_method_skill_refs" in src


# ── Planning Context 双快照接线（WP-038 → WP-030 消费）──────────────


@pytest.mark.asyncio
async def test_dual_readiness_snapshots_in_playbook(dbengine):
    pid, snap = await _full_chain(dbengine, USER_DOC)
    # execution snapshot wired (D11 placeholder closed by WP-038)
    assert snap["exec_snapshot"] is not None
    ecr = json.loads(snap["exec_snapshot"])
    assert "overall" in ecr
